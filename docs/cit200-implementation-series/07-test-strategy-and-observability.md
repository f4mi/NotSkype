# 07 - Test Strategy and Observability

## Must-Have Logs

- RX frame hex with sequence counter
- TX payloads with transport mode path
- state transitions (`old -> new`, reason)
- timeline markers (`q9`, `q10`, `q17`, key RX events)
- emitted semantic events

## HID Trace File

Use a trace file that records:

- relative timestamp
- direction (`TX`, `RX`, `EV`)
- bytes or marker text

This allows packet-by-packet replay and debugging.

## Manual Test Plan

Use a Y/N runner with steps:

1. app launch
2. call connect
3. tone off in idle
4. record start/stop
5. tone off during record
6. playback start/stop
7. playback audible
8. tone off during playback
9. hangup clean reset
10. second full cycle

## Reference Harnesses in Repo

- `manual_yn_test.py` (manual pass/fail logger)
- `keepalive_contacts.py` (protocol-only contacts test)
- `tui.py` (direct handset recorder/playback tool)
