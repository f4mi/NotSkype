from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable, Dict, Optional

from .audio_bridge import AudioBridge
from .cit200 import CIT200Device, Event, Status

log = logging.getLogger(__name__)


class MicModeSession:
    """Owns the handset/audio lifecycle for simple microphone mode."""

    def __init__(
        self,
        cfg: Dict[str, Any],
        status_cb: Callable[[str], None],
        log_cb: Callable[[str], None],
    ) -> None:
        self._cfg = cfg
        self._status_cb = status_cb
        self._log_cb = log_cb
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._phone: Optional[CIT200Device] = None
        self._audio: Optional[AudioBridge] = None
        self._running = False
        self._loopback = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, loopback: bool = False) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loopback = bool(loopback)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mic-gui")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _set_running(self, value: bool) -> None:
        self._running = value

    def _run(self) -> None:
        hid_cfg = self._cfg.get("hid", {})
        audio_cfg = self._cfg.get("audio", {})

        meter_level_raw = audio_cfg.get("meter_level", "debug")
        if isinstance(meter_level_raw, str):
            meter_level = getattr(logging, meter_level_raw.upper(), logging.DEBUG)
        else:
            meter_level = int(meter_level_raw)

        phone = CIT200Device(
            transport_mode=str(hid_cfg.get("transport_mode", "dual")),
            call_setup_delay=float(hid_cfg.get("q9_q10_delay", 0.2)),
            call_connect_delay=float(hid_cfg.get("call_connect_delay", 0.2)),
            contacts_frame_delay_s=float(hid_cfg.get("contacts_frame_delay_s", 0.05)),
            contacts_contact_delay_s=float(hid_cfg.get("contacts_contact_delay_s", 0.05)),
            contacts_transport_mode=str(hid_cfg.get("contacts_transport_mode", "feature_only")),
        )
        audio = AudioBridge(
            sample_rate=int(audio_cfg.get("sample_rate", 16000)),
            channels=int(audio_cfg.get("channels", 1)),
            chunk_size=int(audio_cfg.get("chunk_size", 960)),
            meter_enabled=bool(audio_cfg.get("meter_enabled", False)),
            meter_interval_s=float(audio_cfg.get("meter_interval_s", 10.0)),
            meter_level=meter_level,
        )

        self._phone = phone
        self._audio = audio

        try:
            self._status_cb("Connecting to handset...")
            self._log_cb("Opening CIT200 HID device...")
            if not phone.open():
                raise RuntimeError("CIT200 handset not found. Plug it in and try again.")

            self._log_cb("Handset connected.")
            self._status_cb("Starting audio bridge...")

            if self._loopback:
                audio.on_audio_captured = audio.play_audio
                self._log_cb("Loopback enabled: handset mic will play back to handset speaker.")
            else:
                audio.on_audio_captured = lambda pcm: None
                self._log_cb("Loopback disabled: handset works as a live mic for Windows apps.")

            audio.start()
            phone.send_init(Status.ONLINE)

            self._status_cb("Activating microphone mode...")
            phone.start_local_audio_call()
            time.sleep(0.5)
            phone.confirm_call_connected()

            phone.on(Event.END_CALL, lambda: self._log_cb("Handset END pressed."))
            phone.on(Event.CALL_BUTTON, lambda: self._log_cb("Handset CALL pressed."))

            self._set_running(True)
            self._status_cb("Microphone mode is active.")
            self._log_cb("Handset mic/speaker are live. Choose the CIT200 audio device in Windows apps.")

            cycle = 1
            while not self._stop_event.is_set():
                phone.poll()
                if cycle == 1:
                    phone.send_time_sync()
                cycle = 1 if cycle >= 8 else cycle + 1
                time.sleep(0.2)
        except Exception as exc:
            log.exception("Mic GUI session failed")
            self._status_cb("Failed to start microphone mode.")
            self._log_cb(f"Error: {exc}")
        finally:
            self._set_running(False)
            try:
                if phone:
                    phone.end_call_from_remote()
            except Exception:
                pass
            time.sleep(0.2)
            try:
                if audio:
                    audio.stop()
            except Exception:
                pass
            try:
                if phone:
                    phone.close()
            except Exception:
                pass
            self._status_cb("Microphone mode is off.")
            self._log_cb("Handset released.")


def launch_mic_gui(cfg: Dict[str, Any]) -> None:
    root = tk.Tk()
    root.title("CIT200 Microphone Mode")
    root.geometry("420x300")
    root.resizable(False, False)

    status_var = tk.StringVar(value="Ready.")
    loopback_var = tk.BooleanVar(value=False)

    outer = tk.Frame(root, padx=16, pady=16)
    outer.pack(fill="both", expand=True)

    title = tk.Label(
        outer,
        text="Use Phone as Microphone",
        font=("Segoe UI", 14, "bold"),
        anchor="w",
    )
    title.pack(fill="x")

    subtitle = tk.Label(
        outer,
        text="Starts the CIT200 handset in a call-connected state so Windows apps can use it like a normal mic.",
        justify="left",
        wraplength=380,
        anchor="w",
    )
    subtitle.pack(fill="x", pady=(6, 12))

    status_label = tk.Label(
        outer,
        textvariable=status_var,
        anchor="w",
        relief="sunken",
        padx=8,
        pady=6,
    )
    status_label.pack(fill="x")

    controls = tk.Frame(outer, pady=10)
    controls.pack(fill="x")

    start_btn = tk.Button(controls, text="Start", width=12)
    stop_btn = tk.Button(controls, text="Stop", width=12, state="disabled")
    start_btn.pack(side="left")
    stop_btn.pack(side="left", padx=(8, 0))

    loopback = tk.Checkbutton(
        outer,
        text="Enable loopback (hear yourself in handset speaker)",
        variable=loopback_var,
        anchor="w",
    )
    loopback.pack(fill="x", pady=(0, 10))

    help_text = tk.Label(
        outer,
        text="Tip: in apps like Discord, Skype, OBS, or Voice Recorder, select the CIT200 audio device as the microphone.",
        justify="left",
        wraplength=380,
        anchor="w",
    )
    help_text.pack(fill="x")

    log_box = tk.Text(outer, height=8, wrap="word", state="disabled")
    log_box.pack(fill="both", expand=True, pady=(12, 0))

    def append_log(message: str) -> None:
        def _append() -> None:
            log_box.configure(state="normal")
            log_box.insert("end", f"{message}\n")
            log_box.see("end")
            log_box.configure(state="disabled")
        root.after(0, _append)

    def set_status(message: str) -> None:
        root.after(0, lambda: status_var.set(message))

    session = MicModeSession(cfg, status_cb=set_status, log_cb=append_log)

    def refresh_buttons() -> None:
        running = session.is_running
        start_btn.configure(state="disabled" if running else "normal")
        stop_btn.configure(state="normal" if running else "disabled")
        loopback.configure(state="disabled" if running else "normal")
        root.after(250, refresh_buttons)

    def on_start() -> None:
        append_log("Starting microphone mode...")
        session.start(loopback=loopback_var.get())

    def on_stop() -> None:
        append_log("Stopping microphone mode...")
        session.stop()

    def on_close() -> None:
        if session.is_running:
            session.stop()
            deadline = time.time() + 2.0
            while session.is_running and time.time() < deadline:
                root.update()
                time.sleep(0.05)
        root.destroy()

    start_btn.configure(command=on_start)
    stop_btn.configure(command=on_stop)
    root.protocol("WM_DELETE_WINDOW", on_close)

    append_log("Click Start to enable the handset microphone.")
    refresh_buttons()
    root.mainloop()
