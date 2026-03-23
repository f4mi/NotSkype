# Open-Source Projects Used

This project builds on the following external open-source projects/libraries.

## Core Runtime

- `hidapi` (`hid` Python package)
  - Role: USB HID transport to the Linksys CIT200 handset.
  - Used in: `cit200.py`, `firmware_mock/mock_contacts_from_decomp.py`.

- `sounddevice` (PortAudio bindings)
  - Role: capture/playback for CIT200 USB audio streams.
  - Used in: `audio_bridge.py`.

- `numpy`
  - Role: audio conversion/resampling/meter math.
  - Used in: `audio_bridge.py`, `platforms/discord_.py`.

## Telegram/Voice Backends

- `Telethon`
  - Role: Telegram MTProto signaling, contacts, user/profile lookups.
  - Used in: `platforms/telegram_ntg.py`.

- `NTgCalls` (`ntgcalls`)
  - Role: Telegram private-call media engine.
  - Used in: `platforms/telegram_ntg.py`.

- `Pyrogram` + `py-tgcalls` (legacy/alternative backend)
  - Role: legacy Telegram voice backend implementation.
  - Used in: `platforms/telegram.py`.

## Discord Backend

- `discord.py[voice]`
  - Role: Discord voice channel connection and audio bridge.
  - Used in: `platforms/discord_.py`.

## Desktop Control Center

- `customtkinter`
  - Role: desktop GUI for run/config/log workflows.
  - Used in: `control_center/desktop_gui.py`.

- `pystray`
  - Role: system tray icon/menu for background service mode.
  - Used in: `control_center/tray_app.py`.

- `Pillow` (`PIL`)
  - Role: tray icon drawing/raster image handling.
  - Used in: `control_center/tray_app.py`.

## Low-Level Experimentation

- `PyUSB` (`usb.core`, `usb.util`)
  - Role: direct USB control-transfer experiment path against decompiled protocol assumptions.
  - Used in: `firmware_mock/mock_contacts_from_decomp_pyusb.py`.
