# Runs inside Windows Sandbox for the whole life of the sandbox (its logon command) and waits for
# work from the host. Adapted from the TikTok Live Reader add-on's sandbox\SandboxAgent.ps1.
#
# C:\Stage is the host's artifacts\sandbox (read-only): nvda\nvda_<version>.exe, addon\ (the built
# add-on, unpacked), python313\, driver\ (driver.py, household.py, sonos.py).
# C:\Runs is the host's artifacts\sandbox-runs (writable):
#   heartbeat.txt   every 2 seconds, so the host knows the agent is alive;
#   request.txt     from the host: run id, NVDA version, scenario, household;
#   <id>\           the run's results; done.txt holds the exit code.

$ErrorActionPreference = "Continue"
$stage = "C:\Stage"
$runs = "C:\Runs"
$work = "C:\Work"

# Nothing the sandbox plays may be heard: its sound reaches the host's speakers. Every playback
# device is muted at volume 0 before the agent starts and before every run; a run whose devices
# cannot all be muted does not start.
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class SandboxAudio
{
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] private class MMDeviceEnumerator { }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceEnumerator
    {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
    }

    [Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceCollection
    {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int Item(int index, out IMMDevice device);
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDevice
    {
        [PreserveSig] int Activate(ref Guid iid, int context, IntPtr parameters, [MarshalAs(UnmanagedType.IUnknown)] out object instance);
    }

    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IAudioEndpointVolume
    {
        [PreserveSig] int RegisterControlChangeNotify(IntPtr notify);
        [PreserveSig] int UnregisterControlChangeNotify(IntPtr notify);
        [PreserveSig] int GetChannelCount(out int count);
        [PreserveSig] int SetMasterVolumeLevel(float level, ref Guid context);
        [PreserveSig] int SetMasterVolumeLevelScalar(float level, ref Guid context);
        [PreserveSig] int GetMasterVolumeLevel(out float level);
        [PreserveSig] int GetMasterVolumeLevelScalar(out float level);
        [PreserveSig] int SetChannelVolumeLevel(int channel, float level, ref Guid context);
        [PreserveSig] int SetChannelVolumeLevelScalar(int channel, float level, ref Guid context);
        [PreserveSig] int GetChannelVolumeLevel(int channel, out float level);
        [PreserveSig] int GetChannelVolumeLevelScalar(int channel, out float level);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid context);
        [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
    }

    public static string MuteAll(out bool allMuted)
    {
        var report = new StringBuilder();
        allMuted = true;
        var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
        IMMDeviceCollection devices;
        if (enumerator.EnumAudioEndpoints(0, 1, out devices) != 0) { allMuted = false; return "no device list"; }
        int count;
        devices.GetCount(out count);
        for (var index = 0; index < count; index++)
        {
            IMMDevice device;
            object instance;
            var iid = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
            var none = Guid.Empty;
            if (devices.Item(index, out device) != 0 || device.Activate(ref iid, 23, IntPtr.Zero, out instance) != 0)
            {
                allMuted = false;
                report.AppendLine("device " + index + ": not reached");
                continue;
            }

            var volume = (IAudioEndpointVolume)instance;
            volume.SetMasterVolumeLevelScalar(0f, ref none);
            volume.SetMute(true, ref none);
            bool muted;
            float level;
            volume.GetMute(out muted);
            volume.GetMasterVolumeLevelScalar(out level);
            allMuted &= muted;
            report.AppendLine("device " + index + ": muted " + muted + ", volume " + level.ToString("0.00"));
        }

        report.Insert(0, count + " playback device(s)" + Environment.NewLine);
        return report.ToString();
    }
}
"@

function Set-SandboxSilent([string]$log) {
    $muted = $false
    $text = [SandboxAudio]::MuteAll([ref]$muted)
    if ($log) { Set-Content -Path $log -Value $text }
    return $muted
}

function Install-Nvda([string]$version) {
    $folder = "C:\NVDA\$version"
    if (-not (Test-Path (Join-Path $folder "nvda.exe"))) {
        $installer = Join-Path $stage "nvda\nvda_$version.exe"
        if (-not (Test-Path $installer)) { return $null }
        Start-Process -FilePath $installer -ArgumentList "--create-portable-silent", "--portable-path=$folder" -Wait
    }
    return $folder
}

function Stop-Nvda([string]$folder, [string]$log, [string]$keep) {
    if ($folder -and (Test-Path (Join-Path $folder "nvda.exe"))) {
        $quit = Start-Process -FilePath (Join-Path $folder "nvda.exe") -ArgumentList "-q" -PassThru
        # Keep the log as it grows while NVDA closes: after a crash the restarted
        # NVDA starts it again, smaller.
        $kept = 0
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and ((-not $quit.HasExited) -or (Get-Process -Name "nvda" -ErrorAction SilentlyContinue))) {
            if ($log -and $keep -and (Test-Path $log)) {
                $size = (Get-Item $log).Length
                if ($size -ge $kept) {
                    Copy-Item $log $keep -Force -ErrorAction SilentlyContinue
                    $kept = $size
                }
            }
            Start-Sleep -Milliseconds 100
        }
    }
    Get-Process -Name "nvda", "nvda_slave" -ErrorAction SilentlyContinue | Stop-Process -Force
}

