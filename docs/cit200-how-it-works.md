# CIT200: How It Works

This document explains how the Linksys CIT200 works in this repository at a practical level: what hardware paths exist, how the HID protocol is used, how calls and contacts move through the system, and where the code lives.

It is based on the real implementation in:

- `src/cit200.py`
- `src/audio_bridge.py`
- `src/main.py`

## 1. What the CIT200 actually is

The CIT200 appears to the computer as two separate USB functions:

- a USB audio device for microphone and speaker audio
- a USB HID telephony device for buttons, menus, call control, status, and display updates

That split is the key idea of the whole project:

- audio does not travel over the HID protocol
- button presses and screen updates do not travel over the audio device

So the software has to run two paths in parallel:

1. HID control path
2. PCM audio path

## 2. Main pieces in this repo

### `src/cit200.py`

This is the handset protocol driver. It:

- opens the CIT200 HID interface
- reads raw 8-byte reports from the handset
- decodes them into semantic events like `CALL_BUTTON`, `DIAL`, `CONTACTS_REQUEST`, and `ANSWER_INCOMING`
- sends command frames back to the handset to ring, show caller ID, confirm calls, send contacts, send detail pages, and keep the phone alive

### `src/audio_bridge.py`

This is the USB audio bridge. It:

- finds the CIT200 microphone and speaker devices
- captures mic PCM from the handset
- plays remote PCM back to the handset speaker

### `src/main.py`

This is the runtime coordinator. `PhoneApp` connects:

- `CIT200Device` for control
- `AudioBridge` for sound
- a voice platform backend for calls and contacts

It translates handset events into backend actions, and backend events back into handset actions.

## 3. HID transport

The driver targets:

- VID `0x13B1`
- PID `0x001D`
- interface `3`

The implementation prefers opening the exact HID path returned by `hid.enumerate()` and scores candidates so it picks the telephony interface. On Windows it also opens a native handle for `HidD_SetFeature`, because some writes are more reliable that way.

### Write format

Outgoing commands are sent as 9-byte HID reports:

- byte 0 = report id `0x04`
- bytes 1-7 = payload
- byte 8 = terminator `0x68`

In the code this is done by `CIT200Device._write()`.

The driver supports three transport modes:

- `feature_only`
- `output_only`
- `dual`

`dual` tries both write styles. Contacts transmission often temporarily falls back to `feature_only` because it is more reliable for long frame bursts.

### Read format

Incoming handset messages are read as 8-byte interrupt reports. `poll()` reads one frame and hands it to `_process_message()`.

## 4. Keepalive and initialization

The handset is not fire-and-forget. The PC has to keep talking to it.

`send_init()` sends:

- a fixed handshake frame
- a time/status frame containing current hour, minute, and presence state

This serves two jobs:

- initial setup when the handset opens
- periodic keepalive so the phone stays in sync

In `PhoneApp.run()`, the app resends time/status on a timer using `send_time_sync()`.

## 5. Event model

`src/cit200.py` turns raw frames into higher-level events. The main ones are:

- `CALL_BUTTON`
- `END_CALL`
- `DIAL`
- `ANSWER_INCOMING`
- `REJECT_INCOMING`
- `HOLD_RESUME`
- `CONTACTS_REQUEST`
- `CONTACT_DETAILS`
- `STATUS_CHANGE`
- `PING`

The phone driver is stateful. Some handset actions are one frame, but others are multi-step:

- opening contacts: request frame, then index frame
- opening contact details: request frame, then detail-page selector frame
- dialing: several frames containing chunks of the dialed text

The driver tracks these with an internal `_state` field and a `_callee_buf` buffer.

## 6. Outgoing call flow

When the user presses the green call button on the handset:

1. The handset sends a call-button frame.
2. `CIT200Device` emits `CALL_BUTTON`.
3. The driver also starts a local call-setup sequence with `start_local_audio_call()`.

That setup sequence intentionally nudges the handset through its expected firmware states:

- `setup_call_from_handset()` moves the handset into call setup
- `confirm_call_initiated()` tells it the call is being placed
- `confirm_dial()` nudges it out of local dial tone behavior

Later, one of two things completes the transition:

- the backend says the remote side answered, or
- the handset sends the "call ready" frame (`0x82 0x44 0x11`)

At that point the driver sends `confirm_call_connected()`, which pushes the handset into connected-audio state.

### Important distinction

The green button does not directly place a real network call by itself. It only:

- moves the handset into its expected local call UI state
- emits events so `PhoneApp` can ask the configured backend to place the actual call

## 7. Dialing a target

After call setup, the handset sends the selected contact or typed number as a multi-frame sequence. The driver:

- collects the text into `_callee_buf`
- extracts printable ASCII with `_extract_callee()`
- emits `DIAL` with the resolved string

`PhoneApp._on_phone_dial()` then maps that handset string back to a real backend target. It can resolve from:

- the visible contact list
- cached contacts
- privacy shims used for phone-safe display strings
- missed-call callback shims

Then it asks the backend platform to place the real call.

## 8. Incoming call flow

When the backend receives an incoming call, `PhoneApp` uses the handset driver to present it:

