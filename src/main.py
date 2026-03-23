"""main.py – PhoneApp orchestrator + config I/O.

Modes
-----
  phone    : CIT200 handset + platform + audio, with Skype UI window
  recorder : minimal record/playback diagnostic TUI
  gui      : desktop control-center GUI (default)

Usage
-----
  python -m src.main                        # GUI / control center
  python -m src.main --mode phone           # phone mode (local mock)
  python -m src.main --mode phone --platform telegram_private
  python -m src.main --mode recorder
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import hashlib
import re
import secrets
import signal
import sys
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

DEFAULTS: Dict[str, Any] = {
    "platform": "local",
    "username": "john_smith",
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        "chunk_size": 960,
        "meter_enabled": False,
        "meter_interval_s": 10.0,
        "meter_level": "debug",
    },
    "contacts": {
        "order": "online_first",
        "max_contacts": 100,
        "cache_ttl_s": 15.0,
        "fetch_timeout_s": 8.0,
        "refresh_retries": 2,
        "refresh_backoff_s": 0.35,
        "background_refresh_s": 30.0,
        "min_request_interval_s": 1.0,
        "allow_stale_fallback": True,
        "prefetch_on_connect": True,
        "selected_only": False,
        "selected_prioritize": False,
        "force_selected_only": False,
        "selected_contacts": [],
        "phone_id_shim": True,
        "phone_id_shim_prefix": "6",
        "phone_id_shim_value": "6 345 432 236 543 457 4 222",
        "detail_overrides": [],
        "diagnostics": False,
        "diagnostics_sample": 5,
        "compat_resend": False,
        "compat_page_size": 33,
        "emergency_output_ack": True,
    },
    "hid": {
        "transport_mode": "dual",
        "keepalive_interval": 1.6,
        "q9_q10_delay": 0.2,
        "call_connect_delay": 0.2,
        "contacts_frame_delay_s": 0.05,
        "contacts_contact_delay_s": 0.05,
        "contacts_transport_mode": "feature_only",
    },
    "recording": {
        "enabled": True,
        "auto_record_calls": True,
        "directory": "recordings",
    },
    "local": {
        "call_me_on_start": False,
        "call_me_delay_s": 3.0,
        "auto_incoming_after_s": None,
        "echo_gain": 0.5,
        "contacts": [],
    },
    "telegram": {
        "api_id": 0,
        "api_hash": "",
        "session_name": "skype_session",
    },
}

PLATFORM_CHOICES = ["telegram", "telegram_private", "discord", "local"]


# ── Setup TUI utilities ──────────────────────────────────────────────────────

def _split_csv_list(raw: str) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for part in str(raw or "").split(","):
        item = part.strip()
        if not item:
            continue
        key = _normalize_contact_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _looks_like_api_hash(value: str) -> bool:
    s = str(value or "").strip()
    return bool(re.fullmatch(r"[0-9a-fA-F]{32}", s))


def _apply_telegram_field_repair(cfg: dict) -> dict:
    """Repair common config mistake: api_hash pasted into phone field."""
    out = dict(cfg or {})
    api_hash = str(out.get("api_hash", "") or "").strip()
    phone = str(out.get("phone", "") or "").strip()
    if not api_hash and _looks_like_api_hash(phone):
        out["api_hash"] = phone
        out["phone"] = ""
    return out


def _telegram_settings_with_env(config: dict) -> dict:
    cfg = dict(config.get("telegram", {})) if isinstance(config, dict) else {}

    env_api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    if env_api_id:
        try:
            cfg["api_id"] = int(env_api_id)
        except ValueError:
            log.warning("Ignoring invalid TELEGRAM_API_ID: %r", env_api_id)

    env_api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if env_api_hash:
        cfg["api_hash"] = env_api_hash

    env_phone = os.getenv("TELEGRAM_PHONE", "").strip()
    if env_phone:
        cfg["phone"] = env_phone

    env_session = os.getenv("TELEGRAM_SESSION_NAME", "").strip()
    if env_session:
        cfg["session_name"] = env_session

    return _apply_telegram_field_repair(cfg)


def _telegram_env_override_names() -> List[str]:
    names = []
    for n in ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE", "TELEGRAM_SESSION_NAME"]:
        if os.getenv(n, "").strip():
            names.append(n)
    return names


def _missing_telegram_creds(config: dict) -> List[str]:
    tg_cfg = _telegram_settings_with_env(config)
    missing: List[str] = []
    try:
        api_id = int(tg_cfg.get("api_id", 0))
    except (TypeError, ValueError):
        api_id = 0
    api_hash = str(tg_cfg.get("api_hash", "") or "").strip()

    if not api_id or api_id == 12345678:
        missing.append("api_id")
    if not api_hash or api_hash == "0123456789abcdef0123456789abcdef":
        missing.append("api_hash")
    return missing


async def _fetch_telegram_contacts_for_tui(config: dict) -> list:
    from .platforms.telegram_ntg import TelegramNTGPlatform

    tg_cfg = _telegram_settings_with_env(config)
    audio_cfg = config.get("audio", {}) if isinstance(config, dict) else {}
    platform = TelegramNTGPlatform(
        api_id=int(tg_cfg.get("api_id", 0)),
        api_hash=tg_cfg.get("api_hash", ""),
        phone=tg_cfg.get("phone", ""),
        session_name=tg_cfg.get("session_name", "skype_session"),
        sample_rate=int(audio_cfg.get("sample_rate", 16000)),
        channels=int(audio_cfg.get("channels", 1)),
    )

    try:
        await platform.connect()
        contacts = await platform.get_contacts()
        return contacts
    finally:
        try:
            await platform.disconnect()
        except Exception:
            pass


def _ensure_contacts_defaults(config: dict) -> None:
    contacts_cfg = config.setdefault("contacts", {})
    contacts_cfg.setdefault("order", "online_first")
    contacts_cfg.setdefault("max_contacts", 100)
    contacts_cfg.setdefault("selected_only", False)
    contacts_cfg.setdefault("force_selected_only", False)
    contacts_cfg.setdefault("cache_ttl_s", 15.0)
    contacts_cfg.setdefault("fetch_timeout_s", 8.0)
    contacts_cfg.setdefault("refresh_retries", 2)
    contacts_cfg.setdefault("refresh_backoff_s", 0.35)
    contacts_cfg.setdefault("allow_stale_fallback", True)
    contacts_cfg.setdefault("prefetch_on_connect", True)
    contacts_cfg.setdefault("background_refresh_s", 30.0)
    contacts_cfg.setdefault("diagnostics", False)
    contacts_cfg.setdefault("diagnostics_sample", 5)
    contacts_cfg.setdefault("compat_resend", False)
    contacts_cfg.setdefault("compat_page_size", 33)
    contacts_cfg.setdefault("emergency_output_ack", True)
    contacts_cfg.setdefault("phone_id_shim", True)
    contacts_cfg.setdefault("phone_id_shim_prefix", "6")
    contacts_cfg.setdefault("phone_id_shim_value", "6 345 432 236 543 457 4 222")
    if not contacts_cfg.get("phone_id_shim_salt"):
        contacts_cfg["phone_id_shim_salt"] = secrets.token_hex(16)
    if not isinstance(contacts_cfg.get("detail_overrides"), list):
        contacts_cfg["detail_overrides"] = []
    if not isinstance(contacts_cfg.get("selected_contacts"), list):
        contacts_cfg["selected_contacts"] = []


# ── Config helpers ────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    cfg = _deep_merge({}, DEFAULTS)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            cfg = _deep_merge(cfg, on_disk)
            log.info("Config loaded from %s", path)
        except Exception as e:
            log.warning("Config load failed (%s) – using defaults", e)
    else:
        log.info("No config.json found – using defaults")
    return cfg


def save_config(cfg: Dict[str, Any], path: Path = CONFIG_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        log.info("Config saved to %s", path)
    except Exception as e:
        log.error("Config save failed: %s", e)


# ── Platform factory ──────────────────────────────────────────────────────────

def _build_platform(cfg: Dict[str, Any]):
    name = cfg.get("platform", "local")
    if name == "local":
        from .platforms.local_mock import LocalMockPlatform
        local_cfg = cfg.get("local", {})
        return LocalMockPlatform(
            contacts=local_cfg.get("contacts"),
            auto_incoming_after_s=float(local_cfg.get("auto_incoming_after_s") or 0),
            call_me_on_start=bool(local_cfg.get("call_me_on_start", False)),
            call_me_delay_s=float(local_cfg.get("call_me_delay_s", 3.0)),
            echo_gain=float(local_cfg.get("echo_gain", 0.5)),
        )

    _BACKENDS = {
        "telegram_private": ("telegram_ntg", "TelegramNTGPlatform"),
        "telegram_ntg":     ("telegram_ntg", "TelegramNTGPlatform"),
        "telegram":         ("telegram",     "TelegramPlatform"),
        "discord":          ("discord_",     "DiscordPlatform"),
    }
    if name in _BACKENDS:
        mod_suffix, cls_name = _BACKENDS[name]
        try:
            mod = importlib.import_module(f".platforms.{mod_suffix}", package=__package__)
            cls = getattr(mod, cls_name)
            if cls_name == "TelegramNTGPlatform":
                tg = cfg.get("telegram", {})
                return cls(
                    api_id=int(tg.get("api_id", 0)),
                    api_hash=str(tg.get("api_hash", "")),
                    phone=str(tg.get("phone", "")),
                    session_name=str(tg.get("session_name", "skype_session")),
                    sample_rate=int(cfg.get("audio", {}).get("sample_rate", 16000)),
                    channels=int(cfg.get("audio", {}).get("channels", 1)),
                )
            return cls(cfg)
        except (ImportError, AttributeError, ValueError) as e:
            log.error("%s unavailable: %s – falling back to local", name, e)
            from .platforms.local_mock import LocalMockPlatform
            return LocalMockPlatform()

    log.warning("Unknown platform %r – using local mock", name)
    from .platforms.local_mock import LocalMockPlatform
    return LocalMockPlatform()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalize_contact_key(value: str) -> str:
    """Normalize a contact key for matching (lowercase, no spaces)."""
    return "".join(str(value or "").strip().lower().split())


# ── Contacts ordering ─────────────────────────────────────────────────────────

def _order_contacts(contacts, cfg: dict) -> list:
    contacts_cfg = cfg.get("contacts", {})
    order  = contacts_cfg.get("order", "online_first")
    maxc   = int(contacts_cfg.get("max_contacts", 100))
    sel    = contacts_cfg.get("selected_contacts", [])
    sel_only = bool(contacts_cfg.get("selected_only", False))
    sel_pri  = bool(contacts_cfg.get("selected_prioritize", False))

    def in_sel(c):
        return c.handle in sel or c.name in sel or c.id in sel

    if sel and sel_only:
        contacts = [c for c in contacts if in_sel(c)]
    elif sel and sel_pri:
        sel_c    = [c for c in contacts if in_sel(c)]
        other_c  = [c for c in contacts if not in_sel(c)]
        contacts = sel_c + other_c

    if order == "online_first":
        contacts = sorted(contacts, key=lambda c: (not c.online, (c.name or c.handle or "").lower()))
    else:
        contacts = sorted(contacts, key=lambda c: (c.name or c.handle or "").lower())

    return contacts[:maxc]


# ── Setup TUI ─────────────────────────────────────────────────────────────────

def run_setup_tui(config: dict) -> Tuple[Optional[str], bool]:
    """Interactive setup menu. Returns (platform_name | None, should_run)."""
    _ensure_contacts_defaults(config)

    def _resolve_platform_before_run() -> Optional[str]:
        current_platform = str(config.get("platform", "") or "")
        if current_platform != "local":
            return current_platform

        print("\nCurrent platform is 'local' (feedback demo).")
        print("1) Switch to telegram_private and run")
        print("2) Run local demo")
        print("3) Cancel")
        raw = input("Choose: ").strip().lower()
        if raw in {"1", "telegram_private", "telegram", "tg"}:
            config["platform"] = "telegram_private"
            return "telegram_private"
        if raw in {"2", "local", "l"}:
            return "local"
        return None

    while True:
        contacts_cfg = config.setdefault("contacts", {})
        selected_contacts = contacts_cfg.get("selected_contacts", [])
        platform = str(config.get("platform", "") or "(unset)")
        missing_tg = _missing_telegram_creds(config)
        env_overrides = _telegram_env_override_names()

        print("\n=== CIT200 Setup ===")
        print(f"Platform: {platform}")
        if platform == "local":
            print("Note: 'local' is the loopback/feedback demo platform.")
        print(f"Telegram creds: {'OK' if not missing_tg else 'missing ' + ', '.join(missing_tg)}")
        if env_overrides:
            print(f"Telegram env overrides active: {', '.join(env_overrides)}")
        print(f"Max contacts: {contacts_cfg.get('max_contacts', 100)}")
        print(f"Selected-only mode: {'on' if contacts_cfg.get('selected_only', False) else 'off'}")
        print(f"Selected contacts: {len(selected_contacts)}")
        print("\n0) Telegram settings/auth")
        print("\n1) Set platform")
        print("2) Set max contacts")
        print("3) Toggle selected-only")
        print("4) Edit selected contacts (manual)")
        print("5) Pick selected contacts from Telegram")
        print("6) Save and run")
        print("7) Run without saving")
        print("8) Save and exit")
        print("9) Exit without running")

        choice = input("Choose: ").strip().lower()

        if choice == "0":
            tg = config.setdefault("telegram", {})

            if _looks_like_api_hash(str(tg.get("phone", "") or "")) and not str(tg.get("api_hash", "") or "").strip():
                print("Detected api_hash in phone field. Auto-fixing (phone -> api_hash).")
                tg["api_hash"] = str(tg.get("phone", "") or "").strip()
                tg["phone"] = ""

            print("\nTelegram settings (Enter keeps current value)")
            current_api_id = str(tg.get("api_id", "") or "")
            raw_api_id = input(f"api_id [{current_api_id}]: ").strip()
            if raw_api_id:
                try:
                    tg["api_id"] = int(raw_api_id)
                except ValueError:
                    print("Invalid api_id (must be integer)")

            current_api_hash = str(tg.get("api_hash", "") or "")
            masked_hash = (current_api_hash[:6] + "..." + current_api_hash[-4:]) if len(current_api_hash) > 12 else current_api_hash
            raw_api_hash = input(f"api_hash [{masked_hash or '(empty)'}]: ").strip()
            if raw_api_hash:
                tg["api_hash"] = raw_api_hash
            elif not str(tg.get("api_hash", "") or "").strip():
                print("api_hash is required for Telegram.")

            current_phone = str(tg.get("phone", "") or "")
            raw_phone = input(f"phone [{current_phone}]: ").strip()
            if raw_phone:
                tg["phone"] = raw_phone

            current_session = str(tg.get("session_name", "skype_session") or "skype_session")
            raw_session = input(f"session_name [{current_session}]: ").strip()
            if raw_session:
                tg["session_name"] = raw_session

            run_auth = input("Run Telegram auth bootstrap now? [y/N]: ").strip().lower()
            if run_auth in {"y", "yes"}:
                try:
                    asyncio.run(_run_telegram_auth_bootstrap(config))
                except Exception as e:
                    print(f"Telegram auth failed: {e}")

        elif choice == "1":
            print("\nChoose platform:")
            for i, name in enumerate(PLATFORM_CHOICES, start=1):
                print(f"{i}) {name}")
            raw = input("Platform number/name: ").strip().lower()
            picked = ""
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(PLATFORM_CHOICES):
                    picked = PLATFORM_CHOICES[idx]
            elif raw in PLATFORM_CHOICES:
                picked = raw
            if picked:
                config["platform"] = picked
            else:
                print("Invalid platform")

        elif choice == "2":
            raw = input("Max contacts (1-100): ").strip()
            try:
                contacts_cfg["max_contacts"] = max(1, min(100, int(raw)))
            except ValueError:
                print("Invalid number")

        elif choice == "3":
            contacts_cfg["selected_only"] = not bool(contacts_cfg.get("selected_only", False))

        elif choice == "4":
            print("Enter handles/names/ids comma-separated.")
            print("Leave blank to clear selected contacts.")
            raw = input("selected_contacts: ").strip()
            contacts_cfg["selected_contacts"] = _split_csv_list(raw)

        elif choice == "5":
            missing_tg = _missing_telegram_creds(config)
            if missing_tg:
                print(
                    "Telegram API credentials missing. "
                    "Use option 0 first (or set TELEGRAM_API_ID / TELEGRAM_API_HASH env vars)."
                )
                continue

            print("Fetching Telegram contacts...")
            try:
                contacts = asyncio.run(_fetch_telegram_contacts_for_tui(config))
            except Exception as e:
                print(f"Failed to fetch Telegram contacts: {e}")
                continue

            if not contacts:
                print("No Telegram contacts found.")
                continue

            contacts = sorted(contacts, key=lambda c: (c.name or c.handle or "").lower())
            print("\nTelegram contacts:")
            for i, c in enumerate(contacts, start=1):
                label = c.name or c.handle or c.id
                handle = c.handle or c.id
                print(f"{i:3d}) {label} ({handle})")

            raw = input("\nPick indexes (e.g. 1,2,5-8), 'all', 'none', or Enter to cancel: ").strip().lower()
            if not raw:
                continue
            if raw == "none":
                contacts_cfg["selected_contacts"] = []
                continue
            if raw == "all":
                picked_contacts = contacts
            else:
                indexes: List[int] = []
                for token in raw.split(","):
                    token = token.strip()
                    if not token:
                        continue
                    if "-" in token:
                        a, b = token.split("-", 1)
                        if a.strip().isdigit() and b.strip().isdigit():
                            lo = int(a.strip())
                            hi = int(b.strip())
                            if lo > hi:
                                lo, hi = hi, lo
                            indexes.extend(range(lo, hi + 1))
                    elif token.isdigit():
                        indexes.append(int(token))

                seen_idx: set = set()
                picked_contacts = []
                for idx in indexes:
                    if idx in seen_idx:
                        continue
                    seen_idx.add(idx)
                    z = idx - 1
                    if 0 <= z < len(contacts):
                        picked_contacts.append(contacts[z])

            max_contacts = max(1, min(100, int(contacts_cfg.get("max_contacts", 100))))
            if len(picked_contacts) > max_contacts:
                print(f"Selected {len(picked_contacts)} contacts, trimming to max_contacts={max_contacts}.")
                picked_contacts = picked_contacts[:max_contacts]

            contacts_cfg["selected_contacts"] = [c.handle or c.id for c in picked_contacts]
            print(f"Selected contacts saved: {len(contacts_cfg['selected_contacts'])}")

        elif choice == "6":
            resolved = _resolve_platform_before_run()
            if not resolved:
                continue
            save_config(config)
            return resolved, True

        elif choice == "7":
            resolved = _resolve_platform_before_run()
            if not resolved:
                continue
            return resolved, True

        elif choice == "8":
            save_config(config)
            return None, False

        elif choice == "9":
            return None, False

        else:
            print("Invalid choice")


# ── RecorderTUI ───────────────────────────────────────────────────────────────

class RecorderTUI:
    """Standalone handset recorder/playback TUI integrated into main.py."""

    CLEAR = "\033[2J\033[H"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    RESET = "\033[0m"

    def __init__(
        self,
        hid_write_mode: str = "dual",
        call_delay_a: float = 0.2,
        call_delay_b: float = 0.2,
        hid_trace_path: str = "",
        hid_trace_window: float = 3.0,
    ):
        self._msvcrt = None
        self._termios = None
        self._tty = None
        self._stdin_fd = None
        self._stdin_attrs = None

        if os.name == "nt":
            try:
                import msvcrt as _msvcrt
            except ImportError:
                _msvcrt = None  # type: ignore
            self._msvcrt = _msvcrt
        else:
            try:
                import termios as _termios
                import tty as _tty
            except ImportError:
                _termios = None  # type: ignore
                _tty = None  # type: ignore
            self._termios = _termios
            self._tty = _tty

        from .cit200 import CIT200Device, Event, Status
        from .audio_bridge import AudioBridge

        self.phone = CIT200Device(
            transport_mode=hid_write_mode,
            call_setup_delay=call_delay_a,
            call_connect_delay=call_delay_b,
        )
        self.bridge = AudioBridge()
        self._Event = Event
        self._Status = Status

        self.recorded = bytearray()
        self.is_recording = False
        self.is_playing = False
        self.in_call = False
        self._handset_call_ready = False
        self.record_start = 0.0
        self.last_duration = 0.0
        self.status_msg = "Waiting for handset..."
        self.call_audio_ready = False
        self.hid_write_mode = hid_write_mode
        self.call_delay_a = call_delay_a
        self.call_delay_b = call_delay_b
        self.hid_trace_path = hid_trace_path

        self._running = False
        self._hid_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._needs_redraw = True

        self.bridge.on_audio_captured = self._on_mic_audio

    def _ensure_call_audio_ready(self):
        if not self.bridge.is_running:
            self.bridge.start()
        self.call_audio_ready = self.in_call and self.bridge.is_running and self._handset_call_ready

    def _on_mic_audio(self, pcm):
        if self.is_recording:
            self.recorded.extend(pcm)

    def _console_init(self) -> bool:
        if self._msvcrt is not None:
            return True
        if not self._termios or not self._tty:
            return False
        if not sys.stdin.isatty():
            return False
        try:
            self._stdin_fd = sys.stdin.fileno()
            self._stdin_attrs = self._termios.tcgetattr(self._stdin_fd)
            self._tty.setcbreak(self._stdin_fd)
            return True
        except Exception:
            self._stdin_fd = None
            self._stdin_attrs = None
            return False

    def _console_restore(self):
        if self._msvcrt is not None:
            return
        if self._stdin_fd is None or self._stdin_attrs is None or not self._termios:
            return
        try:
            self._termios.tcsetattr(self._stdin_fd, self._termios.TCSADRAIN, self._stdin_attrs)
        except Exception:
            pass
        self._stdin_fd = None
        self._stdin_attrs = None

    def _key_available(self) -> bool:
        if self._msvcrt is not None:
            return bool(self._msvcrt.kbhit())
        if self._stdin_fd is None:
            return False
        try:
            import select
            r, _, _ = select.select([self._stdin_fd], [], [], 0)
            return bool(r)
        except Exception:
            return False

    def _read_key(self, blocking: bool = False) -> bytes:
        if self._msvcrt is not None:
            if blocking:
                return self._msvcrt.getch()
            return self._msvcrt.getch() if self._msvcrt.kbhit() else b""
        if self._stdin_fd is None:
            return b""
        try:
            if not blocking and not self._key_available():
                return b""
            return os.read(self._stdin_fd, 1)
        except Exception:
            return b""

    def start(self):
        if not self._console_init():
            print("Recorder TUI requires an interactive terminal.")
            return

        if os.name == "nt":
            os.system("")

        if not self.phone.open():
            print("Failed to open CIT200. Is it plugged in?")
            self._console_restore()
            return

        Event = self._Event
        self.phone.on(Event.CALL_BUTTON, self._on_call)
        self.phone.on(Event.END_CALL, self._on_hangup)
        self.phone.on(Event.CALL_ACCEPTED_REMOTE, self._on_call_ready)

        self._running = True
        self._hid_thread = threading.Thread(target=self._hid_loop, daemon=True)
        self._hid_thread.start()

        self._input_loop()

    def _hid_loop(self):
        Status = self._Status
        cycle = 1
        while self._running:
            try:
                self.phone.poll()
            except Exception as e:
                log.error("HID poll error: %s", e)
            if cycle == 1:
                try:
                    self.phone.send_init(Status.ONLINE)
                except Exception as e:
                    log.error("HID init error: %s", e)
            cycle = 1 if cycle >= 8 else cycle + 1
            time.sleep(0.2)

    def _on_call(self):
        with self._lock:
            if self.in_call:
                return
            self.in_call = True
            self._handset_call_ready = False
            self.status_msg = f"{self.GREEN}In call -- handset active{self.RESET}"
            self._needs_redraw = True
        try:
            self._ensure_call_audio_ready()
        except Exception as e:
            with self._lock:
                self.status_msg = f"{self.RED}Audio error: {e}{self.RESET}"
                self._needs_redraw = True

    def _on_call_ready(self):
        with self._lock:
            self._handset_call_ready = True
            self.call_audio_ready = self.in_call and self.bridge.is_running
            self._needs_redraw = True

    def _on_hangup(self):
        with self._lock:
            self.in_call = False
            self._handset_call_ready = False
            self.call_audio_ready = False
            if self.is_recording:
                self.is_recording = False
                self.last_duration = time.time() - self.record_start
            self.is_playing = False
            self.status_msg = f"{self.YELLOW}Hung up -- press green button to reconnect{self.RESET}"
            self._needs_redraw = True
        try:
            self.bridge.stop()
        except Exception:
            pass

    def _draw(self):
        rec_info = ""
        if self.is_recording:
            elapsed = time.time() - self.record_start
            rec_info = f"  {self.RED}* REC {elapsed:.1f}s{self.RESET}"
        elif self.is_playing:
            rec_info = f"  {self.GREEN}> PLAYING{self.RESET}"

        size_kb = len(self.recorded) / 1024
        dur = self.last_duration if not self.is_recording else (time.time() - self.record_start)

        status_label_color = self.GREEN if self.call_audio_ready else self.YELLOW
        link_state = (
            f"{self.GREEN}Connected (tone off expected){self.RESET}"
            if self.call_audio_ready
            else f"{self.YELLOW}Not connected yet{self.RESET}"
        )

        lines = [
            self.CLEAR,
            f"{self.BOLD}+======================================+{self.RESET}",
            f"{self.BOLD}|       CIT200 Audio Recorder          |{self.RESET}",
            f"{self.BOLD}+======================================+{self.RESET}",
            "",
            f"  {status_label_color}Status:{self.RESET} {self.status_msg}{rec_info}",
            f"  Audio link: {link_state}",
            "",
            f"  Buffer: {size_kb:.1f} KB  |  Duration: {dur:.1f}s",
            "",
            f"  {self.BOLD}[R]{self.RESET} {'Stop recording' if self.is_recording else 'Record':<16}"
            f"{self.BOLD}[P]{self.RESET} {'Stop' if self.is_playing else 'Play back'}",
            f"  {self.BOLD}[S]{self.RESET} Save .wav       {self.BOLD}[L]{self.RESET} Load .wav",
            f"  {self.BOLD}[I]{self.RESET} Info            {self.BOLD}[Q]{self.RESET} Quit",
            "",
            f"  {self.DIM}Press green call button on handset first{self.RESET}",
        ]

        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()

    def _input_loop(self):
        last_draw = 0.0
        try:
            while self._running:
                if self._key_available():
                    key = self._read_key(blocking=False).lower()
                    if not key:
                        time.sleep(0.01)
                        continue
                    if key == b"r":
                        self._toggle_recording()
                    elif key == b"p":
                        self._toggle_playback()
                    elif key == b"s":
                        self._save_wav()
                    elif key == b"l":
                        self._load_wav()
                    elif key == b"i":
                        self._show_info()
                    elif key == b"q":
                        break
                    self._needs_redraw = True

                now = time.time()
                should_draw = self._needs_redraw or (self.is_recording and now - last_draw >= 0.25)
                if should_draw:
                    self._needs_redraw = False
                    last_draw = now
                    self._draw()

                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _toggle_recording(self):
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if not self.in_call:
            self.status_msg = f"{self.YELLOW}Press green call button first{self.RESET}"
            return
        if self.is_playing:
            self._stop_playback()
        try:
            self._ensure_call_audio_ready()
        except Exception as e:
            self.status_msg = f"{self.RED}Audio error: {e}{self.RESET}"
            return
        self.recorded.clear()
        self.record_start = time.time()
        self.is_recording = True
        self.status_msg = f"{self.RED}Recording... press [R] to stop{self.RESET}"

    def _stop_recording(self):
        self.is_recording = False
        self.last_duration = time.time() - self.record_start
        size_kb = len(self.recorded) / 1024
        self.status_msg = f"{self.GREEN}Recorded {self.last_duration:.1f}s ({size_kb:.1f} KB){self.RESET}"

    def _toggle_playback(self):
        if self.is_playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        if not self.in_call:
            self.status_msg = f"{self.YELLOW}Press green call button first{self.RESET}"
            return
        if not self.recorded:
            self.status_msg = f"{self.YELLOW}Nothing to play -- record first{self.RESET}"
            return
        try:
            self._ensure_call_audio_ready()
        except Exception as e:
            self.status_msg = f"{self.RED}Audio error: {e}{self.RESET}"
            return
        if self.is_recording:
            self._stop_recording()

        self.is_playing = True
        self.status_msg = f"{self.GREEN}> Playing back...{self.RESET}"

        data = bytes(self.recorded)
        duration = len(data) / (16000 * 1 * 2)
        self.bridge.flush_playback()
        self.bridge.play_audio(data)

        def wait_for_done():
            end_time = time.time() + duration + 0.5
            while self.is_playing and time.time() < end_time:
                time.sleep(0.1)
            if self.is_playing:
                with self._lock:
                    self.is_playing = False
                    self.status_msg = f"{self.GREEN}Playback finished{self.RESET}"
                    self._needs_redraw = True

        threading.Thread(target=wait_for_done, daemon=True).start()

    def _stop_playback(self):
        self.is_playing = False
        self.bridge.flush_playback()
        self.status_msg = f"{self.GREEN}Playback stopped{self.RESET}"

    def _save_wav(self):
        if not self.recorded:
            self.status_msg = f"{self.YELLOW}Nothing to save{self.RESET}"
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"cit200_rec_{ts}.wav"
        try:
            with wave.open(filename, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(bytes(self.recorded))
            size_kb = len(self.recorded) / 1024
            self.status_msg = f"{self.GREEN}Saved: {filename} ({size_kb:.1f} KB){self.RESET}"
        except Exception as e:
            self.status_msg = f"{self.RED}Save failed: {e}{self.RESET}"

    def _load_wav(self):
        wavs = sorted([f for f in os.listdir(".") if f.lower().endswith(".wav")])
        if not wavs:
            self.status_msg = f"{self.YELLOW}No .wav files in current directory{self.RESET}"
            return

        sys.stdout.write(self.CLEAR)
        print(f"{self.BOLD}Load a .wav file:{self.RESET}\n")
        for i, f in enumerate(wavs[:9]):
            try:
                size = os.path.getsize(f) / 1024
            except OSError:
                size = 0
            print(f"  {self.BOLD}[{i+1}]{self.RESET} {f}  ({size:.1f} KB)")
        print(f"\n  {self.DIM}Press 1-{min(len(wavs), 9)} to load, any other key to cancel{self.RESET}")
        sys.stdout.flush()

        key = self._read_key(blocking=True)
        try:
            idx = int(key.decode()) - 1
            if 0 <= idx < len(wavs):
                filename = wavs[idx]
                with wave.open(filename, "rb") as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate()
                    self.recorded = bytearray(wf.readframes(frames))
                    self.last_duration = frames / rate
                self.status_msg = f"{self.GREEN}Loaded: {filename}{self.RESET}"
            else:
                self.status_msg = "Cancelled"
        except (ValueError, UnicodeDecodeError, wave.Error):
            self.status_msg = "Cancelled"
        self._needs_redraw = True

    def _show_info(self):
        from .audio_bridge import AudioBridge as _AB
        sys.stdout.write(self.CLEAR)
        print(f"{self.BOLD}Audio Info:{self.RESET}\n")
        print("  Sample rate:  16000 Hz")
        print("  Channels:     1 (mono)")
        print("  Bit depth:    16-bit (int16)")
        print(f"  Buffer size:  {len(self.recorded)} bytes ({len(self.recorded)/1024:.1f} KB)")
        print(f"  Duration:     {self.last_duration:.2f}s")
        print(f"  In call:      {'Yes' if self.in_call else 'No'}")
        print(f"  Audio link:   {'Connected (tone off expected)' if self.call_audio_ready else 'Not connected yet'}")
        print(f"  Phone:        {'Connected' if self.phone.is_open else 'Disconnected'}")
        print(f"  HID mode:     {self.hid_write_mode}")
        print(f"  Call delays:  {self.call_delay_a:.3f}s / {self.call_delay_b:.3f}s")
        print(f"  HID trace:    {self.hid_trace_path if self.hid_trace_path else 'disabled'}")

        try:
            devs = _AB.find_cit200_devices()
            print(f"  Input dev:    {devs['input']}")
            print(f"  Output dev:   {devs['output']}")
        except Exception:
            print("  Devices:      (query failed)")

        print(f"\n  {self.DIM}Press any key to go back{self.RESET}")
        sys.stdout.flush()
        self._read_key(blocking=True)
        self._needs_redraw = True

    def _shutdown(self):
        self._running = False
        self.is_recording = False
        self.is_playing = False
        self._console_restore()
        try:
            self.bridge.stop()
        except Exception:
            pass
        try:
            self.phone.close()
        except Exception:
            pass
        sys.stdout.write(self.CLEAR)
        print("Goodbye!")
        sys.stdout.flush()


# ── PhoneApp ──────────────────────────────────────────────────────────────────

class PhoneApp:
    """
    Production runtime bridge: CIT200 handset <-> VoicePlatform <-> AudioBridge.

    All handset events arrive on the poll thread.
    All platform events arrive via the async event loop thread.
    UI updates are always marshalled through a UIBridge so tkinter
    is never touched from a background thread.
    """

    POLL_INTERVAL_S          = 0.02   # 50 Hz poll loop
    CONTACT_REFRESH_INTERVAL = 30.0   # background contact refresh (seconds)

    def __init__(self, cfg: Dict[str, Any], ui=None, ui_bridge=None):
        self._cfg     = cfg
        self._ui      = ui          # SkypeUI | None
        self._bridge  = ui_bridge   # UIBridge | None
        self._running = False

        self._platform = _build_platform(cfg)

        # Async event loop for the platform (created in run())
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_thread: Optional[threading.Thread] = None

        # CIT200 handset driver (production version with Windows kernel32, IO lock, etc.)
        from .cit200 import CIT200Device, Event, Contact, Status
        hid_cfg = cfg.get("hid", {})
        self._phone = CIT200Device(
            transport_mode=str(hid_cfg.get("transport_mode", "dual")),
            call_setup_delay=float(hid_cfg.get("q9_q10_delay", 0.2)),
            call_connect_delay=float(hid_cfg.get("call_connect_delay", 0.2)),
            contacts_frame_delay_s=float(hid_cfg.get("contacts_frame_delay_s", 0.05)),
            contacts_contact_delay_s=float(hid_cfg.get("contacts_contact_delay_s", 0.05)),
            contacts_transport_mode=str(hid_cfg.get("contacts_transport_mode", "feature_only")),
        )
        self._Event   = Event
        self._Contact = Contact
        self._Status  = Status

        # Audio bridge (production version with RMS metering, buffer overflow protection)
        from .audio_bridge import AudioBridge
        audio_cfg = cfg.get("audio", {})
        # meter_level is stored as string ("debug") in config — convert to logging int
        _ml_raw = audio_cfg.get("meter_level", "debug")
        if isinstance(_ml_raw, str):
            _meter_level = getattr(logging, _ml_raw.upper(), logging.DEBUG)
        else:
            _meter_level = int(_ml_raw)
        self._audio = AudioBridge(
            sample_rate=int(audio_cfg.get("sample_rate", 16000)),
            channels=int(audio_cfg.get("channels", 1)),
            chunk_size=int(audio_cfg.get("chunk_size", 960)),
            meter_enabled=bool(audio_cfg.get("meter_enabled", False)),
            meter_interval_s=float(audio_cfg.get("meter_interval_s", 10.0)),
            meter_level=_meter_level,
        )

        # ── Call state (guarded by _call_lock) ───────────────────────
        self._current_call_id: Optional[str] = None
        self._current_call_display_name: Optional[str] = None  # friendly name for history
        self._incoming_caller: Optional[str] = None
        self._incoming_call_id: Optional[str] = None  # platform call_id for answer
        self._call_lock = threading.Lock()
        self._in_call_audio = False  # True while call audio should flow

        # ── Call recording ────────────────────────────────────────────
        rec_cfg = cfg.get("recording", {})
        self._recording_enabled = bool(rec_cfg.get("enabled", True))
        self._auto_record_calls = bool(rec_cfg.get("auto_record_calls", True))
        rec_dir_raw = str(rec_cfg.get("directory", "recordings") or "recordings")
        rec_dir = Path(rec_dir_raw)
        if not rec_dir.is_absolute():
            rec_dir = Path(__file__).parent.parent / rec_dir
        self._recording_dir = rec_dir

        self._record_lock = threading.Lock()
        self._recording_active = False
        self._record_wave: Optional[wave.Wave_write] = None
        self._record_path: Optional[Path] = None
        self._rec_mic_buf = bytearray()
        self._rec_remote_buf = bytearray()

        # ── Contacts infrastructure ────────────────────────────────────
        self._contacts_cache: list     = []
        self._contacts_cache_at: float = 0.0
        self._contacts_lock = threading.Lock()

        contacts_cfg = cfg.get("contacts", {})

        # Page size / max contacts
        try:
            max_contacts = int(contacts_cfg.get("max_contacts", 100))
        except (TypeError, ValueError):
            max_contacts = 100
        self._max_contacts = max(1, max_contacts)  # UI list limit
        self._contacts_page_size = max(1, min(33, max_contacts))  # handset page limit

        # Selected contacts filter
        raw_selected = contacts_cfg.get("selected_contacts", [])
        if isinstance(raw_selected, str):
            raw_selected = [raw_selected]
        elif not isinstance(raw_selected, (list, tuple, set)):
            raw_selected = []
        self._selected_contact_keys = {
            _normalize_contact_key(v)
            for v in raw_selected
            if _normalize_contact_key(v)
        }
        self._selected_only = bool(contacts_cfg.get("selected_only", False))
        self._selected_prioritize = bool(contacts_cfg.get("selected_prioritize", False))
        self._force_selected_only = bool(contacts_cfg.get("force_selected_only", False))
        # Backward compatibility: legacy selected_only affected UI + phone.
        # New behavior keeps UI unfiltered and applies subset only to handset.
        if self._selected_only and self._selected_contact_keys:
            self._force_selected_only = True
            self._selected_only = False
        self._selected_missing_warned = False

        # Cache / refresh tuning
        try:
            self._contacts_cache_ttl_s = max(0.0, float(contacts_cfg.get("cache_ttl_s", 15.0)))
        except (TypeError, ValueError):
            self._contacts_cache_ttl_s = 15.0
        try:
            self._contacts_min_interval_s = max(0.0, float(contacts_cfg.get("min_request_interval_s", 1.0)))
        except (TypeError, ValueError):
            self._contacts_min_interval_s = 1.0
        try:
            self._contacts_fetch_timeout_s = max(1.0, float(contacts_cfg.get("fetch_timeout_s", 8.0)))
        except (TypeError, ValueError):
            self._contacts_fetch_timeout_s = 8.0
        try:
            self._contacts_refresh_retries = max(1, int(contacts_cfg.get("refresh_retries", 2)))
        except (TypeError, ValueError):
            self._contacts_refresh_retries = 2
        try:
            self._contacts_refresh_backoff_s = max(0.0, float(contacts_cfg.get("refresh_backoff_s", 0.35)))
        except (TypeError, ValueError):
            self._contacts_refresh_backoff_s = 0.35

        self._contacts_allow_stale_fallback = bool(contacts_cfg.get("allow_stale_fallback", True))
        self._contacts_prefetch_on_connect = bool(contacts_cfg.get("prefetch_on_connect", True))
        try:
            self._contacts_background_refresh_s = max(0.0, float(contacts_cfg.get("background_refresh_s", 30.0)))
        except (TypeError, ValueError):
            self._contacts_background_refresh_s = 30.0

        # Diagnostics
        self._contacts_diagnostics = bool(contacts_cfg.get("diagnostics", False))
        try:
            self._contacts_diag_sample = max(1, min(12, int(contacts_cfg.get("diagnostics_sample", 5))))
        except (TypeError, ValueError):
            self._contacts_diag_sample = 5
        self._contacts_diag_counters = {
            "requests": 0, "sent": 0, "empty": 0,
            "stale_resend": 0, "refresh_ok": 0, "refresh_fail": 0,
        }

        # Contacts state tracking
        self._visible_contacts: list = []
        self._contacts_refresh_fail_at: float = 0.0
        self._last_contacts_request_at: float = 0.0
        self._last_contacts_request_index: int = -1
        self._contacts_fetch_inflight: bool = False
        self._contacts_refresh_lock: Optional[asyncio.Lock] = None
        self._contacts_request_task = None
        self._contacts_background_task = None
        self._contacts_pending_index: Optional[int] = None
        self._contacts_pending_at: float = 0.0
        self._last_ordered_contacts: list = []
        self._last_details_snapshot: list = []
        self._last_sent_contacts: list = []
        self._last_sent_total: int = 0

        # ── Pre-built phone wire cache (proactive pre-caching) ────────
        # Always-warm wire-format contacts list so the handset gets an
        # instant response when it sends a contacts_request event.
        self._prebuilt_phone_wire: list = []          # list of Contact objects
        self._prebuilt_phone_wire_total: int = 0
        self._prebuilt_phone_wire_at: float = 0.0     # monotonic timestamp
        self._prebuilt_phone_wire_lock = threading.Lock()

        # Compat / DLL-exact mode
        try:
            self._contacts_compat_page_size = max(1, min(33, int(contacts_cfg.get("compat_page_size", 33))))
        except (TypeError, ValueError):
            self._contacts_compat_page_size = 33
        self._contacts_compat_resend = bool(contacts_cfg.get("compat_resend", False))
        self._contacts_emergency_output_ack = bool(contacts_cfg.get("emergency_output_ack", os.name == "nt"))
        platform_name = cfg.get("platform", "local")
        self._dll_exact_contacts_mode = platform_name in {"telegram", "telegram_private"}

        # Contact detail overrides
        raw_detail_overrides = contacts_cfg.get("detail_overrides", [])
        self._contact_detail_overrides = raw_detail_overrides if isinstance(raw_detail_overrides, list) else []

        # Contact bio cache
        self._contact_bio_cache: Dict[str, str] = {}

        # ── Phone ID shim (privacy masking for numeric Telegram IDs) ──
        self._phone_id_shim = bool(contacts_cfg.get("phone_id_shim", True))
        self._phone_id_shim_prefix = str(
            contacts_cfg.get("phone_id_shim_prefix", "6") or "6"
        )
        self._phone_id_shim_value = str(
            contacts_cfg.get("phone_id_shim_value",
                             "6 345 432 236 543 457 4 222")
            or "6 345 432 236 543 457 4 222"
        )
        self._phone_id_shim_salt = str(
            contacts_cfg.get("phone_id_shim_salt", "") or ""
        )
        # Per-contact random shim cache: raw_handle -> random phone string
        self._phone_shim_random: Dict[str, str] = {}
        # Reverse lookup: normalized(shim) -> [real_handle, ...]
        self._phone_shim_lookup: Dict[str, List[str]] = {}
        # Incoming-call callback resolution state
        self._last_incoming_dial_target: str = ""
        self._last_incoming_shim_keys: set = set()
        self._last_phone_focus_target: str = ""

        # ── Missed calls list ─────────────────────────────────────────
        self._missed_calls: List[str] = []
        self._missed_lock = threading.Lock()

        # ── Call history (previous calls log) ─────────────────────────
        # Each entry: {name: str, type: 'outgoing'|'incoming'|'missed',
        #              timestamp: float (time.time()), duration_secs: int}
        self._call_history: List[Dict[str, Any]] = []
        self._call_history_lock = threading.Lock()
        self._call_start_time: float = 0.0    # time.time() when call started
        self._call_was_incoming: bool = False  # True if current call originated as incoming

        self._wire_phone_events()
        self._wire_platform_events()
        self._wire_audio()

    # ── Wiring ────────────────────────────────────────────────────────

    def _wire_phone_events(self) -> None:
        E = self._Event
        self._phone.on(E.CALL_BUTTON,      self._on_phone_call)
        self._phone.on(E.END_CALL,         self._on_phone_hangup)
        self._phone.on(E.DIAL,             self._on_phone_dial)
        self._phone.on(E.CONTACTS_REQUEST, self._on_phone_contacts)
        self._phone.on(E.CONTACT_DETAILS,  self._on_phone_contact_details)
        self._phone.on(E.ANSWER_INCOMING,  self._on_phone_answer)
        self._phone.on(E.REJECT_INCOMING,  self._on_phone_reject)
        self._phone.on(E.HOLD_RESUME,      self._on_phone_hold)
        self._phone.on(E.STATUS_CHANGE,    self._on_phone_status_change)

    def _wire_platform_events(self) -> None:
        self._platform.on_incoming_call(self._on_platform_incoming)
        self._platform.on_call_answered(self._on_platform_answered)
        self._platform.on_call_ended(self._on_platform_ended)
        self._platform.on_audio_received(self._on_platform_audio)

    def _wire_audio(self) -> None:
        self._audio.on_audio_captured = self._on_mic_audio

    # ── Thread-safe UI dispatch ───────────────────────────────────────

    def _ui_call(self, fn: Callable, *args: Any, **kw: Any) -> None:
        """Schedule fn on the tkinter main thread. Safe from any thread."""
        if self._bridge:
            self._bridge.call(fn, *args, **kw)
        elif self._ui:
            try:
                fn(*args, **kw)
            except Exception:
                log.exception("_ui_call direct error")

    # ── Async helper — run a coroutine on the platform's event loop ───

    def _run_async(self, coro):
        """Submit a coroutine to the platform's async loop. Fire-and-forget."""
        if self._async_loop and self._async_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._async_loop)
        else:
            log.debug("_run_async: async loop not available, skipping")

    def _run_async_wait(self, coro, timeout=10.0):
        """Submit a coroutine and wait for its result (with timeout)."""
        if self._async_loop and self._async_loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, self._async_loop)
            try:
                return fut.result(timeout=timeout)
            except Exception as e:
                log.debug("_run_async_wait error: %s", e)
                return None
        return None

    def _spawn_task(self, coro, label: str):
        """Create an async task on the platform loop and surface exceptions."""
        if not self._async_loop or not self._async_loop.is_running():
            log.debug("_spawn_task(%s): async loop not available", label)
            return None

        def _create():
            task = self._async_loop.create_task(coro)

            def _done(t: asyncio.Task):
                try:
                    t.result()
                except asyncio.CancelledError:
                    return
                except Exception:
                    log.exception("Async task failed: %s", label)

            task.add_done_callback(_done)
            return task

        # If called from the async loop thread, create directly;
        # otherwise schedule on the loop.
        if threading.current_thread() is self._async_thread:
            return _create()
        else:
            fut = asyncio.run_coroutine_threadsafe(
                self._spawn_task_async(coro, label), self._async_loop
            )
            try:
                return fut.result(timeout=5.0)
            except Exception:
                return None

    async def _spawn_task_async(self, coro, label: str):
        """Helper: create a task from within the async loop."""
        task = asyncio.ensure_future(coro)

        def _done(t: asyncio.Task):
            try:
                t.result()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Async task failed: %s", label)

        task.add_done_callback(_done)
        return task

    # ── Async event loop thread ───────────────────────────────────────

    def _start_async_loop(self) -> None:
        """Start the asyncio event loop in a daemon thread for async platform ops."""
        self._async_loop = asyncio.new_event_loop()

        def _run():
            asyncio.set_event_loop(self._async_loop)
            self._async_loop.run_until_complete(self._async_bootstrap())
            # Keep running until stopped
            self._async_loop.run_forever()

        self._async_thread = threading.Thread(
            target=_run, daemon=True, name="async-loop"
        )
        self._async_thread.start()

    async def _async_bootstrap(self) -> None:
        """Connect the platform on the async loop."""
        try:
            await self._platform.connect()
            log.info("Platform connected on async loop")
            # Prefetch contacts cache on connect (for fast first handset request)
            if self._dll_exact_contacts_mode and self._contacts_prefetch_on_connect:
                await self._refresh_contacts_cache()
            # Start background contacts refresher
            if self._contacts_background_refresh_s > 0.0:
                self._contacts_background_task = asyncio.ensure_future(
                    self._contacts_background_refresher()
                )
        except Exception as e:
            log.error("Platform async connect failed: %s", e, exc_info=True)

    def _stop_async_loop(self) -> None:
        """Shut down the async loop cleanly."""
        if self._async_loop and self._async_loop.is_running():
            async def _shutdown():
                try:
                    await self._platform.disconnect()
                except Exception:
                    pass
                self._async_loop.stop()
            asyncio.run_coroutine_threadsafe(_shutdown(), self._async_loop)

    # ── Run loop (runs in daemon thread) ──────────────────────────────

    def run(self) -> None:
        self._running = True
        log.debug("PhoneApp.run() – opening phone device")
        self._phone.open()
        log.debug("PhoneApp.run() – starting audio bridge")
        self._audio.start()
        log.debug("PhoneApp.run() – starting async platform loop")
        self._start_async_loop()

        log.info("PhoneApp running (platform=%s)", self._cfg.get("platform", "local"))

        last_keepalive = 0.0
        keepalive_every = float(self._cfg.get("hid", {}).get("keepalive_interval", 1.6))
        last_refresh = 0.0
        last_wire_rebuild = 0.0
        cycle = 0

        try:
            while self._running:
                self._phone.poll()

                # Periodic keepalive (time sync) to handset
                now = time.monotonic()
                if now - last_keepalive >= keepalive_every:
                    last_keepalive = now
                    self._phone.send_time_sync()

                # Periodic contact refresh (UI + phone wire cache)
                if now - last_refresh > self.CONTACT_REFRESH_INTERVAL:
                    last_refresh = now
                    self._push_contacts_to_ui()
                    # Rebuild the pre-built wire cache in background so the
                    # handset always gets instant responses.
                    last_wire_rebuild = now
                    threading.Thread(
                        target=self._rebuild_phone_wire_cache,
                        daemon=True,
                        name="wire-cache-periodic",
                    ).start()

                time.sleep(self.POLL_INTERVAL_S)
        except KeyboardInterrupt:
            log.info("PhoneApp interrupted")
        finally:
            self.stop()

    def request_stop(self) -> None:
        """Signal the app to stop gracefully (callable from any thread)."""
        self._running = False

    def stop(self) -> None:
        self._running = False
        self._stop_call_audio()
        # Cancel background contacts refresher
        if self._contacts_background_task and not self._contacts_background_task.done():
            self._contacts_background_task.cancel()
        self._contacts_background_task = None
        try:
            self._audio.stop()
        except Exception:
            pass
        self._stop_async_loop()
        self._phone.close()
        log.info("PhoneApp stopped")

    # ── Per-call audio lifecycle ─────────────────────────────────────

    def _start_call_audio(self) -> None:
        """Enable audio flow for a call. Idempotent."""
        self._in_call_audio = True
        log.debug("Call audio: ENABLED")
        if self._auto_record_calls:
            self.start_call_recording(reason="auto")

    def _stop_call_audio(self) -> None:
        """Disable audio flow after call ends. Idempotent."""
        self._in_call_audio = False
        self.stop_call_recording()
        log.debug("Call audio: DISABLED")

    # ── Audio routing ─────────────────────────────────────────────────

    def _on_mic_audio(self, pcm: bytes) -> None:
        """CIT200 mic → platform (async send_audio). Only during call."""
        if not self._in_call_audio:
            return
        self._run_async(self._platform.send_audio(pcm))
        self._record_mic_audio(pcm)

    def _on_platform_audio(self, pcm: bytes) -> None:
        """Platform RX → CIT200 speaker. Only during call."""
        if not self._in_call_audio:
            return
        self._audio.play_audio(pcm)
        self._record_remote_audio(pcm)

    def _notify_ui_recording(self, active: bool, saved_path: str = "") -> None:
        """Push recording state to UI thread if available."""
        if not self._ui:
            return

        def _cb():
            ui = self._ui
            if ui and hasattr(ui, "set_call_recording"):
                try:
                    ui.set_call_recording(bool(active), saved_path=str(saved_path or ""))
                except Exception:
                    log.exception("Failed to update UI recording indicator")

        self._ui_call(_cb)

    # ── Call recording ────────────────────────────────────────────────

    @property
    def is_recording_call(self) -> bool:
        with self._record_lock:
            return bool(self._recording_active)

    def start_call_recording(self, reason: str = "manual") -> Optional[str]:
        """Start recording the current call. Returns WAV path or None."""
        if not self._recording_enabled:
            log.debug("Call recording is disabled in config")
            return None

        with self._record_lock:
            if self._recording_active:
                return str(self._record_path) if self._record_path else None

            try:
                self._recording_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log.error("Failed to create recording directory %s: %s",
                          self._recording_dir, e)
                return None

            ts = time.strftime("%Y%m%d_%H%M%S")
            path = self._recording_dir / f"call_{ts}.wav"
            try:
                wf = wave.open(str(path), "wb")
                wf.setnchannels(2)  # stereo: left=mic, right=remote
                wf.setsampwidth(2)  # 16-bit PCM
                wf.setframerate(16000)
            except Exception as e:
                log.error("Failed to open recording file %s: %s", path, e)
                return None

            self._record_wave = wf
            self._record_path = path
            self._rec_mic_buf.clear()
            self._rec_remote_buf.clear()
            self._recording_active = True

        log.info("Call recording started (%s): %s", reason, path)
        self._notify_ui_recording(True)
        return str(path)

    def stop_call_recording(self) -> Optional[str]:
        """Stop recording and close the WAV file. Returns path or None."""
        with self._record_lock:
            if not self._recording_active:
                return None

            path = self._record_path
            wf = self._record_wave

            if wf:
                try:
                    self._flush_recording_locked(final=True)
                    wf.close()
                except Exception as e:
                    log.warning("Error closing recording file: %s", e)

            self._recording_active = False
            self._record_wave = None
            self._record_path = None
            self._rec_mic_buf.clear()
            self._rec_remote_buf.clear()

        if path:
            log.info("Call recording saved: %s", path)
            self._notify_ui_recording(False, str(path))
            return str(path)
        self._notify_ui_recording(False)
        return None

    def _record_mic_audio(self, pcm_data: bytes) -> None:
        if not pcm_data:
            return
        with self._record_lock:
            if not self._recording_active:
                return
            self._rec_mic_buf.extend(pcm_data)
            self._flush_recording_locked(final=False)

    def _record_remote_audio(self, pcm_data: bytes) -> None:
        if not pcm_data:
            return
        with self._record_lock:
            if not self._recording_active:
                return
            self._rec_remote_buf.extend(pcm_data)
            self._flush_recording_locked(final=False)

    def _flush_recording_locked(self, final: bool = False) -> None:
        """Interleave mic+remote into stereo WAV. Called under _record_lock."""
        wf = self._record_wave
        if wf is None:
            return

        frame_bytes = 2  # int16 mono sample size
        min_frames = min(len(self._rec_mic_buf),
                         len(self._rec_remote_buf)) // frame_bytes

        if final:
            # Pad shorter buffer with silence
            if len(self._rec_mic_buf) > len(self._rec_remote_buf):
                self._rec_remote_buf.extend(
                    b"\x00" * (len(self._rec_mic_buf) - len(self._rec_remote_buf)))
            elif len(self._rec_remote_buf) > len(self._rec_mic_buf):
                self._rec_mic_buf.extend(
                    b"\x00" * (len(self._rec_remote_buf) - len(self._rec_mic_buf)))
            min_frames = min(len(self._rec_mic_buf),
                             len(self._rec_remote_buf)) // frame_bytes

        if min_frames <= 0:
            return

        size = min_frames * frame_bytes
        mic_chunk = bytes(self._rec_mic_buf[:size])
        rem_chunk = bytes(self._rec_remote_buf[:size])
        del self._rec_mic_buf[:size]
        del self._rec_remote_buf[:size]

        mic_arr = array("h")
        rem_arr = array("h")
        mic_arr.frombytes(mic_chunk)
        rem_arr.frombytes(rem_chunk)

        interleaved = array("h")
        for i in range(min_frames):
            interleaved.append(mic_arr[i])
            interleaved.append(rem_arr[i])

        wf.writeframes(interleaved.tobytes())

    # ── Phone ID shim (dialer obfuscation) ──────────────────────────

    def _make_phone_id_shim(self, value: str) -> str:
        """Replace a purely-numeric handle with a random fake phone number.

        The generated number is deterministic per handle (seeded from a hash)
        and cached so the same contact always shows the same number.
        """
        raw = str(value or "").strip()
        if not raw.isdigit():
            return raw  # non-numeric handles pass through unchanged

        # Return cached value if already generated for this handle
        cached = self._phone_shim_random.get(raw)
        if cached:
            return cached

        prefix = "".join(
            ch for ch in self._phone_id_shim_prefix if ch.isdigit()
        )[:1] or "6"

        # Generate deterministic random digits from a salted hash of the handle
        h = hashlib.sha256((self._phone_id_shim_salt + raw).encode()).hexdigest()
        digits = "".join(str(int(ch, 16) % 10) for ch in h[:9])
        # Format as "P DDD DDD DDD"
        shim = f"{prefix} {digits[:3]} {digits[3:6]} {digits[6:9]}"
        self._phone_shim_random[raw] = shim
        return shim

    def _phone_display_name(self, c, shim_handle: str) -> str:
        """Display name for the handset — prefer real name, fall back to shim."""
        name = str(getattr(c, 'name', '') or "").strip()
        if name:
            return name
        return shim_handle or str(getattr(c, 'handle', '') or getattr(c, 'id', '') or "")

    def _phone_display_handle(self, c) -> str:
        """Display handle for the handset — numeric IDs replaced with shim."""
        real = str(getattr(c, 'handle', '') or getattr(c, 'id', '') or "").strip()
        if not self._phone_id_shim:
            return real
        shim = self._make_phone_id_shim(real)
        return shim or real

    def _phone_incoming_caller_label(self, caller_name: str) -> str:
        """Scrub numeric Telegram IDs from incoming caller ID strings."""
        text = str(caller_name or "").strip()
        if not text:
            return text
        if not self._phone_id_shim:
            return text

        # Fully numeric label → replace entirely
        if text.isdigit():
            return self._make_phone_id_shim(text)

        # Remove parenthesized numeric IDs like "(123456789)"
        text = re.sub(r"\(\s*\d{4,}\s*\)", "", text)
        # Replace remaining long numeric tokens with per-ID shim
        text = re.sub(r"\b\d{4,}\b", lambda m: self._make_phone_id_shim(m.group()), text)
        # Compact whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _rebuild_phone_shim_lookup(self, ordered: list) -> None:
        """Build reverse lookup: normalized(shim) -> [real_handle, ...]."""
        self._phone_shim_lookup = {}
        if not self._phone_id_shim:
            return
        for c in ordered:
            real = str(getattr(c, 'handle', '') or getattr(c, 'id', '') or "").strip()
            if not real:
                continue
            shim = self._make_phone_id_shim(real)
            if not shim or shim == real:
                continue
            k = _normalize_contact_key(shim)
            if not k:
                continue
            bucket = self._phone_shim_lookup.setdefault(k, [])
            if real not in bucket:
                bucket.append(real)

    def _refresh_last_incoming_shim_keys(self, phone_label: str) -> None:
        """Populate shim keys for the most recent incoming caller,
        so missed-call callbacks from the handset can be resolved."""
        keys: set = set()

        _incoming_shim = self._make_phone_id_shim(
            str(self._last_incoming_dial_target or "")
        ) if self._last_incoming_dial_target else ""
        for candidate in (phone_label, _incoming_shim):
            k = _normalize_contact_key(candidate)
            if k:
                keys.add(k)

        # Include numeric fragments the handset might emit for callback
        for num in re.findall(r"\d{2,}", phone_label or ""):
            keys.add(_normalize_contact_key(num))

        tgt = str(self._last_incoming_dial_target or "")
        if tgt.isdigit():
            keys.add(_normalize_contact_key(tgt[-4:]))
            keys.add(_normalize_contact_key(tgt[-6:]))

        self._last_incoming_shim_keys = {k for k in keys if k}

    # ── Outgoing call from UI (green button, no incoming pending) ────────

    def place_call_from_ui(self, contact_name: str) -> None:
        """Called on tkinter thread. Dispatches actual call to worker thread."""
        def _worker():
            target = self._resolve_dial_target(contact_name)
            log.info("[ui->backend] place_call %s -> %s", contact_name, target)
            with self._call_lock:
                self._current_call_id = target
                self._current_call_display_name = contact_name
            self._call_was_incoming = False
            self._record_call_start()
            self._start_call_audio()
            self._phone.start_local_audio_call()
            self._run_async(self._platform.place_call(target))
        threading.Thread(target=_worker, daemon=True, name="place-call").start()

    # ── Answer incoming call from UI (green button when ringing) ─────────

    def answer_call_from_ui(self, contact_name: str) -> None:
        """Called on tkinter thread when user clicks Answer on an incoming call."""
        def _worker():
            log.info("[ui->backend] answer_call from %s", contact_name)
            with self._call_lock:
                call_id = self._incoming_call_id or ""
            self._start_call_audio()
            self._run_async(self._platform.answer_call(call_id))
            self._phone.confirm_call_connected()
            with self._call_lock:
                self._current_call_id = contact_name
                self._incoming_caller = None
                self._incoming_call_id = None
        threading.Thread(target=_worker, daemon=True, name="answer-call").start()

    # ── Hangup from UI (red button) ───────────────────────────────────

    def hangup_from_ui(self) -> None:
        """Called on tkinter thread. Dispatches teardown to worker thread."""
        def _worker():
            log.info("[ui->backend] hangup")
            self._stop_call_audio()
            self._run_async(self._platform.end_call())
            self._phone.end_call_from_remote()
            with self._call_lock:
                display_name = self._current_call_display_name or self._current_call_id
                self._current_call_id = None
                self._current_call_display_name = None
                self._incoming_caller = None
                self._incoming_call_id = None
            if display_name:
                ctype = 'incoming' if self._call_was_incoming else 'outgoing'
                self._call_was_incoming = False
                self._record_call_history(display_name, ctype)
        threading.Thread(target=_worker, daemon=True, name="hangup").start()

    # ── Handset event handlers (poll thread) ──────────────────────────

    def _on_phone_call(self) -> None:
        with self._call_lock:
            incoming = self._incoming_caller
            call_id = self._incoming_call_id
        if incoming:
            log.info("Handset: answer incoming from %s", incoming)
            self._start_call_audio()
            self._run_async(self._platform.answer_call(call_id or ""))
            self._phone.confirm_call_connected()
            with self._call_lock:
                self._current_call_id = incoming
                self._incoming_caller = None
                self._incoming_call_id = None
            self._ui_call(lambda: self._ui and self._ui.answer_call())
        else:
            log.debug("Handset: call button – no pending incoming")

    def _on_phone_hangup(self) -> None:
        log.info("Handset: hangup")
        self._stop_call_audio()
        self._run_async(self._platform.end_call())
        # Note: handset already initiated hangup internally, but sending
        # end_call_from_remote() is harmless (handset ignores in idle state)
        self._phone.end_call_from_remote()
        with self._call_lock:
            display_name = self._current_call_display_name or self._current_call_id
            self._current_call_id = None
            self._current_call_display_name = None
            self._incoming_caller = None
            self._incoming_call_id = None
        if display_name:
            ctype = 'incoming' if self._call_was_incoming else 'outgoing'
            self._call_was_incoming = False
            self._record_call_history(display_name, ctype)
        self._ui_call(lambda: self._ui and self._ui.end_call())

    def _on_phone_dial(self, callee: str) -> None:
        target = self._resolve_dial_target(callee)
        log.info("Handset: dial %r -> %s", callee, target)
        with self._call_lock:
            self._current_call_id = target
            self._current_call_display_name = self._resolve_display_name(target)
        self._call_was_incoming = False
        self._record_call_start()
        self._start_call_audio()
        self._run_async(self._platform.place_call(target))
        self._ui_call(lambda: self._ui and self._ui.start_call(target))

    def _on_phone_contacts(self, index: int) -> None:
        """Contacts menu opened on handset — serves from pre-built cache instantly.

        If the proactive wire cache is warm, the handset gets an immediate
        response with zero async latency.  A background refresh is still
        kicked off to keep the cache fresh for the next request.
        """
        req_index = max(0, int(index))
        now = time.monotonic()
        self._contacts_diag_counters["requests"] += 1

        # Deduplicate rapid duplicate requests
        if (
            req_index == self._last_contacts_request_index
            and (now - self._last_contacts_request_at) < self._contacts_min_interval_s
        ):
            delta = now - self._last_contacts_request_at
            log.debug(
                "Phone: throttled duplicate contacts request (index=%d since_last=%.3fs min=%.2fs)",
                req_index, delta, self._contacts_min_interval_s,
            )
            return

        self._last_contacts_request_index = req_index
        self._last_contacts_request_at = now
        log.info("Phone: contacts request (index %d)", req_index)
        self._contacts_pending_index = req_index
        self._contacts_pending_at = now

        # ── Proactive pre-cache: instant response ─────────────────────
        with self._prebuilt_phone_wire_lock:
            wire_ready = bool(self._prebuilt_phone_wire)
        if wire_ready:
            sent = self._send_prebuilt_phone_wire()
            if sent:
                log.debug("Phone: served contacts request from pre-built cache")
                # Still kick a background rebuild to keep data fresh
                threading.Thread(
                    target=self._rebuild_phone_wire_cache,
                    daemon=True,
                    name="wire-cache-refresh",
                ).start()
                return

        # ── Fallback: cache cold, use async pipeline ──────────────────
        log.debug("Phone: pre-built cache cold, falling back to async fetch")
        if self._dll_exact_contacts_mode:
            # Legacy-like timing: consume request via periodic tick.
            # Schedule a one-shot legacy tick on the async loop.
            self._run_async(self._service_contacts_legacy_tick())
            return

        # Non-DLL mode: spawn async worker
        self._spawn_task(
            self._fetch_and_send_contacts(req_index),
            f"contacts_fetch:{req_index}",
        )

    def _on_phone_contact_details(self, index: int, detail_type) -> None:
        """Contact detail view opened — rich version with override support."""
        log.info("Phone: contact details (index %d, page=%s)", index, detail_type)
        pc = None

        # In DLL-exact mode, resolve index from last details snapshot
        if self._dll_exact_contacts_mode and 0 <= index < len(self._last_details_snapshot):
            pc = self._last_details_snapshot[index]
            log.debug("Phone: details index resolved from last details snapshot (index=%d)", index)
        elif 0 <= index < len(self._visible_contacts):
            pc = self._visible_contacts[index]
        elif 0 <= index < len(self._last_ordered_contacts):
            pc = self._last_ordered_contacts[index]
            log.debug("Phone: details index resolved from ordered cache (index=%d)", index)
        else:
            # Fallback: try the old synchronous path
            contacts = self._get_ordered_contacts_sync()
            if contacts and 0 <= index < len(contacts):
                pc = contacts[index]

        if not pc:
            log.warning(
                "Phone: details request out of range (index=%d visible=%d total=%d)",
                index, len(self._visible_contacts), len(self._last_ordered_contacts),
            )
            return

        # Track which contact the user is browsing (for shim disambiguation)
        real_handle = str(getattr(pc, 'handle', '') or getattr(pc, 'id', '') or "")
        if real_handle:
            self._last_phone_focus_target = real_handle

        phone_handle = self._phone_display_handle(pc)
        phone_name = self._phone_display_name(pc, phone_handle)
        override = self._match_contact_detail_override(pc)

        mode = 0
        if str(detail_type) != "details":
            try:
                mode = max(0, int(detail_type))
            except Exception:
                mode = 0

        Contact = self._Contact

        if mode <= 0:
            # Page 0: main details (language, birthday, gender from overrides)
            language = str(override.get("language", "") or "")
            birthday = str(override.get("birthday", "") or "")
            gender = override.get("gender", 0xFF)
            contact = Contact(
                index=max(0, int(index)),
                handle=phone_handle,
                name=phone_name,
                status=pc.status,
            )
            self._phone.send_contact_details(
                contact,
                phone_handle,
                language=language,
                birthday_ymd=birthday,
                gender=gender,
            )
            return

        if mode == 1:
            # Page 1: phone numbers from overrides
            phone_home = str(override.get("phone_home", "") or "")
            phone_office = str(override.get("phone_office", "") or "")
            phone_mobile = str(override.get("phone_mobile", "") or self._make_phone_id_shim(real_handle))
            self._phone.send_contact_numbers_page(
                office=phone_office,
                home=phone_home,
                mobile=phone_mobile,
            )
            return

        # Page 2+: address/bio/timezone
        key = str(getattr(pc, 'id', '') or getattr(pc, 'handle', '') or "").strip()
        bio_override = str(override.get("bio", "") or "").strip()
        bio = bio_override or self._contact_bio_cache.get(key, "").strip()
        if not bio:
            bio = "Loading bio..."
            self._spawn_task(
                self._refresh_contact_bio_page(pc, index, override),
                f"contact_bio:{index}",
            )

        about_text = self._compose_detail_address_text(override, bio)
        _shim_handle = str(getattr(pc, 'handle', '') or getattr(pc, 'id', '') or "")
        about_phone = re.sub(r"\b\d{4,}\b", lambda m: self._make_phone_id_shim(m.group()), about_text)
        about_phone = re.sub(r"\s+", " ", about_phone).strip()
        tz_h, tz_m = self._parse_tz_offset(override.get("timezone", ""))
        self._phone.send_contact_bio_page(about_phone, local_hh=tz_h, local_mm=tz_m)

    def _on_phone_answer(self) -> None:
        with self._call_lock:
            incoming = self._incoming_caller
            call_id = self._incoming_call_id
        if incoming:
            self._start_call_audio()
            self._run_async(self._platform.answer_call(call_id or ""))
            self._phone.confirm_call_connected()
            with self._call_lock:
                self._current_call_id = incoming
                self._incoming_caller = None
                self._incoming_call_id = None
            self._ui_call(lambda: self._ui and self._ui.answer_call())

    def _on_phone_reject(self) -> None:
        log.info("Handset: reject incoming")
        self._stop_call_audio()
        self._run_async(self._platform.end_call())
        with self._call_lock:
            self._incoming_caller = None
            self._incoming_call_id = None
        self._ui_call(lambda: self._ui and self._ui.end_call())

    def _on_phone_hold(self) -> None:
        self._run_async(self._platform.hold_call())

    def _on_phone_status_change(self, status_int: int) -> None:
        Status = self._Status
        status_map = {
            Status.ONLINE: "Online",
            Status.OFFLINE: "Offline",
            Status.NA: "Away",
            Status.AWAY: "Away",
            Status.DND: "Do Not Disturb",
            Status.INVISIBLE: "Invisible",
            Status.SKYPEME: "Online",
        }
        status_str = status_map.get(status_int, "Online")
        log.info("Handset: status change -> %s (%d)", status_str, status_int)
        self._run_async(self._platform.set_status(status_str))

    # ── Platform event handlers (async loop thread) ───────────────────

    def _on_platform_incoming(self, call_id: str, caller_name: str) -> None:
        log.info("Platform: incoming call from %s (call_id=%s)", caller_name, call_id)
        with self._call_lock:
            self._incoming_caller  = caller_name
            self._incoming_call_id = call_id
            self._current_call_id  = caller_name
            self._current_call_display_name = caller_name
        self._call_was_incoming = True
        self._record_call_start()

        # Store real target for callback resolution
        try:
            self._last_incoming_dial_target = str(
                self._platform.get_last_incoming_target() or ""
            )
        except Exception:
            self._last_incoming_dial_target = ""

        # Scrub numeric IDs from caller label for the handset display
        phone_label = self._phone_incoming_caller_label(caller_name)
        self._refresh_last_incoming_shim_keys(phone_label)

        # Ring the handset and display caller ID (privacy-safe)
        self._phone.ring()
        self._phone.display_caller_id(phone_label)
        # UI still sees the real caller name
        self._ui_call(lambda: self._ui and self._ui.incoming_call(caller_name))

    def _on_platform_answered(self) -> None:
        log.info("Platform: call answered by remote")
        self._phone.confirm_call_connected()
        self._ui_call(lambda: self._ui and self._ui.answer_call())

    def _on_platform_ended(self) -> None:
        log.info("Platform: call ended by remote")
        self._stop_call_audio()
        self._phone.end_call_from_remote()
        with self._call_lock:
            display_name = self._current_call_display_name or self._current_call_id
            still_ringing = self._incoming_caller is not None
            self._current_call_id = None
            self._current_call_display_name = None
            self._incoming_caller = None
            self._incoming_call_id = None
        if display_name:
            if still_ringing:
                # Incoming call ended before we answered — missed
                self._record_missed(display_name)
                self._record_call_history(display_name, 'missed')
            else:
                ctype = 'incoming' if self._call_was_incoming else 'outgoing'
                self._record_call_history(display_name, ctype)
            self._call_was_incoming = False
        self._ui_call(lambda: self._ui and self._ui.end_call())

    # ── Missed call tracking ──────────────────────────────────────────

    def _record_missed(self, caller: str) -> None:
        with self._missed_lock:
            if caller not in self._missed_calls:
                self._missed_calls.append(caller)
        log.info("Missed call from %s (total %d)", caller, len(self._missed_calls))
        self._ui_call(self._push_missed_to_ui)

    def _push_missed_to_ui(self) -> None:
        if self._ui is None:
            return
        with self._missed_lock:
            missed = list(self._missed_calls)
        self._ui.missed_calls = missed
        if self._ui.state == 'log':
            self._ui._render()

    # ── Call history (previous calls log) ─────────────────────────────

    def _record_call_start(self) -> None:
        """Mark the start time of a call for duration tracking."""
        self._call_start_time = time.time()

    def _record_call_history(self, name: str, call_type: str) -> None:
        """Append a completed call to the history and push to UI.

        call_type: 'outgoing', 'incoming', or 'missed'
        """
        duration = 0
        if self._call_start_time > 0 and call_type != 'missed':
            duration = max(0, int(time.time() - self._call_start_time))
        self._call_start_time = 0.0

        entry: Dict[str, Any] = {
            'name': name,
            'type': call_type,
            'timestamp': time.time(),
            'duration_secs': duration,
        }
        with self._call_history_lock:
            self._call_history.append(entry)
            # Cap at 50 most recent entries
            if len(self._call_history) > 50:
                self._call_history = self._call_history[-50:]
        log.info("Call history: %s %s (%ds)", call_type, name, duration)
        self._ui_call(self._push_call_history_to_ui)

    def _push_call_history_to_ui(self) -> None:
        if self._ui is None:
            return
        with self._call_history_lock:
            history = list(self._call_history)
        self._ui.call_history = history
        if self._ui.state == 'log':
            self._ui._render()

    # ── Contact refresh → UI ──────────────────────────────────────────

    def _push_contacts_to_ui(self) -> None:
        contacts = self._get_ordered_contacts_sync()
        ui_dicts = [c.as_ui_dict() for c in contacts]
        self._ui_call(lambda: self._ui and self._ui.update_contacts(ui_dicts))

    # ── Pre-built phone wire cache (proactive pre-caching) ────────────

    def _rebuild_phone_wire_cache(self) -> None:
        """Rebuild the always-warm wire-format contacts list for the handset.

        Reads from ``_contacts_cache`` (platform contacts), applies the
        phone filter (selected_contacts + max 33), builds ``Contact``
        wire objects, and stores them in ``_prebuilt_phone_wire`` so the
        next handset contacts_request can be served instantly.

        Safe to call from any thread.
        """
        Contact = self._Contact
        phone_contacts = self._get_ordered_contacts_sync(for_phone=True)
        self._rebuild_phone_shim_lookup(phone_contacts)

        wire: list = []
        for i, c in enumerate(phone_contacts):
            ph = self._phone_display_handle(c)
            pn = self._phone_display_name(c, ph)
            wire.append(Contact(index=i, name=pn, handle=ph, status=c.status))

        with self._prebuilt_phone_wire_lock:
            self._prebuilt_phone_wire = wire
            self._prebuilt_phone_wire_total = len(wire)
            self._prebuilt_phone_wire_at = time.monotonic()

        # Keep _visible_contacts and snapshot in sync for detail resolution
        self._visible_contacts = list(phone_contacts)
        self._last_details_snapshot = list(phone_contacts)
        self._last_sent_contacts = list(wire)
        self._last_sent_total = len(wire)

        log.debug(
            "Phone wire cache rebuilt: %d contacts (shim=%s selected=%d)",
            len(wire), self._phone_id_shim, len(self._selected_contact_keys),
        )

    def _send_prebuilt_phone_wire(self) -> bool:
        """Send the pre-built wire cache to the handset.

        Returns True if sent successfully, False if the cache is empty.
        Handles both standard and DLL-exact (legacy) send modes, including
        the emergency output ack for DLL-exact mode.
        """
        with self._prebuilt_phone_wire_lock:
            wire = list(self._prebuilt_phone_wire)
            total = self._prebuilt_phone_wire_total

        if not wire:
            return False

        try:
            if self._dll_exact_contacts_mode:
                if self._contacts_emergency_output_ack:
                    try:
                        self._phone.send_contacts_legacy(
                            wire, 0,
                            transport_override="output_only",
                            first_only=True,
                        )
                    except Exception as e:
                        log.debug("Emergency contacts ack failed: %s", e)
                self._phone.send_contacts_legacy(wire, 0)
            else:
                self._phone.send_contacts(wire, total_count=total)
            self._contacts_diag_counters["sent"] += 1
            log.info("Phone: sent %d pre-cached contacts instantly (total=%d)", len(wire), total)
            self._log_wire_preview(wire, total)
            return True
        except Exception as e:
            log.warning("Phone: failed to send pre-cached contacts: %s", e)
            return False

    # ── Contacts: synchronous wrapper (for UI thread / poll thread) ────

    def _get_ordered_contacts_sync(self, for_phone: bool = False) -> list:
        """Synchronous wrapper — used by UI push and dial resolution.

        When for_phone=True the selected_contacts filter is applied so
        only the user-chosen subset (max 33) reaches the handset.
        When for_phone=False (default) ALL contacts are returned for the UI.
        """
        now = time.monotonic()
        with self._contacts_lock:
            cache_age = now - self._contacts_cache_at if self._contacts_cache_at > 0.0 else float("inf")
            if self._contacts_cache and cache_age <= self._contacts_cache_ttl_s:
                ordered = self._order_contacts_impl(self._contacts_cache)
                if for_phone:
                    ordered = self._apply_selected_contacts_filter(ordered, for_phone=True)
                    ordered = ordered[:self._contacts_page_size]
                return ordered

        # Cache stale or empty — blocking fetch
        try:
            contacts = self._run_async_wait(
                self._platform.get_contacts(),
                timeout=self._contacts_fetch_timeout_s,
            ) or []
            log.debug("_get_ordered_contacts_sync: got %d contacts", len(contacts))
        except Exception as e:
            log.warning("get_contacts failed: %s", e, exc_info=True)
            with self._contacts_lock:
                ordered = self._order_contacts_impl(self._contacts_cache)
                if for_phone:
                    ordered = self._apply_selected_contacts_filter(ordered, for_phone=True)
                    ordered = ordered[:self._contacts_page_size]
                return ordered

        with self._contacts_lock:
            self._contacts_cache = list(contacts)
            self._contacts_cache_at = time.monotonic()

        ordered = self._order_contacts_impl(contacts)
        if for_phone:
            ordered = self._apply_selected_contacts_filter(ordered, for_phone=True)
            ordered = ordered[:self._contacts_page_size]
        return ordered

    # Keep backward compat alias
    def _get_ordered_contacts(self) -> list:
        return self._get_ordered_contacts_sync()

    # ── Contacts: async version (for the async pipeline) ──────────────

    async def _get_ordered_contacts_async(self) -> Tuple[list, str, str]:
        """Async version returning (contacts, order_mode, source)."""
        contacts_cfg = self._cfg.get("contacts", {}) if isinstance(self._cfg, dict) else {}
        order_mode = str(contacts_cfg.get("order", "online_first")).strip().lower()
        if order_mode not in {"online_first", "alphabetical_only"}:
            order_mode = "online_first"

        now = time.monotonic()
        cache_age = now - self._contacts_cache_at if self._contacts_cache_at > 0.0 else float("inf")
        cache_fresh = bool(self._contacts_cache) and cache_age <= self._contacts_cache_ttl_s

        source = "empty"
        if cache_fresh:
            source = "cache_fresh"
        elif self._contacts_cache:
            source = "cache_stale"
            if self._contacts_allow_stale_fallback:
                asyncio.ensure_future(self._refresh_contacts_cache())
            else:
                await self._refresh_contacts_cache()
                source = "refreshed"
        else:
            refreshed = await self._refresh_contacts_cache()
            source = "refreshed" if refreshed else "empty"

        ordered = self._order_contacts_impl(self._contacts_cache)
        # Apply selected_contacts filter (phone gets only the chosen subset)
        ordered = self._apply_selected_contacts_filter(ordered, for_phone=True)
        # Cap to handset limit (33)
        ordered = ordered[:self._contacts_page_size]
        self._last_ordered_contacts = ordered
        self._log_contacts_preview(f"ordered:{order_mode}:{source}", ordered)
        return ordered, order_mode, source

    # ── Contacts: async refresh with retry/backoff ────────────────────

    async def _refresh_contacts_cache(self, attempts: Optional[int] = None) -> bool:
        """Refresh app-side contact cache from platform contacts API with retries."""
        if self._contacts_refresh_lock is None:
            self._contacts_refresh_lock = asyncio.Lock()

        max_attempts = max(1, int(attempts or self._contacts_refresh_retries))
        async with self._contacts_refresh_lock:
            for attempt in range(1, max_attempts + 1):
                try:
                    fetched = await asyncio.wait_for(
                        self._platform.get_contacts(),
                        timeout=self._contacts_fetch_timeout_s,
                    )
                except asyncio.TimeoutError:
                    log.warning(
                        "Contacts refresh timed out (attempt %d/%d, timeout=%.1fs)",
                        attempt, max_attempts, self._contacts_fetch_timeout_s,
                    )
                    fetched = None
                except Exception as e:
                    log.warning(
                        "Contacts refresh failed (attempt %d/%d): %s",
                        attempt, max_attempts, e,
                    )
                    fetched = None

                if fetched:
                    with self._contacts_lock:
                        self._contacts_cache = list(fetched)
                        self._contacts_cache_at = time.monotonic()
                    self._contacts_refresh_fail_at = 0.0
                    self._contacts_diag_counters["refresh_ok"] += 1
                    self._log_contacts_preview("cache_refresh", self._contacts_cache)
                    return True

                if fetched == [] and self._contacts_cache:
                    log.warning(
                        "Contacts refresh returned empty; keeping %d cached entries",
                        len(self._contacts_cache),
                    )
                    return False

                if attempt < max_attempts and self._contacts_refresh_backoff_s > 0.0:
                    await asyncio.sleep(self._contacts_refresh_backoff_s * attempt)

            if not self._contacts_cache:
                with self._contacts_lock:
                    self._contacts_cache = []
                    self._contacts_cache_at = time.monotonic()
            self._contacts_refresh_fail_at = time.monotonic()
            self._contacts_diag_counters["refresh_fail"] += 1
            return False

    # ── Contacts: background refresher ────────────────────────────────

    async def _contacts_background_refresher(self) -> None:
        """Periodically refresh contacts cache to keep handset requests fast."""
        interval = self._contacts_background_refresh_s
        if interval <= 0.0:
            return
        try:
            while self._running:
                await asyncio.sleep(interval)
                if not self._running:
                    return
                is_connected = getattr(self._platform, 'is_connected', True)
                if not is_connected:
                    continue
                await self._refresh_contacts_cache()
        except asyncio.CancelledError:
            return

    # ── Contacts: legacy DLL-exact tick ───────────────────────────────

    async def _service_contacts_legacy_tick(self) -> None:
        """Legacy request servicing path for DLL-exact mode."""
        if not self._dll_exact_contacts_mode:
            return
        if self._contacts_fetch_inflight:
            return
        if self._contacts_pending_index is None:
            return

        req_index = int(self._contacts_pending_index)
        age = time.monotonic() - self._contacts_pending_at if self._contacts_pending_at > 0.0 else 0.0
        self._contacts_pending_index = None
        self._contacts_pending_at = 0.0
        if self._contacts_diagnostics:
            log.debug("Contacts legacy tick: processing index=%d queued_for=%.3fs", req_index, age)
        await self._fetch_and_send_contacts(req_index)

    # ── Contacts: ordering (instance method with DLL-exact support) ───

    def _order_contacts_impl(self, contacts: list) -> list:
        """Order contacts — supports DLL-exact mode for Telegram.

        Returns ALL contacts ordered for the UI.  The handset 33-contact
        limit is enforced separately in _build_contacts_page /
        _fetch_and_send_contacts.
        """
        contacts_cfg = self._cfg.get("contacts", {}) if isinstance(self._cfg, dict) else {}
        order_mode = str(contacts_cfg.get("order", "online_first")).strip().lower()
        Status = self._Status

        if self._dll_exact_contacts_mode:
            offline = int(Status.OFFLINE)
            return sorted(
                contacts,
                key=lambda c: (
                    10 if int(c.status) == offline else int(c.status),
                    (c.name or c.handle or "").lower(),
                ),
            )

        if order_mode == "alphabetical_only":
            return sorted(contacts, key=lambda c: (c.name or c.handle or "").lower())

        return sorted(
            contacts,
            key=lambda c: (
                1 if int(c.status) == int(Status.OFFLINE) else 0,
                (c.name or c.handle or "").lower(),
            ),
        )

    # ── Contacts: selected filter ─────────────────────────────────────

    def _apply_selected_contacts_filter(self, ordered: list, for_phone: bool = False) -> list:
        """Apply selected_contacts filter when enabled for this target."""
        if not self._selected_contact_keys:
            return ordered

        filter_enabled = (
            self._selected_only
            or self._selected_prioritize
            or (for_phone and self._force_selected_only)
        )
        if not filter_enabled:
            log.debug(
                "selected_contacts configured (%d) but filter disabled",
                len(self._selected_contact_keys),
            )
            return ordered

        selected_only_mode = self._selected_only or (for_phone and self._force_selected_only)

        selected = []
        matched_keys: set = set()
        for c in ordered:
            keys = {
                _normalize_contact_key(c.id),
                _normalize_contact_key(c.handle),
                _normalize_contact_key(c.name),
            }
            if any(k in self._selected_contact_keys for k in keys if k):
                selected.append(c)
                matched_keys.update(k for k in keys if k in self._selected_contact_keys)

        missing = sorted(self._selected_contact_keys - matched_keys)
        if missing and not self._selected_missing_warned:
            log.warning("Configured selected_contacts not found: %s", ", ".join(missing))
            self._selected_missing_warned = True

        if selected_only_mode:
            if selected:
                out = selected
            else:
                log.warning("selected_only enabled but 0 contacts matched; falling back to unfiltered")
                out = ordered
        else:
            selected_ids = {str(c.id) for c in selected}
            out = selected + [c for c in ordered if str(c.id) not in selected_ids]

        log.info(
            "Selected contacts filter: matched=%d configured=%d mode=%s",
            len(selected), len(self._selected_contact_keys),
            "only" if selected_only_mode else "prioritize",
        )
        return out

    # ── Contacts: paging ──────────────────────────────────────────────

    def _build_contacts_page(self, ordered: list, start_index: int) -> list:
        if not ordered:
            return []
        total = len(ordered)
        start = max(0, int(start_index))
        if start >= total:
            return []

        if self._dll_exact_contacts_mode:
            if total <= 1:
                page_size = 1
            else:
                # Legacy DLL: initial request returns 2 entries, scroll returns 1.
                page_size = 2 if start == 0 else 1
        else:
            page_size = self._contacts_page_size

        page = ordered[start:start + page_size]
        if self._dll_exact_contacts_mode:
            return page

        if len(page) < page_size and start > 0:
            page.extend(ordered[:page_size - len(page)])
        return page

    def _build_compat_page(self, ordered: list, start_index: int) -> list:
        if not ordered:
            return []
        total = len(ordered)
        start = max(0, int(start_index))
        if start >= total:
            start = 0
        page_size = min(self._contacts_compat_page_size, total)
        page = ordered[start:start + page_size]
        if len(page) < page_size and start > 0:
            page.extend(ordered[:page_size - len(page)])
        return page

    # ── Contacts: fetch and send to phone (full async flow) ───────────

    async def _fetch_and_send_contacts(self, start_index: int = 0) -> None:
        """Fetch contacts from platform and send to phone."""
        if self._contacts_fetch_inflight:
            return

        self._contacts_fetch_inflight = True
        Contact = self._Contact
        try:
            ordered, order_mode, source = await self._get_ordered_contacts_async()

            if not ordered:
                refreshed = await self._refresh_contacts_cache(
                    attempts=max(2, self._contacts_refresh_retries + 1)
                )
                if refreshed:
                    ordered, order_mode, source = await self._get_ordered_contacts_async()

            if not ordered:
                self._log_contacts_health("ordered_empty")
                if self._contacts_allow_stale_fallback and self._last_sent_contacts:
                    self._phone.send_contacts(
                        self._last_sent_contacts, total_count=self._last_sent_total
                    )
                    self._contacts_diag_counters["stale_resend"] += 1
                    log.warning(
                        "Contacts unavailable; resent last payload (%d contacts total=%d)",
                        len(self._last_sent_contacts), self._last_sent_total,
                    )
                    self._log_wire_preview(self._last_sent_contacts, self._last_sent_total)
                    return

                self._visible_contacts = []
                self._phone.send_contacts([])
                self._contacts_diag_counters["empty"] += 1
                log.info("Sent 0 contacts to phone")
                return

            start_index = max(0, int(start_index))
            if start_index >= len(ordered) and not self._dll_exact_contacts_mode:
                start_index = 0

            if ordered:
                focus = ordered[start_index if start_index < len(ordered) else 0]
                self._last_phone_focus_target = str(
                    getattr(focus, 'handle', '') or getattr(focus, 'id', '') or ""
                )

            self._rebuild_phone_shim_lookup(ordered)

            page = self._build_contacts_page(ordered, start_index)
            legacy_ordered = ordered[:33] if self._dll_exact_contacts_mode else ordered
            if self._dll_exact_contacts_mode:
                page = self._build_contacts_page(legacy_ordered, start_index)

            self._visible_contacts = page
            self._last_details_snapshot = list(
                legacy_ordered if self._dll_exact_contacts_mode else ordered
            )

            contacts = []
            for i, pc in enumerate(self._visible_contacts):
                phone_handle = self._phone_display_handle(pc)
                phone_name = self._phone_display_name(pc, phone_handle)
                contacts.append(Contact(
                    index=i,
                    handle=phone_handle,
                    name=phone_name,
                    status=pc.status,
                ))

            if self._dll_exact_contacts_mode:
                legacy_contacts: list = []
                for i, pc in enumerate(legacy_ordered):
                    phone_handle = self._phone_display_handle(pc)
                    phone_name = self._phone_display_name(pc, phone_handle)
                    legacy_contacts.append(Contact(
                        index=i,
                        handle=phone_handle,
                        name=phone_name,
                        status=pc.status,
                    ))
                if self._contacts_emergency_output_ack:
                    try:
                        self._phone.send_contacts_legacy(
                            legacy_contacts,
                            start_index,
                            transport_override="output_only",
                            first_only=True,
                        )
                        log.info("Sent emergency contacts ack via output_only (start=%d)", start_index)
                    except Exception as e:
                        log.debug("Emergency contacts ack failed: %s", e)
                self._phone.send_contacts_legacy(legacy_contacts, start_index)
                self._last_sent_contacts = list(contacts)
                self._last_sent_total = len(legacy_ordered)
                self._contacts_diag_counters["sent"] += 1
                self._log_wire_preview(contacts, len(legacy_ordered))
            else:
                self._phone.send_contacts(contacts, total_count=len(ordered))
                self._last_sent_contacts = list(contacts)
                self._last_sent_total = len(ordered)
                self._contacts_diag_counters["sent"] += 1
                self._log_wire_preview(contacts, len(ordered))

            log.info(
                "Sent %d contacts to phone (start=%d total=%d order=%s mode=%s source=%s)",
                len(contacts), start_index,
                len(legacy_ordered if self._dll_exact_contacts_mode else ordered),
                order_mode,
                "dll_exact" if self._dll_exact_contacts_mode else f"page_{self._contacts_page_size}",
                source,
            )
        except Exception as e:
            log.error("Failed to fetch contacts: %s", e)
            self._log_contacts_health("fetch_exception")
            if self._contacts_allow_stale_fallback and self._last_sent_contacts:
                try:
                    self._phone.send_contacts(
                        self._last_sent_contacts, total_count=self._last_sent_total
                    )
                    self._contacts_diag_counters["stale_resend"] += 1
                    log.warning(
                        "Contacts fetch failed; resent last payload (%d contacts total=%d)",
                        len(self._last_sent_contacts), self._last_sent_total,
                    )
                    return
                except Exception:
                    pass
            self._visible_contacts = []
            self._phone.send_contacts([])
            self._contacts_diag_counters["empty"] += 1
        finally:
            self._contacts_fetch_inflight = False

    # ── Contacts: diagnostics ─────────────────────────────────────────

    def _contact_diag_token(self, c) -> str:
        return f"{c.id}|{c.handle}|{c.name}|{int(c.status)}"

    def _log_contacts_preview(self, label: str, contacts: list) -> None:
        if not self._contacts_diagnostics:
            return
        if not contacts:
            log.debug("Contacts preview [%s]: <empty>", label)
            return
        sample = contacts[:self._contacts_diag_sample]
        tokens = [self._contact_diag_token(c) for c in sample]
        log.debug("Contacts preview [%s]: count=%d sample=%s", label, len(contacts), tokens)

    def _log_wire_preview(self, contacts: list, total_count: int) -> None:
        if not self._contacts_diagnostics:
            return
        if not contacts:
            log.debug("Contacts wire preview: <empty> total=%d", total_count)
            return
        sample = contacts[:self._contacts_diag_sample]
        wire = [f"idx={c.index}/total={total_count}/st={int(c.status)}:{c.handle}" for c in sample]
        log.debug("Contacts wire preview: sent=%d total=%d sample=%s", len(contacts), total_count, wire)

    def _log_contacts_health(self, reason: str) -> None:
        now = time.monotonic()
        cache_age = now - self._contacts_cache_at if self._contacts_cache_at > 0.0 else -1.0
        fail_age = now - self._contacts_refresh_fail_at if self._contacts_refresh_fail_at > 0.0 else -1.0
        pending = self._contacts_pending_index if self._contacts_pending_index is not None else -1
        log.warning(
            "Contacts health [%s]: cache_size=%d cache_age=%.1fs visible=%d ordered=%d "
            "last_sent=%d last_total=%d refresh_fail_age=%.1fs fetch_inflight=%s pending_index=%d",
            reason,
            len(self._contacts_cache), cache_age,
            len(self._visible_contacts), len(self._last_ordered_contacts),
            len(self._last_sent_contacts), self._last_sent_total,
            fail_age, self._contacts_fetch_inflight, pending,
        )
        if self._contacts_diagnostics:
            log.warning("Contacts counters: %s", self._contacts_diag_counters)

    # ── Contact detail overrides ──────────────────────────────────────

    def _match_contact_detail_override(self, pc) -> dict:
        """Match a contact against config detail_overrides entries."""
        id_key = _normalize_contact_key(getattr(pc, 'id', ''))
        handle_key = _normalize_contact_key(getattr(pc, 'handle', ''))
        name_key = _normalize_contact_key(getattr(pc, 'name', ''))

        def _values(v) -> list:
            if isinstance(v, (list, tuple, set)):
                seq = list(v)
            else:
                seq = [v]
            out = []
            for item in seq:
                key = _normalize_contact_key(item)
                if key:
                    out.append(key)
            return out

        for entry in self._contact_detail_overrides:
            if not isinstance(entry, dict):
                continue

            match = entry.get("match") if isinstance(entry.get("match"), dict) else {}
            if match:
                ids = _values(match.get("id", []))
                handles = _values(match.get("handle", []))
                names = _values(match.get("name", []))
            else:
                ids = _values(entry.get("id", []))
                handles = _values(entry.get("handle", []))
                names = _values(entry.get("name", []))

            if ids and id_key not in ids:
                continue
            if handles and handle_key not in handles:
                continue
            if names and name_key not in names:
                continue
            if not ids and not handles and not names:
                continue

            return entry

        return {}

    def _parse_tz_offset(self, value) -> Tuple[Optional[int], Optional[int]]:
        raw = str(value or "").strip()
        if not raw:
            return None, None
        m = re.fullmatch(r"([+-]?)(\d{1,2})(?::?(\d{2}))?", raw)
        if not m:
            return None, None
        sign = -1 if m.group(1) == "-" else 1
        hh = int(m.group(2))
        mm = int(m.group(3) or "0")
        if hh > 23 or mm > 59:
            return None, None
        return sign * hh, sign * mm

    def _compose_detail_address_text(self, override: dict, bio: str) -> str:
        city = str(override.get("city", "") or "").strip()
        province = str(override.get("province", "") or "").strip()
        country = str(override.get("country", "") or "").strip()
        location = ", ".join(x for x in [city, province, country] if x)
        if bio and location:
            return f"{bio} | {location}"
        return bio or location

    async def _refresh_contact_bio_page(self, pc, index: int, override: Optional[dict] = None) -> None:
        """Async: fetch bio from platform and re-send the bio page to handset."""
        try:
            bio = await self._platform.get_contact_bio(pc)
        except Exception:
            return

        bio = str(bio or "").strip()
        if not bio:
            return

        key = str(getattr(pc, 'id', '') or getattr(pc, 'handle', '') or "").strip()
        if key:
            self._contact_bio_cache[key] = bio

        # If user is still in details flow, refresh the bio page payload.
        ovr = override or self._match_contact_detail_override(pc)
        about_text = self._compose_detail_address_text(ovr, bio)
        about_phone = re.sub(r"\b\d{4,}\b", lambda m: self._make_phone_id_shim(m.group()), about_text)
        about_phone = re.sub(r"\s+", " ", about_phone).strip()
        tz_h, tz_m = self._parse_tz_offset(ovr.get("timezone", ""))
        self._phone.send_contact_bio_page(about_phone, local_hh=tz_h, local_mm=tz_m)

    # ── Dial target resolution ────────────────────────────────────────

    def _resolve_dial_target(self, raw: str) -> str:
        raw_str = str(raw or "").strip()
        if not raw_str:
            return raw_str

        key = _normalize_contact_key(raw_str)
        if not key:
            return raw_str

        # ── Shim callback resolution (missed-call redial from handset) ──
        if self._phone_id_shim and self._last_incoming_dial_target:
            if key in self._last_incoming_shim_keys:
                log.info("Dial shim callback matched last incoming target")
                return self._last_incoming_dial_target
            for k in self._last_incoming_shim_keys:
                if k.startswith(key):
                    log.info("Dial shim callback prefix matched last incoming")
                    return self._last_incoming_dial_target

        # ── Shim reverse lookup (numeric ID was replaced with fake number) ──
        if key in self._phone_shim_lookup:
            choices = self._phone_shim_lookup[key]
            if len(choices) == 1:
                return choices[0]
            if self._last_phone_focus_target and self._last_phone_focus_target in choices:
                return self._last_phone_focus_target
            return choices[0]
        for shim_key, reals in self._phone_shim_lookup.items():
            if shim_key.startswith(key):
                if len(reals) == 1:
                    return reals[0]
                if self._last_phone_focus_target and self._last_phone_focus_target in reals:
                    return self._last_phone_focus_target
                return reals[0]

        # ── Standard resolution (name/handle matching) ──
        pool = self._visible_contacts or self._contacts_cache
        if not pool:
            pool = self._get_ordered_contacts_sync()

        # Exact key match against handle, id, or name
        for c in pool:
            keys = (
                _normalize_contact_key(c.handle),
                _normalize_contact_key(c.id),
                _normalize_contact_key(c.name),
            )
            if key in keys:
                return c.handle or c.id or raw_str

        # Prefix match on handle first, then display name
        handle_prefix = []
        name_prefix = []
        for c in pool:
            handle_key = _normalize_contact_key(c.handle)
            name_key_c = _normalize_contact_key(c.name)
            if handle_key.startswith(key):
                handle_prefix.append(c)
            elif name_key_c.startswith(key):
                name_prefix.append(c)

        if handle_prefix:
            best = min(handle_prefix, key=lambda c: len(c.handle or c.id or ""))
            return best.handle or best.id or raw_str
        if name_prefix:
            best = min(name_prefix, key=lambda c: len(c.handle or c.id or ""))
            return best.handle or best.id or raw_str

        return raw_str

    def _resolve_display_name(self, target: str) -> str:
        """Look up a friendly display name for a platform target/handle/id.

        Returns the contact's name if found, otherwise the raw target string.
        """
        if not target:
            return target
        key = _normalize_contact_key(target)
        if not key:
            return target
        pool = self._visible_contacts or self._contacts_cache
        if not pool:
            pool = self._get_ordered_contacts_sync()
        for c in pool:
            keys = (
                _normalize_contact_key(c.handle),
                _normalize_contact_key(c.id),
                _normalize_contact_key(c.name),
            )
            if key in keys and c.name:
                return c.name
        return target


