# Platform Modules

## `platforms/base.py`

Purpose:
- Defines `PlatformContact` and abstract `VoicePlatform` interface used by `main.py`.

Open-source dependencies used by this module:
- None (stdlib only).

Pseudocode:

```text
class VoicePlatform(ABC):
  callbacks: on_incoming_call, on_call_ended, on_audio_received, on_call_answered
  abstract methods: connect/disconnect/place_call/answer_call/end_call/hold_call/send_audio/get_contacts/get_status/set_status
  optional methods: get_contact_bio, get_last_incoming_target
```

Key contract guarantees:
- `get_contacts` returns `list[PlatformContact]` where `id` is stable, `status` is CIT200-compatible integer.
- `send_audio` accepts PCM16 LE mono frames at runtime sample rate (typically 16 kHz).
- callbacks are invoked from backend context and should be lightweight (main module handles heavy work asynchronously).

---

## `platforms/telegram_ntg.py`

Purpose:
- Primary Telegram private-call backend.
- Uses Telethon for signaling/contacts and NTgCalls for media.

Open-source dependencies used by this module:
- `telethon`
- `ntgcalls`

Pseudocode:

```text
connect():
  create Telethon client/session
  authorize
  create NTgCalls engine
  wire raw update callbacks

raw updates:
  parse incoming/outgoing phone call updates
  maintain pending call objects + peer maps
  emit incoming/answered/ended callbacks to main

place_call(target):
  resolve target -> user entity
  build DH/protocol handshake
  request call via Telethon
  connect ntgcalls P2P media

get_contacts():
  serve cache if fresh
  otherwise refresh from GetContactsRequest
  normalize to PlatformContact and sort

get_contact_bio(contact):
  use users.GetFullUserRequest
  cache about text per id
```

Detailed behavior:
- Session/auth:
  - Uses Telethon session file path from `session_name`.
  - Supports non-interactive startup and explicit auth mode.
- Call signaling:
  - Handles `PhoneCallRequested`, waiting/accepted/discard updates.
  - Tracks `_pending_incoming_*` and `_current_*` call fields.
  - Records `_last_incoming_target` for missed-call callback shim in `main.py`.
- Media pacing:
  - Buffers outbound PCM and slices to 10 ms frames (`_tx_frame_bytes`).
  - Maintains queue and timing watermark `_tx_next_ts_ms`.
- Contacts:
  - cache + lock to avoid parallel refresh storms
  - fallback behavior and deterministic sorting
  - caller label resolver suppresses numeric-handle leakage where possible
- Diagnostics:
  - periodic audio TX/RX counters
  - explicit logging for auth, contact refresh, call transitions, and media errors

---

## `platforms/local_mock.py`

Purpose:
- Local fake backend for development without network services.
- Simulates contacts, incoming calls, and echo audio.

Open-source dependencies used by this module:
- None (stdlib only).

Pseudocode:

```text
connect(): mark connected; optionally schedule auto incoming
place_call(target): mark in-call, emit call_answered, send tone bytes
send_audio(pcm): if in call and not held, echo back (optional gain)
get_contacts(): return synthetic contacts list
```

Notable details:
- `auto_incoming_after_s` and `call_me_on_start` provide deterministic UI testing hooks.
- `send_audio` path can apply gain-clamped PCM scaling for echo simulation.
- Generates synthetic tone payload on answer for quick audio-path validation.

---

## `platforms/telegram.py` (legacy path)

Purpose:
- Alternative/legacy Telegram backend using Pyrogram + py-tgcalls.

Open-source dependencies used by this module:
- `pyrogram`
- `py-tgcalls`

Pseudocode:

```text
connect(): start Pyrogram client, start PyTgCalls, wire voice close events
place_call(target): resolve target chat/user, join group call with AudioPiped source
send_audio(pcm): write to temporary raw audio pipe
get_contacts(): fetch contacts from Pyrogram and map statuses
```

Notes:
- Kept as alternate backend/reference; primary production backend is `telegram_ntg.py`.
- Uses temp-file/pipe style audio feed into py-tgcalls stack.

---

## `platforms/discord_.py` (experimental path)

Purpose:
- Discord backend using bot/voice APIs.

Open-source dependencies used by this module:
- `discord.py[voice]`
- `numpy`

Pseudocode:

```text
connect(): create discord client with voice/presence intents
place_call(target): resolve voice channel and connect VoiceClient
send_audio(pcm16_16k_mono): upsample to 48k stereo and feed AudioSource buffer
get_contacts(): walk guild members, map presence to CIT200 statuses
```

Notable details:
- Converts CIT200 16 kHz mono PCM to Discord 48 kHz stereo by repeat upsampling.
- Detects pseudo-incoming call by voice-state join in active channel.
- Intended for experimentation; feature coverage is below Telegram private backend.

---

## `platforms/__init__.py`

Purpose:
- Package marker (currently empty).

Open-source dependencies used by this module:
- None.

Notes:
- Empty marker file; package imports are explicit in callers.
