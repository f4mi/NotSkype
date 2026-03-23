# CIT200 Language-Agnostic Implementation Series

This folder is a complete engineering guide for rebuilding the Linksys CIT200 desktop pipeline in any language.

It is based on the working implementation in this repository plus observed handset behavior.

For module-by-module pseudocode docs of this specific codebase, see:

- `docs/module-pseudocode/README.md`

## Reading Order

1. `00-system-overview.md`
2. `01-hid-transport-and-framing.md`
3. `02-state-machine-and-events.md`
4. `03-call-control-and-tone-behavior.md`
5. `04-audio-bridge-and-threading.md`
6. `05-contacts-list-and-detail-pages.md`
7. `06-platform-abstraction.md`
8. `07-test-strategy-and-observability.md`
9. `08-porting-checklist.md`
10. `09-frame-catalog.md`
11. `10-reference-pseudocode.md`

## Scope

This series covers:

- USB HID transport specifics
- handset event decoding
- call setup/answer/hangup behavior
- busy/dial tone behavior and transitions
- contact list and detail page responses
- audio callback handoff and async safety
- backend abstraction (local/demo or real provider)
- debugging, traces, and manual validation

## Implementation Targets

You can use this to build a CIT200 stack in:

- Rust
- C/C++
- Go
- Java/Kotlin
- C#
- Node.js
- Python (already done)

All protocol details are expressed as frame bytes, state transitions, and pseudocode rather than language-specific APIs.
