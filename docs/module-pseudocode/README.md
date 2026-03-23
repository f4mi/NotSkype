# Module Pseudocode Docs

This folder documents every maintained Python module in this repository with implementation-oriented pseudocode.

It complements `docs/cit200-implementation-series/`:
- implementation-series = protocol/system behavior by topic
- module-pseudocode = codebase walkthrough by file/module

## Open-Source Projects Used

See `docs/module-pseudocode/open-source-projects-used.md` for the canonical list.

## Files In This Folder

- `runtime-core.md`
  - `main.py`
  - `cit200.py`
  - `audio_bridge.py`
- `platforms.md`
  - `platforms/base.py`
  - `platforms/telegram_ntg.py`
  - `platforms/local_mock.py`
  - `platforms/telegram.py`
  - `platforms/discord_.py`
  - `platforms/__init__.py`
- `control-center.md`
  - `control_center/desktop_gui.py`
  - `control_center/tray_app.py`
  - `control_center/dep_installer.py`
  - `control_center/__main__.py`
  - `control_center/__init__.py`
  - `desktop_gui.py`
  - `tray_app.py`
- `tooling-and-tests.md`
  - `contacts_experiments_tui.py`
  - `run_phone_capture.py`
  - `keepalive_contacts.py`
  - `firmware_mock/mock_contacts_from_decomp.py`
  - `firmware_mock/mock_contacts_from_decomp_pyusb.py`
  - `fake_calls_tui.py`
  - `tui.py`
  - `manual_yn_test.py`
  - `test_loopback.py`
- `config-reference.md`
  - runtime config keys used by maintained modules
- `event-and-frame-reference.md`
  - runtime event/frame mapping cheat sheet
- `rebuild-checklist-from-docs.md`
  - staged reconstruction + acceptance checklist

## Reading Order

1. `runtime-core.md`
2. `platforms.md`
3. `control-center.md`
4. `tooling-and-tests.md`
5. `config-reference.md`
6. `event-and-frame-reference.md`
7. `rebuild-checklist-from-docs.md`
8. `open-source-projects-used.md`
