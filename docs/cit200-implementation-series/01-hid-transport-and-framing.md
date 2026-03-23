# 01 - HID Transport and Framing

## Device Identity

- Vendor ID: `0x13b1`
- Product ID: `0x001d`

## Write Format

All command payloads are wrapped as a 9-byte report:

- Byte 0: `0x04`
- Bytes 1..7: payload (7 bytes)
- Byte 8: `0x68`

## Read Format

Interrupt reads return 8-byte frames.

- Byte 0 is report prefix (`0x03` in observed traces)
- Bytes 1..7 contain semantic message family and data

## Transport Modes

Implement support for:

- `feature_only` (HID feature report)
- `output_only` (HID output report)
- `dual` (attempt both)

Different host stacks/controllers can vary; transport mode should be configurable.

## Keepalive

Send periodic init/time sync frame pair:

1. `c1 33 00 43 07 9a 4f`
2. `05 HH MM 06 STATUS 02 00`

Recommended cadence from working implementation: every ~1.6s (8 cycles x 0.2s loop).

## Pseudocode

```text
function write7(payload7):
  report = [0x04] + payload7 + [0x68]
  if mode in {feature_only, dual}: send_feature_report(report)
  if mode in {output_only, dual}: write_output_report(report)

function periodic_tick():
  if cycle == 1:
    send_init(current_status, now_hour, now_min)
  cycle = (cycle % 8) + 1
```