# ── Entry point helpers ───────────────────────────────────────────────────────

def _setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt   = "%(asctime)s %(levelname)-7s %(name)s – %(message)s"

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Always log everything to skypeui.log (truncated on each run)
    log_path = Path(__file__).parent.parent / "skypeui.log"
    fh = logging.FileHandler(str(log_path), mode='w', encoding='utf-8', delay=False)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt))
    fh.flush = lambda: (fh.stream.flush() if fh.stream else None)  # type: ignore[method-assign]
    root_logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(fmt))
    root_logger.addHandler(ch)

    logging.getLogger(__name__).info(
        "Logging started  level=%s  file=%s",
        logging.getLevelName(level), log_path
    )


def _run_list_audio() -> None:
    """List available audio devices and exit."""
    try:
        import sounddevice as sd
        print("Available audio devices:")
        print(sd.query_devices())
    except ImportError:
        print("sounddevice not installed. Run: pip install sounddevice")
    except Exception as e:
        print(f"Error listing audio devices: {e}")


async def _run_telegram_auth_bootstrap(config: dict) -> None:
    """Async Telegram auth-only mode (used by Setup TUI and CLI)."""
    from .platforms.telegram_ntg import TelegramNTGPlatform

    tg_cfg = _telegram_settings_with_env(config)
    audio_cfg = config.get("audio", {}) if isinstance(config, dict) else {}

    platform = TelegramNTGPlatform(
        api_id=int(tg_cfg.get("api_id", 0)),
        api_hash=tg_cfg.get("api_hash", ""),
        phone=tg_cfg.get("phone", ""),
        session_name=tg_cfg.get("session_name", "skype_session"),
        sample_rate=int(audio_cfg.get("sample_rate", 16000)),
        channels=int(audio_cfg.get("channels", 1)),
    )

    await platform.authorize_only()
    print("Telegram auth bootstrap complete. Session saved.")


