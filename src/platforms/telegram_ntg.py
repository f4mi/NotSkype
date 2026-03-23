"""
Experimental Telegram private-call backend for CIT200.

Uses:
- Telethon (MTProto signaling)
- ntgcalls (media engine)

This is intentionally compact and focused on CIT200 integration.
"""

import asyncio
import concurrent.futures
import getpass
import inspect
import logging
import random
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .base import VoicePlatform, PlatformContact
from ..cit200 import Status

log = logging.getLogger(__name__)

DIAL_PRIVACY_SEQUENCE = "6 345 432 236 543 457 4 222"
DIAL_PRIVACY_SPEED_X = 3


TELEGRAM_STATUS_MAP = {
    "online": Status.ONLINE,
    "offline": Status.OFFLINE,
    "recently": Status.AWAY,
    "within_week": Status.AWAY,
    "within_month": Status.NA,
    "long_time_ago": Status.OFFLINE,
}


class TelegramNTGPlatform(VoicePlatform):
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        phone: str = "",
        session_name: str = "cit200_ntg",
        sample_rate: int = 16000,
        channels: int = 1,
    ):
        super().__init__()
        self._api_id = int(api_id or 0)
        self._api_hash = api_hash or ""
        self._phone = phone or ""
        self._session_name = session_name
        self._session_path = self._build_session_path(session_name)
        self._sample_rate = int(sample_rate)
        self._channels = int(channels)

        self._connected = False
        self._in_call = False

        self._client = None
        self._ntg = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._self_user_id: Optional[int] = None

        self._current_user_id: Optional[int] = None
        self._current_call_obj = None
        self._input_calls = {}
        self._p2p = {}
        self._p2p_ready_users: set[int] = set()
        self._pending_incoming_call = None
        self._pending_incoming_peer = None
        self._pending_incoming_user_id: Optional[int] = None
        self._last_incoming_target = ""
        self._key_ready_calls: set[int] = set()
        self._call_direction: Optional[str] = None

        self._contacts_cache: list[PlatformContact] = []
        self._contacts_cache_at = 0.0
        self._contacts_cache_ttl_s = 300.0
        self._contacts_lock: Optional[asyncio.Lock] = None
        self._about_cache: dict[str, str] = {}

        # Outbound external-audio pacing (ntgcalls expects 10ms PCM16 frames)
        frame_bytes = int((self._sample_rate * self._channels * 2) / 100)
        self._tx_frame_bytes = max(2, frame_bytes)
        self._tx_audio_buffer = bytearray()
        self._tx_next_ts_ms: Optional[int] = None
        self._tx_lock: Optional[asyncio.Lock] = None
        self._last_audio_send_error_at = 0.0

        # Auth callbacks (set by orchestrator for GUI-based auth instead of stdin)
        self._auth_ask_phone: Optional[Callable] = None
        self._auth_ask_code: Optional[Callable] = None
        self._auth_ask_password: Optional[Callable] = None
        self._auth_ready = threading.Event()

        # Runtime media counters (helps diagnose one-way/no-audio calls).
        self._audio_stats_lock = threading.Lock()
        self._audio_stats_interval_s = 1.0
        self._audio_stats_last_log = time.monotonic()
        self._audio_stats = {
            "tx_frames": 0,
            "tx_bytes": 0,
            "tx_drop_bytes": 0,
            "tx_errors": 0,
            "tx_queue_bytes": 0,
            "rx_frames": 0,
            "rx_bytes": 0,
        }

    def set_auth_callbacks(
        self,
        ask_phone: Optional[Callable] = None,
        ask_code: Optional[Callable] = None,
        ask_password: Optional[Callable] = None,
    ) -> None:
        """Set GUI-based auth callbacks. When set, _ensure_authorized
        will use these instead of stdin input()/getpass."""
        self._auth_ask_phone = ask_phone
        self._auth_ask_code = ask_code
        self._auth_ask_password = ask_password

    @property
    def platform_name(self) -> str:
        return "TelegramNTG"

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_in_call(self) -> bool:
        return self._in_call

    async def connect(self) -> None:
        try:
            from telethon import TelegramClient, events
            import ntgcalls
        except ImportError as e:
            raise RuntimeError(
                "Install dependencies: pip install telethon ntgcalls"
            ) from e

        if not self._api_id or not self._api_hash:
            raise RuntimeError("telegram.api_id and telegram.api_hash are required")
        if self._api_id == 12345678 or self._api_hash == "0123456789abcdef0123456789abcdef":
            raise RuntimeError("telegram.api_id/api_hash are sample placeholders; set real values in config.json")

        self._loop = asyncio.get_running_loop()
        self._client = TelegramClient(self._session_path, self._api_id, self._api_hash)
        await self._client.connect()

        # Try non-interactive first; fall back to interactive if callbacks are wired
        try:
            await self._ensure_authorized(interactive=False)
        except RuntimeError:
            if self._auth_ask_code is not None:
                log.info("Session not authorized; starting interactive auth via callbacks")
                await self._ensure_authorized(interactive=True)
            else:
                raise

        self._auth_ready.set()

        me = await self._client.get_me()
        self._self_user_id = int(getattr(me, "id", 0) or 0) or None
        log.info("TelegramNTG connected as %s (@%s)", getattr(me, "first_name", "me"), getattr(me, "username", ""))

        self._ntg = ntgcalls.NTgCalls()
        self._wire_ntg_callbacks(ntgcalls)

        @self._client.on(events.Raw)
        async def _raw(update):
            await self._handle_raw_update(update)

        # Touch protocol once (also validates native lib loads correctly).
        _ = ntgcalls.NTgCalls.get_protocol()

        self._connected = True

    async def authorize_only(self) -> None:
        """Interactive auth bootstrap mode: create/refresh session and exit."""
        try:
            from telethon import TelegramClient
        except ImportError as e:
            raise RuntimeError("Install dependencies: pip install telethon") from e

        if not self._api_id or not self._api_hash:
            raise RuntimeError("telegram.api_id and telegram.api_hash are required")
        if self._api_id == 12345678 or self._api_hash == "0123456789abcdef0123456789abcdef":
            raise RuntimeError("telegram.api_id/api_hash are sample placeholders; set real values in config.json")

        self._client = TelegramClient(self._session_path, self._api_id, self._api_hash)
        await self._client.connect()
        await self._ensure_authorized(interactive=True)
        me = await self._client.get_me()
        log.info(
            "Telegram auth successful as %s (@%s)",
            getattr(me, "first_name", "me"),
            getattr(me, "username", ""),
        )
        await self._client.disconnect()
        self._client = None

    async def disconnect(self) -> None:
        if self._in_call:
            await self.end_call()
        if self._client:
            await self._client.disconnect()
        self._connected = False

    async def place_call(self, target: str) -> None:
        if not self._client or not self._ntg:
            return

        private_target = f"privacy({DIAL_PRIVACY_SPEED_X}x): {DIAL_PRIVACY_SEQUENCE}"

        from telethon import errors, functions, types, utils
        import ntgcalls

        try:
            self._clear_pending_incoming()
            self._reset_outbound_audio_state()
            self._call_direction = "outgoing"
            user = await self._resolve_user(target)
            if not user:
                log.error("TelegramNTG: cannot resolve target %s", private_target)
                return

            user_id = int(user.id)
            self._current_user_id = user_id

            dh = await self._client(functions.messages.GetDhConfigRequest(version=0, random_length=256))
            await self._ensure_p2p_session(user_id)

            request_hash = await self._await_ntg(
                self._ntg.init_exchange(
                    user_id,
                    self._build_dh_config(dh),
                    None,
                )
            )
            request_hash = await self._to_bytes_payload(request_hash, "init_exchange")
            log.debug("TelegramNTG: outbound request hash ready (len=%d)", len(request_hash))

            protocol = self._build_protocol(types)
            random_id = random.randint(1, 2**31 - 1)
            input_user = utils.get_input_user(user)

            res = await self._client(
                functions.phone.RequestCallRequest(
                    user_id=input_user,
                    g_a_hash=request_hash,
                    protocol=protocol,
                    random_id=random_id,
                    video=False,
                )
            )

            call_obj = getattr(res, "phone_call", None)
            if call_obj is not None and hasattr(call_obj, "id") and hasattr(call_obj, "access_hash"):
                self._input_calls[user_id] = types.InputPhoneCall(id=call_obj.id, access_hash=call_obj.access_hash)
                self._current_call_obj = call_obj
                await self._connect_p2p_from_phone_call(call_obj, user_id_hint=user_id, source="request_call")

            self._in_call = True
            log.info("TelegramNTG: call requested to %s", private_target)
        except errors.UserPrivacyRestrictedError as e:
            self._in_call = False
            self._call_direction = None
            self._current_call_obj = None
            self._current_user_id = None
            self._reset_outbound_audio_state()
            log.error(
                "TelegramNTG place_call blocked by target privacy settings target=%s err=%s",
                private_target,
                e,
            )
            if self._on_call_ended:
                self._on_call_ended()
        except Exception as e:
            self._in_call = False
            self._call_direction = None
            self._current_call_obj = None
            self._current_user_id = None
            self._reset_outbound_audio_state()
            log.exception("TelegramNTG place_call failed target=%s err=%s", private_target, e)
            if self._on_call_ended:
                self._on_call_ended()

    async def answer_call(self, call_id: str = "") -> None:
        if not self._client or not self._ntg:
            return

        from telethon import functions, types as tl_types

        req = self._pending_incoming_call
        peer = self._pending_incoming_peer
        user_id = int(self._pending_incoming_user_id or 0)

        if req is None or peer is None or not user_id:
            log.debug("TelegramNTG answer_call ignored: no pending incoming call")
            return

        req_id = int(getattr(req, "id", 0) or 0)
        if call_id:
            try:
                asked = int(call_id)
                if asked and req_id and asked != req_id:
                    log.debug("TelegramNTG answer_call call_id mismatch: asked=%s pending=%s", asked, req_id)
            except Exception:
                pass

        try:
            await self._ensure_p2p_session(user_id)

            dh = await self._client(functions.messages.GetDhConfigRequest(version=0, random_length=256))
            req_hash = await self._to_bytes_payload(getattr(req, "g_a_hash", b""), "phone_call_requested.g_a_hash")

            g_b = await self._to_bytes_payload(
                self._ntg.init_exchange(
                    user_id,
                    self._build_dh_config(dh),
                    req_hash,
                ),
                "init_exchange_incoming",
            )

            protocol = getattr(req, "protocol", None) or self._build_protocol(tl_types)
            res = await self._client(
                functions.phone.AcceptCallRequest(
                    peer=peer,
                    g_b=g_b,
                    protocol=protocol,
                )
            )

            self._input_calls[user_id] = peer
            self._current_user_id = user_id
            self._current_call_obj = getattr(res, "phone_call", None) or req
            self._in_call = True
            self._call_direction = "incoming"
            log.info("TelegramNTG: incoming call accepted (user_id=%s call_id=%s)", user_id, req_id)

            self._clear_pending_incoming()

            accepted_call = getattr(res, "phone_call", None)
            if accepted_call is not None:
                await self._connect_p2p_from_phone_call(accepted_call, user_id_hint=user_id, source="accept_call")
        except Exception as e:
            self._in_call = False
            self._call_direction = None
            log.exception("TelegramNTG answer_call failed user_id=%s call_id=%s err=%s", user_id, req_id, e)

    async def end_call(self) -> None:
        if not self._client:
            self._in_call = False
            self._reset_outbound_audio_state()
            return

        try:
            from telethon import functions, types
            if self._current_call_obj is not None:
                await self._client(
                    functions.phone.DiscardCallRequest(
                        peer=types.InputPhoneCall(
                            id=self._current_call_obj.id,
                            access_hash=self._current_call_obj.access_hash,
                        ),
                        duration=0,
                        reason=types.PhoneCallDiscardReasonHangup(),
                        connection_id=0,
                        video=False,
                    )
                )
        except Exception as e:
            log.debug("TelegramNTG end_call warning: %s", e)

        if self._ntg and self._current_user_id is not None:
            try:
                await self._await_ntg(self._ntg.stop(self._current_user_id))
            except Exception:
                pass

        self._in_call = False
        self._call_direction = None
        self._current_call_obj = None
        self._current_user_id = None
        self._clear_pending_incoming()
        self._reset_outbound_audio_state()

    async def hold_call(self) -> None:
        if not self._ntg or self._current_user_id is None:
            return
        try:
            await self._await_ntg(self._ntg.pause(self._current_user_id))
        except Exception as e:
            log.debug("TelegramNTG hold warning: %s", e)

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if not self._ntg or self._current_user_id is None or not self._in_call:
            return
        import ntgcalls
        try:
            if not pcm_chunk:
                return

            if self._tx_lock is None:
                self._tx_lock = asyncio.Lock()

            async with self._tx_lock:
                self._tx_audio_buffer.extend(pcm_chunk)
                frame_ms = 10
                max_buffer = self._tx_frame_bytes * 100  # Cap at ~1s queued audio.
                sent_frames = 0
                sent_bytes = 0
                dropped_bytes = 0

                if self._tx_next_ts_ms is None:
                    self._tx_next_ts_ms = int(time.monotonic() * 1000)

                while len(self._tx_audio_buffer) >= self._tx_frame_bytes:
                    frame = bytes(self._tx_audio_buffer[: self._tx_frame_bytes])
                    del self._tx_audio_buffer[: self._tx_frame_bytes]

                    frame_data = ntgcalls.FrameData(int(self._tx_next_ts_ms), 0, 0, 0)
                    self._tx_next_ts_ms += frame_ms

                    await self._await_ntg(
                        self._ntg.send_external_frame(
                            self._current_user_id,
                            ntgcalls.StreamDevice.MICROPHONE,
                            frame,
                            frame_data,
                        )
                    )
                    sent_frames += 1
                    sent_bytes += len(frame)

                if len(self._tx_audio_buffer) > max_buffer:
                    drop = len(self._tx_audio_buffer) - max_buffer
                    del self._tx_audio_buffer[:drop]
                    self._tx_next_ts_ms = int(time.monotonic() * 1000)
                    dropped_bytes += int(drop)
                    log.debug("TelegramNTG: dropped %d buffered mic bytes", drop)

                self._note_audio_stats(
                    tx_frames=sent_frames,
                    tx_bytes=sent_bytes,
                    tx_drop_bytes=dropped_bytes,
                    queue_bytes=len(self._tx_audio_buffer),
                )
        except Exception as e:
            self._note_audio_stats(tx_errors=1)
            now = time.monotonic()
            if now - self._last_audio_send_error_at > 2.0:
                self._last_audio_send_error_at = now
                log.debug("TelegramNTG send_audio warning: %s", e)

    async def get_contacts(self) -> list[PlatformContact]:
        if not self._client:
            return []

        now = time.monotonic()
        if self._contacts_cache and (now - self._contacts_cache_at) <= self._contacts_cache_ttl_s:
            return list(self._contacts_cache)

        if self._contacts_lock is None:
            self._contacts_lock = asyncio.Lock()

        async with self._contacts_lock:
            now = time.monotonic()
            if self._contacts_cache and (now - self._contacts_cache_at) <= self._contacts_cache_ttl_s:
                return list(self._contacts_cache)

            refreshed = await self._fetch_contacts_via_telethon()
            if refreshed:
                self._contacts_cache = refreshed
                self._contacts_cache_at = time.monotonic()
            elif not self._contacts_cache:
                self._contacts_cache_at = time.monotonic()

            return list(self._contacts_cache)

    async def _fetch_contacts_via_telethon(self) -> list[PlatformContact]:
        if not self._client:
            return []

        try:
            if not self._client.is_connected():
                await self._client.connect()
            if not await self._client.is_user_authorized():
                log.error("TelegramNTG: session unauthorized (run with --telegram-auth)")
                return []
        except Exception as e:
            log.error("TelegramNTG: reconnect before contacts failed: %s", e)
            return []

        users_by_id: dict[int, PlatformContact] = {}
        contacts_count = 0
        dialogs_count = 0

        # Address-book contacts via raw API (Telethon 1.42 has no iter_contacts).
        try:
            from telethon import functions

            res = await self._client(functions.contacts.GetContactsRequest(hash=0))
            for user in (getattr(res, "users", None) or []):
                if getattr(user, "bot", False):
                    continue
                if getattr(user, "deleted", False):
                    continue
                pc = self._to_platform_contact(user)
                if pc:
                    users_by_id[int(pc.id)] = pc
                    contacts_count += 1
        except Exception as e:
            log.debug("TelegramNTG GetContactsRequest warning: %s", e)

        # Fallback: include user dialogs only when address-book contacts are empty.
        if contacts_count == 0:
            try:
                async for d in self._client.iter_dialogs():
                    ent = getattr(d, "entity", None)
                    if ent is None:
                        continue
                    if getattr(ent, "bot", False):
                        continue
                    if not hasattr(ent, "id"):
                        continue
                    # Keep only user peers here; no channels/groups for P2P calls.
                    if ent.__class__.__name__.lower().find("user") == -1:
                        continue
                    pc = self._to_platform_contact(ent)
                    if pc:
                        users_by_id[int(pc.id)] = pc
                        dialogs_count += 1
            except Exception as e:
                log.debug("TelegramNTG iter_dialogs warning: %s", e)
        else:
            log.debug("TelegramNTG: skip dialogs fallback (getcontacts=%d)", contacts_count)

        out = list(users_by_id.values())
        out.sort(
            key=lambda c: (
                1 if int(c.status) == int(Status.OFFLINE) else 0,
                (c.name or c.handle or c.id or "").lower(),
                (c.handle or c.id or "").lower(),
            )
        )
        log.info(
            "TelegramNTG contacts sources: getcontacts=%d dialogs=%d exported=%d",
            contacts_count,
            dialogs_count,
            len(out),
        )
        log.info("TelegramNTG contacts cached: %d", len(out))
        return out

    async def _resolve_caller_label(self, user_id: int) -> str:
        uid = int(user_id or 0)
        if uid <= 0:
            return str(user_id)

        uid_s = str(uid)

        # 1) Fast path: current contacts cache.
        for c in self._contacts_cache:
            if str(getattr(c, "id", "")) == uid_s:
                name = (getattr(c, "name", "") or "").strip()
                handle = (getattr(c, "handle", "") or "").strip()
                handle_is_numeric = handle.isdigit()
                if name and handle and (not handle_is_numeric) and name.lower() != handle.lower():
                    return f"{name} ({handle})"
                return name or ("" if handle_is_numeric else handle) or uid_s

        # 2) Telegram entity lookup.
        if self._client:
            try:
                from telethon import types as tl_types

                ent = await self._client.get_entity(tl_types.PeerUser(uid))
                pc = self._to_platform_contact(ent)
                if pc:
                    name = (pc.name or "").strip()
                    handle = (pc.handle or "").strip()
                    handle_is_numeric = handle.isdigit()
                    if name and handle and (not handle_is_numeric) and name.lower() != handle.lower():
                        return f"{name} ({handle})"
                    return name or ("" if handle_is_numeric else handle) or uid_s
            except Exception:
                pass

        return uid_s

    def _to_platform_contact(self, user) -> Optional[PlatformContact]:
        try:
            uid = int(getattr(user, "id"))
        except Exception:
            return None

        first = getattr(user, "first_name", "") or ""
        last = getattr(user, "last_name", "") or ""
        name = (first + " " + last).strip()
        username = getattr(user, "username", None)
        handle = username or str(uid)

        status = Status.OFFLINE
        st = str(getattr(user, "status", "")).lower()
        for k, v in TELEGRAM_STATUS_MAP.items():
            if k in st:
                status = v
                break

        return PlatformContact(
            id=str(uid),
            handle=str(handle),
            name=name or str(handle),
            status=int(status),
        )

    async def get_status(self) -> str:
        return "online"

    async def set_status(self, status: str) -> None:
        log.info("TelegramNTG: status set requested (%s), ignored", status)

    async def get_contact_bio(self, contact: PlatformContact) -> str:
        if not self._client:
            return ""

        key = str(getattr(contact, "id", "") or getattr(contact, "handle", "") or "").strip()
        if not key:
            return ""

        cached = self._about_cache.get(key)
        if cached is not None:
            return cached

        try:
            from telethon import functions, types as tl_types

            ent = None
            try:
                ent = await self._client.get_entity(tl_types.PeerUser(int(key)))
            except Exception:
                pass

            if ent is None:
                try:
                    ent = await self._client.get_entity(str(getattr(contact, "handle", "") or key))
                except Exception:
                    ent = None

            if ent is None:
                self._about_cache[key] = ""
                return ""

            full = await self._client(functions.users.GetFullUserRequest(id=ent))
            about = str(getattr(getattr(full, "full_user", None), "about", "") or "").strip()
            self._about_cache[key] = about
            return about
        except Exception as e:
            log.debug("TelegramNTG get_contact_bio failed key=%s err=%s", key, e)
            self._about_cache[key] = ""
            return ""

    def get_last_incoming_target(self) -> str:
        return str(self._last_incoming_target or "")

    async def _resolve_user(self, target: str):
        if not self._client:
            return None
        cleaned = str(target or "").strip()
        if not cleaned:
            return None

        try:
            return await self._client.get_entity(cleaned)
        except Exception:
            pass

        if not cleaned.startswith("@"):
            try:
                return await self._client.get_entity("@" + cleaned)
            except Exception:
                pass

        # Fallback for contact handles that are numeric IDs.
        try:
            tid = int(cleaned)
            return await self._client.get_entity(tid)
        except Exception:
            pass

        # Last fallback: contacts lookup for truncated/alias text from handset.
        norm_target = "".join(cleaned.lower().split())
        try:
            contacts = await self.get_contacts()
            for c in contacts:
                keys = {
                    "".join(str(c.handle or "").lower().split()),
                    "".join(str(c.id or "").lower().split()),
                    "".join(str(c.name or "").lower().split()),
                }
                if norm_target in keys:
                    return await self._client.get_entity(c.id)

            # Prefix fallback (common with CIT200 handle truncation)
            for c in contacts:
                handle_key = "".join(str(c.handle or "").lower().split())
                if handle_key.startswith(norm_target) and c.id:
                    return await self._client.get_entity(c.id)
        except Exception as e:
            log.debug("TelegramNTG resolve fallback warning: %s", e)

        return None

    def _wire_ntg_callbacks(self, ntgcalls_mod):
        playback_mode = getattr(getattr(ntgcalls_mod, "StreamMode", None), "PLAYBACK", None)
        mic_device = getattr(getattr(ntgcalls_mod, "StreamDevice", None), "MICROPHONE", None)

        # Outbound signaling from ntgcalls -> Telegram phone.SendSignalingData
        def on_signal(user_id, data):
            if not self._loop or not self._client:
                log.debug(
                    "TelegramNTG: signaling drop (loop/client unavailable user_id=%s bytes=%d)",
                    user_id,
                    len(data or b""),
                )
                return
            log.debug("TelegramNTG: signaling generated user_id=%s bytes=%d", user_id, len(data or b""))
            asyncio.run_coroutine_threadsafe(self._send_signaling(user_id, data), self._loop)

        def on_connection_change(user_id, state):
            st = getattr(state, "state", None)
            log.debug("TelegramNTG: connection state user_id=%s state=%s", user_id, st)
            if st in {ntgcalls_mod.ConnectionState.CONNECTED}:
                if self._call_direction == "outgoing" and self._on_call_answered:
                    self._on_call_answered()
            if st in {ntgcalls_mod.ConnectionState.CLOSED, ntgcalls_mod.ConnectionState.FAILED, ntgcalls_mod.ConnectionState.TIMEOUT}:
                self._in_call = False
                self._call_direction = None
                self._reset_outbound_audio_state()
                if self._on_call_ended:
                    self._on_call_ended()

        def on_frames(user_id, mode, device, frames):
            if not self._on_audio_received:
                return
            if not self._in_call:
                return
            if playback_mode is not None and mode != playback_mode:
                return
            if mic_device is not None and device != mic_device:
                return
            try:
                rx_frames = 0
                rx_bytes = 0
                for fr in frames:
                    data = getattr(fr, "data", b"")
                    if data:
                        payload = bytes(data)
                        rx_frames += 1
                        rx_bytes += len(payload)
                        self._on_audio_received(payload)
                if rx_frames or rx_bytes:
                    self._note_audio_stats(rx_frames=rx_frames, rx_bytes=rx_bytes)
            except Exception:
                pass

        self._ntg.on_signaling(on_signal)
        self._ntg.on_connection_change(on_connection_change)
        self._ntg.on_frames(on_frames)

    async def _send_signaling(self, user_id: int, data: bytes):
        if not self._client:
            return
        from telethon import functions
        peer = self._input_calls.get(int(user_id))
        if not peer:
            log.debug(
                "TelegramNTG: signaling drop (no peer mapping user_id=%s bytes=%d)",
                user_id,
                len(data or b""),
            )
            return
        try:
            log.debug("TelegramNTG: signaling -> Telegram user_id=%s bytes=%d", user_id, len(data or b""))
            await self._client(functions.phone.SendSignalingDataRequest(peer=peer, data=data))
        except Exception as e:
            log.debug("TelegramNTG: SendSignalingDataRequest failed user_id=%s err=%s", user_id, e)

    async def _handle_raw_update(self, update):
        from telethon.tl.types import (
            UpdatePhoneCall,
            UpdatePhoneCallSignalingData,
            PhoneCallRequested,
            PhoneCallAccepted,
            PhoneCall,
            PhoneCallDiscarded,
            InputPhoneCall,
        )

        updates = self._flatten_raw_updates(update)
        if len(updates) > 1:
            log.debug("TelegramNTG: unpacked %d raw updates from %s", len(updates), type(update).__name__)

        for up in updates:
            if isinstance(up, UpdatePhoneCallSignalingData):
                log.debug(
                    "TelegramNTG: signaling <- Telegram call_id=%s bytes=%d",
                    getattr(up, "phone_call_id", 0),
                    len(getattr(up, "data", b"") or b""),
                )
                try:
                    user_id = self._find_user_by_call_id(up.phone_call_id)
                    if user_id is not None and self._ntg:
                        await self._await_ntg(self._ntg.send_signaling(user_id, up.data))
                    else:
                        log.debug("TelegramNTG: no mapping for incoming signaling call_id=%s", getattr(up, "phone_call_id", 0))
                except Exception as e:
                    log.debug("TelegramNTG: incoming signaling forward failed err=%s", e)
                continue

            if not isinstance(up, UpdatePhoneCall):
                continue

            call = up.phone_call
            log.debug(
                "TelegramNTG: phone update type=%s id=%s",
                type(call).__name__,
                getattr(call, "id", 0),
            )

            if isinstance(call, PhoneCallRequested):
                user_id = self._resolve_call_user_id(call, preferred=int(getattr(call, "admin_id", 0) or 0))
                if not user_id:
                    log.debug("TelegramNTG: incoming PhoneCallRequested without user id")
                    continue
                peer = InputPhoneCall(id=call.id, access_hash=call.access_hash)
                self._input_calls[user_id] = peer
                self._pending_incoming_call = call
                self._pending_incoming_peer = peer
                self._pending_incoming_user_id = user_id
                self._last_incoming_target = str(user_id)
                self._call_direction = "incoming"
                self._current_user_id = user_id
                self._current_call_obj = call
                self._in_call = False

                if self._client:
                    try:
                        from telethon import functions

                        await self._client(functions.phone.ReceivedCallRequest(peer=peer))
                    except Exception as e:
                        log.debug("TelegramNTG: ReceivedCallRequest warning call_id=%s err=%s", getattr(call, "id", 0), e)

                if self._on_incoming_call:
                    caller_label = await self._resolve_caller_label(user_id)
                    self._on_incoming_call(str(call.id), caller_label)
                continue

            if isinstance(call, PhoneCallAccepted):
                if not self._client or not self._ntg:
                    continue
                user_id = self._resolve_call_user_id(call)
                peer = InputPhoneCall(id=call.id, access_hash=call.access_hash)
                self._input_calls[user_id] = peer
                self._current_user_id = user_id
                self._current_call_obj = call
                try:
                    from telethon import functions, types as tl_types

                    g_b = await self._to_bytes_payload(getattr(call, "g_b", b""), "phone_call_accepted.g_b")
                    auth = await self._await_ntg(
                        self._ntg.exchange_keys(
                            user_id,
                            g_b,
                            int(getattr(call, "key_fingerprint", 0) or 0),
                        )
                    )

                    g_a = await self._to_bytes_payload(getattr(auth, "g_a_or_b", None), "exchange_keys.g_a_or_b")
                    key_fingerprint = int(
                        getattr(auth, "key_fingerprint", 0)
                        or getattr(call, "key_fingerprint", 0)
                        or 0
                    )
                    if not key_fingerprint:
                        raise RuntimeError("exchange_keys returned empty key_fingerprint")

                    call_id = int(getattr(call, "id", 0) or 0)
                    if call_id:
                        self._key_ready_calls.add(call_id)

                    confirm_res = await self._client(
                        functions.phone.ConfirmCallRequest(
                            peer=peer,
                            g_a=g_a,
                            key_fingerprint=key_fingerprint,
                            protocol=getattr(call, "protocol", None) or self._build_protocol(tl_types),
                        )
                    )
                    log.info("TelegramNTG: call confirmed (user_id=%s)", user_id)
                    confirm_call = getattr(confirm_res, "phone_call", None)
                    if confirm_call is not None:
                        await self._connect_p2p_from_phone_call(confirm_call, user_id_hint=user_id, source="confirm_call")
                except Exception as e:
                    self._in_call = False
                    self._call_direction = None
                    log.exception("TelegramNTG confirm_call failed user_id=%s err=%s", user_id, e)
                continue

            if isinstance(call, PhoneCall):
                await self._connect_p2p_from_phone_call(call, source="update_phone_call")
                continue

            if isinstance(call, PhoneCallDiscarded):
                self._in_call = False
                self._call_direction = None
                self._clear_pending_incoming()
                self._reset_outbound_audio_state()
                if self._on_call_ended:
                    self._on_call_ended()

    def _find_user_by_call_id(self, call_id: int) -> Optional[int]:
        for uid, inp in self._input_calls.items():
            if int(getattr(inp, "id", -1)) == int(call_id):
                return uid
        return None

    def _resolve_call_user_id(self, call_obj, preferred: Optional[int] = None) -> int:
        pid = int(preferred or 0)
        if pid:
            return pid

        call_id = int(getattr(call_obj, "id", 0) or 0)
        mapped = self._find_user_by_call_id(call_id) if call_id else None
        if mapped:
            return int(mapped)

        if self._current_user_id:
            return int(self._current_user_id)

        admin_id = int(getattr(call_obj, "admin_id", 0) or 0)
        participant_id = int(getattr(call_obj, "participant_id", 0) or 0)

        if self._self_user_id:
            if admin_id == self._self_user_id and participant_id:
                return participant_id
            if participant_id == self._self_user_id and admin_id:
                return admin_id

        return participant_id or admin_id

    async def _connect_p2p_from_phone_call(self, call_obj, user_id_hint: Optional[int] = None, source: str = ""):
        if not self._ntg:
            return

        call_id = int(getattr(call_obj, "id", 0) or 0)
        if call_id and self._p2p.get(call_id):
            return

        connections = getattr(call_obj, "connections", None)
        protocol = getattr(call_obj, "protocol", None)
        if not connections or protocol is None:
            log.debug(
                "TelegramNTG: connect_p2p deferred source=%s call_id=%s (connections/protocol missing)",
                source,
                call_id,
            )
            return

        uid = self._resolve_call_user_id(call_obj, preferred=user_id_hint)
        if not uid:
            log.debug("TelegramNTG: connect_p2p deferred source=%s call_id=%s (user unresolved)", source, call_id)
            return

        self._current_user_id = uid
        self._current_call_obj = call_obj

        try:
            await self._ensure_p2p_session(uid)
        except Exception as e:
            log.debug("TelegramNTG: ensure_p2p_session failed source=%s user_id=%s err=%s", source, uid, e)
            return

        await self._ensure_exchange_for_phone_call(call_obj, uid, source)

        if call_id and call_id not in self._key_ready_calls:
            log.debug("TelegramNTG: connect_p2p deferred source=%s call_id=%s (key exchange pending)", source, call_id)
            return

        from telethon.tl.types import InputPhoneCall

        if hasattr(call_obj, "access_hash") and call_id:
            self._input_calls[uid] = InputPhoneCall(id=call_id, access_hash=call_obj.access_hash)

        try:
            await self._await_ntg(
                self._ntg.connect_p2p(
                    uid,
                    self._parse_rtc_servers(connections),
                    list(getattr(protocol, "library_versions", []) or []),
                    bool(getattr(call_obj, "p2p_allowed", True)),
                )
            )
            if call_id:
                self._p2p[call_id] = True
            self._in_call = True
            log.info("TelegramNTG: connect_p2p ready source=%s user_id=%s call_id=%s", source, uid, call_id)
        except Exception as e:
            log.debug("TelegramNTG connect_p2p warning source=%s user_id=%s call_id=%s err=%s", source, uid, call_id, e)

    async def _ensure_exchange_for_phone_call(self, call_obj, user_id: int, source: str):
        if not self._ntg:
            return

        call_id = int(getattr(call_obj, "id", 0) or 0)
        if call_id and call_id in self._key_ready_calls:
            return

        g_a_or_b = getattr(call_obj, "g_a_or_b", None)
        key_fingerprint = int(getattr(call_obj, "key_fingerprint", 0) or 0)
        if g_a_or_b is None or not key_fingerprint:
            return

        try:
            payload = await self._to_bytes_payload(g_a_or_b, "phone_call.g_a_or_b")
            await self._await_ntg(self._ntg.exchange_keys(int(user_id), payload, key_fingerprint))
            if call_id:
                self._key_ready_calls.add(call_id)
            log.info(
                "TelegramNTG: exchange_keys ready source=%s user_id=%s call_id=%s",
                source,
                int(user_id),
                call_id,
            )
        except Exception as e:
            msg = str(e).lower()
            if "already" in msg and "key" in msg:
                if call_id:
                    self._key_ready_calls.add(call_id)
                return
            log.debug(
                "TelegramNTG: exchange_keys deferred source=%s user_id=%s call_id=%s err=%s",
                source,
                int(user_id),
                call_id,
                e,
            )

    def _clear_pending_incoming(self):
        self._pending_incoming_call = None
        self._pending_incoming_peer = None
        self._pending_incoming_user_id = None

    def _flatten_raw_updates(self, update) -> list:
        updates = getattr(update, "updates", None)
        if isinstance(updates, (list, tuple)):
            return list(updates)

        inner = getattr(update, "update", None)
        if inner is not None:
            return [inner]

        return [update]

    async def _ensure_p2p_session(self, user_id: int) -> None:
        if not self._ntg:
            return
        uid = int(user_id or 0)
        if not uid:
            return
        if uid in self._p2p_ready_users:
            return

        try:
            await self._await_ntg(self._ntg.create_p2p_call(uid))
        except Exception as e:
            msg = str(e).lower()
            if "already" not in msg and "exists" not in msg:
                raise
            log.debug("TelegramNTG: create_p2p_call already initialized user_id=%s", uid)

        await self._configure_stream_sources(uid)
        self._p2p_ready_users.add(uid)
        log.debug("TelegramNTG: p2p session ready user_id=%s", uid)

    async def _configure_stream_sources(self, user_id: int) -> None:
        if not self._ntg:
            return
        import ntgcalls

        capture_mode = getattr(ntgcalls.StreamMode, "CAPTURE", None)
        playback_mode = getattr(ntgcalls.StreamMode, "PLAYBACK", None)
        if capture_mode is None:
            raise RuntimeError("ntgcalls StreamMode.CAPTURE is unavailable")

        capture_media = self._media_description()

        await self._await_ntg(
            self._ntg.set_stream_sources(
                user_id,
                capture_mode,
                capture_media,
            )
        )
        if playback_mode is None:
            log.warning("TelegramNTG: StreamMode.PLAYBACK unavailable; incoming audio may be silent")
        else:
            playback_media = self._media_description()
            await self._await_ntg(
                self._ntg.set_stream_sources(
                    user_id,
                    playback_mode,
                    playback_media,
                )
            )

        log.debug("TelegramNTG: stream sources configured user_id=%s (capture+playback external)", user_id)

    def _note_audio_stats(
        self,
        tx_frames: int = 0,
        tx_bytes: int = 0,
        tx_drop_bytes: int = 0,
        tx_errors: int = 0,
        rx_frames: int = 0,
        rx_bytes: int = 0,
        queue_bytes: Optional[int] = None,
    ) -> None:
        now = time.monotonic()
        with self._audio_stats_lock:
            self._audio_stats["tx_frames"] += int(tx_frames)
            self._audio_stats["tx_bytes"] += int(tx_bytes)
            self._audio_stats["tx_drop_bytes"] += int(tx_drop_bytes)
            self._audio_stats["tx_errors"] += int(tx_errors)
            if queue_bytes is not None:
                self._audio_stats["tx_queue_bytes"] = int(queue_bytes)
            self._audio_stats["rx_frames"] += int(rx_frames)
            self._audio_stats["rx_bytes"] += int(rx_bytes)

            if now - self._audio_stats_last_log >= self._audio_stats_interval_s:
                self._emit_audio_stats_locked(now, queue_bytes=queue_bytes)

    def _emit_audio_stats_locked(self, now: Optional[float] = None, queue_bytes: Optional[int] = None) -> None:
        ts = time.monotonic() if now is None else now
        stats = self._audio_stats

        if any(int(stats[k]) for k in ("tx_frames", "tx_bytes", "tx_drop_bytes", "tx_errors", "rx_frames", "rx_bytes")):
            qv = int(stats.get("tx_queue_bytes", 0))
            log.debug(
                "TelegramNTG audio: tx_frames=%d tx_bytes=%d rx_frames=%d rx_bytes=%d tx_drop_bytes=%d tx_errors=%d tx_queue=%s",
                int(stats["tx_frames"]),
                int(stats["tx_bytes"]),
                int(stats["rx_frames"]),
                int(stats["rx_bytes"]),
                int(stats["tx_drop_bytes"]),
                int(stats["tx_errors"]),
                str(qv),
            )

        for key in ("tx_frames", "tx_bytes", "tx_drop_bytes", "tx_errors", "rx_frames", "rx_bytes"):
            stats[key] = 0
        self._audio_stats_last_log = ts

    def _build_dh_config(self, dh):
        import ntgcalls
        return ntgcalls.DhConfig(g=dh.g, p=bytes(dh.p), random=bytes(dh.random))

    async def _await_ntg(self, result):
        """Await ntgcalls Future-like results across binding variants."""
        value = result
        for _ in range(8):
            if asyncio.isfuture(value) or inspect.isawaitable(value):
                value = await value
                continue
            if isinstance(value, concurrent.futures.Future):
                value = await asyncio.wrap_future(value)
                continue
            break
        return value

    async def _to_bytes_payload(self, value, label: str) -> bytes:
        value = await self._await_ntg(value)
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, memoryview):
            return value.tobytes()
        if isinstance(value, list):
            try:
                return bytes(value)
            except Exception:
                pass
        raise RuntimeError(f"{label} produced non-bytes payload: {type(value)!r}")

    def _reset_outbound_audio_state(self):
        queued = len(self._tx_audio_buffer)
        self._tx_audio_buffer.clear()
        self._tx_next_ts_ms = None
        self._p2p.clear()
        self._p2p_ready_users.clear()
        self._key_ready_calls.clear()
        with self._audio_stats_lock:
            self._audio_stats["tx_queue_bytes"] = 0
            self._emit_audio_stats_locked(time.monotonic(), queue_bytes=queued)

    @staticmethod
    def _build_session_path(session_name: str) -> str:
        p = Path(session_name)
        if p.is_absolute():
            return str(p)
        repo_root = Path(__file__).resolve().parents[1]
        return str((repo_root / p).resolve())

    def _ask_input(self, prompt: str, callback: Optional[Callable], secret: bool = False) -> str:
        """Get user input via GUI callback or stdin fallback."""
        if callback is not None:
            val = callback()
            return (val or "").strip()
        if secret:
            return getpass.getpass(prompt).strip()
        return input(prompt).strip()

    async def _ensure_authorized(self, interactive: bool) -> None:
        """Explicit Telethon auth flow with code + 2FA support.

        When GUI auth callbacks are wired via set_auth_callbacks(), those
        are used instead of stdin input()/getpass(). This allows the
        tkinter UI to show dialog boxes for phone/code/password entry.
        """
        from telethon.errors import (
            SessionPasswordNeededError,
            PhoneCodeInvalidError,
            PhoneCodeExpiredError,
            PasswordHashInvalidError,
            FloodWaitError,
        )

        if not self._client:
            raise RuntimeError("Telegram client not initialized")

        if await self._client.is_user_authorized():
            return

        if not interactive:
            raise RuntimeError(
                "Telegram session is not authorized. "
                "Run: python run.py --platform telegram_private --telegram-auth --debug"
            )

        # Get phone number from config or ask via callback/stdin
        phone = self._phone
        if not phone:
            phone = self._ask_input(
                "Phone number (e.g. +15551234567): ",
                self._auth_ask_phone,
            )
            self._phone = phone
        if not phone:
            raise RuntimeError("telegram.phone is required for interactive login")

        try:
            sent = await self._client.send_code_request(phone)
        except FloodWaitError as e:
            raise RuntimeError(f"Telegram flood wait: retry in {int(getattr(e, 'seconds', 0))}s") from e

        code_ok = False
        need_password = False
        phone_code_hash = sent.phone_code_hash

        for attempt in range(1, 4):
            code = self._ask_input(
                "Please enter the code you received: ",
                self._auth_ask_code,
            )
            if not code:
                log.error("Empty code (attempt %d/3)", attempt)
                continue
            try:
                await self._client.sign_in(
                    phone=phone,
                    code=code,
                    phone_code_hash=phone_code_hash,
                )
                code_ok = True
                break
            except SessionPasswordNeededError:
                need_password = True
                code_ok = True
                break
            except PhoneCodeInvalidError:
                log.error("Invalid code (attempt %d/3)", attempt)
            except PhoneCodeExpiredError:
                log.error("Code expired; requesting a new one")
                sent = await self._client.send_code_request(phone)
                phone_code_hash = sent.phone_code_hash
            except FloodWaitError as e:
                raise RuntimeError(f"Telegram flood wait: retry in {int(getattr(e, 'seconds', 0))}s") from e

        if not code_ok:
            raise RuntimeError("Telegram login failed: code verification failed")

        if need_password:
            for attempt in range(1, 4):
                pwd = self._ask_input(
                    "Please enter your Telegram 2FA password: ",
                    self._auth_ask_password,
                    secret=True,
                )
                try:
                    await self._client.sign_in(password=pwd)
                    break
                except PasswordHashInvalidError:
                    log.error("Invalid 2FA password (attempt %d/3)", attempt)
                except FloodWaitError as e:
                    raise RuntimeError(f"Telegram flood wait: retry in {int(getattr(e, 'seconds', 0))}s") from e
            else:
                raise RuntimeError("Telegram login failed: invalid 2FA password")

        if not await self._client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized after login")

    def _build_protocol(self, types_mod):
        import ntgcalls
        p = ntgcalls.NTgCalls.get_protocol()
        return types_mod.PhoneCallProtocol(
            min_layer=p.min_layer,
            max_layer=p.max_layer,
            library_versions=list(p.library_versions),
            udp_p2p=bool(p.udp_p2p),
            udp_reflector=bool(p.udp_reflector),
        )

    def _media_description(self):
        import ntgcalls

        def _mk_audio_desc():
            # ntgcalls Python bindings differ by version.
            # Newer builds accept 4 args; some older builds used a 5th bool flag.
            try:
                return ntgcalls.AudioDescription(
                    ntgcalls.MediaSource.EXTERNAL,
                    self._sample_rate,
                    self._channels,
                    "",
                )
            except TypeError:
                return ntgcalls.AudioDescription(
                    ntgcalls.MediaSource.EXTERNAL,
                    self._sample_rate,
                    self._channels,
                    "",
                    True,
                )

        mic = _mk_audio_desc()
        spk = _mk_audio_desc()
        return ntgcalls.MediaDescription(microphone=mic, speaker=spk)

    def _parse_rtc_servers(self, conn_list):
        import ntgcalls
        out = []
        for s in conn_list:
            out.append(
                ntgcalls.RTCServer(
                    int(getattr(s, "id", 0)),
                    str(getattr(s, "ip", "0.0.0.0")),
                    str(getattr(s, "ipv6", "::")),
                    int(getattr(s, "port", 0)),
                    getattr(s, "username", None),
                    getattr(s, "password", None),
                    bool(getattr(s, "turn", False)),
                    bool(getattr(s, "stun", False)),
                    bool(getattr(s, "tcp", False)),
                    getattr(s, "peer_tag", None),
                )
            )
        return out
