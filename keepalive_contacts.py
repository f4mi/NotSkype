#!/usr/bin/env python3
"""keepalive_contacts.py – Minimal CIT200 keepalive + static contacts harness.

Purpose
-------
Keeps the handset alive and serves a static contacts/detail-pages list.
Useful for HID/protocol sanity checks without starting the full app.

Usage
-----
  python keepalive_contacts.py
  python keepalive_contacts.py --debug

Requirements
------------
  - hid (hidapi) must be installed: pip install hid
  - CIT200 handset plugged in via USB
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

# Make sure the src package is importable
sys.path.insert(0, str(Path(__file__).parent))

from src.cit200 import CIT200Device, Contact, Event, Status

log = logging.getLogger(__name__)

# ── Static demo contacts ───────────────────────────────────────────────────────

DEMO_CONTACTS: List[Contact] = [
    Contact(name="pamela",      handle="pamela",      status=Status.ONLINE),
    Contact(name="andrew",      handle="andrew",      status=Status.ONLINE),
    Contact(name="skype_lover", handle="skype_lover", status=Status.AWAY),
    Contact(name="skype_rocks", handle="skype_rocks", status=Status.OFFLINE),
    Contact(name="catherine",   handle="catherine",   status=Status.ONLINE),
    Contact(name="hilary",      handle="hilary",      status=Status.BUSY),
]

DEMO_BIO = [
    "Lives in: London, UK",
    "Skype user since 2005",
]


# ── Handlers ───────────────────────────────────────────────────────────────────

def on_contacts_request(phone: CIT200Device, index: int) -> None:
    log.info("CONTACTS_REQUEST index=%d (serving %d contacts)", index, len(DEMO_CONTACTS))
    phone.send_contacts(DEMO_CONTACTS, index, len(DEMO_CONTACTS))


def on_contact_details(phone: CIT200Device, index: int, detail_type: int) -> None:
    log.info("CONTACT_DETAILS index=%d detail_type=%d", index, detail_type)
    if index >= len(DEMO_CONTACTS):
        log.warning("  index out of range – ignoring")
        return

    contact = DEMO_CONTACTS[index]
    log.info("  -> contact: %s (%s)", contact.name, contact.handle)

    if detail_type == 0:
        phone.send_contact_details(
            contact,
            language=0,
            birthday="1990-06-15",
            gender=1,
        )
    elif detail_type == 1:
        phone.send_contact_numbers_page(
            home="0441234567",
            office="0449876543",
            mobile="07700900000",
        )
    else:
        addr = DEMO_BIO[index % len(DEMO_BIO)]
        now  = __import__("datetime").datetime.now()
        phone.send_contact_bio_page(addr, hh=now.hour, mm=now.minute)


def on_call_button(phone: CIT200Device) -> None:
    log.info("CALL_BUTTON pressed (this harness does not place calls)")


def on_end_call(phone: CIT200Device) -> None:
    log.info("END_CALL pressed")
    phone.end_call_from_handset()


# ── Main loop ──────────────────────────────────────────────────────────────────

def run(debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s – %(message)s",
    )

    cfg = {
        "hid": {
            "transport_mode": "dual",
            "keepalive_interval": 1.6,
        }
    }
    phone = CIT200Device(cfg)

    if not phone.open():
        log.error("Could not open CIT200. Is the handset plugged in and hidapi installed?")
        sys.exit(1)

    # Wire handlers
    phone.on(Event.CONTACTS_REQUEST, lambda idx: on_contacts_request(phone, idx))
    phone.on(Event.CONTACT_DETAILS,  lambda idx, dt: on_contact_details(phone, idx, dt))
    phone.on(Event.CALL_BUTTON,      lambda: on_call_button(phone))
    phone.on(Event.END_CALL,         lambda: on_end_call(phone))
    phone.on(Event.PING,             lambda: log.debug("PING"))

    log.info("Keepalive harness running. Press Ctrl+C to stop.")
    log.info("Contacts loaded: %d entries", len(DEMO_CONTACTS))

    try:
        while True:
            phone.poll()
            phone.send_keepalive()
            time.sleep(0.02)
    except KeyboardInterrupt:
        log.info("Interrupted – closing.")
    finally:
        phone.close()
        log.info("Done.")


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(
        description="CIT200 keepalive + static contacts harness"
    )
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args(argv)
    run(debug=args.debug)


if __name__ == "__main__":
    main()
