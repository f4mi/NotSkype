# SkypeUI

Turn a Linksys CIT200 DECT handset into a working voice terminal with Telegram, Discord, or local echo — wrapped in a faithful Skype 2.x / Windows XP Luna UI.

## What it does

- Drives the CIT200 handset over USB HID (ring, caller ID, contacts, call state)
- Streams audio bidirectionally through the handset's built-in USB speaker and mic
- Pluggable voice backends: Telegram private calls, Discord voice, or local mock/echo
- XP-era Skype UI in tkinter with contacts, call history, and a settings editor
- Desktop control center with a system tray mode

## Requirements

- Python 3.10+
- A Linksys CIT200 handset connected via USB
- Windows (primary target; Linux may work with udev rules for HID access)

## Quick start

```bash
pip install -r requirements.txt
cp config.example.json config.json   # edit with your credentials
python run.py
```

### Other modes

```bash
python run.py --mode gui          # desktop control center
python run.py --mode mic_gui      # simple microphone window
python run.py --platform telegram_private  # use Telegram backend
python run.py --platform discord_          # use Discord backend
```

## Configuration

Copy `config.example.json` to `config.json` and fill in your platform credentials. The app will also offer a settings UI on first run.

For Telegram you'll need an `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org). For Discord, a bot token with voice permissions.

See `docs/` for detailed documentation on the HID protocol, platform abstraction, and architecture.

## Project structure

```
run.py                  # entry point
skypeui.py              # XP Luna theme UI (tkinter)
src/
  main.py               # orchestrator, config, call/contact logic
  cit200.py             # USB HID driver for the handset
  audio_bridge.py       # bidirectional PCM audio
  ui_bridge.py          # thread-safe tkinter marshaling
  platforms/             # voice backends (local_mock, telegram, discord)
control_center/          # desktop GUI and system tray launcher
assets/                  # XP theme icons and window controls
docs/                    # protocol docs and implementation notes
```

## Credits

- **cit200xSkype** — [philsmd/cit200xSkype](https://github.com/philsmd/cit200xSkype) — original CIT200 Linux/Skype project that pioneered the USB HID reverse-engineering for this handset
- **XP Luna theme assets** — [B00merang-Project/Windows-XP](https://github.com/ArtworkPascal/b00merang-Skype-for-Linux/blob/b00merang-Skype-for-Linux/Windows-XP) (icons and window controls)
- **Telethon** — Telegram MTProto client for signaling, contacts, and profile lookups
- **NTgCalls** — Telegram private-call media engine
- **Pyrogram** — alternative Telegram backend
- **discord.py** — Discord voice integration
- **hidapi** — USB HID communication with the CIT200 handset
- **sounddevice** / **PortAudio** — real-time audio capture and playback
- **customtkinter** — modern desktop control center GUI
- **pystray** — system tray integration
- **Pillow** — image handling for UI assets

## Security note

This is a hobby project. Credentials are stored as **plaintext JSON** in `config.json` and Telegram session tokens live as unencrypted files on disk. Treat `config.json` and `*.session` files like passwords. Both are gitignored.
