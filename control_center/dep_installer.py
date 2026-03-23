"""dep_installer.py – Boot-time dependency checker/installer.

Checks for required and optional packages, installs missing ones via pip,
and provides platform-specific hints for native libraries.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import logging

log = logging.getLogger(__name__)

REQUIRED: list[str] = []           # All runtime deps are optional for the UI-only path

OPTIONAL: list[str] = [
    "sounddevice",
    "numpy",
    "customtkinter",
    "pystray",
    "Pillow",
]

NATIVE_HINTS: dict[str, str] = {
    "hid":         "Install hidapi native lib: `apt install libhidapi-dev` / `brew install hidapi`",
    "sounddevice": "Install portaudio: `apt install portaudio19-dev` / `brew install portaudio`",
    "tkinter":     "Install tkinter: `apt install python3-tk` / system Python on macOS",
}


def _importable(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def _pip_install(packages: list[str]) -> bool:
    if not packages:
        return True
    log.info("Installing: %s", packages)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", *packages],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("pip install failed:\n%s", result.stderr)
        return False
    return True


def ensure_dependencies(include_optional: bool = False) -> None:
    """Check and install missing packages. Silently no-op if everything is present."""
    to_check = list(REQUIRED)
    if include_optional:
        to_check.extend(OPTIONAL)

    missing = [p for p in to_check if not _importable(p.replace("-", "_").lower())]
    if not missing:
        return

    has_display = True
    try:
        import tkinter
        tkinter.Tk().destroy()
    except Exception:
        has_display = False

    if not has_display:
        _console_install(missing)
    else:
        _gui_install(missing)

    # Re-import and print native hints for still-missing packages
    still_missing = [p for p in missing if not _importable(p.replace("-", "_").lower())]
    for p in still_missing:
        hint = NATIVE_HINTS.get(p.lower())
        if hint:
            print(f"  HINT [{p}]: {hint}")


def _console_install(packages: list[str]) -> None:
    print(f"Installing missing packages: {packages}")
    _pip_install(packages)


def _gui_install(packages: list[str]) -> None:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Installing dependencies…")
    root.geometry("380x120")
    root.resizable(False, False)

    lbl = tk.Label(root, text=f"Installing: {', '.join(packages)}", wraplength=360, pady=12)
    lbl.pack()
    bar = ttk.Progressbar(root, mode="indeterminate", length=340)
    bar.pack(pady=4)
    bar.start(12)

    def _install() -> None:
        _pip_install(packages)
        root.after(0, root.destroy)

    import threading
    t = threading.Thread(target=_install, daemon=True)
    t.start()
    root.mainloop()
