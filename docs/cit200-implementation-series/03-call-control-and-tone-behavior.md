# 03 - Call Control and Tone Behavior

## Outgoing Call Sequence (Proven)

Use staged progression:

1. `q9` setup sequence:
   - `82 22 11 00 00 00 00`
   - `85 33 11 68 01 01 00`
   - `83 33 11 67 00 00 00`
   - `82 43 11 00 00 00 00`
2. Delay (~200ms)
3. `q10` call initiated:
   - `c1 33 ff 43 04 9a 51`
   - `02 01 00 00 00 00 00`
4. Wait for either:
   - backend call answered callback, or
   - handset call-ready frame `82 44 11`
5. `q17` connected confirm:
   - `83 32 ff 43 00 00 00`

## Dial Tone Notes

- A local dial tone can be firmware-generated until handset perceives dial progress.
- In demo/local backends, sending dial-confirm nudge can help transition tone state:
  - `83 32 11 35 00 00 00`

## Incoming Call

- Ring command to handset:
  - `84 23 00 01 80 00 00`
- Caller ID prelude + multipart payload:
  - starts with `82 12 00 ...` then `c5 33 ff 43 1f 9a 00` and text chunks
- When answered (`82 24 11 ...`), send:
  - `85 33 11 68 01 01 00`
  - `82 43 11 00 00 00 00`

## End/Reject

- Handset end call command:
  - `82 52 11 00 00 00 00`
- Remote/PC ended call indication (busy tone style):
  - `84 53 11 01 00 00 00`
- Reject incoming:
  - `83 32 11 43 00 00 00`
  - `84 53 00 01 00 00 00`

## Recommended Safeguards

- Protect setup sequence from overlap (mutex + active flag).
- Make setup/connect delays configurable.
- Add timeline markers for q9/q10/q17 in logs and traces.