async def run_telegram_allow_everyone_calls(config: dict) -> None:
    """Set Telegram call privacy to allow everyone to call this account."""
    from telethon import functions, types
    from .platforms.telegram_ntg import TelegramNTGPlatform

    tg_cfg = _telegram_settings_with_env(config)
    audio_cfg = config.get("audio", {}) if isinstance(config, dict) else {}

    platform = TelegramNTGPlatform(
        api_id=int(tg_cfg.get("api_id", 0)),
        api_hash=tg_cfg.get("api_hash", ""),
        phone=tg_cfg.get("phone", ""),
        session_name=tg_cfg.get("session_name", "skype_session"),
        sample_rate=int(audio_cfg.get("sample_rate", 16000)),
        channels=int(audio_cfg.get("channels", 1)),
    )

    try:
        await platform.connect()
        client = getattr(platform, "_client", None)
        if client is None:
            raise RuntimeError("Telegram client unavailable")
        await client(
            functions.account.SetPrivacyRequest(
                key=types.InputPrivacyKeyPhoneCall(),
                rules=[types.InputPrivacyValueAllowAll()],
            )
        )
        print("Telegram call privacy set to allow everyone.")
    finally:
        try:
            await platform.disconnect()
        except Exception:
            pass


def _run_telegram_auth(cfg: Dict[str, Any]) -> None:
    """Interactive Telegram auth bootstrap — creates/refreshes the session file."""
    try:
        asyncio.run(_run_telegram_auth_bootstrap(cfg))
    except RuntimeError as e:
        log.error("Telegram auth failed: %s", e)
        print(f"Auth failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAuth cancelled.")


