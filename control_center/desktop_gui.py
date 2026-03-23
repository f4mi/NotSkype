"""desktop_gui.py – Full desktop control-center GUI.

Tabs: Run / Config / Logs
Uses tkinter (built-in) with an optional customtkinter upgrade.
Falls back gracefully to vanilla tkinter if customtkinter is missing.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

ROOT_DIR   = Path(__file__).parent.parent
CONFIG_PATH = ROOT_DIR / "config.json"

try:
    import customtkinter as ctk
    USE_CTK = True
except ImportError:
    import tkinter as ctk       # type: ignore[no-redef]
    USE_CTK = False

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


# ── Helper: themed widgets ─────────────────────────────────────────────────────

def _frame(parent, **kw):
    return ttk.Frame(parent, **kw) if not USE_CTK else ctk.CTkFrame(parent)  # type: ignore[attr-defined]

def _label(parent, text, **kw):
    return ttk.Label(parent, text=text, **kw)

def _button(parent, text, command, **kw):
    return ttk.Button(parent, text=text, command=command, **kw)

def _entry(parent, textvariable=None, **kw):
    return ttk.Entry(parent, textvariable=textvariable, **kw)

def _check(parent, text, variable, **kw):
    return ttk.Checkbutton(parent, text=text, variable=variable, **kw)

def _combo(parent, values, textvariable=None, **kw):
    return ttk.Combobox(parent, values=values, textvariable=textvariable, state="readonly", **kw)


# ── GUI ────────────────────────────────────────────────────────────────────────

class DesktopGUI:
    """Main desktop control-center window."""

    def __init__(self, cfg: Dict[str, Any]):
        self._cfg: Dict[str, Any] = cfg
        self._raw_cfg: Dict[str, Any] = dict(cfg)
        self._proc: Optional[subprocess.Popen] = None
        self._log_q: queue.Queue = queue.Queue()
        self._log_lines: list[str] = []

        # ── Window ────────────────────────────────────────────────────
        self.root = tk.Tk()
        self.root.title("CIT200 Not Skype Control Center")
        self.root.geometry("700x540")
        self.root.resizable(True, True)

        self._build_menu()
        self._nb = ttk.Notebook(self.root)
        self._nb.pack(fill="both", expand=True, padx=6, pady=6)

        self._run_tab   = self._build_run_tab()
        self._cfg_tab   = self._build_config_tab()
        self._log_tab   = self._build_log_tab()

        self._status_var = tk.StringVar(value="Stopped")
        ttk.Label(self.root, textvariable=self._status_var, relief="sunken",
                  anchor="w").pack(fill="x", padx=6, pady=(0, 4))

        self.root.after(300, self._drain_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Tabs ───────────────────────────────────────────────────────────

    def _build_run_tab(self) -> ttk.Frame:
        f = ttk.Frame(self._nb, padding=10)
        self._nb.add(f, text=" Run ")

        # Mode selector
        row0 = ttk.Frame(f)
        row0.pack(fill="x", pady=(0, 8))
        ttk.Label(row0, text="Mode:").pack(side="left")
        self._mode_var = tk.StringVar(value="phone")
        _combo(row0, ["phone", "recorder"], textvariable=self._mode_var,
               width=14).pack(side="left", padx=6)

        ttk.Label(row0, text="Platform:").pack(side="left", padx=(12, 0))
        self._plat_var = tk.StringVar(value=self._cfg.get("platform", "local"))
        _combo(row0, ["local", "telegram_private", "telegram", "discord"],
               textvariable=self._plat_var, width=18).pack(side="left", padx=6)

        # Console mode toggle
        self._ext_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="External console (detached)", variable=self._ext_var).pack(anchor="w")

        # Start / Stop
        btn_row = ttk.Frame(f)
        btn_row.pack(fill="x", pady=8)
        self._btn_start = _button(btn_row, "Start", self._start_service)
        self._btn_start.pack(side="left", padx=(0, 6))
        self._btn_stop  = _button(btn_row, "Stop",  self._stop_service, state="disabled")
        self._btn_stop.pack(side="left")

        return f

    def _build_config_tab(self) -> ttk.Frame:
        f = ttk.Frame(self._nb, padding=10)
        self._nb.add(f, text=" Config ")

        canvas = tk.Canvas(f, borderwidth=0, highlightthickness=0)
        sb     = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        self._cfg_vars: dict[str, tk.Variable] = {}

        def _row(label: str, key_path: str, var: tk.Variable, widget_fn=None) -> None:
            r = ttk.Frame(inner)
            r.pack(fill="x", pady=2)
            ttk.Label(r, text=label, width=28, anchor="e").pack(side="left")
            w = widget_fn(r) if widget_fn else _entry(r, textvariable=var, width=30)
            w.pack(side="left", padx=4)
            self._cfg_vars[key_path] = var

        # Telegram section
        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(inner, text="Telegram", font=("", 9, "bold")).pack(anchor="w")
        tg = self._cfg.get("telegram", {})
        _row("API ID",       "telegram.api_id",    tk.StringVar(value=str(tg.get("api_id", ""))))
        _row("API Hash",     "telegram.api_hash",  tk.StringVar(value=str(tg.get("api_hash", ""))))
        _row("Session name", "telegram.session_name",
             tk.StringVar(value=str(tg.get("session_name", "skype_session"))))

        # Audio section
        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(inner, text="Audio", font=("", 9, "bold")).pack(anchor="w")
        au = self._cfg.get("audio", {})
        _row("Sample rate",  "audio.sample_rate",  tk.StringVar(value=str(au.get("sample_rate", 16000))))
        _row("Chunk size",   "audio.chunk_size",   tk.StringVar(value=str(au.get("chunk_size", 960))))
        _row("Channels",     "audio.channels",     tk.StringVar(value=str(au.get("channels", 1))))
        meter_var = tk.BooleanVar(value=bool(au.get("meter_enabled", False)))
        _row("Meter enabled", "audio.meter_enabled", meter_var,
             lambda p: ttk.Checkbutton(p, variable=meter_var))

        # Contacts section
        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(inner, text="Contacts", font=("", 9, "bold")).pack(anchor="w")
        co = self._cfg.get("contacts", {})
        _row("Order",        "contacts.order",     tk.StringVar(value=co.get("order", "online_first")))
        _row("Max contacts", "contacts.max_contacts",
             tk.StringVar(value=str(co.get("max_contacts", 100))))
        _row("Cache TTL (s)", "contacts.cache_ttl_s",
             tk.StringVar(value=str(co.get("cache_ttl_s", 300))))

        # HID section
        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(inner, text="HID", font=("", 9, "bold")).pack(anchor="w")
        hid = self._cfg.get("hid", {})
        _row("Transport mode", "hid.transport_mode",
             tk.StringVar(value=hid.get("transport_mode", "dual")))
        _row("Keepalive (s)", "hid.keepalive_interval",
             tk.StringVar(value=str(hid.get("keepalive_interval", 1.6))))

        save_btn = _button(inner, "Save Config", self._save_config)
        save_btn.pack(anchor="e", pady=8)

        return f

    def _build_log_tab(self) -> ttk.Frame:
        f = ttk.Frame(self._nb, padding=6)
        self._nb.add(f, text=" Logs ")

        self._log_text = scrolledtext.ScrolledText(
            f, state="disabled", font=("Courier", 8), wrap="none",
            bg="#1e1e1e", fg="#d4d4d4"
        )
        self._log_text.pack(fill="both", expand=True)

        btn_row = ttk.Frame(f)
        btn_row.pack(fill="x", pady=4)
        _button(btn_row, "Clear",  self._clear_logs).pack(side="left", padx=(0, 6))
        _button(btn_row, "Export", self._export_logs).pack(side="left")

        return f

    # ── Menu ───────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        mb = tk.Menu(self.root)
        file_m = tk.Menu(mb, tearoff=0)
        file_m.add_command(label="Save Config", command=self._save_config)
        file_m.add_separator()
        file_m.add_command(label="Exit", command=self._on_close)
        mb.add_cascade(label="File", menu=file_m)
        self.root.config(menu=mb)

    # ── Service process ────────────────────────────────────────────────

    def _start_service(self) -> None:
        if self._proc and self._proc.poll() is None:
            messagebox.showinfo("Already running", "Service is already running.")
            return
        self._save_config()
        mode     = self._mode_var.get()
        platform = self._plat_var.get()
        cmd = [
            sys.executable, "-m", "src.main",
            "--mode", mode,
            "--platform", platform,
        ]
        if self._ext_var.get():
            # Detached external console
            if sys.platform == "win32":
                subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE,
                                 cwd=str(ROOT_DIR))
            else:
                subprocess.Popen(["x-terminal-emulator", "-e"] + cmd, cwd=str(ROOT_DIR))
            self._log("Launched external console process.")
            return

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(ROOT_DIR),
        )
        self._status_var.set("Running")
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._log(f"Started: {' '.join(cmd)}")
        t = threading.Thread(target=self._read_proc_output, daemon=True)
        t.start()

    def _stop_service(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self._status_var.set("Stopped")
        self._btn_start.config(state="normal")
        self._btn_stop.config(state="disabled")
        self._log("Service stopped.")

    def _read_proc_output(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        for line in self._proc.stdout:
            self._log_q.put(line.rstrip())
        self._log_q.put("--- process exited ---")
        self.root.after(0, self._proc_ended)

    def _proc_ended(self) -> None:
        self._status_var.set("Stopped")
        self._btn_start.config(state="normal")
        self._btn_stop.config(state="disabled")

    # ── Config I/O ─────────────────────────────────────────────────────

    def _save_config(self) -> None:
        # Read form vars back into cfg
        for key_path, var in self._cfg_vars.items():
            parts = key_path.split(".")
            d = self._raw_cfg
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            raw_val = var.get()
            # Coerce numeric strings
            try:
                raw_val = int(raw_val)      # type: ignore[assignment]
            except (ValueError, TypeError):
                try:
                    raw_val = float(raw_val)  # type: ignore[assignment]
                except (ValueError, TypeError):
                    pass
            d[parts[-1]] = raw_val

        self._raw_cfg["platform"] = self._plat_var.get()
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(self._raw_cfg, fh, indent=2)
            self._log(f"Config saved to {CONFIG_PATH}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    # ── Log tab ────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_q.put(f"[{ts}] {msg}")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self._log_q.get_nowait()
                self._log_lines.append(line)
                self._log_text.config(state="normal")
                self._log_text.insert("end", line + "\n")
                self._log_text.see("end")
                self._log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(200, self._drain_log_queue)

    def _clear_logs(self) -> None:
        self._log_lines.clear()
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    def _export_logs(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Text files", "*.txt")]
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(self._log_lines))
            self._log(f"Logs exported to {path}")

    # ── Lifecycle ──────────────────────────────────────────────────────

    def _on_close(self) -> None:
        if self._proc and self._proc.poll() is None:
            if messagebox.askyesno("Exit", "Service is running. Stop it?"):
                self._stop_service()
            else:
                return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# ── Entry point ────────────────────────────────────────────────────────────────

def launch_gui(cfg: Optional[Dict[str, Any]] = None) -> None:
    if cfg is None:
        from src.main import load_config
        cfg = load_config()
    gui = DesktopGUI(cfg)
    gui.run()


if __name__ == "__main__":
    launch_gui()
