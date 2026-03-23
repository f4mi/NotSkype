# Rebuild Checklist From Docs Only

This is a practical, staged checklist to reconstruct the full program using only docs in this repo.

Use alongside:
- `docs/module-pseudocode/*.md`
- `docs/cit200-implementation-series/*.md`

## Stage 0 — Skeleton and Config

Goal:
- Boot a minimal app shell that can parse config and select runtime mode.

Implement:
- `load_config` / `save_config`
- defaults for `audio`, `contacts`, `hid`, `telegram`, `recording`
- mode dispatch (`phone`, `recorder`)

Acceptance checks:
- App starts without hardware/backend.
- Missing config keys are auto-populated.

## Stage 1 — HID Transport + Event Decode

Goal:
- Open CIT200 and decode handset frames into semantic events.

Implement:
- `CIT200Device.open/close/poll/_read/_write/_process_message`
- state-machine transitions for contacts, detail pages, dial accumulation
- event emitter (`CALL_BUTTON`, `END_CALL`, `DIAL`, `CONTACTS_REQUEST`, `CONTACT_DETAILS`, etc.)

Acceptance checks:
- `keepalive_contacts.py` can receive contacts/detail events from handset.
- `send_init` keeps handset responsive.

## Stage 2 — Frame Writers (Call + Contacts + Details)

Goal:
- Send correct handset payload families.

Implement:
- call/ring/status writers
- contacts writers:
  - generic (`send_contacts`)
  - legacy (`send_contacts_legacy`)
- details writers:
  - base page (`send_contact_details`)
  - numbers page (`send_contact_numbers_page`)
  - address/time page (`send_contact_bio_page`)

Acceptance checks:
- Contacts list renders on handset.
- MORE/details pages rotate and show correct structured fields.

## Stage 3 — Audio Bridge

Goal:
- Bidirectional PCM bridge between handset USB audio and platform callbacks.

Implement:
- `AudioBridge` discovery, capture callback, playback queue, stream lifecycle
- optional metering and buffer-drop diagnostics

Acceptance checks:
- `test_loopback.py` records and replays through handset.
- No hard crash on missing audio devices.

## Stage 4 — Platform Abstraction + Local Mock

Goal:
- Make runtime backend-agnostic and testable without network.

Implement:
- `VoicePlatform` interface + callback contract
- `LocalMockPlatform` with call simulation + contacts + echo audio

Acceptance checks:
- End-to-end call flow works locally (`phone` mode + local backend).

## Stage 5 — Telegram Private Backend

Goal:
- Connect real Telegram private-call signaling/media.

Implement:
- `TelegramNTGPlatform`:
  - auth/session
  - contact fetch cache
  - incoming/outgoing signaling
  - NTgCalls media hooks
  - bio lookup API

Acceptance checks:
- Outgoing and incoming calls connect.
- Audio TX/RX both directions confirmed.
- Contacts fetched and shown on handset.

## Stage 6 — Runtime Orchestration (`PhoneApp`)

Goal:
- Integrate handset, platform, and audio into one stable loop.

Implement:
- event wiring (`_wire_phone_events`, `_wire_platform_events`, `_wire_audio`)
- run loop with keepalive and lifecycle management
- call control handlers
- contacts queue/worker + legacy tick path

Acceptance checks:
- Stable continuous run; clean shutdown/restart.
- No deadlocks across poll loop and async tasks.

## Stage 7 — Contacts Hardening and Compatibility

Goal:
- Match firmware behavior while surviving real transport quirks.

Implement:
- cache/retry/backoff/stale-fallback
- DLL-exact paging path for telegram modes
- total/index correctness in wire payloads
- details index snapshot (`_last_details_snapshot`)

Acceptance checks:
- "Contacts unavailable" eliminated on known-good USB path.
- detail page index resolves to correct contact consistently.

## Stage 8 — Privacy + Callback Shim

Goal:
- Keep handset display private without changing call-side target resolution.

Implement:
- phone-only ID shim (display-side)
- reverse mapping for dial target resolution
- missed-calls callback interception to map fake number -> last real incoming target
- incoming caller label sanitization

Acceptance checks:
- Handset shows fake ID, backend still dials real target.
- Missed-calls callback works with fake displayed number.

## Stage 9 — Detail Overrides + Bio Enrichment

Goal:
- User-configurable per-contact mapping for details pages.

Implement:
- `contacts.detail_overrides` matcher
- page-specific override application (base/numbers/address-time)
- async bio refresh + cache

Acceptance checks:
- Overridden fields appear on correct pages for matched contacts.
- No cross-contact field mixing.

## Stage 10 — GUI/Tray/Tooling

Goal:
- Production operator UX and repeatable diagnostics.

Implement:
- `control_center` GUI + tray runner
- dependency installer
- contacts experiments runner + capture runner
- fake-call/manual-test scripts

Acceptance checks:
- GUI save/run/stop/restart works.
- Tray mode logs and recovery behavior are stable.

## Stage 11 — Regression Test Matrix (Manual)

Run at least:
- 3 cold starts + contacts open
- outgoing call + hangup
- incoming call + answer + hangup
- missed-calls callback
- details pages (0/1/2+) across multiple contacts
- record/playback loopback

Use:
- `manual_yn_test.py`
- `test_loopback.py`
- `contacts_experiments_tui.py`
- `run_phone_capture.py`

## Stage 12 — Freeze and Document

Finalize:
- lock known-good config profile
- archive logs for known-good run
- update docs when behavior/interface changes

Definition of done:
- Runtime stable on target machine.
- Contacts/call/details/privacy all pass regression matrix.
- Docs reflect current behavior and config keys.
