"""
Audio Bridge for CIT200

Routes PCM audio between the CIT200 USB audio device (mic + speaker)
and a voice platform via callbacks.
"""

import logging
import threading
import time
import numpy as np
import sounddevice as sd
import wave
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Default audio parameters for telephony
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_CHUNK_SIZE = 960  # 60ms at 16kHz mono

# Device name substring to match the CIT200 USB audio device
CIT200_DEVICE_NAME = "CIT200"


class AudioBridge:
    """
    Bidirectional audio bridge between CIT200 USB audio and a voice platform.

    - Captures mic audio from CIT200 input device -> on_audio_captured callback
    - Plays audio from platform -> CIT200 speaker output device

    Capture and playback can be started/stopped independently.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
        meter_enabled: bool = False,
        meter_interval_s: float = 1.0,
        meter_level: int = logging.DEBUG,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._input_device = input_device
        self._output_device = output_device
        self._devices_discovered = False

        self._input_stream: Optional[sd.InputStream] = None
        self._output_stream: Optional[sd.OutputStream] = None

        self._input_device_name: str = ""
        self._output_device_name: str = ""

        # Callback: receives PCM bytes (int16) from CIT200 mic
        self.on_audio_captured: Optional[Callable[[bytes], None]] = None

        # Playback buffer (thread-safe)
        self._play_lock = threading.Lock()
        self._play_buffer = bytearray()
        self._play_max_buffer_bytes = int(self.sample_rate * self.channels * 2 * 0.6)
        self._last_play_drop_log_at = 0.0

        # Lightweight audio metering (for one-line health visibility)
        self._meter_enabled = bool(meter_enabled)
        self._meter_interval_s = max(0.2, float(meter_interval_s or 1.0))
        self._meter_level = int(meter_level)
        self._meter_lock = threading.Lock()
        self._meter = {
            "capture": {
                "samples": 0,
                "sum_sq": 0.0,
                "peak": 0,
                "bytes": 0,
                "chunks": 0,
                "clips": 0,
                "last_log": time.monotonic(),
            },
            "playback": {
                "samples": 0,
                "sum_sq": 0.0,
                "peak": 0,
                "bytes": 0,
                "chunks": 0,
                "clips": 0,
                "underrun_events": 0,
                "underrun_bytes": 0,
                "drop_bytes": 0,
                "last_log": time.monotonic(),
            },
        }

    # -- Device Discovery --

    def _ensure_devices(self):
        """Find CIT200 audio devices if not already set."""
        if self._devices_discovered:
            return
        devs = self.find_cit200_devices()
        if self._input_device is None:
            self._input_device = devs["input"]
        if self._output_device is None:
            self._output_device = devs["output"]
        if self._input_device is not None:
            try:
                self._input_device_name = str(sd.query_devices(self._input_device)["name"])
            except Exception:
                self._input_device_name = str(self._input_device)
        if self._output_device is not None:
            try:
                self._output_device_name = str(sd.query_devices(self._output_device)["name"])
            except Exception:
                self._output_device_name = str(self._output_device)
        self._devices_discovered = True

    @staticmethod
    def find_cit200_devices() -> dict:
        """
        Find CIT200 audio input and output device indices.
        Returns dict with 'input' and 'output' keys (None if not found).
        """
        result = {"input": None, "output": None}
        devices = sd.query_devices()

        for i, dev in enumerate(devices):
            name = dev["name"]
            if CIT200_DEVICE_NAME.lower() in name.lower():
                if dev["max_input_channels"] > 0 and result["input"] is None:
                    result["input"] = i
                    log.info("Found CIT200 input: [%d] %s", i, name)
                if dev["max_output_channels"] > 0 and result["output"] is None:
                    result["output"] = i
                    log.info("Found CIT200 output: [%d] %s", i, name)

        return result

    @staticmethod
    def list_devices():
        """Print all audio devices for debugging."""
        print(sd.query_devices())

    # -- Stream Control --

    def start(self):
        """Start both capture and playback streams."""
        self._ensure_devices()
        self.start_capture()
        self.start_playback()
        log.info("AudioBridge started (rate=%d, ch=%d, chunk=%d)",
                 self.sample_rate, self.channels, self.chunk_size)
        if self._meter_enabled:
            log.log(
                self._meter_level,
                "Audio meters enabled (interval=%.2fs input='%s' output='%s')",
                self._meter_interval_s,
                self._input_device_name or str(self._input_device),
                self._output_device_name or str(self._output_device),
            )

    def start_capture(self):
        """Start capturing audio from CIT200 microphone."""
        # Stop existing capture first
        self.stop_capture()

        self._ensure_devices()
        if self._input_device is None:
            log.warning("No CIT200 input device found - capture disabled")
            return

        def _capture_callback(indata, frames, time_info, status):
            if status:
                log.warning("Capture status: %s", status)
            if self.on_audio_captured:
                pcm = (indata * 32767).astype(np.int16).tobytes()
                self.on_audio_captured(pcm)
                self._meter_accumulate("capture", pcm)

        self._input_stream = sd.InputStream(
            device=self._input_device,
            samplerate=self.sample_rate,
            channels=self.channels,
            blocksize=self.chunk_size,
            dtype="float32",
            callback=_capture_callback,
        )
        self._input_stream.start()
        log.info("Capture started on device %d", self._input_device)

    def start_playback(self):
        """Start playing audio to CIT200 speaker.
        Pre-fill the buffer with play_audio() BEFORE calling this."""
        # Stop existing stream but preserve the buffer
        if self._output_stream:
            try:
                self._output_stream.stop()
                self._output_stream.close()
            except Exception:
                pass
            self._output_stream = None

        self._ensure_devices()
        if self._output_device is None:
            log.warning("No CIT200 output device found - playback disabled")
            return

        def _playback_callback(outdata, frames, time_info, status):
            if status:
                log.warning("Playback status: %s", status)
            needed = frames * self.channels * 2  # int16 = 2 bytes per sample
            underrun = 0
            with self._play_lock:
                if len(self._play_buffer) >= needed:
                    chunk = bytes(self._play_buffer[:needed])
                    del self._play_buffer[:needed]
                else:
                    chunk = bytes(self._play_buffer)
                    del self._play_buffer[:]
                    underrun = needed - len(chunk)
                    chunk += b'\x00' * underrun

            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32767.0
            outdata[:] = samples.reshape(-1, self.channels)
            self._meter_accumulate("playback", chunk, underrun_bytes=underrun)

        self._output_stream = sd.OutputStream(
            device=self._output_device,
            samplerate=self.sample_rate,
            channels=self.channels,
            blocksize=self.chunk_size,
            dtype="float32",
            callback=_playback_callback,
        )
        self._output_stream.start()
        log.info("Playback started on device %d", self._output_device)

    def stop(self):
        """Stop both streams."""
        self.stop_capture()
        self.stop_playback()
        if self._meter_enabled:
            self._meter_flush(force=True)

    def stop_capture(self):
        """Stop capture stream only."""
        if self._input_stream:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception as e:
                log.warning("Error stopping capture: %s", e)
            self._input_stream = None
            log.info("Capture stopped")

    def stop_playback(self):
        """Stop playback stream only."""
        if self._output_stream:
            try:
                self._output_stream.stop()
                self._output_stream.close()
            except Exception as e:
                log.warning("Error stopping playback: %s", e)
            self._output_stream = None
            log.info("Playback stopped")
        with self._play_lock:
            self._play_buffer.clear()

    def _meter_accumulate(self, direction: str, pcm_data: bytes, underrun_bytes: int = 0):
        if not self._meter_enabled or not pcm_data:
            return

        arr = np.frombuffer(pcm_data, dtype=np.int16)
        if arr.size == 0:
            return

        abs_arr = np.abs(arr.astype(np.int32))
        peak = int(abs_arr.max()) if abs_arr.size else 0
        clips = int((abs_arr >= 32760).sum()) if abs_arr.size else 0
        sum_sq = float(np.dot(arr.astype(np.float64), arr.astype(np.float64)))

        with self._meter_lock:
            m = self._meter.get(direction)
            if m is None:
                return
            m["samples"] += int(arr.size)
            m["sum_sq"] += sum_sq
            m["peak"] = max(int(m["peak"]), peak)
            m["bytes"] += len(pcm_data)
            m["chunks"] += 1
            m["clips"] += clips
            if direction == "playback" and underrun_bytes > 0:
                m["underrun_events"] += 1
                m["underrun_bytes"] += int(underrun_bytes)

            now = time.monotonic()
            if (now - float(m["last_log"])) >= self._meter_interval_s:
                self._meter_emit_locked(direction, now)

    def _meter_emit_locked(self, direction: str, now: Optional[float] = None):
        m = self._meter.get(direction)
        if not m:
            return
        samples = int(m.get("samples", 0))
        if samples <= 0:
            m["last_log"] = time.monotonic() if now is None else now
            return

        rms = (float(m["sum_sq"]) / float(samples)) ** 0.5
        dbfs = -120.0 if rms <= 0.0 else 20.0 * np.log10(rms / 32767.0)
        peak = int(m.get("peak", 0))
        clips = int(m.get("clips", 0))
        chunks = int(m.get("chunks", 0))
        bytes_n = int(m.get("bytes", 0))

        if direction == "playback":
            underruns = int(m.get("underrun_events", 0))
            underrun_bytes = int(m.get("underrun_bytes", 0))
            dropped_bytes = int(m.get("drop_bytes", 0))
            log.log(
                self._meter_level,
                "AudioMeter %s: rms_dbfs=%.1f peak=%d clips=%d bytes=%d chunks=%d underruns=%d underrun_bytes=%d dropped_bytes=%d",
                direction,
                dbfs,
                peak,
                clips,
                bytes_n,
                chunks,
                underruns,
                underrun_bytes,
                dropped_bytes,
            )
            m["underrun_events"] = 0
            m["underrun_bytes"] = 0
            m["drop_bytes"] = 0
        else:
            log.log(
                self._meter_level,
                "AudioMeter %s: rms_dbfs=%.1f peak=%d clips=%d bytes=%d chunks=%d",
                direction,
                dbfs,
                peak,
                clips,
                bytes_n,
                chunks,
            )

        m["samples"] = 0
        m["sum_sq"] = 0.0
        m["peak"] = 0
        m["bytes"] = 0
        m["chunks"] = 0
        m["clips"] = 0
        m["last_log"] = time.monotonic() if now is None else now

    def _meter_flush(self, force: bool = False):
        if not self._meter_enabled:
            return
        with self._meter_lock:
            now = time.monotonic()
            for direction in ("capture", "playback"):
                m = self._meter.get(direction)
                if not m:
                    continue
                if force or int(m.get("samples", 0)) > 0:
                    self._meter_emit_locked(direction, now)

    # -- Audio I/O --

    def _note_playback_drop(self, byte_count: int):
        if byte_count <= 0:
            return
        with self._meter_lock:
            pm = self._meter.get("playback")
            if pm is not None:
                pm["drop_bytes"] = int(pm.get("drop_bytes", 0)) + int(byte_count)

    def play_audio(self, pcm_data: bytes):
        """
        Queue PCM audio (int16, little-endian) for playback through CIT200 speaker.
        Called by the platform when it receives audio from the remote side.
        """
        if not pcm_data:
            return

        if not self.is_playing_back:
            self._note_playback_drop(len(pcm_data))
            now = time.monotonic()
            if now - self._last_play_drop_log_at >= 2.0:
                self._last_play_drop_log_at = now
                log.debug("Dropped %d playback bytes while stream stopped", len(pcm_data))
            return

        dropped = 0
        with self._play_lock:
            self._play_buffer.extend(pcm_data)
            overflow = len(self._play_buffer) - self._play_max_buffer_bytes
            if overflow > 0:
                dropped = int(overflow)
                del self._play_buffer[:overflow]

        if dropped > 0:
            self._note_playback_drop(dropped)
            now = time.monotonic()
            if now - self._last_play_drop_log_at >= 1.0:
                self._last_play_drop_log_at = now
                log.debug("Playback buffer trimmed by %d bytes (max=%d)", dropped, self._play_max_buffer_bytes)

    def flush_playback(self):
        """Clear the playback buffer without stopping the stream."""
        with self._play_lock:
            self._play_buffer.clear()

    @property
    def is_capturing(self) -> bool:
        return self._input_stream is not None

    @property
    def is_playing_back(self) -> bool:
        return self._output_stream is not None

    @property
    def is_running(self) -> bool:
        return self.is_capturing or self.is_playing_back


# -- Standalone Test --

if __name__ == "__main__":
    import sys
    import time

    logging.basicConfig(
        level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=== CIT200 Audio Bridge Test ===\n")

    print("Available audio devices:")
    AudioBridge.list_devices()
    print()

    devs = AudioBridge.find_cit200_devices()
    print(f"\nCIT200 input device:  {devs['input']}")
    print(f"CIT200 output device: {devs['output']}\n")

    if "--record" in sys.argv:
        print("Recording 5 seconds from CIT200 mic...")
        bridge = AudioBridge()
        recorded = bytearray()

        def on_audio(pcm):
            recorded.extend(pcm)

        bridge.on_audio_captured = on_audio
        bridge.start_capture()
        time.sleep(5)
        bridge.stop_capture()

        filename = "cit200_test.wav"
        with wave.open(filename, "wb") as wf:
            wf.setnchannels(DEFAULT_CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(DEFAULT_SAMPLE_RATE)
            wf.writeframes(bytes(recorded))
        print(f"Saved {len(recorded)} bytes to {filename}")

    elif "--play" in sys.argv:
        filename = sys.argv[sys.argv.index("--play") + 1] if len(sys.argv) > sys.argv.index("--play") + 1 else "cit200_test.wav"
        print(f"Playing {filename} through CIT200 speaker...")

        with wave.open(filename, "rb") as wf:
            pcm_data = wf.readframes(wf.getnframes())

        bridge = AudioBridge()
        # Pre-fill buffer THEN start the stream
        bridge.play_audio(pcm_data)
        bridge.start_playback()
        duration = len(pcm_data) / (DEFAULT_SAMPLE_RATE * DEFAULT_CHANNELS * 2)
        time.sleep(duration + 1)
        bridge.stop_playback()
        print("Playback complete")

    else:
        print("Usage:")
        print("  python audio_bridge.py              -- list devices")
        print("  python audio_bridge.py --record      -- record 5s from CIT200 mic")
        print("  python audio_bridge.py --play [file] -- play wav through CIT200 speaker")
