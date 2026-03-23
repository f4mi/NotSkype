#!/usr/bin/env python3
"""manual_yn_test.py – Guided human yes/no regression test checklist.

Usage
-----
  python manual_yn_test.py
  python manual_yn_test.py --out test_logs/

Output
------
  Timestamped CSV in the output directory (default: test_logs/).
  Prints pass/fail summary to stdout at the end.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys
from pathlib import Path
from typing import List, Optional

# ── Test step definitions ──────────────────────────────────────────────────────

STEPS = [
    # (id, description, instructions)
    ("boot_no_hw",
     "Cold start – no hardware attached",
     "Run: python run.py\n"
     "  Expected: Skype UI window opens, 'Online' in status bar, contacts list loads.\n"
     "  No crash or traceback in the terminal."),

    ("boot_with_cit200",
     "Cold start – CIT200 plugged in",
     "Plug in the CIT200 handset, then run: python run.py\n"
     "  Expected: Same as above. Terminal log shows CIT200 opened (or graceful skip)."),

    ("contacts_load",
     "Contacts list loads on Friends tab",
     "Click the 'Friends' tab.\n"
     "  Expected: At least 1 contact visible. Online contacts show green dot."),

    ("contacts_handset",
     "Contacts rendered on handset",
     "On the CIT200, navigate to Contacts menu.\n"
     "  Expected: Contact names appear on the handset display."),

    ("contact_detail_page0",
     "Contact details – base page (page 0)",
     "On the CIT200, open details for any contact.\n"
     "  Expected: Name, handle, status visible on display."),

    ("contact_detail_page1",
     "Contact details – numbers page (page 1)",
     "From the contact details page, press MORE or navigate to page 1.\n"
     "  Expected: Phone number fields appear (may be blank if not configured)."),

    ("contact_detail_page2",
     "Contact details – address/bio page (page 2)",
     "Navigate to page 2 of contact details.\n"
     "  Expected: Bio/address text and clock visible (or blank fields, no crash)."),

    ("outgoing_call",
     "Outgoing call – UI green button",
     "Select a contact in Friends tab, click the green handset button.\n"
     "  Expected: UI switches to 'Calling <name>' view. Status bar shows call info.\n"
     "  (Local mock: auto-answers after ~1 second → moves to 'Call with <name>'.)"),

    ("outgoing_call_handset",
     "Outgoing call – handset call button",
     "Select a contact on the handset and press the call button.\n"
     "  Expected: UI switches to calling state. Handset shows call-in-progress."),

    ("call_answer",
     "Remote answer transitions to in-call",
     "After placing an outgoing call (local mock), wait for auto-answer.\n"
     "  Expected: UI switches from 'Calling' to 'Call with <name>'. Timer starts."),

    ("incoming_call",
     "Incoming call – rings handset + UI",
     "(With local mock: set call_me_on_start=true in config.json, restart.)\n"
     "  Expected: UI shows 'Calling <caller>' or incoming state.\n"
     "  Handset rings. Can answer on handset."),

    ("hangup_ui",
     "Hangup via UI red button",
     "While in a call, click the red handset button.\n"
     "  Expected: Call ends, UI returns to Log tab. Timer stops."),

    ("hangup_handset",
     "Hangup via handset end-call key",
     "While in a call, press the End key on the CIT200.\n"
     "  Expected: Same result as above, from handset side."),

    ("missed_call",
     "Missed call recorded in Log",
     "Trigger an incoming call but do NOT answer. Let it time out or reject.\n"
     "  Expected: Log tab shows '1 missed call' with caller name link."),

    ("audio_tx",
     "Audio TX – voice captured from handset mic",
     "During an active call, speak into the CIT200 mic.\n"
     "  Expected: No crash. Terminal shows no audio errors.\n"
     "  (With local mock echo, you should hear yourself back.)"),

    ("audio_rx",
     "Audio RX – playback through handset speaker",
     "During a call with local echo enabled, confirm RX audio.\n"
     "  Expected: Captured audio is played back through handset earpiece."),

    ("status_bar_local",
     "Status bar – local mode shows 'Online'",
     "With platform=local, check the status bar at the bottom.\n"
     "  Expected: Shows 'Online' (no suffix for local)."),

    ("status_bar_telegram",
     "Status bar – telegram mode shows 'Online · Telegram'",
     "With platform=telegram_private, check the status bar.\n"
     "  Expected: Shows 'Online  ·  Telegram'."),

    ("stable_run_3min",
     "Stable continuous run – 3 minutes no crash",
     "Leave the app running for 3 minutes with no interaction.\n"
     "  Expected: No crash, no exception tracebacks, keepalive ticking in log."),

    ("clean_shutdown",
     "Clean shutdown via window close",
     "Click the X button on the Skype UI window.\n"
     "  Expected: App shuts down cleanly. No hanging processes. Terminal exits."),
]


# ── CSV writer ─────────────────────────────────────────────────────────────────

def _write_csv(results: list, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"manual_test_{ts}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "description", "result", "notes", "timestamp"])
        for row in results:
            w.writerow(row)
    return path


# ── Interactive runner ─────────────────────────────────────────────────────────

def _prompt_yn(prompt: str) -> str:
    """Return 'y', 'n', or 's' (skip)."""
    while True:
        raw = input(prompt).strip().lower()
        if raw in ("y", "n", "s", ""):
            return raw or "s"
        print("  Enter y (pass), n (fail), or s (skip).")


def run_tests(steps: list, out_dir: Path,
              start_at: Optional[str] = None) -> None:
    results   = []
    passed    = 0
    failed    = 0
    skipped   = 0
    started   = start_at is None

    print()
    print("=" * 66)
    print("  CIT200 / Skype UI – Manual Regression Test")
    print(f"  {len(steps)} tests  |  y=pass  n=fail  s=skip  Ctrl+C=abort")
    print("=" * 66)
    print()

    try:
        for idx, (tid, desc, instructions) in enumerate(steps, 1):
            if not started:
                if tid == start_at:
                    started = True
                else:
                    continue

            print(f"[{idx:02d}/{len(steps)}] {desc}")
            print(f"      ID: {tid}")
            print()
            for line in instructions.splitlines():
                print(f"    {line}")
            print()

            result = _prompt_yn("  Result? [y/n/s]: ")
            notes  = ""
            if result == "n":
                notes = input("  Notes (optional): ").strip()

            ts = datetime.datetime.now().isoformat(timespec="seconds")
            results.append([tid, desc, result, notes, ts])

            if result == "y":
                passed  += 1
                print("  ✓ PASS\n")
            elif result == "n":
                failed  += 1
                print("  ✗ FAIL\n")
            else:
                skipped += 1
                print("  – SKIP\n")

    except KeyboardInterrupt:
        print("\n\n  Aborted by user.")

    # ── Summary ────────────────────────────────────────────────────────────
    total_run = passed + failed
    pct       = int(100 * passed / total_run) if total_run else 0

    print()
    print("=" * 66)
    print(f"  Results:  {passed} pass  {failed} fail  {skipped} skip")
    if total_run:
        print(f"  Pass rate: {pct}%")
    print("=" * 66)

    if results:
        csv_path = _write_csv(results, out_dir)
        print(f"  Saved:  {csv_path}")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Manual yes/no regression tests")
    parser.add_argument("--out",      default="test_logs",
                        help="Directory for CSV output (default: test_logs/)")
    parser.add_argument("--start-at", default=None, metavar="TEST_ID",
                        help="Skip to a specific test ID")
    parser.add_argument("--list",     action="store_true",
                        help="List all test IDs and descriptions, then exit")
    args = parser.parse_args(argv)

    if args.list:
        print(f"{'ID':<30} {'Description'}")
        print("-" * 66)
        for tid, desc, _ in STEPS:
            print(f"{tid:<30} {desc}")
        return

    run_tests(STEPS, Path(args.out), start_at=args.start_at)


if __name__ == "__main__":
    main()
