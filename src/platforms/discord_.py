"""platforms/discord_.py – Experimental Discord voice backend.

Uses discord.py (with voice extras) to connect to a Discord voice channel.
Audio is upsampled from CIT200's 16 kHz mono PCM to Discord's required
48 kHz stereo PCM and fed through a custom AudioSource.

This backend is experimental.  Feature coverage is below the Telegram backend:
  - Incoming calls are detected by a user joining the active voice channel.
  - There is no private-call signaling — it uses a shared voice channel.

Dependencies (required for this platform):
    pip install "discord.py[voice]" numpy

Config keys used (under "discord"):
    token        – Bot token (str)
    guild_id     – Target guild (server) ID (int)
    channel_id   – Voice channel ID to join (int)

Environment overrides:
    DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_CHANNEL_ID
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import time
from typing import List, Optional

from .base import PlatformContact, VoicePlatform

log = logging.getLogger(__name__)

# ── Optional deps ──────────────────────────────────────────────────────────────
try:
    import discord
    from discord.ext import tasks as discord_tasks
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False
    log.warning("discord.py not installed – DiscordPlatform unavailable")

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    log.warning("numpy not installed – Discord audio upsampling unavailable")


# ── Constants ──────────────────────────────────────────────────────────────────
_SRC_RATE   = 16_000    # CIT200 sample rate
_DST_RATE   = 48_000    # Discord required rate
_UPSAMPLE   = _DST_RATE // _SRC_RATE   # = 3
_DST_CHANS  = 2         # Discord requires stereo
_FRAME_BYTES_SRC  = 960 * 2            # 10 ms at 16 kHz mono int16
_FRAME_BYTES_DST  = 960 * _UPSAMPLE * _DST_CHANS * 2  # after upsampling


class _PCMSource:
    """
    discord.py AudioSource subclass that drains a queue of 48 kHz stereo
    int16 PCM frames (each exactly 20 ms = 3840 bytes for discord.py).
    """

    DISCORD_FRAME_BYTES = 3840  # 20 ms × 48 kHz × 2 ch × 2 bytes

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=100)
        self._buf = bytearray()

    def push(self, pcm_48k_stereo: bytes) -> None:
        if self._q.full():
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
        try:
            self._q.put_nowait(pcm_48k_stereo)
        except queue.Full:
            pass

    # discord.py calls read() every 20 ms
    def read(self) -> bytes:
        while len(self._buf) < self.DISCORD_FRAME_BYTES:
            try:
                chunk = self._q.get_nowait()
                self._buf.extend(chunk)
            except queue.Empty:
                # Pad silence
                return b"\x00" * self.DISCORD_FRAME_BYTES
        frame = bytes(self._buf[:self.DISCORD_FRAME_BYTES])
        del self._buf[:self.DISCORD_FRAME_BYTES]
        return frame

    @property
    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        pass


def _upsample_mono16k_to_stereo48k(pcm_mono: bytes) -> bytes:
    """
    Upsample 16 kHz mono int16 PCM to 48 kHz stereo int16 PCM by
    simple nearest-neighbour repeat (×3 rate, ×2 channels).
    Falls back to silence if numpy is unavailable.
    """
    if not HAS_NUMPY:
        return b"\x00" * (len(pcm_mono) * _UPSAMPLE * _DST_CHANS)
    arr  = np.frombuffer(pcm_mono, dtype="<i2")             # (N,)
    up   = np.repeat(arr, _UPSAMPLE)                        # (N×3,)
    st   = np.stack([up, up], axis=-1).flatten()            # (N×3×2,)
    return st.astype("<i2").tobytes()


class DiscordPlatform(VoicePlatform):
    """
    Experimental Discord voice-channel backend.

    Threading model
    ---------------
    A discord.py Client runs its own asyncio event loop in a daemon thread.
    Audio from the handset is upsampled and pushed into a _PCMSource queue.
    discord.py's voice client drains the queue from its own audio thread.
    """

    def __init__(self, cfg: dict):
        discord_cfg = cfg.get("discord", {})

        self._token: str = str(
            os.environ.get("DISCORD_TOKEN") or discord_cfg.get("token", "")
        )
        _guild_raw   = os.environ.get("DISCORD_GUILD_ID")   or discord_cfg.get("guild_id",   0)
        _channel_raw = os.environ.get("DISCORD_CHANNEL_ID") or discord_cfg.get("channel_id", 0)
        self._guild_id:   int = int(_guild_raw)   if _guild_raw   else 0
        self._channel_id: int = int(_channel_raw) if _channel_raw else 0

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional["discord.Client"] = None   # type: ignore[name-defined]
        self._voice: Optional["discord.VoiceClient"] = None  # type: ignore[name-defined]
        self._source = _PCMSource()

        self._in_call = False
        self._held    = False
        self._status  = "Online"

        self._contacts_cache: List[PlatformContact] = []
        self._contacts_lock   = threading.Lock()
        self._contacts_cache_at: float = 0.0
        self._cache_ttl: float = float(
            cfg.get("contacts", {}).get("cache_ttl_s", 300.0)
        )

        if not HAS_DISCORD:
            raise ImportError(
                "discord.py[voice] is required for DiscordPlatform. "
                "Install it: pip install 'discord.py[voice]'"
            )
        if not self._token:
            raise ValueError(
                "discord.token must be set in config.json or via DISCORD_TOKEN."
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════════════════

    def connect(self) -> None:
        self._loop = asyncio.new_event_loop()
        threading.Thread(
            target=self._run_loop, daemon=True, name="discord-loop"
        ).start()

    def disconnect(self) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_shutdown(), self._loop)

    # ═══════════════════════════════════════════════════════════════════════════
    # Call control
    # ═══════════════════════════════════════════════════════════════════════════

    def place_call(self, target: str) -> None:
        """Join the configured voice channel (Discord has no private-call concept)."""
        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(
            self._async_join_channel(), self._loop  # type: ignore[arg-type]
        )

    def answer_call(self) -> None:
        self._in_call = True
        if self.on_call_answered:
            self.on_call_answered()

    def end_call(self) -> None:
        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(
            self._async_leave_channel(), self._loop  # type: ignore[arg-type]
        )

    def hold_call(self) -> None:
        self._held = not self._held
        log.info("[discord] hold=%s", self._held)

    # ═══════════════════════════════════════════════════════════════════════════
    # Media
    # ═══════════════════════════════════════════════════════════════════════════

    def send_audio(self, pcm: bytes) -> None:
        if not self._in_call or self._held:
            return
        upsampled = _upsample_mono16k_to_stereo48k(pcm)
        self._source.push(upsampled)

    # ═══════════════════════════════════════════════════════════════════════════
    # Contacts / presence
    # ═══════════════════════════════════════════════════════════════════════════

    def get_contacts(self) -> List[PlatformContact]:
        now = time.monotonic()
        with self._contacts_lock:
            if self._contacts_cache and (now - self._contacts_cache_at) < self._cache_ttl:
                return list(self._contacts_cache)
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_refresh_contacts(), self._loop
            )
        with self._contacts_lock:
            return list(self._contacts_cache)

    def get_status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        self._status = status

    def get_last_incoming_target(self) -> Optional[str]:
        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # Private — loop thread
    # ═══════════════════════════════════════════════════════════════════════════

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception:
            log.exception("[discord] loop crashed")
        finally:
            self._loop.close()

    async def _async_main(self) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.members      = True
        intents.presences    = True

        self._client = discord.Client(intents=intents, loop=self._loop)

        @self._client.event
        async def on_ready():
            log.info("[discord] logged in as %s", self._client.user)   # type: ignore[union-attr]
            await self._async_refresh_contacts()

        @self._client.event
        async def on_voice_state_update(member, before, after):
            await self._on_voice_state(member, before, after)

        await self._client.start(self._token)

    async def _async_shutdown(self) -> None:
        await self._async_leave_channel()
        if self._client:
            await self._client.close()

    def _ensure_loop(self) -> None:
        if not self._loop or not self._loop.is_running():
            raise RuntimeError("DiscordPlatform not connected")

    # ═══════════════════════════════════════════════════════════════════════════
    # Private — voice channel management
    # ═══════════════════════════════════════════════════════════════════════════

    async def _async_join_channel(self) -> None:
        if self._client is None:
            return
        guild   = self._client.get_guild(self._guild_id)
        if guild is None:
            log.error("[discord] guild %d not found", self._guild_id)
            return
        channel = guild.get_channel(self._channel_id)
        if channel is None:
            log.error("[discord] channel %d not found", self._channel_id)
            return

        log.info("[discord] joining voice channel: %s", channel.name)   # type: ignore[union-attr]
        try:
            self._voice = await channel.connect()                        # type: ignore[union-attr]
            self._in_call = True
            # Start playing from the PCM source
            self._voice.play(self._source)
            if self.on_call_answered:
                self.on_call_answered()
        except Exception:
            log.exception("[discord] join voice failed")

    async def _async_leave_channel(self) -> None:
        self._in_call = False
        self._held    = False
        if self._voice and self._voice.is_connected():
            try:
                await self._voice.disconnect()
            except Exception as e:
                log.debug("[discord] disconnect error: %s", e)
        self._voice = None
        if self.on_call_ended:
            self.on_call_ended()

    async def _on_voice_state(self, member, before, after) -> None:
        """Detect a member joining the active voice channel as a pseudo-incoming call."""
        if self._client is None:
            return
        # Ignore our own bot's state changes
        if member == self._client.user:
            return
        # If they joined a channel we're in, treat as incoming
        if (after.channel is not None
                and self._voice is not None
                and after.channel == self._voice.channel
                and before.channel != after.channel):
            caller = str(member.display_name)
            log.info("[discord] member joined voice: %s — treating as incoming call", caller)
            if self.on_incoming_call:
                self.on_incoming_call(caller)

    # ═══════════════════════════════════════════════════════════════════════════
    # Private — contacts
    # ═══════════════════════════════════════════════════════════════════════════

    async def _async_refresh_contacts(self) -> None:
        if self._client is None:
            return
        try:
            guild = self._client.get_guild(self._guild_id)
            if guild is None:
                return
            contacts = []
            for member in guild.members:
                if member.bot:
                    continue
                status     = str(member.status)
                online     = (status == "online")
                status_int = 1 if online else (2 if status == "idle" else 0)
                contacts.append(PlatformContact(
                    id=str(member.id),
                    name=member.display_name,
                    handle=str(member.name),
                    status=status_int,
                    online=online,
                ))
            with self._contacts_lock:
                self._contacts_cache    = contacts
                self._contacts_cache_at = time.monotonic()
            log.info("[discord] contacts refreshed: %d members", len(contacts))
        except Exception:
            log.exception("[discord] contact refresh failed")
