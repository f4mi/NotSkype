#!/usr/bin/env python3
"""run.py – Convenience launcher for the Not Skype UI / CIT200 phone app.

Usage
-----
  python run.py                              # Not Skype UI, local mock backend
  python run.py --platform telegram_private  # Not Skype UI + real Telegram calls
  python run.py --mode gui                   # control-center GUI
  python run.py --mode mic_gui               # simple phone-as-microphone window
  python run.py --mode recorder              # audio loopback diagnostic
  python run.py --debug                      # verbose logging
"""

import io
import sys
from pathlib import Path

# ── Force UTF-8 output on Windows so log messages with Unicode don't crash ──
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                  errors='replace', line_buffering=True)
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8',
                                  errors='replace', line_buffering=True)

# Project root on path
sys.path.insert(0, str(Path(__file__).parent))

from src.main import main

if __name__ == '__main__':
    args = sys.argv[1:]
    # Default to --mode phone (shows the Not Skype UI window) when no mode given
    if not any(a.startswith('--mode') for a in args):
        args = ['--mode', 'phone'] + args
    main(args)
