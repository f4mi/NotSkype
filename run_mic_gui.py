#!/usr/bin/env python3
"""Simple launcher for the CIT200 microphone GUI."""

import io
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
    )
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer,
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
    )

sys.path.insert(0, str(Path(__file__).parent))

from src.main import main


if __name__ == "__main__":
    main(["--mode", "mic_gui", *sys.argv[1:]])