def _run_test_phone() -> None:
    """CIT200 HID-only test harness — no platform, just handset events."""
    from .cit200 import CIT200Device, Event, Status, Contact

    phone = CIT200Device()
    phone.on(Event.CALL_BUTTON, lambda: print(">>> CALL BUTTON"))
    phone.on(Event.END_CALL, lambda: print(">>> END CALL"))
    phone.on(Event.DIAL, lambda c: print(f">>> DIAL: {c}"))
    phone.on(Event.CONTACTS_REQUEST, lambda idx: print(f">>> CONTACTS (index {idx})"))
    phone.on(Event.STATUS_CHANGE, lambda s: print(f">>> STATUS: {Status(s).name}"))
    phone.on(Event.ANSWER_INCOMING, lambda: print(">>> ANSWER"))
    phone.on(Event.REJECT_INCOMING, lambda: print(">>> REJECT"))
    phone.on(Event.HOLD_RESUME, lambda: print(">>> HOLD/RESUME"))

    def on_contacts(idx):
        phone.send_contacts([
            Contact(index=0, handle="testuser", name="Test User", status=Status.ONLINE),
        ])
    phone.on(Event.CONTACTS_REQUEST, on_contacts)

    print("CIT200 HID test mode — press buttons on handset")
    phone.run_loop()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="CIT200 Not Skype UI runtime")
    parser.add_argument("--mode", default=None,
                        choices=["gui", "phone", "recorder", "setup", "tray", "fakecalls", "mic", "mic_gui", "fakecall"])
    parser.add_argument("--platform", "-p", default=None,
                        choices=PLATFORM_CHOICES)
    parser.add_argument("--debug", "-d", action="store_true")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--telegram-auth", action="store_true",
                        help="Interactive Telegram session auth (creates/refreshes .session)")
    parser.add_argument("--telegram-allow-calls", action="store_true",
                        help="Set Telegram call privacy to allow everyone")
    parser.add_argument("--list-audio", action="store_true",
                        help="List available audio devices and exit")
    parser.add_argument("--test-phone", action="store_true",
                        help="Run CIT200 HID test mode (no platform)")
    parser.add_argument("--tui", action="store_true",
                        help="Open interactive setup menu before running")
    parser.add_argument("--no-tui", action="store_true",
                        help="Skip interactive setup menu")
    parser.add_argument("--hid-write-mode", default="dual",
                        help="Recorder mode HID write mode (dual|feature_only|output_only)")
    parser.add_argument("--call-delay-a", type=float, default=0.2,
                        help="Recorder mode handset call setup delay seconds")
    parser.add_argument("--call-delay-b", type=float, default=0.2,
                        help="Recorder mode handset call connect delay seconds")
    parser.add_argument("--hid-trace", default="",
                        help="Recorder mode HID trace file path")
    parser.add_argument("--hid-trace-window", type=float, default=3.0,
                        help="Recorder mode HID trace capture window seconds")
    parser.add_argument("--fakecaller", default=None,
                        help="Fake caller display name for --mode fakecall")
    parser.add_argument("--faketime", default=None,
                        help="Override handset clock for --mode fakecall (HH:MM)")
    args = parser.parse_args(argv)

    _setup_logging(args.debug)

    cfg = load_config(Path(args.config))
    if args.platform:
        cfg["platform"] = args.platform

    # ── Quick-exit modes ──────────────────────────────────────────────
    if args.list_audio:
        _run_list_audio()
        return

    if args.telegram_auth:
        _run_telegram_auth(cfg)
        return

    if args.telegram_allow_calls:
        try:
            asyncio.run(run_telegram_allow_everyone_calls(cfg))
        except Exception as e:
            print(f"Failed: {e}")
            sys.exit(1)
        return

    if args.test_phone:
        _run_test_phone()
        return

    if args.mode == "tray":
        try:
            from control_center import launch_tray
            launch_tray()
        except Exception as e:
            print(f"Tray mode failed: {e}")
            print("Install requirements: pip install pystray pillow")
        return

    if args.mode == "fakecalls":
        try:
            from fake_calls_tui import main as run_fake_calls_tui
            run_fake_calls_tui()
        except ImportError:
            print("fake_calls_tui module not available")
        return

    if args.mode == "recorder":
        _run_recorder(
            cfg,
            hid_write_mode=str(args.hid_write_mode or "dual"),
            call_delay_a=float(args.call_delay_a),
            call_delay_b=float(args.call_delay_b),
            hid_trace=str(args.hid_trace or ""),
            hid_trace_window=float(args.hid_trace_window),
        )
        return

    # ── Setup TUI ─────────────────────────────────────────────────────
    open_tui = False
    if args.mode == "setup":
        open_tui = True
    elif args.tui:
        open_tui = True

    if open_tui:
        selected_platform, should_run = run_setup_tui(cfg)
        if not should_run:
            return
        if selected_platform:
            cfg["platform"] = selected_platform

    # ── Headless mic mode ────────────────────────────────────────────
    if args.mode == "mic":
        _run_mic(cfg)
        return

    if args.mode == "mic_gui":
        from .mic_gui import launch_mic_gui
        launch_mic_gui(cfg)
        return

    # ── Headless fake-call mode ───────────────────────────────────────
    if args.mode == "fakecall":
        _run_fakecall(cfg, caller_name=args.fakecaller, fake_time=args.faketime)
        return

    # ── Main run ──────────────────────────────────────────────────────
    if args.mode == "phone":
        _run_phone(cfg)
    else:
        _run_gui(cfg)


