# 02 - State Machine and Events

## Internal Parser States

Use an integer parser state similar to legacy implementations:

- `0`: idle
- `5`: waiting for status value
- `6`: waiting for contacts index
- `8`: waiting for contact details request payload
- `18`: collecting multi-frame dial target

## Core Event Families (RX)

- Call button: `c1 21 11 04 80 9a 60`
- End call: `84 51 11 01 00 00 00`
- Contacts button: `c1 31 01 43 05 9a 4c`
- Details request: `c1 31 01 43 05 9a 4d`
- Status menu: `86 31 01 43 02 9a 42`
- Status change request: `c1 31 01 43 03 9a 43`
- Incoming answer step 2: `82 24 11 ...`
- Reject incoming: `86 31 11 43 02 9a ...`
- Hold/resume: `85 31 11 35 01 15 ...`
- Call ready: `82 44 11 ...`

## Event Emission Pattern

Decode bytes first, then emit semantic callbacks:

- `CALL_BUTTON`
- `END_CALL`
- `CONTACTS_REQUEST(index)`
- `CONTACT_DETAILS(index, more_type)`
- `STATUS_CHANGE(status_index)`
- `DIAL(handle)`
- `ANSWER_INCOMING`
- `REJECT_INCOMING`
- `HOLD_RESUME`

## Dial Sequence Collection

Dial target comes as multi-frame chunks where frame byte 1 acts as countdown/length family.

Algorithm:

1. Detect start family `(c1..c6, 31, 11, 35)` and seed buffer from bytes 6..7.
2. In state 18, append continuation bytes from `d[2:8]` while `d[1] in 0x07..0x45`.
3. Final chunk when `d[1] in 0x01..0x06`; append only remaining bytes.
4. Extract printable ASCII for handle.
