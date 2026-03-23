"""platforms/telegram.py – Legacy Telegram backend (Pyrogram + py-tgcalls).

This is the alternate/legacy backend.  The primary production backend is
telegram_ntg.py (Telethon + NTgCalls).  Use this as a fallback or for
environments where py-tgcalls is available but ntgcalls is not.

Dependencies (required for this platform):
    pip install pyrogram py-tgcalls

Config keys used (under "telegram"):
    api_id       – Telegram app API ID (int)
    api_hash     – Telegram app API hash (str)
    session_name – Pyrogram session file name stem (str)
    phone        – Account phone number for interactive auth (str, optional)

Environment overrides:
    TG_API_ID, TG_API_HASH, TG_PHONE, TG_SESSION
"""

from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
import time
from typing import List, Optional

from .base import PlatformContact, VoicePlatform

log = logging.getLogger(__name__)

# ── Optional deps ──────────────────────────────────────────────────────────────
try:
    from pyrogram import Client as PyroClient
    from pyrogram import filters
    from pyrogram.handlers import RawUpdateHandler
    import pyrogram.raw.functions.phone as pyro_phone
    import pyrogram.raw.types as pyro_types
    HAS_PYROGRAM = True
except ImportError:
    HAS_PYROGRAM = False
    log.warning("pyrogram not installed – TelegramPlatform (legacy) unavailable")

try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types.input_stream import AudioPiped
    HAS_PYTGCALLS = True
except ImportError:
    HAS_PYTGCALLS = False
    log.warning("py-tgcalls not installed – audio unavailable for legacy Telegram backend")

_CONTACTS_TTL = 300.0   # seconds


def _pyro_user_name(user) -> str:
    parts = [
        getattr(user, "first_name", "") or "",
        getattr(user, "last_name",  "") or "",
    ]
    name = " ".join(p for p in parts if p).strip()
    return name or (getattr(user, "username", "") or str(getattr(user, "id", "")))