# The simulated household goes into the staged copy of the add-on, never into the
# repository's: two modules beside the add-on's own, and four lines appended to
# vendors.py and discovery.py that call them when SONOS_SIM is set. Without the
# variable the staged add-on behaves exactly like the published one.
function Add-SimulatedHousehold([string]$addon) {
    $package = Join-Path $addon "globalPlugins\sonosController"
    Copy-Item (Join-Path $stage "driver\household.py") (Join-Path $package "_sandbox_household.py")
    Copy-Item (Join-Path $stage "driver\sonos.py") (Join-Path $package "_sandbox_sonos.py")
    $hook = @(
        "",
        "import os as _os",
        "if _os.environ.get('SONOS_SIM'):",
        "    from . import _sandbox_household as _sandbox",
        "    _sandbox.patch_vendors(globals())")
    Add-Content -Path (Join-Path $package "vendors.py") -Encoding UTF8 -Value $hook
    $hook[4] = "    _sandbox.patch_discovery(globals())"
    Add-Content -Path (Join-Path $package "discovery.py") -Encoding UTF8 -Value $hook
}

Set-SandboxSilent "" | Out-Null
reg add HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy /v VerifiedAndReputablePolicyState /t REG_DWORD /d 0 /f | Out-Null
CiTool.exe --refresh --json | Out-Null

while ($true) {
    Set-Content -Path (Join-Path $runs "heartbeat.txt") -Value (Get-Date -Format o)

    $request = Join-Path $runs "request.txt"
    if (Test-Path $request) {
        $lines = @(Get-Content $request)
        Remove-Item $request -Force
        $id, $version, $scenario, $household = $lines[0..3] | ForEach-Object { "$_".Trim() }
        $out = Join-Path $runs $id
        New-Item -ItemType Directory -Force -Path $out | Out-Null

        if (-not (Set-SandboxSilent (Join-Path $out "audio-muted.txt"))) {
            Set-Content -Path (Join-Path $out "console.txt") -Value "A sound device of the sandbox could not be muted; nothing was run."
            Set-Content -Path (Join-Path $out "done.txt") -Value "1"
            continue
        }

        Get-Process -Name "nvda", "nvda_slave", "python" -ErrorAction SilentlyContinue | Stop-Process -Force
        $nvdaHome = Install-Nvda $version
        if (-not $nvdaHome) {
            Set-Content -Path (Join-Path $out "console.txt") -Value "No installer for NVDA $version in $stage\nvda."
            Set-Content -Path (Join-Path $out "done.txt") -Value "1"
            continue
        }

        # A fresh NVDA configuration per run: no welcome dialog, update check or usage
        # question, the silent synthesizer, and the add-on installed by copying it into
        # the add-ons folder.
        $config = Join-Path $work "$id\config"
        New-Item -ItemType Directory -Force -Path $config | Out-Null
        Set-Content -Path (Join-Path $config "nvda.ini") -Encoding UTF8 -Value @(
            "[general]", "language = en", "showWelcomeDialogAtStartup = False", "playStartAndExitSounds = False",
            "[update]", "autoCheck = False", "startupNotification = False", "allowUsageStats = False", "askedAllowUsageStats = True",
            "[speech]", "synth = silence")
        $addon = Join-Path $config "addons\sonosController"
        Copy-Item (Join-Path $stage "addon") $addon -Recurse
        Add-SimulatedHousehold $addon
        $env:SONOS_SIM = if ($household) { $household } else { "default" }

        $nvdaLog = Join-Path $out "nvda.log"
        Start-Process -FilePath (Join-Path $nvdaHome "nvda.exe") `
            -ArgumentList "--config-path=$config", "--log-file=$nvdaLog", "--log-level=12"

        $python = Join-Path $stage "python313\python.exe"
        $driver = Join-Path $stage "driver\driver.py"
        $process = Start-Process -FilePath $python -ArgumentList "-X", "utf8", "`"$driver`"", $scenario, "`"$nvdaLog`"", "`"$out`"" `
            -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $out "console.txt") -RedirectStandardError (Join-Path $out "console.err.txt")
        $null = $process.Handle
        while (-not $process.WaitForExit(2000)) {
            Set-Content -Path (Join-Path $runs "heartbeat.txt") -Value (Get-Date -Format o)
        }

        Start-Sleep -Seconds 2
        Stop-Nvda $nvdaHome $nvdaLog (Join-Path $out "nvda-closing.log")
        $exitCode = $process.ExitCode
        # A crashed NVDA restarts and writes its log anew, so the driver's log check
        # cannot see it; the dump it leaves can.
        if (Test-Path (Join-Path $out "nvda_crash.dmp")) {
            Add-Content -Path (Join-Path $out "results.txt") -Value "FAIL  NVDA did not crash  - nvda_crash.dmp written; nvda-closing.log has the log until then"
            $exitCode = 1
        }
        Copy-Item (Join-Path $config "nvda.ini") $out -ErrorAction SilentlyContinue
        Remove-Item Env:\SONOS_SIM -ErrorAction SilentlyContinue
        Set-Content -Path (Join-Path $out "done.txt") -Value $exitCode
    }

    Start-Sleep -Seconds 2
}
