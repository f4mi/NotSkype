#!/usr/bin/env python3
"""contacts_experiments_tui.py – Contacts transport experiment matrix runner.

Purpose
-------
Interactively runs combinations of contacts transport configuration knobs
and records YES/NO outcomes.  Designed for diagnosing the 'Contacts unavailable'
problem and isolating the best transport mode + firmware timing for a given
setup.

Experiment dimensions
---------------------
  - transport_mode      : feature_only | output_only | dual
  - contacts_transport  : (same; separate override or inherit)
  - emergency_ack       : on | off
  - compat_resend       : on | off
  - contacts_max        : 3 | 6 | 12 | 20
  - cache_ttl           : 30 | 120 | 300

Usage
-----
  python contacts_experiments_tui.py
  python contacts_experiments_tui.py --quick        # smaller matrix
  python contacts_experiments_tui.py --out results/ # custom output dir

Output
------
  summary.json  in the output directory with all experiment results.
  Prints pass/fail counts at the end.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

BASE_CONFIG_PATH = Path(__file__).parent / "config.json"


# ── Experiment matrix ──────────────────────────────────────────────────────────

FULL_MATRIX: Dict[str, List] = {
    "hid.transport_mode":         ["dual", "feature_only", "output_only"],
    "contacts.emergency_output_ack": [False, True],
    "contacts.compat_resend":     [False, True],
    "contacts.max_contacts":      [6, 20],
    "contacts.cache_ttl_s":       [120, 300],
}

QUICK_MATRIX: Dict[str, List] = {
    "hid.transport_mode":            ["dual", "feature_only"],
    "contacts.emergency_output_ack": [False, True],
    "contacts.max_contacts":         [6],
    "contacts.cache_ttl_s":          [120],
}


def _matrix_rows(matrix: Dict[str, List]) -> Iterator[Dict[str, object]]:
    keys   = list(matrix.keys())
    values = list(matrix.values())
    for combo in product(*values):
        yield dict(zip(keys, combo))


# ── Config patch helpers ───────────────────────────────────────────────────────

def _load_base_config() -> dict:
    if BASE_CONFIG_PATH.exists():
        with open(BASE_CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _apply_overrides(cfg: dict, overrides: Dict[str, object]) -> dict:
    """Apply dot-notation overrides like 'hid.transport_mode' = 'dual'."""
    import copy
    cfg = copy.deepcopy(cfg)
    for dotkey, val in overrides.items():
        parts = dotkey.split(".")
        node  = cfg
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = val
    return cfg


# ── Prompt helpers ─────────────────────────────────────────────────────────────

def _prompt_yn(msg: str) -> str:
    while True:
        raw = input(msg).strip().lower()
        if raw in ("y", "n", "s", ""):
            return raw or "s"
        print("  Enter y (yes), n (no), or s (skip).")


def _describe(overrides: Dict[str, object]) -> str:
    parts = []
    for k, v in overrides.items():
        short_key = k.split(".")[-1]
        parts.append(f"{short_key}={v}")
    return "  ".join(parts)


# ── Experiment runner ──────────────────────────────────────────────────────────

def run_experiment(overrides: Dict[str, object],
                   duration: float,
                   python: str,
                   out_dir: Path) -> Dict:
    """
    Write a patched config to a temp file, launch run.py with it for
    `duration` seconds, then ask the operator for a Y/N result.
    """
    base_cfg   = _load_base_config()
    patched    = _apply_overrides(base_cfg, overrides)

    # Write patched config to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tf:
        json.dump(patched, tf, indent=2)
        tmp_path = tf.name

    cmd = [python, "run.py", "--mode", "phone", "--config", tmp_path]

    start_ts = datetime.now().isoformat(timespec="seconds")
    proc     = None
    notes    = ""
    result   = "s"

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(__file__).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        print(f"\n  Process started (PID {proc.pid}) – running for {duration:.0f}s")
        print("  Navigate to Contacts on the handset now...")
        time.sleep(duration)

        print()
        result = _prompt_yn("  Contacts displayed correctly? [y/n/s]: ")
        if result == "n":
            notes = input("  Notes: ").strip()

    except KeyboardInterrupt:
        result = "s"
        notes  = "aborted"
        raise
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return {
        "overrides":   overrides,
        "result":      result,
        "notes":       notes,
        "timestamp":   start_ts,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Contacts transport experiment matrix runner"
    )
    parser.add_argument("--quick",    action="store_true",
                        help="Use the smaller quick-matrix (fewer combos)")
    parser.add_argument("--out",      default="results",
                        help="Output directory for summary.json (default: results/)")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Seconds to run each experiment (default: 10)")
    parser.add_argument("--python",   default=sys.executable,
                        help="Python interpreter to use")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print experiment list and exit without running")
    args = parser.parse_args(argv)

    matrix   = QUICK_MATRIX if args.quick else FULL_MATRIX
    rows     = list(_matrix_rows(matrix))
    out_dir  = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nContacts experiment matrix: {len(rows)} combinations")
    if args.quick:
        print("  (Quick mode)\n")

    if args.dry_run:
        for i, row in enumerate(rows, 1):
            print(f"  [{i:02d}] {_describe(row)}")
        return

    results  = []
    passed   = 0
    failed   = 0
    skipped  = 0

    print(f"Each experiment runs for {args.duration:.0f}s.  "
          "Press Ctrl+C anytime to abort.\n")
    print("=" * 66)

    try:
        for i, overrides in enumerate(rows, 1):
            print(f"\n[{i:02d}/{len(rows)}] {_describe(overrides)}")
            try:
                rec = run_experiment(
                    overrides=overrides,
                    duration=args.duration,
                    python=args.python,
                    out_dir=out_dir,
                )
            except KeyboardInterrupt:
                print("\nAborted.")
                break

            results.append(rec)
            if rec["result"] == "y":
                passed  += 1
                print("  ✓ PASS")
            elif rec["result"] == "n":
                failed  += 1
                print("  ✗ FAIL")
            else:
                skipped += 1
                print("  – SKIP")

    finally:
        summary_path = out_dir / "summary.json"
        payload = {
            "matrix":   matrix,
            "runs":     results,
            "totals":   {"pass": passed, "fail": failed, "skip": skipped},
            "generated": datetime.now().isoformat(),
        }
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

        print()
        print("=" * 66)
        print(f"  Results: {passed} pass  {failed} fail  {skipped} skip")
        print(f"  Summary: {summary_path}")
        print()

        # Print passing configs for easy reference
        passing = [r["overrides"] for r in results if r["result"] == "y"]
        if passing:
            print("  Passing configurations:")
            for cfg in passing:
                print(f"    {_describe(cfg)}")
        print()


if __name__ == "__main__":
    main()
