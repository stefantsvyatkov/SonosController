<#
.SYNOPSIS
    Runs a scenario of the add-on in real NVDA inside a Windows Sandbox that stays open between runs.

.DESCRIPTION
    Adapted from the TikTok Live Reader add-on's sandbox\Run-Sandbox.ps1. The first run starts
    Windows Sandbox minimised without taking the focus, with SandboxAgent.ps1 as its logon command;
    later runs reuse it. What is staged is the built package, unpacked, so a run exercises exactly
    what would be published - with the simulated household behind it, because the sandbox's network
    is behind its own virtual switch and cannot reach the speakers on the house network.

    The agent mutes every sound device of the sandbox before each run; nothing is ever heard.

.PARAMETER Scenario
    A scenario of driver.py: smoke, pagekeys, alarms, settings or norooms.

.PARAMETER Nvda
    NVDA versions to run on; each needs artifacts\sandbox\nvda\nvda_<version>.exe.

.PARAMETER Household
    "default" (the household the host tests use) or "empty" (no speakers, for norooms).

.PARAMETER Stop
    Close the sandbox. Do this when finished: BST Radio and TikTok Live Reader use it too.

.EXAMPLE
    .\sandbox\Run-Sandbox.ps1
    .\sandbox\Run-Sandbox.ps1 -Scenario alarms -Nvda 2026.2
    .\sandbox\Run-Sandbox.ps1 -Scenario norooms -Household empty
    .\sandbox\Run-Sandbox.ps1 -Stop