# ── Headless mic mode ─────────────────────────────────────────────────────────

def _run_mic(cfg: Dict[str, Any]) -> None:
    """
    Headless mic mode — CIT200 handset as plain mic/speaker, no GUI, no platform.

    Opens the CIT200 HID device and AudioBridge, puts the handset into
    call-connected state for raw audio passthrough.  Runs until Ctrl+C.
    No tkinter, no Telegram, no UI — just the handset audio pipeline.
    """
    from .cit200 import CIT200Device, Event, Status
    from .audio_bridge import AudioBridge

    hid_cfg = cfg.get("hid", {})
    phone = CIT200Device(
        transport_mode=str(hid_cfg.get("transport_mode", "dual")),
        call_setup_delay=float(hid_cfg.get("q9_q10_delay", 0.2)),
        call_connect_delay=float(hid_cfg.get("call_connect_delay", 0.2)),
        contacts_frame_delay_s=float(hid_cfg.get("contacts_frame_delay_s", 0.05)),
        contacts_contact_delay_s=float(hid_cfg.get("contacts_contact_delay_s", 0.05)),
        contacts_transport_mode=str(hid_cfg.get("contacts_transport_mode", "feature_only")),
    )

    audio_cfg = cfg.get("audio", {})
    _ml_raw = audio_cfg.get("meter_level", "debug")
    if isinstance(_ml_raw, str):
        _meter_level = getattr(logging, _ml_raw.upper(), logging.DEBUG)
    else:
        _meter_level = int(_ml_raw)
    audio = AudioBridge(
        sample_rate=int(audio_cfg.get("sample_rate", 16000)),
        channels=int(audio_cfg.get("channels", 1)),
        chunk_size=int(audio_cfg.get("chunk_size", 960)),
        meter_enabled=bool(audio_cfg.get("meter_enabled", False)),
        meter_interval_s=float(audio_cfg.get("meter_interval_s", 10.0)),
        meter_level=_meter_level,
    )

    # ── Open HID device ──────────────────────────────────────────────
    print("Opening CIT200 HID device...")
    if not phone.open():
        print("ERROR: CIT200 handset not found. Is it plugged in?")
        sys.exit(1)
    print("CIT200 connected.")

    # ── Start audio streams ──────────────────────────────────────────
    # In headless mic mode, captured audio is not forwarded anywhere
    # (no platform). The handset mic is live and speaker receives silence.
    # If the user wants loopback (hear yourself), set
    #   audio.on_audio_captured = audio.play_audio
    audio.on_audio_captured = lambda pcm: None
    print("Starting audio bridge...")
    audio.start()
    print("Audio bridge active.")

    # ── Send initial keepalive ───────────────────────────────────────
    phone.send_init(Status.ONLINE)

    # ── Enter call-connected state ───────────────────────────────────
    # Same sequence as the GUI mic mode: q9+q10+q19 → wait → q17
    print("Activating handset call state...")
    phone.start_local_audio_call()
    time.sleep(0.5)
    phone.confirm_call_connected()
    print("Handset mic/speaker LIVE.")

    # ── Wire handset events for logging ──────────────────────────────
    phone.on(Event.END_CALL, lambda: log.info("[mic-mode] handset END_CALL pressed"))
    phone.on(Event.CALL_BUTTON, lambda: log.info("[mic-mode] handset CALL button pressed"))

    # ── Main poll loop ───────────────────────────────────────────────
    # Polls HID at 50 Hz, sends keepalive every ~1.6s (8 cycles * 0.2s)
    print("Mic mode active. Press Ctrl+C to stop.")
    cycle = 1
    try:
        while True:
            phone.poll()

            if cycle == 1:
                phone.send_time_sync()

            if cycle >= 8:
                cycle = 1
            else:
                cycle += 1

            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        phone.end_call_from_remote()
        time.sleep(0.2)
        audio.stop()
        phone.close()
        print("Mic mode stopped.")


