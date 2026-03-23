"""control_center/__main__.py – Entry point for `python -m control_center`.

Usage:
  python -m control_center          # desktop GUI
  python -m control_center --tray   # system tray mode
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback


def main() -> None:
    parser = argparse.ArgumentParser(description="CIT200 Control Center")
    parser.add_argument("--tray",  action="store_true", help="Run as system tray service")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s – %(message)s")

    from .dep_installer import ensure_dependencies
    ensure_dependencies()

    try:
        if args.tray:
            from .tray_app import launch_tray
            launch_tray()
        else:
            from .desktop_gui import launch_gui
            launch_gui()
    except Exception:
        print("FATAL: control_center crashed:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
