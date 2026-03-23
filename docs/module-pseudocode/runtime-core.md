# Runtime Core Modules

This page is the implementation-level map for the runtime core.

## `main.py`

### Responsibility

- Entry point and coordinator for runtime modes:
  - `--mode phone` (`PhoneApp`)
  - `--mode recorder` (`RecorderTUI`)
  - setup/config repair path (`run_setup_tui`)
- Configuration loading/defaulting/repair (`_ensure_contacts_defaults`, Telegram env overrides).
- End-to-end orchestration between HID transport, backend platform, and audio bridge.

### Open-source dependencies used by this module

- No direct third-party import for core logic.
- Integrates modules that use `hidapi`, `sounddevice`, `telethon`, `ntgcalls`, etc.

### Main classes/functions

- `RecorderTUI`: local on-device recorder/playback UX for call mode diagnostics.
- `PhoneApp`: production runtime bridge (CIT200 <-> platform + audio).
- `load_config` / `save_config`: config I/O and migration defaults.

### Core state in `PhoneApp`

- Call state:
  - `_current_call_id`, `_incoming_caller`, backend in-call flags via platform.
- Contacts state:
  - `_contacts_cache`, `_contacts_cache_at`, refresh timers/retry knobs.
  - `_visible_contacts`, `_last_ordered_contacts`, `_last_details_snapshot`.
  - `_contact_bio_cache`, selected filtering and priority flags.
- Privacy and shim state:
  - phone-only caller/id masking
  - callback shim keys for missed-calls recall
  - phone display/reverse mapping dictionary
- Recording state:
  - dual-track buffers (`_record_mic_buffer`, `_record_remote_buffer`), file handles, flush schedule.

### End-to-end phone mode pseudocode

```text
main():
  cfg = load_config()
  maybe run setup wizard
  if mode == recorder:
    RecorderTUI(cfg).start()
  else:
    app = PhoneApp(cfg, selected_platform)
    asyncio.run(app.run())

PhoneApp.run():
  phone.open()
  wire handlers
  platform.connect()
  prefetch contacts (optional)
  running = true
  while running:
    phone.poll()                         # decode handset frames -> emit events
    await _service_contacts_legacy_tick()# legacy qwerty timing path
    periodic phone.send_init/status/time
    flush/reap background tasks
  finally:
    stop recording
    stop audio bridge
    platform.disconnect()
    phone.close()
```

### Handset event handling pseudocode

```text
_on_phone_call():
  if incoming exists -> answer path
  else -> setup outgoing call mode on handset

_on_phone_dial(callee):
  target = _resolve_dial_target(callee)
  _start_audio()
  platform.place_call(target)

_on_phone_hangup():
  platform.end_call()
  phone.end_call_from_handset()
  _stop_audio()

_on_phone_contacts(index):
  coalesce and queue index requests
  process via worker or legacy tick
```

### Contacts list pipeline details

```text
_get_ordered_contacts():
  if cache fresh -> use cache
  elif stale and allowed -> use stale + refresh in background
  else -> refresh with timeout/retries/backoff

  ordered = sort(online-first or alphabetical)
  ordered = apply selected_contacts mode
  _last_ordered_contacts = ordered

_fetch_and_send_contacts(index):
  ordered = _get_ordered_contacts()
  if empty:
    attempt forced refresh
    if still empty -> resend last known payload or send empty

  _rebuild_phone_shim_lookup(ordered)
  page = _build_contacts_page(ordered, index)
  _visible_contacts = page
  _last_details_snapshot = legacy_ordered_or_ordered

  if telegram legacy mode:
    phone.send_contacts_legacy(...)
  else:
    phone.send_contacts(..., total_count)
```

### Contact details mapping pipeline

```text
_on_phone_contact_details(index, detail_type):
  resolve contact from:
    1) _last_details_snapshot (legacy-safe)
    2) _visible_contacts
    3) _last_ordered_contacts

  override = _match_contact_detail_override(contact)
  mode = parse(detail_type)

  if mode == 0:
    phone.send_contact_details(
      handle/name/status,
      language/birthday/gender from override
    )

  elif mode == 1:
    phone.send_contact_numbers_page(
      office/home/mobile from override
    )

  else:
    bio = override.bio or cached_bio or "Loading bio..."
    address_text = compose(bio + city/province/country)
    hh:mm from override timezone
    phone.send_contact_bio_page(address_text, hh, mm)
    if bio fetched asynchronously -> refresh page payload
```

