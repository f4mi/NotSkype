# 04 - Audio Bridge and Threading

## Audio Topology

- Input: handset mic (USB audio capture)
- Output: handset earpiece/speaker (USB audio playback)
- Codec: PCM int16 LE, mono, typically 16kHz

## Critical Threading Rule

Audio callbacks usually run on a non-async thread. Do not call `get_event_loop().create_task(...)` inside callback threads.

Use thread-safe loop handoff:

```text
loop = asyncio.get_running_loop()  # store at startup

audio_callback(pcm):
  if loop is running:
    run_coroutine_threadsafe(platform.send_audio(pcm), loop)
```

## Why This Matters

Without thread-safe handoff, you get runtime errors like:

- `RuntimeError: There is no current event loop in thread ...`

## Practical Parameters

- sample rate: `16000`
- channels: `1`
- chunk size: `960`

## Lifecycle

- Start audio when call is active/answered.
- Stop audio on hangup/end.
- Keep transport and audio lifecycles decoupled (HID loop can run even when not in call).