# ── Headless fake-call mode ──────────────────────────────────────────────────

_FAKECALL_NUMBER = "133713371337"
_FAKECALL_NAME   = "Fake Caller"

def _run_fakecall(
    cfg: Dict[str, Any],
    caller_name: Optional[str] = None,
    fake_time: Optional[str] = None,
) -> None:
    """
    Headless fake-call mode — no GUI, no platform.

    Presents one fake contact on the CIT200 handset.  The user browses
    to the contact, dials from the handset, and the fake call pipeline
    simulates dialing → ringing → connected.  Audio from the handset mic
    is recorded to a WAV file.  Hanging up from the handset (or Ctrl+C)
    stops recording and saves the file.

    Usage:
        python run.py --mode fakecall
        python run.py --mode fakecall --fakecaller "John Doe"
        python run.py --mode fakecall --faketime 14:30
    """
    import wave as _wave
    from .cit200 import CIT200Device, Event, Status, Contact
    from .audio_bridge import AudioBridge

    display_name = caller_name or _FAKECALL_NAME

    # ── CIT200 HID setup ─────────────────────────────────────────────
    hid_cfg = cfg.get("hid", {})
    phone = CIT200Device(
        transport_mode=str(hid_cfg.get("transport_mode", "dual")),
        call_setup_delay=float(hid_cfg.get("q9_q10_delay", 0.2)),
        call_connect_delay=float(hid_cfg.get("call_connect_delay", 0.2)),
        contacts_frame_delay_s=float(hid_cfg.get("contacts_frame_delay_s", 0.05)),
        contacts_contact_delay_s=float(hid_cfg.get("contacts_contact_delay_s", 0.05)),
        contacts_transport_mode=str(hid_cfg.get("contacts_transport_mode", "feature_only")),
    )

    # ── AudioBridge setup ────────────────────────────────────────────
    audio_cfg = cfg.get("audio", {})
    _ml_raw = audio_cfg.get("meter_level", "debug")
    if isinstance(_ml_raw, str):
        _meter_level = getattr(logging, _ml_raw.upper(), logging.DEBUG)
    else:
        _meter_level = int(_ml_raw)
    audio = AudioBridge(
        sample_rate=int(audio_cfg.get("sample_rate", 16000)),
        channels=int(audio_cfg.get("channels", 1)),
        chunk_size=int(audio_cfg.get("chunk_size", 960)),
        meter_enabled=bool(audio_cfg.get("meter_enabled", False)),
        meter_interval_s=float(audio_cfg.get("meter_interval_s", 10.0)),
        meter_level=_meter_level,
    )

    # ── Recording state ──────────────────────────────────────────────
    rec_lock = threading.Lock()
    recorded = bytearray()
    in_call = threading.Event()       # set while call is active
    call_start_time: List[float] = [] # mutable container for start timestamp

    def _on_audio(pcm: bytes) -> None:
        if in_call.is_set():
            with rec_lock:
                recorded.extend(pcm)

    audio.on_audio_captured = _on_audio

    # ── Recording output path ────────────────────────────────────────
    rec_cfg = cfg.get("recording", {})
    rec_dir_raw = str(rec_cfg.get("directory", "recordings") or "recordings")
    rec_dir = Path(rec_dir_raw)
    if not rec_dir.is_absolute():
        rec_dir = Path(__file__).resolve().parent.parent / rec_dir
    rec_dir.mkdir(parents=True, exist_ok=True)
    sample_rate = int(audio_cfg.get("sample_rate", 16000))

    # ── Save WAV helper ──────────────────────────────────────────────
    def _save_recording() -> None:
        with rec_lock:
            pcm_data = bytes(recorded)
            recorded.clear()
        if not pcm_data:
            print("No audio captured — nothing to save.")
            return
        duration = len(pcm_data) / (sample_rate * 2)
        ts = time.strftime("%Y%m%d_%H%M%S")
        wav_path = rec_dir / f"fakecall_{ts}.wav"
        with _wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)          # mono — mic only
            wf.setsampwidth(2)          # 16-bit int16
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)
        print(f"Saved {duration:.1f}s of audio to {wav_path}")

    # ── Open HID device ──────────────────────────────────────────────
    print("Opening CIT200 HID device...")
    if not phone.open():
        print("ERROR: CIT200 handset not found. Is it plugged in?")
        sys.exit(1)
    print("CIT200 connected.")

    # ── Fake time override ───────────────────────────────────────────
    if fake_time:
        try:
            parts = fake_time.strip().split(":")
            ft_hour = int(parts[0]) % 24
            ft_min = int(parts[1]) % 60 if len(parts) > 1 else 0
            phone.set_time_override(ft_hour, ft_min)
            print(f"Handset clock set to {ft_hour:02d}:{ft_min:02d} (fake time).")
        except (ValueError, IndexError):
            print(f"WARNING: invalid --faketime '{fake_time}', expected HH:MM. Using system clock.")

    # ── Build fake contact ───────────────────────────────────────────
    fake_contact = Contact(
        index=0,
        handle=_FAKECALL_NUMBER,
        name=display_name,
        status=Status.ONLINE,
    )

    # ── Wire handset events ──────────────────────────────────────────

    def _on_contacts(idx: int) -> None:
        log.debug("[fakecall] contacts request (index=%d)", idx)
        phone.send_contacts([fake_contact])

    def _on_dial(callee: str) -> None:
        """User dialed from the handset — simulate ringing then connect."""
        if in_call.is_set():
            log.debug("[fakecall] already in call, ignoring dial")
            return
        print(f"Dialing {display_name} ({_FAKECALL_NUMBER})...")

        def _connect_worker():
            # Simulate ringing delay
            time.sleep(1.0)
            # Transition handset from dialing to connected
            phone.confirm_call_connected()
            in_call.set()
            call_start_time.clear()
            call_start_time.append(time.time())
            print(f"Call connected to {display_name} — recording.")

        threading.Thread(
            target=_connect_worker, daemon=True, name="fakecall-connect"
        ).start()

    def _on_hangup() -> None:
        """User pressed hangup on handset — end call, save recording."""
        if not in_call.is_set():
            log.debug("[fakecall] hangup but no active call")
            return
        in_call.clear()
        duration = time.time() - call_start_time[0] if call_start_time else 0
        print(f"\nCall ended after {duration:.1f}s. Saving recording...")
        phone.end_call_from_remote()
        _save_recording()
        print(f"Ready — dial {display_name} again or Ctrl+C to quit.")

    phone.on(Event.CONTACTS_REQUEST, _on_contacts)
    phone.on(Event.DIAL, _on_dial)
    phone.on(Event.END_CALL, _on_hangup)
    phone.on(Event.CALL_BUTTON, lambda: log.debug("[fakecall] CALL button"))

    # ── Start audio streams ──────────────────────────────────────────
    print("Starting audio bridge...")
    audio.start()

    # ── Send initial keepalive + push contact ─────────────────────────
    phone.send_init(Status.ONLINE)
    time.sleep(0.3)
    phone.send_contacts([fake_contact])

    print(f"Ready — contact \"{display_name}\" ({_FAKECALL_NUMBER}) on handset.")
    print("Browse to the contact and dial. Ctrl+C to quit.")

    # ── Main poll loop ───────────────────────────────────────────────
    cycle = 1
    try:
        while True:
            phone.poll()

            if cycle == 1:
                phone.send_time_sync()

            if cycle >= 8:
                cycle = 1
            else:
                cycle += 1

            time.sleep(0.2)
    except KeyboardInterrupt:
        pass

    # ── Teardown ─────────────────────────────────────────────────────
    if in_call.is_set():
        in_call.clear()
        duration = time.time() - call_start_time[0] if call_start_time else 0
        print(f"\nCall ended after {duration:.1f}s. Saving recording...")
        phone.end_call_from_remote()
        _save_recording()
    else:
        print()

    audio.stop()
    phone.close()
    print("Fake call mode stopped.")


