#!/usr/bin/env python3
"""test_loopback.py – Combined HID + audio loopback sanity check.

Purpose
-------
Wait for the call button on the CIT200 handset, then:
  1. Capture mic audio for N seconds.
  2. Save captured PCM to a WAV file.
  3. Play the captured audio back through the handset speaker.

Useful for verifying the full HID + audio path without needing a backend.

Usage
-----
  python test_loopback.py
  python test_loopback.py --duration 5 --out loopback.wav --debug

Press the green call button on the CIT200 to start recording.
Press it again (or wait for the timeout) to stop.

Output
------
  WAV file at the path specified by --out (default: cit200_loopback.wav)

Requirements
------------
  - hid (hidapi): pip install hid
  - sounddevice + numpy: pip install sounddevice numpy
  - CIT200 handset plugged in via USB
"""

from __future__ import annotations

import argparse
import logging
import struct
import sys
import threading
import time
import wave
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))

log = logging.getLogger(__name__)


# ── WAV writer ─────────────────────────────────────────────────────────────────

def _save_wav(path: str, pcm_chunks: List[bytes], sample_rate: int,
              channels: int = 1) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)          # int16 = 2 bytes
        wf.setframerate(sample_rate)
        for chunk in pcm_chunks:
            wf.writeframes(chunk)
    total_bytes = sum(len(c) for c in pcm_chunks)
    secs = total_bytes / (sample_rate * channels * 2)
    log.info("Saved %d chunks (%.2f s) to %s", len(pcm_chunks), secs, path)


# ── Main logic ─────────────────────────────────────────────────────────────────

def run(duration: float, out_path: str, debug: bool) -> None:
    from src.cit200      import CIT200Device, Event
    from src.audio_bridge import AudioBridge

    cfg = {
        "hid":   {"transport_mode": "dual", "keepalive_interval": 1.6},
        "audio": {
            "sample_rate": 16000,
            "channels":    1,
            "chunk_size":  960,
            "meter_enabled": True,
            "meter_interval_s": 5.0,
        },
    }

    phone = CIT200Device(cfg)
    audio = AudioBridge(cfg)

    # ── Shared recording state ─────────────────────────────────────────
    recording    = threading.Event()
    call_pressed = threading.Event()
    pcm_buf: List[bytes] = []
    buf_lock = threading.Lock()

    def _on_captured(pcm: bytes) -> None:
        if recording.is_set():
            with buf_lock:
                pcm_buf.append(pcm)

    audio.on_audio_captured = _on_captured

    # ── Open devices ───────────────────────────────────────────────────
    if not phone.open():
        log.error("CIT200 not found. Is hidapi installed and handset plugged in?")
        sys.exit(1)

    audio.start()

    def _on_call_button() -> None:
        call_pressed.set()

    phone.on(Event.CALL_BUTTON, _on_call_button)
    phone.on(Event.END_CALL, lambda: None)

    # ── Poll thread ────────────────────────────────────────────────────
    stop_poll = threading.Event()

    def _poll():
        while not stop_poll.is_set():
            phone.poll()
            phone.send_keepalive()
            time.sleep(0.02)

    poll_t = threading.Thread(target=_poll, daemon=True, name="poll")
    poll_t.start()

    # ── Wait for call button ───────────────────────────────────────────
    print(f"\nReady. Press the GREEN call button on the CIT200 to start recording.")
    print(f"Recording will last {duration:.0f} seconds, then auto-stop.\n")

    call_pressed.wait()
    print("Recording started...")
    recording.set()
    time.sleep(duration)
    recording.clear()
    print("Recording stopped.")

    # ── Save WAV ───────────────────────────────────────────────────────
    with buf_lock:
        captured = list(pcm_buf)

    if not captured:
        log.warning("No audio captured. Check that CIT200 audio device is available.")
    else:
        _save_wav(out_path, captured, sample_rate=16000)
        print(f"Saved to: {out_path}")

    # ── Playback ───────────────────────────────────────────────────────
    if captured:
        print("\nPlaying back captured audio through the handset speaker...")
        chunk_duration = cfg["audio"]["chunk_size"] / cfg["audio"]["sample_rate"]
        for chunk in captured:
            audio.play_audio(chunk)
            time.sleep(chunk_duration)
        # Allow final chunks to drain
        time.sleep(0.5)
        print("Playback complete.")
    else:
        print("No audio to play back.")

    # ── Shutdown ───────────────────────────────────────────────────────
    stop_poll.set()
    poll_t.join(timeout=1.0)
    audio.stop()
    phone.close()
    print("Done.")


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(
        description="CIT200 HID + audio loopback test"
    )
    parser.add_argument("--duration", type=float, default=5.0,
                        help="Recording duration in seconds (default: 5)")
    parser.add_argument("--out", default="cit200_loopback.wav",
                        help="Output WAV file path (default: cit200_loopback.wav)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s – %(message)s",
    )

    run(duration=args.duration, out_path=args.out, debug=args.debug)


if __name__ == "__main__":
    main()
