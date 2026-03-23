# Event and Frame Reference (Runtime-Focused)

This page summarizes how maintained modules map handset frames <-> semantic events.

For deeper protocol catalogs, also see `docs/cit200-implementation-series/09-frame-catalog.md`.

## Handset Events (`cit200.py` -> `main.py`)

- `CALL_BUTTON`
  - Meaning: user pressed green call key.
  - Runtime action: answer incoming or prepare outgoing call mode.

- `END_CALL`
  - Meaning: user pressed red end key.
  - Runtime action: end backend call and reset audio/call state.

- `DIAL(callee)`
  - Meaning: handset emitted callee text from dial flow.
  - Runtime action: resolve target (with shim reverse mapping) and place call.

- `CONTACTS_REQUEST(index)`
  - Meaning: contacts menu opened/scrolled at `index`.
  - Runtime action: list fetch/paging/send.

- `CONTACT_DETAILS(index, detail_type)`
  - Meaning: user opened details page for contact index.
  - Runtime action: page-specific detail payload (`0/1/2+`).

- `STATUS_CHANGE(new_status)`
  - Meaning: handset status changed.
  - Runtime action: backend status update + status echo/confirm.

- `ANSWER_INCOMING`, `REJECT_INCOMING`, `HOLD_RESUME`
  - Meaning: call control keys from handset.
  - Runtime action: backend answer/reject/hold toggle paths.

## Platform Events (`platforms/*` -> `main.py`)

- `on_incoming_call(call_id, caller_label)`
  - Runtime action: ring handset + display caller id (with phone-only sanitization).
- `on_call_answered()`
  - Runtime action: transition handset to connected-call UI.
- `on_call_ended()`
  - Runtime action: handset end-call signal + audio stop.
- `on_audio_received(pcm)`
  - Runtime action: playback through `AudioBridge.play_audio` + optional recording.

## Contacts/List Frames (runtime-visible families)

- Prelude: `83 32 01 43 00 00 00`

- Contacts list record family:
  - Header: `c6 33 01 43 23 9a 4c`
  - Followed by payload frames (`45/44/43/42/41/03`) carrying index/total/name/handle/status.

- Empty contacts response family:
  - `c1 33 01 43 06 9a 4c` + empty payload frame.

Legacy qwerty behavior in telegram-private mode:
- request index `0` -> sends 2 records (`0`, `1`)
- request index `n>0` -> sends 1 record (`n`)

## Details Frames

All details responses start with prelude:
- `83 32 01 43 00 00 00`

Page families:
- Base/details page (mode 0):
  - `c9 33 01 43 35 9a 4d`
  - includes handle/lang/birthday/gender/status bytes.

- Phone numbers page (mode 1):
  - `c6 33 01 43 23 9a 4d`
  - BCD-style packed `office/home/mobile` digits.

- Address/time page (mode 2+):
  - `c8 33 01 43 2f 9a 4d`
  - text payload (bio/address composite) + local hh:mm bytes.

## Incoming caller display path

```text
platform caller label -> main._phone_incoming_caller_label
  strip/replace long numeric tokens when privacy shim enabled
  hide parenthesized numeric ids
-> phone.display_caller_id(sanitized_label)
```

## Missed-calls callback shim path

```text
on incoming call:
  remember last incoming real target (backend id)
  derive acceptable shim keys/fragments from displayed fake number

on dial from missed-calls list:
  if dialed text matches shim key/fragment
    resolve to last incoming real target
  else continue normal contact resolution
```
