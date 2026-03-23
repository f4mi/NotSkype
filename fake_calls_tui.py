#!/usr/bin/env python3
"""fake_calls_tui.py – CLI REPL to trigger fake incoming calls on the CIT200.

Purpose
-------
Manually trigger incoming calls, end calls, and check call state on the
CIT200 handset without needing a backend platform.  Useful for:
  - Incoming-call UI and callback-flow tests
  - Handset display / ring-tone validation
  - Testing answer/reject event wiring

Usage
-----
  python fake_calls_tui.py
  python fake_calls_tui.py --debug

REPL commands
-------------
  call <name>   – Ring the handset with <name> as the caller ID
  end           – Signal call ended from PC side
  connected     – Send the q17 "call connected" frame (simulate answer)
  status        – Print current call state
  help          – Show this help
  quit / exit   – Stop and exit

Requirements
------------
  - hid (hidapi) installed: pip install hid
  - CIT200 plugged in via USB
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from src.cit200 import CIT200Device, Event, Status

log = logging.getLogger(__name__)


# ── State ──────────────────────────────────────────────────────────────────────

class CallState:
    def __init__(self) -> None:
        self.lock         = threading.Lock()
        self.caller_name: Optional[str] = None
        self.in_call      = False
        self.answered     = False

    def ring(self, name: str) -> None:
        with self.lock:
            self.caller_name = name
            self.in_call     = True
            self.answered    = False

    def answer(self) -> None:
        with self.lock:
            self.answered = True

    def end(self) -> None:
        with self.lock:
            self.caller_name = None
            self.in_call     = False
            self.answered    = False

    def summary(self) -> str:
        with self.lock:
            if not self.in_call:
                return "Idle"
            if self.answered:
                return f"In call with: {self.caller_name}"
            return f"Ringing: {self.caller_name}"


# ── Poll thread ────────────────────────────────────────────────────────────────

def _poll_thread(phone: CIT200Device, state: CallState, stop: threading.Event) -> None:
    last_init = 0.0
    while not stop.is_set():
        phone.poll()
        now = time.monotonic()
        if now - last_init >= 1.6:
            last_init = now
            phone.send_init()
        time.sleep(0.02)


# ── Handset event handlers ─────────────────────────────────────────────────────

def _on_answer(phone: CIT200Device, state: CallState) -> None:
    log.info("Handset: ANSWER pressed")
    state.answer()
    phone.answer_incoming_call()
    phone.confirm_call_connected()
    print("\n  [handset] Answered – call connected.\n> ", end="", flush=True)


def _on_end(phone: CIT200Device, state: CallState) -> None:
    log.info("Handset: END_CALL pressed")
    phone.end_call_from_handset()
    state.end()
    print("\n  [handset] Call ended.\n> ", end="", flush=True)


def _on_reject(phone: CIT200Device, state: CallState) -> None:
    log.info("Handset: REJECT pressed")
    phone.end_call_from_handset()
    state.end()
    print("\n  [handset] Call rejected.\n> ", end="", flush=True)


# ── REPL ───────────────────────────────────────────────────────────────────────

_HELP = """\
Commands:
  call <name>   Ring the handset with <name> as caller ID
  end           Signal call ended from the PC
  connected     Send q17 connected frame (simulate remote answer)
  status        Print current call state
  help          Show this message
  quit / exit   Exit
"""


def _cmd_call(phone: CIT200Device, state: CallState, args: str) -> None:
    name = args.strip() or "Unknown Caller"
    if state.in_call:
        print("  Already in a call. End it first.")
        return
    print(f"  Ringing handset with caller: {name!r}")
    state.ring(name)
    phone.ring()
    phone.display_caller_id(name)
    # Send repeated ring nudges so caller ID stays visible
    def _nudge():
        for _ in range(3):
            time.sleep(0.5)
            if not state.in_call or state.answered:
                break
            phone.ring()
            phone.display_caller_id(name)
    threading.Thread(target=_nudge, daemon=True).start()


def _cmd_end(phone: CIT200Device, state: CallState) -> None:
    if not state.in_call:
        print("  No active call.")
        return
    phone.end_call_from_remote()
    state.end()
    print("  Call ended (PC side).")


def _cmd_connected(phone: CIT200Device, state: CallState) -> None:
    if not state.in_call:
        print("  No active call to connect.")
        return
    phone.confirm_call_connected()
    state.answer()
    print("  Sent q17 connected frame.")


def run_repl(phone: CIT200Device, state: CallState) -> None:
    print(_HELP)
    print("Type a command (or 'help'):\n")
    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not raw:
            continue
        parts   = raw.split(None, 1)
        cmd     = parts[0].lower()
        rest    = parts[1] if len(parts) > 1 else ""

        if cmd in ("quit", "exit", "q"):
            print("Bye.")
            break
        elif cmd == "call":
            _cmd_call(phone, state, rest)
        elif cmd == "end":
            _cmd_end(phone, state)
        elif cmd == "connected":
            _cmd_connected(phone, state)
        elif cmd == "status":
            print(f"  {state.summary()}")
        elif cmd == "help":
            print(_HELP)
        else:
            print(f"  Unknown command: {cmd!r}. Type 'help'.")


# ── Entry point ────────────────────────────────────────────────────────────────

def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Fake incoming call REPL for CIT200"
    )
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s – %(message)s",
    )

    phone = CIT200Device(
        transport_mode="dual",
        call_setup_delay=0.2,
        call_connect_delay=0.2,
    )
    if not phone.open():
        log.error("Could not open CIT200. Is hidapi installed and handset connected?")
        sys.exit(1)

    state    = CallState()
    stop_evt = threading.Event()

    # Wire handset events
    phone.on(Event.ANSWER_INCOMING, lambda: _on_answer(phone, state))
    phone.on(Event.CALL_BUTTON,     lambda: _on_answer(phone, state))
    phone.on(Event.END_CALL,        lambda: _on_end(phone, state))
    phone.on(Event.REJECT_INCOMING, lambda: _on_reject(phone, state))

    # Start poll thread
    t = threading.Thread(
        target=_poll_thread, args=(phone, state, stop_evt),
        daemon=True, name="cit200-poll"
    )
    t.start()

    try:
        run_repl(phone, state)
    finally:
        stop_evt.set()
        t.join(timeout=1.0)
        phone.close()
        log.info("Closed.")


if __name__ == "__main__":
    main()
