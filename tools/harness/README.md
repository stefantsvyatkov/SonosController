# Sonos Controller emulator

A headless stand-in for NVDA plus a simulated Sonos household, so the add-on
can be exercised without a screen reader, a speaker, or a network.

The real, unmodified `globalPlugins/sonosController` package is loaded against
fake NVDA modules.  A test presses keys the way NVDA dispatches them and reads
back the speech the user would have heard.

```
tools/harness/
  session.py           the emulator: boots the add-on, presses keys, collects speech
  nvda.py              fake config / ui / inputCore / scriptHandler / logHandler
  fakewx.py            fake wx that models Win32 control keyboard behaviour
  sonos.py             simulated speakers, groups, favorites, alarms, transport
  scenarios.py         runnable reproductions of the code-review findings
```

## Running it

```bash
python tools/harness/scenarios.py                    # every scenario
python tools/harness/scenarios.py group_up_cancels   # one scenario
python -m unittest tests.test_sonos_emulator -v      # the regression tests
```

The emulator needs nothing but the standard library.

## Writing a check

```python
from harness import Session

session = Session()                              # 4 rooms, 2 favorites, 1 alarm
session.press("kb:NVDA+shift+control+s")         # toggle the layer
session.select_room("Living Room")
print(session.press("kb:p"))                     # -> ['Sonos queue, Blue Monday, ...']
print(session.mode)                              # -> 'layer'
session.close()
```

`press()` reproduces NVDA's dispatch order: every registered
`decide_executeGesture` handler runs first, and only then is the gesture
looked up in the add-on's bindings.  That is what makes mode-switching bugs
visible, because the add-on unbinds keys from inside its own handler.

After each press the emulator waits until the worker thread, the simulated
network and the `wx.CallAfter` queue are all idle, so assertions do not race.

### Dialogs

Modal dialogs are driven from the test.  The dialog object exposes what a
screen reader would report:

```python
def inspect(dialog):
    print(dialog.read())            # title, then every visible control in tab order
    dialog.focus_on("Volume (%)")
    dialog.press("up")              # real Win32 key semantics per control type
    return False                    # False cancels, True presses the default button

session.on_dialog(inspect)
session.press("kb:enter")
```

Non-dialog prompts (`wx.MessageBox`, `wx.TextEntryDialog`, file pickers) are
answered through `session.responder`.

### The simulated household

```python
from harness.session import default_household

house = default_household(latency=0.05)   # 50 ms per network request
session = Session(household=house)

house.topology()                          # 'Kitchen | Living Room [playing] | ...'
house.find("Kitchen").playback.state
house.fail("Pause", SoCoUPnPException("no", error_code="701"))
session.count_calls()                     # how many requests since the last reset
```

Every operation that would cross the network goes through `Household.call`, so
round trips can be counted and given a realistic latency.  That is how the
responsiveness findings were measured rather than guessed.

Models carry their real capability sets (`Sonos Arc` is a soundbar with
satellites, `Sonos Move 2` has a battery, `Sonos Port` supports fixed volume),
so the capability filtering in `settings.py` is exercised properly and reading
an unsupported setting raises `AttributeError` the way SoCo does.

## Talking to real speakers

`Session(network="real")` leaves the vendored SoCo in place, so the add-on
performs genuine discovery and control on the LAN.  Everything else — the fake
NVDA, the speech transcript, the key dispatch — stays the same.  Use it only
against speakers you are willing to have change state.

## Limits worth knowing

- SSDP discovery is stubbed in `sim` mode; the saved-address reconnect path
  runs the real code in `discovery.py`.
- `fakewx` models focus, visibility, tab order and key handling, not layout.
  Anything about sizing, painting or what a screen reader actually says has
  to be checked in real wx.  Three probes used to do that against a wxPython
  virtual environment; `sandbox/` replaced them with real NVDA, which answers
  the same questions without a second wx to keep in step.
- Capability lookups (`is_soundbar`, `has_subwoofer`, ...) count as requests,
  because SoCo asks the device for them.
- The `Alarms` registry in `sonos.py` is a singleton, like SoCo's, so a test
  that cares about stale alarm state should call `session.alarms_module.reset()`.
- `fakewx.Dialog.modal_hook` is process-global; one `Session` at a time.