#>
[CmdletBinding()]
param(
    [ValidateSet("smoke", "pagekeys", "alarms", "settings", "norooms")]
    [string]$Scenario = "smoke",
    [string[]]$Nvda = @("2026.2"),
    [ValidateSet("default", "empty")]
    [string]$Household = "default",
    [string]$Python = ".tools\python313\python.exe",
    [switch]$Stop,
    [int]$TimeoutMinutes = 10
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $repo "artifacts\sandbox"
$runs = Join-Path $repo "artifacts\sandbox-runs"
$heartbeat = Join-Path $runs "heartbeat.txt"
$sandboxExe = Join-Path $env:windir "System32\WindowsSandbox.exe"

function Test-AgentAlive {
    if (-not (Get-Process -Name "vmmemWindowsSandbox" -ErrorAction SilentlyContinue)) { return $false }
    if (-not (Test-Path $heartbeat)) { return $false }
    return ((Get-Date) - (Get-Item $heartbeat).LastWriteTime).TotalSeconds -lt 20
}

if ($Stop) {
    Get-Process -Name "WindowsSandboxRemoteSession", "WindowsSandboxClient" -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host "Windows Sandbox closed."
    return
}

if (-not (Test-Path $sandboxExe)) { throw "Windows Sandbox is not turned on (Windows Features)." }
New-Item -ItemType Directory -Force -Path $stage, $runs | Out-Null

# ---- Stage ------------------------------------------------------------------------

# Build and unpack the package, so what runs in NVDA is what would be published.
$interpreter = Join-Path $repo $Python
if (-not (Test-Path $interpreter)) { $interpreter = $Python }
$built = & $interpreter (Join-Path $repo "tools\build.py")
if ($LASTEXITCODE -ne 0) { throw "Building the add-on failed." }
$package = ($built | Select-Object -Last 1).Trim()
if (-not (Test-Path $package)) { throw "The build reported $package, which is not there." }

$addon = Join-Path $stage "addon"
if (Test-Path $addon) { Remove-Item $addon -Recurse -Force }
# Expand-Archive insists on a .zip name, and the package is a .nvda-addon.
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory((Resolve-Path $package), $addon)
Write-Host "Staged $(Split-Path -Leaf $package)."

$driver = Join-Path $stage "driver"
New-Item -ItemType Directory -Force -Path $driver | Out-Null
Copy-Item (Join-Path $PSScriptRoot "driver.py"), (Join-Path $PSScriptRoot "household.py") $driver -Force
Copy-Item (Join-Path $repo "tools\harness\sonos.py") $driver -Force
Copy-Item (Join-Path $PSScriptRoot "SandboxAgent.ps1") $stage -Force

if (-not (Test-Path (Join-Path $stage "python313\python.exe"))) {
    $portable = Join-Path $repo ".tools\python313"
    if (-not (Test-Path (Join-Path $portable "python.exe"))) {
        throw "No portable Python in .tools\python313 to stage for the driver."
    }
    Copy-Item $portable (Join-Path $stage "python313") -Recurse
}
foreach ($version in $Nvda) {
    if (-not (Test-Path (Join-Path $stage "nvda\nvda_$version.exe"))) {
        throw "No installer for NVDA $version in $stage\nvda."
    }
}

# ---- Start the sandbox once ----------------------------------------------------------

if (-not (Test-AgentAlive)) {
    if (Get-Process -Name "vmmemWindowsSandbox" -ErrorAction SilentlyContinue) {
        throw "A Windows Sandbox without this agent is running (BST Radio's or TikTok Live Reader's?). Close it first."
    }

    Remove-Item $heartbeat -Force -ErrorAction SilentlyContinue
    $configuration = @"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Enable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$stage</HostFolder>
      <SandboxFolder>C:\Stage</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>$runs</HostFolder>
      <SandboxFolder>C:\Runs</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Stage\SandboxAgent.ps1</Command>
  </LogonCommand>
</Configuration>
"@
    $configurationPath = Join-Path $stage "SonosController.wsb"
    Set-Content -Path $configurationPath -Value $configuration -Encoding UTF8

    Add-Type -Namespace SonosSandbox -Name Native -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool ShowWindowAsync(System.IntPtr window, int command);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool IsIconic(System.IntPtr window);
"@

    function Hide-SandboxWindow {
        foreach ($session in Get-Process -Name "WindowsSandboxRemoteSession", "WindowsSandboxClient" -ErrorAction SilentlyContinue) {
            $window = $session.MainWindowHandle
            if ($window -ne [IntPtr]::Zero -and -not [SonosSandbox.Native]::IsIconic($window)) {
                # 7 is SW_SHOWMINNOACTIVE: minimised, and the focus stays where it was.
                [SonosSandbox.Native]::ShowWindowAsync($window, 7) | Out-Null
            }
        }
    }

    Write-Host "Starting Windows Sandbox (minimised; it stays open for later runs)..."
    Start-Process -FilePath $sandboxExe -ArgumentList "`"$configurationPath`""
    $deadline = (Get-Date).AddMinutes(4)
    while (-not (Test-AgentAlive)) {
        if ((Get-Date) -gt $deadline) { throw "The sandbox agent did not start." }
        Hide-SandboxWindow
        Start-Sleep -Milliseconds 300
    }
    for ($i = 0; $i -lt 20; $i++) {
        Hide-SandboxWindow
        Start-Sleep -Milliseconds 300
    }
}

# ---- Run, one NVDA version after the other ----------------------------------------------

# Only the latest call's results are kept.
Get-ChildItem $runs -Directory | Remove-Item -Recurse -Force
$failed = 0
foreach ($version in $Nvda) {
    $id = (Get-Date -Format "yyyyMMdd-HHmmss") + "-$version"
    $temporary = Join-Path $runs "request.tmp"
    Set-Content -Path $temporary -Value @($id, $version, $Scenario, $Household) -Encoding Ascii
    Move-Item $temporary (Join-Path $runs "request.txt") -Force

    $results = Join-Path $runs $id
    $done = Join-Path $results "done.txt"
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    Write-Host "NVDA $version, scenario $Scenario, household $Household ($id)..."
    while (-not (Test-Path $done)) {
        if ((Get-Date) -gt $deadline) { Write-Warning "Timed out. Partial results: $results"; $failed++; break }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-Path $done)) { continue }

    $code = [int]((Get-Content $done -Raw).Trim())
    if ($code -ne 0) { $failed++ }
    $summary = Join-Path $results "results.txt"
    if (Test-Path $summary) { Get-Content $summary -Encoding UTF8 | Write-Host }
    else { Get-Content (Join-Path $results "console.txt"), (Join-Path $results "console.err.txt") -ErrorAction SilentlyContinue | Write-Host }
    Write-Host ""
}

Write-Host "Results: $runs"
exit $failed
