"""ui_bridge.py – Thread-safe bridge from backend threads to tkinter main thread.

The CIT200 poll loop, platform callbacks, and audio streams all run on
background threads.  Tkinter widgets must only be touched from the main
thread.  This module solves that with a simple Queue + periodic drain.

Usage
-----
    bridge = UIBridge(root)        # root is the tk.Tk instance
    bridge.start()                 # begins draining every 40 ms

    # From any thread:
    bridge.call(fn, *args, **kw)   # schedules fn(*args, **kw) on main thread
    bridge.stop()                  # cancels the periodic drain (call on exit)
"""

from __future__ import annotations

import queue
import logging
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_DRAIN_MS = 40   # drain interval in milliseconds (25 fps)


class UIBridge:
    """Marshals callables from any thread onto tkinter's main thread."""

    def __init__(self, root: Any) -> None:
        self._root    = root
        self._q: queue.Queue = queue.Queue()
        self._after_id: Optional[str] = None

    def start(self) -> None:
        """Start the periodic drain loop (call once after root is created)."""
        self._schedule()

    def stop(self) -> None:
        """Cancel the drain loop (call on app shutdown)."""
        if self._after_id:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def call(self, fn: Callable, *args: Any, **kw: Any) -> None:
        """Schedule *fn* to be called on the tkinter main thread."""
        self._q.put((fn, args, kw))

    # ── internals ─────────────────────────────────────────────────────

    def _drain(self) -> None:
        try:
            while True:
                fn, args, kw = self._q.get_nowait()
                try:
                    fn(*args, **kw)
                except Exception:
                    log.exception("UIBridge: error in dispatched callback")
        except queue.Empty:
            pass
        self._schedule()

    def _schedule(self) -> None:
        try:
            self._after_id = self._root.after(_DRAIN_MS, self._drain)
        except Exception:
            pass   # root already destroyed
