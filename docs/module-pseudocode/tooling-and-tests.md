# Tooling and Test Modules

## `contacts_experiments_tui.py`

Purpose:
- Interactive experiment runner to compare contacts transport/mode combinations and record YES/NO outcomes.

Open-source dependencies used by this module:
- None (stdlib only).

Pseudocode:

```text
load base config
build experiment matrix (main modes + firmware mock scripts)
user selects experiments
for each experiment:
  apply temporary config overrides
  run process and tee output to log file
  user marks result (y/n/s)
restore original config
write summary.json
```

Experiment dimensions currently modeled:
- contacts transport mode (`feature_only` / `output_only`)
- emergency ack on/off
- compatibility resend on/off
- contacts max size variants
- cache TTL stress variants
- shim/selection behavior variants
- standalone firmware mock runners

---

## `run_phone_capture.py`

Purpose:
- Launches `main.py --mode phone` and tees stdout/stderr to both terminal and file.

Open-source dependencies used by this module:
- None.

Pseudocode:

```text
parse args (platform/python/log options)
build command
spawn child process with stdout pipe
stream lines to console + log file
on Ctrl+C terminate child gracefully
```

Usage pattern:
- Primary quick-capture harness for reproductions shared in chat/log analysis.
- Keeps exact command line and log path printed at process start.

---

## `keepalive_contacts.py`

Purpose:
- Minimal harness to keep handset alive and serve static contacts/detail pages.

Open-source dependencies used by this module:
- Indirectly via `cit200.py` (`hidapi`).

Pseudocode:

```text
open CIT200
register CONTACTS_REQUEST and CONTACT_DETAILS handlers
loop:
  phone.poll()
  periodically send_init(ONLINE)

on contacts request:
  rotate demo contacts by requested index
  send contacts list

on details request:
  mode 0 -> send base profile page
  mode 1 -> send packed phone numbers page
  mode 2+ -> send address/time page
```

Why this exists:
- Fast binary/protocol sanity checks without Telegram/Discord dependencies.
- Useful when isolating handset parsing bugs from backend logic.

---

## `firmware_mock/mock_contacts_from_decomp.py`

Purpose:
- Pure HIDAPI firmware-style contacts responder (decomp-driven) for transport/protocol diagnostics.

Open-source dependencies used by this module:
- `hidapi` (`hid`).

Pseudocode:

```text
open HID device
loop:
  periodic send_init
  read frame
  detect contacts button family
  read continuation frame for requested index
send qwerty==2 contacts response (index 0 sends 2 entries)
```

Design constraints:
- Intentionally minimal and decomp-shaped.
- Avoids app-layer abstractions; talks directly to HID frames.

---

## `firmware_mock/mock_contacts_from_decomp_pyusb.py`

Purpose:
- Same firmware mock idea, but with PyUSB control transfers.

Open-source dependencies used by this module:
- `pyusb`

Pseudocode:

```text
find USB device/interface
claim interface
loop:
  send init via ctrl_transfer
  read interrupt endpoint
  decode contacts button/index
emit qwerty==2 contact frames via ctrl_transfer
release interface on exit
```

Use case:
- Validate assumptions against direct USB control path when HIDAPI path behaves differently.

---

## `fake_calls_tui.py`

Purpose:
- Manual CLI to trigger fake incoming calls on handset for UI/flow testing.

Open-source dependencies used by this module:
- Indirectly via `cit200.py` (`hidapi`).

Pseudocode:

```text
open CIT200
wire answer/reject/end/call-button events
start poll thread with periodic send_init
REPL commands: call <name>, end, status, quit
on call command:
  ring handset
  display caller id (repeated nudge)
```

Useful for:
- Incoming-call UI and callback-flow tests without needing external platform events.

---

## `tui.py`

Purpose:
- Compatibility shim that forwards to `main.py --mode recorder`.

Open-source dependencies used by this module:
- None.

Pseudocode:

```text
rewrite argv to add --mode recorder
import and execute main.main()
```

---

## `manual_yn_test.py`

Purpose:
- Guided human yes/no test checklist; writes CSV results.

Open-source dependencies used by this module:
- None.

Pseudocode:

```text
define static test steps
prompt operator for y/n/s per step
collect notes
write timestamped CSV
print pass/fail summary
```

Output:
- Timestamped CSV in `test_logs/` by default.
- Designed for manual regression sweeps and side-by-side runs.

---

## `test_loopback.py`

Purpose:
- Combined HID + audio loopback sanity check.

Open-source dependencies used by this module:
- Indirectly uses `hidapi`, `sounddevice`, `numpy` via imported modules.

Pseudocode:

```text
open CIT200 + AudioBridge
wait for call button
on call button:
  capture mic for N seconds
  save WAV
  play captured PCM back to handset
main loop sends keepalive and polls HID
```

Why this matters:
- Quick confidence test for complete HID+audio loop with minimal dependencies.
- Produces `cit200_loopback.wav` as reproducible artifact.