### Dial-target resolution details

- Accepts handset raw string and tries, in order:
  1. missed-calls callback shim match (maps fake visible number back to last incoming target)
  2. phone-only id shim reverse-map (shim text -> real handle/id)
  3. selected visible contacts
  4. full cached contacts
  5. raw passthrough fallback

### Observability and diagnostics

- Contacts diagnostics counters: requests/sent/empty/stale-resend/refresh_ok/refresh_fail.
- Preview logs: ordered contacts sample, wire sample, health snapshots.
- Detail-resolution log: `details index -> id/handle/name`.

---

## `cit200.py`

### Responsibility

- Single-source implementation of CIT200 HID protocol framing/state-machine.
- Decodes handset frames into semantic events.
- Encodes command responses for call, contacts, details, status, ring, and sync.

### Open-source dependencies used by this module

- `hidapi` (`hid` Python package).

### Main entities

- `Status`: handset status enum mapping.
- `Contact`: wire-ready contact DTO.
- `Event`: emitted handset events.
- `CIT200Device`: HID driver and protocol engine.

### HID transport details

- Supports transport modes:
  - `feature_only`
  - `output_only`
  - `dual`
- Contacts-specific mode override and adaptive fallback for reliability.
- Windows-specific feature-handle helpers for alternate feature write path.
- Trace/timeline instrumentation for frame-level debugging.

### Poll/decode pseudocode

```text
poll():
  frame = _read()
  if frame:
    _process_message(frame)

_process_message(frame):
  if stateless command family:
    emit event directly (CALL_BUTTON, END_CALL, PING, etc.)
    maybe switch state (contacts/detail/status request)

  if state == contacts_index_wait:
    emit(CONTACTS_REQUEST, index)
    state = idle

  if state == details_wait:
    emit(CONTACT_DETAILS, index, detail_type)
    state = idle

  if state == dial_accumulate:
    buffer text chunks until terminal frame
    confirm_dial()
    emit(DIAL, callee)
    state = idle
```

### Contacts writer variants

- `send_contacts`: generic paged writer with explicit `total_count`.
- `send_contacts_legacy`: decomp-aligned qwerty==2 behavior:
  - index `0` sends records `0` and `1`
  - index `n>0` sends one record
  - invalid index emits empty contacts response family

### Details-page frame writers

- `send_contact_details`: page 0 (`c9`, handle/lang/birthday/gender/status).
- `send_contact_numbers_page`: page 1 (`c6`, packed phone digits).
- `send_contact_bio_page`: page 2+ (`c8`, address-like text + clock bytes).

### Helper encoders

- `_sanitize_latin1`: safe handset character set.
- `_pad`: fixed-width protocol fields.
- `_pack_tel_digits`: BCD-ish nibble-pair number encoding with `A` filler.
- `_birthday_bytes`: protocol birthday nibble bytes from `YYYY-MM-DD`.

---

## `audio_bridge.py`

### Responsibility

- Real-time bidirectional PCM bridge between CIT200 USB audio and platform callbacks.
- Independent control of capture and playback paths.

### Open-source dependencies used by this module

- `sounddevice`
- `numpy`

### Device discovery

- Searches all `sounddevice` devices for name containing `CIT200`.
- Separately captures input and output indices.

### Runtime pseudocode

```text
start():
  ensure devices
  start_capture()
  start_playback()

capture callback:
  float32 block -> int16 PCM bytes
  on_audio_captured(pcm)
  meter(capture)

playback callback:
  needed = frames * channels * 2
  pop from queue or pad silence on underrun
  int16 -> float32
  write outdata
  meter(playback)

play_audio(pcm):
  if playback stopped: drop and log
  else queue bytes
  trim head on overflow to cap latency
```

### Metering subsystem

- Per-direction counters:
  - sample count, RMS sum, peak, clip count, byte/chunk counters
- Playback-specific counters:
  - underrun events/bytes
  - dropped bytes from queue trims
- Periodic one-line meter logs for health checks.
