# Control Center Modules

## `control_center/desktop_gui.py`

Purpose:
- Full desktop UI to run service modes, edit config, select contacts, edit detail overrides, and view/export logs.

Open-source dependencies used by this module:
- `customtkinter`

Pseudocode:

```text
startup:
  build tabs: Run / Config / Logs
  load config.json into form fields
  start periodic log-queue drain

Run tab:
  save config
  build subprocess command for selected mode
  launch embedded process (captured logs) OR external console mode

Config tab:
  map form -> config dict
  preserve unknown keys by merging onto _raw_config
  contacts picker fetches Telegram contacts and writes selected_contacts
  detail_overrides editor validates JSON schema-ish fields

Logs tab:
  append timestamped lines
  clear / export log history
```

Detailed config coverage in GUI:
- Platform + mode selection
- Telegram credentials/session
- Audio tuning (sample/chunk/meter)
- Contacts controls:
  - selected-only / prioritize
  - selected_contacts editor
  - Telegram contacts picker
  - `detail_overrides` JSON editor
  - `Insert Override Template`
  - `Validate Overrides` (birthday/timezone/gender/phones sanity checks)
- HID transport knobs for contact bursts
- Recording directory + auto-record toggle

Process model:
- Embedded mode: subprocess logs routed into GUI queue/text box.
- External mode: detached shell launch for easier interactive debugging.
- Stop/restart logic protects against orphan processes and updates status indicator.

---

## `control_center/tray_app.py`

Purpose:
- System tray service manager for auto-running `main.py --mode phone`.

Open-source dependencies used by this module:
- `pystray`
- `Pillow`
- `customtkinter` (settings dialog path)

Pseudocode:

```text
run tray:
  create icon + menu
  start service subprocess

start():
  read config for platform/debug
  spawn main.py phone mode
  start reader thread for stdout -> tray log file

stop():
  terminate subprocess with timeout + kill fallback

settings window:
  edit key config values
  save/restart service
```

Operational details:
- Writes persistent log to `logs/tray_service.log`.
- Keeps recent log ring buffer for export.
- Menu actions:
  - start/stop/restart service
  - open logs/recordings directories
  - toggle auto-record setting
  - open compact settings dialog

---

## `control_center/dep_installer.py`

Purpose:
- Boot-time dependency checker/installer with GUI splash or console fallback.

Open-source dependencies used by this module:
- None directly (imports/install checks are dynamic).

Pseudocode:

```text
ensure_dependencies(include_optional=False):
  missing = import-check(REQUIRED [+ OPTIONAL])
  if none missing: return
  if pip unavailable: print error and return
  if tkinter/display unavailable: console install flow
  else: GUI splash install flow
  after install: re-import each package and show native-lib hints if needed
```

Native-hint behavior:
- Provides platform-specific guidance for missing native libs (e.g., hidapi/portaudio/tkinter packages).
- Detects headless/no-display and gracefully falls back to console installer.

---

## `control_center/__main__.py`

Purpose:
- Entry point for `python -m control_center`; selects GUI or tray mode.

Open-source dependencies used by this module:
- None directly; dispatches to modules that use external packages.

Pseudocode:

```text
parse --tray
run ensure_dependencies()
if --tray: import launch_tray and run
else: import launch_gui and run
on crash: print fatal + traceback
```

Runtime guarantees:
- Never hard-crashes silently: mode launch is wrapped and surfaced as explicit fatal message.
- Dependency installer runs before GUI/tray imports to reduce bootstrap friction.

---

## `control_center/__init__.py`

Purpose:
- Re-export convenience API: `launch_gui`, `launch_tray`.

Open-source dependencies used by this module:
- None.

---

## `desktop_gui.py` (wrapper)

Purpose:
- Compatibility wrapper that forwards to `control_center.desktop_gui.launch_gui`.

Open-source dependencies used by this module:
- None directly.

Pseudocode:

```text
import launch_gui from control_center.desktop_gui
if run as script: launch_gui()
```

---

## `tray_app.py` (wrapper)

Purpose:
- Compatibility wrapper that forwards to `control_center.tray_app.launch_tray`.

Open-source dependencies used by this module:
- None directly.

Pseudocode:

```text
import launch_tray from control_center.tray_app
if run as script: launch_tray()
```
