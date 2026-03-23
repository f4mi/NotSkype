#!/usr/bin/env python3
"""run_phone_capture.py – Subprocess tee launcher for phone mode.

Launches `python run.py --mode phone [args]` and tees all output to both
the terminal and a timestamped log file simultaneously.

Usage
-----
  python run_phone_capture.py
  python run_phone_capture.py --platform telegram_private
  python run_phone_capture.py --log-dir captures/ --debug

Press Ctrl+C to stop the child process and close the log file.

Output
------
  Log file at <log_dir>/phone_<timestamp>.log  (default: captures/)
  Prints exact command and log path at startup.
"""

from __future__ import annotations

import argparse
import datetime
import io
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def build_command(platform: Optional[str], debug: bool,
                  python: str, extra: List[str]) -> List[str]:
    cmd = [python, "run.py", "--mode", "phone"]
    if platform:
        cmd += ["--platform", platform]
    if debug:
        cmd.append("--debug")
    cmd.extend(extra)
    return cmd


def tee_stream(proc: subprocess.Popen, log_fh: io.TextIOBase) -> None:
    """Stream subprocess stdout+stderr to console and log file."""
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace")
        sys.stdout.write(line)
        sys.stdout.flush()
        log_fh.write(line)
        log_fh.flush()


def run(platform: Optional[str], debug: bool, log_dir: Path,
        python: str, extra: List[str]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"phone_{ts}.log"

    cmd = build_command(platform, debug, python, extra)

    print("=" * 66)
    print(f"  Command : {' '.join(cmd)}")
    print(f"  Log file: {log_path}")
    print("  Press Ctrl+C to stop.")
    print("=" * 66)
    print()

    with open(log_path, "w", encoding="utf-8") as log_fh:
        # Write header to log
        log_fh.write(f"# run_phone_capture.py  –  {datetime.datetime.now().isoformat()}\n")
        log_fh.write(f"# Command: {' '.join(cmd)}\n\n")
        log_fh.flush()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,    # merge stderr into stdout
            cwd=str(Path(__file__).parent),
        )

        try:
            tee_stream(proc, log_fh)
        except KeyboardInterrupt:
            print("\n\nInterrupted – terminating child process...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        rc = proc.wait()
        footer = f"\n# Exit code: {rc}\n"
        log_fh.write(footer)

    print(f"\nLog saved to: {log_path}")
    if rc != 0:
        print(f"Child process exited with code {rc}.")
    sys.exit(rc)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Tee-capture launcher for phone mode"
    )
    parser.add_argument("--platform", default=None,
                        help="Backend platform (e.g. telegram_private)")
    parser.add_argument("--debug", action="store_true",
                        help="Pass --debug to run.py")
    parser.add_argument("--log-dir", default="captures",
                        help="Directory for log files (default: captures/)")
    parser.add_argument("--python", default=sys.executable,
                        help="Python interpreter to use (default: current interpreter)")
    # Remaining args are forwarded to run.py
    args, extra = parser.parse_known_args(argv)

    run(
        platform=args.platform,
        debug=args.debug,
        log_dir=Path(args.log_dir),
        python=args.python,
        extra=extra,
    )


if __name__ == "__main__":
    main()
