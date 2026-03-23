"""tray_app.py – System tray service manager.

Runs `python -m src.main --mode phone` as a managed subprocess and exposes
start/stop/restart/settings via a system-tray icon.

Dependencies (optional): pystray, Pillow
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).parent.parent
LOG_DIR  = ROOT_DIR / "logs"
LOG_FILE = LOG_DIR / "tray_service.log"
LOG_RING: list[str] = []
MAX_RING = 500

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    pystray = None   # type: ignore
    HAS_TRAY = False
    log.debug("pystray/Pillow not available – tray will be a no-op")


def _make_icon_image(size: int = 64) -> "Image.Image":
    """Create a simple Skype-logo-like blue circle icon."""
    from PIL import Image, ImageDraw
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse([margin, margin, size - margin, size - margin], fill="#00AFF0")
    # Phone icon (simplified)
    cx, cy = size // 2, size // 2
    r = size // 5
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="white")
    return img


class TrayApp:
    """System-tray service runner."""

    def __init__(self, cfg: dict):
        self._cfg  = cfg
        self._proc: Optional[subprocess.Popen] = None
        self._running = False
        self._icon   = None
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ── Service lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            log.info("Service already running")
            return
        platform = self._cfg.get("platform", "local")
        debug    = self._cfg.get("tray", {}).get("debug", False)
        cmd = [sys.executable, "-m", "src.main", "--mode", "phone", "--platform", platform]
        if debug:
            cmd.append("--debug")
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(ROOT_DIR),
        )
        log.info("Service started: PID %d", self._proc.pid)
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()
        self._update_icon_title()

    def stop(self) -> None:
        if not self._proc:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
        log.info("Service stopped")
        self._update_icon_title()

    def restart(self) -> None:
        self.stop()
        time.sleep(0.5)
        self.start()

    # ── Log reader ─────────────────────────────────────────────────────

    def _reader(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                for line in self._proc.stdout:
                    line = line.rstrip()
                    fh.write(line + "\n")
                    LOG_RING.append(line)
                    if len(LOG_RING) > MAX_RING:
                        LOG_RING.pop(0)
        except Exception as e:
            log.error("Reader error: %s", e)

    # ── Tray icon ──────────────────────────────────────────────────────

    def _update_icon_title(self) -> None:
        if self._icon:
            running = self._proc and self._proc.poll() is None
            self._icon.title = f"CIT200 – {'Running' if running else 'Stopped'}"

    def run(self) -> None:
        if not HAS_TRAY:
            log.warning("pystray/Pillow not installed – running headless phone mode instead")
            self.start()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()
            return

        img = _make_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("Start",   lambda icon, item: self.start()),
            pystray.MenuItem("Stop",    lambda icon, item: self.stop()),
            pystray.MenuItem("Restart", lambda icon, item: self.restart()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Logs Folder", self._open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._exit),
        )
        self._icon = pystray.Icon("cit200", img, "CIT200 – Stopped", menu)
        self.start()
        self._icon.run()

    def _open_logs(self, *_) -> None:
        if sys.platform == "win32":
            os.startfile(str(LOG_DIR))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(LOG_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(LOG_DIR)])

    def _exit(self, icon, item) -> None:
        self.stop()
        icon.stop()


# ── Entry point ────────────────────────────────────────────────────────────────

def launch_tray(cfg: Optional[dict] = None) -> None:
    if cfg is None:
        from src.main import load_config
        cfg = load_config()
    app = TrayApp(cfg)
    app.run()


if __name__ == "__main__":
    launch_tray()