class TelegramPlatform(VoicePlatform):
    """
    Legacy Telegram backend using Pyrogram for signaling and py-tgcalls for
    audio.  For production use, prefer TelegramNTGPlatform (telegram_ntg.py).

    Audio is fed to py-tgcalls via a temporary raw PCM pipe file.
    """

    def __init__(self, cfg: dict):
        tg_cfg = cfg.get("telegram", {})
        _api_id_raw = os.environ.get("TG_API_ID") or tg_cfg.get("api_id", 0)
        self._api_id: int   = int(_api_id_raw) if _api_id_raw else 0
        self._api_hash: str = str(os.environ.get("TG_API_HASH") or tg_cfg.get("api_hash", ""))
        self._phone: Optional[str] = (
            os.environ.get("TG_PHONE") or tg_cfg.get("phone") or None
        )
        self._session: str = (
            os.environ.get("TG_SESSION") or
            tg_cfg.get("session_name", "skype_session_pyro")
        )

        self._contacts_cache: List[PlatformContact] = []
        self._contacts_cache_at: float = 0.0
        self._contacts_lock = threading.Lock()
        self._cache_ttl: float = float(
            cfg.get("contacts", {}).get("cache_ttl_s", _CONTACTS_TTL)
        )

        self._app: Optional["PyroClient"]   = None   # type: ignore[name-defined]
        self._calls: Optional["PyTgCalls"]  = None   # type: ignore[name-defined]
        self._in_call   = False
        self._held      = False
        self._status    = "Online"
        self._current_chat_id: Optional[int] = None
        self._last_incoming_target: Optional[str] = None

        # Audio pipe path for py-tgcalls
        self._audio_pipe_path: Optional[str] = None
        self._audio_pipe_fh = None
        self._audio_lock = threading.Lock()

        if not HAS_PYROGRAM:
            raise ImportError(
                "pyrogram is required for the legacy Telegram backend. "
                "Install it: pip install pyrogram"
            )
        if not self._api_id or not self._api_hash:
            raise ValueError(
                "telegram.api_id and telegram.api_hash must be configured."
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════════════════

    def connect(self) -> None:
        threading.Thread(
            target=self._run_sync, daemon=True, name="tg-pyro-loop"
        ).start()

    def disconnect(self) -> None:
        if self._app:
            try:
                self._app.stop()
            except Exception:
                pass
        self._close_pipe()

    # ═══════════════════════════════════════════════════════════════════════════
    # Call control
    # ═══════════════════════════════════════════════════════════════════════════

    def place_call(self, target: str) -> None:
        if self._app is None or not HAS_PYTGCALLS:
            log.error("[tg-pyro] cannot place call – not connected or py-tgcalls missing")
            return
        threading.Thread(
            target=self._sync_place_call, args=(target,), daemon=True
        ).start()

    def answer_call(self) -> None:
        log.info("[tg-pyro] answer_call (signaling only in legacy backend)")
        self._in_call = True
        self._open_pipe()
        if self.on_call_answered:
            self.on_call_answered()

    def end_call(self) -> None:
        log.info("[tg-pyro] end_call")
        self._in_call = False
        self._held    = False
        self._close_pipe()
        if self._calls and self._current_chat_id:
            threading.Thread(
                target=self._sync_end_call, daemon=True
            ).start()
        if self.on_call_ended:
            self.on_call_ended()

    def hold_call(self) -> None:
        self._held = not self._held
        log.info("[tg-pyro] hold=%s", self._held)

    # ═══════════════════════════════════════════════════════════════════════════
    # Media
    # ═══════════════════════════════════════════════════════════════════════════

    def send_audio(self, pcm: bytes) -> None:
        if not self._in_call or self._held:
            return
        with self._audio_lock:
            if self._audio_pipe_fh:
                try:
                    self._audio_pipe_fh.write(pcm)
                    self._audio_pipe_fh.flush()
                except OSError as e:
                    log.debug("[tg-pyro] audio pipe write error: %s", e)

    # ═══════════════════════════════════════════════════════════════════════════
    # Contacts / presence
    # ═══════════════════════════════════════════════════════════════════════════

    def get_contacts(self) -> List[PlatformContact]:
        now = time.monotonic()
        with self._contacts_lock:
            if self._contacts_cache and (now - self._contacts_cache_at) < self._cache_ttl:
                return list(self._contacts_cache)
        # Refresh synchronously from caller thread (blocking is OK for background refresh)
        if self._app:
            threading.Thread(
                target=self._sync_refresh_contacts, daemon=True
            ).start()
        with self._contacts_lock:
            return list(self._contacts_cache)

    def get_status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        self._status = status

    def get_contact_bio(self, contact: PlatformContact) -> str:
        return ""   # Not implemented in legacy backend

    def get_last_incoming_target(self) -> Optional[str]:
        return self._last_incoming_target

    # ═══════════════════════════════════════════════════════════════════════════
    # Private — sync helpers (run in daemon threads)
    # ═══════════════════════════════════════════════════════════════════════════

    def _run_sync(self) -> None:
        """Start Pyrogram client + py-tgcalls and idle."""
        self._app = PyroClient(
            self._session,
            api_id=self._api_id,
            api_hash=self._api_hash,
        )
        if HAS_PYTGCALLS:
            self._calls = PyTgCalls(self._app)
            self._calls.on_closed_voice_chat()(self._on_call_closed)

        self._app.start()
        log.info("[tg-pyro] connected as %s", self._app.get_me().username)

        self._sync_refresh_contacts()

        if HAS_PYTGCALLS:
            self._calls.start()

        log.info("[tg-pyro] ready")
        self._app.idle()

    def _sync_place_call(self, target: str) -> None:
        try:
            chat = self._app.get_chat(target)
            chat_id = chat.id
        except Exception as e:
            log.error("[tg-pyro] can't resolve target %r: %s", target, e)
            return

        self._current_chat_id = chat_id
        self._open_pipe()
        self._in_call = True
        log.info("[tg-pyro] joining call for chat_id=%d", chat_id)
        try:
            self._calls.join_group_call(
                chat_id,
                AudioPiped(self._audio_pipe_path),
            )
            if self.on_call_answered:
                self.on_call_answered()
        except Exception:
            log.exception("[tg-pyro] join_group_call failed")
            self._in_call = False
            self._close_pipe()

    def _sync_end_call(self) -> None:
        try:
            if self._calls and self._current_chat_id:
                self._calls.leave_group_call(self._current_chat_id)
        except Exception as e:
            log.debug("[tg-pyro] leave_group_call error: %s", e)
        finally:
            self._current_chat_id = None

    def _sync_refresh_contacts(self) -> None:
        if self._app is None:
            return
        try:
            contacts = []
            for c in self._app.get_contacts():
                user_id = getattr(c, "id", None) or getattr(c, "user_id", None)
                if user_id is None:
                    continue
                name   = _pyro_user_name(c)
                handle = getattr(c, "username", None) or str(user_id)
                status = getattr(c, "status", None)
                online = (str(status) == "UserStatus.ONLINE") if status else False
                contacts.append(PlatformContact(
                    id=str(user_id),
                    name=name,
                    handle=handle,
                    status=1 if online else 0,
                    online=online,
                ))
            with self._contacts_lock:
                self._contacts_cache    = contacts
                self._contacts_cache_at = time.monotonic()
            log.info("[tg-pyro] contacts refreshed: %d total", len(contacts))
        except Exception:
            log.exception("[tg-pyro] contact refresh failed")

    # ── Audio pipe lifecycle ───────────────────────────────────────────────────

    def _open_pipe(self) -> None:
        """Open a raw PCM temp file to feed into py-tgcalls."""
        self._close_pipe()
        tf = tempfile.NamedTemporaryFile(
            suffix=".raw", delete=False, prefix="tg_audio_"
        )
        self._audio_pipe_path = tf.name
        self._audio_pipe_fh   = tf
        log.debug("[tg-pyro] audio pipe: %s", self._audio_pipe_path)

    def _close_pipe(self) -> None:
        with self._audio_lock:
            if self._audio_pipe_fh:
                try:
                    self._audio_pipe_fh.close()
                except OSError:
                    pass
                try:
                    os.unlink(self._audio_pipe_path)  # type: ignore[arg-type]
                except OSError:
                    pass
                self._audio_pipe_fh   = None
                self._audio_pipe_path = None

    # ── py-tgcalls event ──────────────────────────────────────────────────────

    def _on_call_closed(self, _client, _chat_id) -> None:
        log.info("[tg-pyro] call closed by remote")
        self._in_call = False
        self._held    = False
        self._close_pipe()
        if self.on_call_ended:
            self.on_call_ended()