1. `ring()` starts handset ringing.
2. `display_caller_id()` writes the incoming caller name and timestamp to the handset screen.

If the user answers on the handset:

1. the handset emits its answer sequence
2. `CIT200Device` recognizes the accept frame
3. `answer_incoming_call()` sends the required confirmation frames back
4. the driver emits `ANSWER_INCOMING`
5. `PhoneApp` tells the backend to answer the real call

If the user rejects it:

- the driver sends `reject_incoming_call()`
- the app emits `REJECT_INCOMING`
- the backend side is ended or declined

## 9. Ending and holding calls

### End call

If the handset red button is pressed, the driver:

- detects the end-call frame
- sends `end_call_from_handset()`
- emits `END_CALL`

`PhoneApp` then ends the backend call and stops audio flow.

If the remote side ends first, `PhoneApp` can call `end_call_from_remote()`, which tells the handset the call ended and triggers its end/busy-tone behavior.

### Hold/resume

The R button maps to `HOLD_RESUME`. The driver sends `confirm_hold()`, then `PhoneApp` decides what backend behavior should happen.

## 10. Contacts

The handset has a contacts UI, but the contacts do not live on the device. The PC serves them on demand.

### How the request works

When the user opens the contacts menu:

1. the handset sends a contacts-request frame
2. the next frame contains the requested index
3. `CIT200Device` emits `CONTACTS_REQUEST(index)`

`PhoneApp` responds by building a handset-formatted page from backend contacts.

### Contact payload format

`send_contacts()` writes a burst of frames for each visible contact:

- name
- handle
- status
- list index
- total count

The handset can only handle a limited wire payload cleanly, so this implementation caps a burst to 33 contacts per page.

There are two send styles:

- `send_contacts()`: normal paged sender
- `send_contacts_legacy()`: old `cit200xSkype`-compatible behavior used for stricter compatibility paths

## 11. Contact detail pages

When the user opens a contact entry, the handset can ask for several detail views. The driver emits `CONTACT_DETAILS(index, detail_type)` and the app responds with one of these writers:

- `send_contact_details()`: main details page
- `send_contact_numbers_page()`: office/home/mobile numbers
- `send_contact_bio_page()`: bio/address-like text plus local time

The implementation repurposes the handset's original detail-page fields to show modern platform data cleanly enough on the CIT200 screen.

## 12. Status and voicemail

The handset also has menu paths for presence and voicemail-like indicators.

- `send_status_echo()` shows the current selected status when the status menu opens.
- `confirm_status_change()` commits a new handset status.
- `send_voicemail_count()` updates the voicemail counter area.

The status values are represented by the `Status` enum in `src/cit200.py`.

## 13. Audio path

All voice audio is handled separately from HID.

`AudioBridge` searches the system audio device list for names containing `CIT200`, then opens:

- one input stream for the handset microphone
- one output stream for the handset speaker

### Mic direction

- CIT200 mic captures audio
- `AudioBridge` converts it to 16-bit PCM bytes
- `PhoneApp._on_mic_audio()` forwards it to the backend with `send_audio()`

### Speaker direction

- backend audio arrives in PCM chunks
- `PhoneApp._on_platform_audio()` queues it into `AudioBridge`
- `AudioBridge` feeds the handset speaker stream

### Important rule

Audio is only allowed to flow while `_in_call_audio` is true. The app turns this on when a call is active and turns it off when the call ends.

## 14. Threading model

The runtime is intentionally split across threads so UI, HID, backend, and audio do not block each other:

- poll loop thread: reads CIT200 HID frames
- async loop thread: runs the backend platform client
- audio callbacks: real-time capture and playback
- UI thread: optional desktop interface

`PhoneApp` acts as the bridge between all of them.

This is why the code is careful about:

- event callbacks
- async handoff with `run_coroutine_threadsafe`
- locks around HID I/O
- not touching tkinter directly from background threads

## 15. Failure handling and observability

The driver includes several practical reliability features:

- non-blocking HID reads
- read-error streak tracking
- automatic HID reopen/recovery after repeated failures
- optional frame tracing to a text file
- timeline markers around tricky call-state transitions

This matters because the CIT200 is old hardware and HID timing can be picky, especially on Windows.

## 16. Configuration knobs that matter most

The main runtime tuning values are under `config.json`, especially in the `hid` and `audio` sections.

Useful HID knobs:

- transport mode
- call setup delay
- contacts frame delay
- contacts transport mode
- keepalive interval

Useful audio knobs:

- sample rate
- chunk size
- meter/logging settings

## 17. Mental model to keep in mind

The simplest correct way to think about the CIT200 in this repo is:

- the handset is a small remote terminal, not a self-contained softphone
- the PC keeps the handset alive with HID frames
- the handset asks the PC for contacts, call progress, status, and screen content
- the backend platform does the real network calling
- the USB audio device carries only live PCM audio

If you keep those five points in mind, the code becomes much easier to follow.

## 18. Best files to read next

If you want to go from overview to implementation, read these in order:

1. `src/cit200.py`
2. `src/audio_bridge.py`
3. `src/main.py`
4. `docs/cit200-implementation-series/00-system-overview.md`
5. `docs/module-pseudocode/runtime-core.md`
