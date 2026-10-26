# Testing in real NVDA

`tools/harness` runs the add-on against fake NVDA modules in a fraction of a
second, which is what the suite uses. It cannot tell you what a screen reader
actually says, whether a wx control raises the events NVDA listens for, or
whether a dialog is usable with the keyboard. This does: a Windows Sandbox with
a real, portable NVDA, the real add-on, and real key presses.

```bash
.\sandbox\Run-Sandbox.ps1 -Scenario smoke
.\sandbox\Run-Sandbox.ps1 -Scenario alarms -Nvda 2026.2
.\sandbox\Run-Sandbox.ps1 -Scenario norooms -Household empty
.\sandbox\Run-Sandbox.ps1 -Stop
```

The first run starts the sandbox and leaves it open; later runs reuse it. It
starts **minimised, without taking the focus**, and the agent **mutes every
playback device of the sandbox before each run** - a run whose devices cannot
all be muted does not start, because the sandbox's sound comes out of the
host's speakers.

Windows Sandbox runs one instance at a time and the BST Radio and TikTok Live
Reader projects use it too. `-Stop` when finished, and tell those sessions.

## What runs

`Run-Sandbox.ps1` builds the package with `tools/build.py` and unpacks it into
the stage, so a run exercises exactly what would be published rather than a
copy of the source tree. `artifacts\sandbox` is mapped read-only as `C:\Stage`
and `artifacts\sandbox-runs` writable as `C:\Runs`; the agent writes a
heartbeat there and takes one request at a time.

Per run, `SandboxAgent.ps1` makes a portable NVDA of the requested version once
(`C:\NVDA\<version>`) and starts it with a fresh configuration: silent
synthesizer, no welcome or update dialogs, log level 12, the staged add-on
copied into the add-ons folder. `driver.py` then presses real keys with
SendInput, reads the speech back out of NVDA's log and writes `results.txt`:
one PASS/FAIL line per check, then the whole transcript.

Each call removes the earlier calls' run folders, so only the latest results
stay. Changing `SandboxAgent.ps1` needs `-Stop` first: the running agent keeps
its old copy.

## Why the speakers are simulated

The sandbox's network sits behind its own virtual switch, so SSDP never reaches
the speakers on the house network: real Sonos is not available from inside, at
any price. The screen reader is the half that cannot be faked usefully, so that
is the half that is real here, and the speakers are simulated.

The agent copies `household.py` and `tools/harness/sonos.py` - the simulator
the host suite already uses - into the **staged** add-on as
`_sandbox_household.py` and `_sandbox_sonos.py`, then appends four lines to the
staged `vendors.py` and `discovery.py` that call them when `SONOS_SIM` is set.
The add-on in the repository is never modified, and with the variable unset the
staged copy behaves exactly like the published one.

`tests/test_sandbox_staging.py` does that same staging on the host and imports
the result, so this hook is checked by the ordinary suite instead of only
failing as a sandbox run that quietly talked to nothing.

## Scenarios

- `smoke` - the add-on loads, the layer turns on and names a room, the arrows
  move the volume one step, play and track reporting answer, the layer turns
  off.
- `pagekeys` - Page Up and Page Down move five, in the arrows' direction, both
  for the volume and inside the speaker settings.
- `alarms` - the alarm dialog: Up gives the next larger hour, the hours wrap
  round past the smallest, and a new alarm repeats Once.
- `settings` - the settings sweep, and the room named once rather than twice
  ("Move 2, Sonos Move 2").
- `norooms` - an empty household is reported instead of hanging.

Every scenario also fails if NVDA logged an error or left a crash dump. A
crashed NVDA restarts and writes its log anew, so the driver's own log check
cannot see it; the agent keeps the log written while NVDA closes as
`nvda-closing.log`.

## What has to be staged by hand

`artifacts\sandbox\nvda\nvda_<version>.exe` - the NVDA installers to test
against, one per version. Add a version by dropping its installer there and
naming it on the command line. The portable Python for the driver is taken from
`.tools\python313`.
