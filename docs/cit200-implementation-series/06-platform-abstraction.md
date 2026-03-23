# 06 - Platform Abstraction

## Why Abstract

The handset protocol should not depend on any one backend (Skype legacy, local demo, SIP, etc.).

## Interface Contract

Define a language-agnostic contract with methods:

- `connect()` / `disconnect()`
- `place_call(target)`
- `answer_call(call_id)`
- `end_call()`
- `hold_call()`
- `send_audio(pcm_chunk)`
- `get_contacts()`
- `get_status()` / `set_status(status)`

And callbacks/events:

- `on_incoming_call(call_id, caller_name)`
- `on_call_answered()`
- `on_call_ended()`
- `on_audio_received(pcm_chunk)`

## Orchestrator Mapping

- Handset `CALL_BUTTON` -> backend dial or answer path
- Handset `DIAL(handle)` -> `place_call(handle)`
- Handset `END_CALL` -> `end_call()`
- Backend incoming -> handset ring + caller id
- Backend answered -> handset q17 connected confirm
- Backend ended -> handset end-from-remote frame

## Local Mock Backend

Use a local backend first to verify protocol correctness before integrating real networks.

Recommended mock behavior:

- deterministic contacts
- optional incoming call on start
- mic echo loopback
- short tone on answer for media confirmation
