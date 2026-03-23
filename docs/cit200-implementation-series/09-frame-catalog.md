# 09 - Frame Catalog

This is a practical frame catalog used by the current implementation.

## Keepalive / Init

- `c1 33 00 43 07 9a 4f`
- `05 HH MM 06 ST 02 00`

## Contacts

- Contacts list prelude: `83 32 01 43 00 00 00`
- Contacts item block header: `c6 33 01 43 23 9a 4c`
- Details request RX family: `c1 31 01 43 05 9a 4d`
- Base details send header: `c9 33 01 43 35 9a 4d`
- Phones page send header: `c6 33 01 43 23 9a 4d`
- Address page send header: `c8 33 01 43 2f 9a 4d`

## Calls

- Outgoing setup: `82 22 11 00 00 00 00`
- Outgoing setup step: `85 33 11 68 01 01 00`
- Outgoing setup step: `83 33 11 67 00 00 00`
- Outgoing setup step: `82 43 11 00 00 00 00`
- Call initiated: `c1 33 ff 43 04 9a 51`
- Initiated payload: `02 01 00 00 00 00 00`
- Connected confirm: `83 32 ff 43 00 00 00`
- Dial confirm nudge: `83 32 11 35 00 00 00`

## Incoming

- Ring handset: `84 23 00 01 80 00 00`
- Caller id prelude: `82 12 00 00 00 00 00`
- Caller id header: `c5 33 ff 43 1f 9a 00`
- Incoming answer: `85 33 11 68 01 01 00` + `82 43 11 00 00 00 00`

## End / Reject

- End from handset: `82 52 11 00 00 00 00`
- End from remote: `84 53 11 01 00 00 00`
- Reject incoming: `83 32 11 43 00 00 00` + `84 53 00 01 00 00 00`

## Hold / Resume

- RX trigger family: `85 31 11 35 01 15`
- Confirm hold frame: `83 32 11 35 00 00 00`

## Common RX Events

- Call button: `c1 21 11 04 80 9a 60`
- Call ready: `82 44 11 ...`
- Ping family: `83 34 .. 43 .. .. ..`