# ── Phone mode ────────────────────────────────────────────────────────────────

def _run_phone(cfg: Dict[str, Any]) -> None:
    """
    Full phone mode: Skype UI window + CIT200 + platform + audio.

    Thread layout
    -------------
    Main thread    – tkinter event loop; SkypeUI lives here.
    phone_thread   – PhoneApp.run() poll loop (daemon).
    async_thread   – asyncio event loop for async platform ops (daemon).
    """
    import tkinter as tk
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import skypeui
    from .ui_bridge import UIBridge

    username = cfg.get("username", "john_smith")
    platform = cfg.get("platform", "local")

    skypeui.prepare_windows_dpi_awareness()
    root = tk.Tk()
    ui   = skypeui.SkypeUI(root, username=username)

    ui.set_status("Online", platform_label=platform)

    bridge = UIBridge(root)
    bridge.start()

    app = PhoneApp(cfg, ui=ui, ui_bridge=bridge)

    # ── Telegram auth callbacks (UI dialogs instead of terminal input) ──
    def _tg_ask(title: str, prompt: str, secret: bool = False) -> str:
        result_holder: list = []
        done = threading.Event()

        def _show():
            val = ''
            if hasattr(ui, 'prompt_input'):
                try:
                    val = ui.prompt_input(title, prompt, secret=secret)
                except Exception:
                    log.exception("UI prompt_input failed; falling back to simpledialog")
            if not val:
                from tkinter import simpledialog
                if secret:
                    val = simpledialog.askstring(title, prompt,
                                                 show='*', parent=root)
                else:
                    val = simpledialog.askstring(title, prompt, parent=root)
            result_holder.append(val or '')
            done.set()

        bridge.call(_show)
        done.wait(timeout=300)
        return result_holder[0] if result_holder else ''

    try:
        plat = app._platform
        if hasattr(plat, 'set_auth_callbacks'):
            plat.set_auth_callbacks(
                ask_phone    = lambda: _tg_ask('Telegram', 'Phone number (e.g. +15551234567):'),
                ask_code     = lambda: _tg_ask('Telegram', 'OTP code sent to your phone:'),
                ask_password = lambda: _tg_ask('Telegram', '2FA password:', secret=True),
            )
            log.info("Telegram auth callbacks wired")
    except Exception as e:
        log.warning("Could not wire Telegram auth callbacks: %s", e)

    # ── UI → backend callbacks ─────────────────────────────────────────
    ui.on_call_start  = lambda name: app.place_call_from_ui(name)
    ui.on_call_answer = lambda name: app.answer_call_from_ui(name)
    ui.on_call_end    = lambda name, secs: app.hangup_from_ui()

    ui.on_contact_sel   = lambda name:  log.info("[ui] contact selected: %s", name)
    ui.on_state_change  = lambda state: log.debug("[ui] state -> %s", state)

    def _on_mic_mode(enabled: bool) -> None:
        """Toggle handset mic/speaker passthrough mode."""
        def _worker():
            if enabled:
                log.info("[ui] mic mode ON — starting handset audio passthrough")
                # End any active call first
                app._stop_call_audio()
                app._run_async(app._platform.end_call())
                app._phone.end_call_from_remote()
                with app._call_lock:
                    app._current_call_id = None
                    app._current_call_display_name = None
                    app._incoming_caller = None
                    app._incoming_call_id = None
                import time as _t
                _t.sleep(0.3)
                # Put handset into call-connected state so audio flows
                app._phone.start_local_audio_call()
                _t.sleep(0.5)
                app._phone.confirm_call_connected()
                # Enable audio flow (mic captures, speaker plays)
                app._in_call_audio = True
                log.info("[ui] mic mode active — handset audio live")
            else:
                log.info("[ui] mic mode OFF — stopping handset audio")
                app._in_call_audio = False
                app._phone.end_call_from_remote()
                log.info("[ui] mic mode stopped")
        threading.Thread(target=_worker, daemon=True, name="mic-mode").start()

    ui.on_mic_mode = _on_mic_mode

    def _on_status_change(status: str) -> None:
        log.info("[ui] status -> %s", status)
        app._run_async(app._platform.set_status(status))

    ui.on_status_change = _on_status_change

    def _on_config_save(cfg_dict: Dict[str, Any]) -> None:
        if cfg_dict.get("__action__") == "refresh_contacts":
            log.info("[ui] refresh_contacts requested")
            # Reload config from disk so _order_contacts sees new filters
            app._cfg = load_config(Path(cfg.get("__config_path__", str(CONFIG_PATH))))
            # Reload shim + contacts config
            new_ccfg = app._cfg.get("contacts", {})
            app._phone_id_shim = bool(new_ccfg.get("phone_id_shim", True))
            app._phone_id_shim_prefix = str(new_ccfg.get("phone_id_shim_prefix", "6") or "6")
            app._phone_id_shim_value = str(
                new_ccfg.get("phone_id_shim_value", "6 345 432 236 543 457 4 222")
                or "6 345 432 236 543 457 4 222"
            )
            app._phone_id_shim_salt = str(new_ccfg.get("phone_id_shim_salt", "") or "")
            app._phone_shim_random.clear()  # reset cache after config reload
            # Reload selected contacts filter
            raw_sel = new_ccfg.get("selected_contacts", [])
            if isinstance(raw_sel, str):
                raw_sel = [raw_sel]
            elif not isinstance(raw_sel, (list, tuple, set)):
                raw_sel = []
            app._selected_contact_keys = {
                _normalize_contact_key(v) for v in raw_sel if _normalize_contact_key(v)
            }
            app._selected_only = bool(new_ccfg.get("selected_only", False))
            app._selected_prioritize = bool(new_ccfg.get("selected_prioritize", False))
            app._force_selected_only = bool(new_ccfg.get("force_selected_only", False))
            if app._selected_only and app._selected_contact_keys:
                app._force_selected_only = True
                app._selected_only = False
            app._selected_missing_warned = False
            # Reload detail overrides
            raw_ovr = new_ccfg.get("detail_overrides", [])
            app._contact_detail_overrides = raw_ovr if isinstance(raw_ovr, list) else []
            def _worker():
                # Fully invalidate contacts cache so the next fetch goes to
                # the platform instead of returning stale data.
                with app._contacts_lock:
                    app._contacts_cache = []
                    app._contacts_cache_at = 0.0
                # Also invalidate the phone wire cache
                with app._prebuilt_phone_wire_lock:
                    app._prebuilt_phone_wire = []
                    app._prebuilt_phone_wire_total = 0
                    app._prebuilt_phone_wire_at = 0.0
                log.info("[ui] contacts cache fully invalidated for refresh")

                # Full list for UI (all contacts, no phone filter)
                all_contacts = app._get_ordered_contacts_sync(for_phone=False)
                ui_dicts = [c.as_ui_dict() for c in all_contacts]
                bridge.call(ui.update_contacts, ui_dicts)
                bridge.call(app._push_missed_to_ui)

                # Rebuild pre-built wire cache and push to handset
                app._rebuild_phone_wire_cache()
                sent = app._send_prebuilt_phone_wire()
                if sent:
                    log.info("[ui] Pushed pre-cached contacts to handset (shim=%s, selected=%d)",
                             app._phone_id_shim, len(app._selected_contact_keys))
                else:
                    log.warning("[ui] Failed to push contacts to handset (wire cache empty)")
            threading.Thread(target=_worker, daemon=True,
                             name="refresh-contacts").start()
        else:
            log.info("[ui] config saved by user: %s", list(cfg_dict.keys()))
            # Hot-reload recording settings from saved config
            try:
                app._cfg = load_config(Path(cfg.get("__config_path__", str(CONFIG_PATH))))
                rec_cfg = app._cfg.get("recording", {}) if isinstance(app._cfg, dict) else {}
                app._recording_enabled = bool(rec_cfg.get("enabled", True))
                app._auto_record_calls = bool(rec_cfg.get("auto_record_calls", True))
                rec_dir_raw = str(rec_cfg.get("directory", "recordings") or "recordings")
                rec_dir = Path(rec_dir_raw)
                if not rec_dir.is_absolute():
                    rec_dir = Path(__file__).parent.parent / rec_dir
                app._recording_dir = rec_dir
            except Exception as e:
                log.warning("[ui] failed to hot-reload recording settings: %s", e)

    ui.on_config_save = _on_config_save

    # ── Initial contact load (off main thread) ─────────────────────────
    def _initial_load_worker():
        import time as _time
        log.debug("initial-load: waiting 1.5s for platform to connect…")
        _time.sleep(1.5)

        # For Telegram: wait until auth finishes
        auth_event = getattr(app._platform, '_auth_ready', None)
        if auth_event is not None and not auth_event.is_set():
            log.info("initial-load: waiting for Telegram auth to complete…")
            bridge.call(lambda: ui.set_status("Authenticating…",
                                              platform_label=platform))
            auth_event.wait(timeout=300)
            if not auth_event.is_set():
                log.error("initial-load: Telegram auth timed out after 5 min")
                bridge.call(lambda: ui.set_status("Auth timed out",
                                                  platform_label=platform))
                return
            log.info("initial-load: auth complete, fetching contacts")
            bridge.call(lambda: ui.set_status("Online", platform_label=platform))
            _time.sleep(1.5)

        # Force-invalidate contacts cache on every startup so we always
        # fetch a fresh list from the platform.
        with app._contacts_lock:
            app._contacts_cache = []
            app._contacts_cache_at = 0.0
        log.debug("initial-load: cache invalidated, calling _get_ordered_contacts…")
        try:
            contacts = app._get_ordered_contacts()
            for _retry in range(6):
                if contacts:
                    break
                log.debug("initial-load: contacts empty, retrying in 1s… (attempt %d)", _retry + 1)
                _time.sleep(1.0)
                with app._contacts_lock:
                    app._contacts_cache = []
                    app._contacts_cache_at = 0.0
                contacts = app._get_ordered_contacts()
            log.debug("initial-load: got %d contacts", len(contacts))
        except Exception as e:
            log.error("initial-load: _get_ordered_contacts raised: %s", e, exc_info=True)
            contacts = []
        ui_dicts = [c.as_ui_dict() for c in contacts]
        def _apply():
            ui.update_contacts(ui_dicts)
            app._push_missed_to_ui()
            if ui.state == 'log' and ui_dicts:
                ui._switch('friends')
        log.debug("initial-load: pushing %d contacts to UI", len(ui_dicts))
        bridge.call(_apply)

        # Prime the pre-built wire cache and push to handset on startup
        if contacts:
            try:
                app._rebuild_phone_wire_cache()
                sent = app._send_prebuilt_phone_wire()
                if sent:
                    log.info("initial-load: primed wire cache and pushed contacts to handset")
                else:
                    log.warning("initial-load: wire cache empty after rebuild")
            except Exception as e:
                log.warning("initial-load: failed to prime wire cache: %s", e)

    threading.Thread(
        target=_initial_load_worker, daemon=True, name="initial-load"
    ).start()

    # ── Shutdown ───────────────────────────────────────────────────────
    def _on_close():
        log.info("Window closing – stopping PhoneApp")
        bridge.stop()
        app.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)

    # ── Start backend ──────────────────────────────────────────────────
    threading.Thread(target=app.run, daemon=True, name="phone-app").start()

    log.info("Not Skype UI ready  (platform=%s  username=%s)", platform, username)
    root.mainloop()


# ── Recorder mode ─────────────────────────────────────────────────────────────

def _run_recorder(
    cfg: Dict[str, Any],
    hid_write_mode: str = "dual",
    call_delay_a: float = 0.2,
    call_delay_b: float = 0.2,
    hid_trace: str = "",
    hid_trace_window: float = 3.0,
) -> None:
    """Recorder mode using the full RecorderTUI."""
    RecorderTUI(
        hid_write_mode=hid_write_mode,
        call_delay_a=call_delay_a,
        call_delay_b=call_delay_b,
        hid_trace_path=hid_trace,
        hid_trace_window=hid_trace_window,
    ).start()


# ── GUI / control-center mode ─────────────────────────────────────────────────

def _run_gui(cfg: Dict[str, Any]) -> None:
    try:
        from control_center import launch_gui
        launch_gui(cfg)
    except ImportError:
        log.warning("control_center not available – falling back to phone mode")
        _run_phone(cfg)


if __name__ == "__main__":
    main()
