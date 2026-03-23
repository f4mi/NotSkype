"""
Local replacement platform for CIT200.

This backend emulates the legacy desktop app pipeline without external services:
- Provides a synthetic contact list
- Simulates outgoing/incoming call state
- Echoes mic audio back to handset speaker during active calls
"""

import asyncio
import logging
import math
import random
import string
import struct
from typing import Optional

from .base import PlatformContact, VoicePlatform

log = logging.getLogger(__name__)


class LocalMockPlatform(VoicePlatform):
    def __init__(
        self,
        contacts: Optional[list[dict]] = None,
        auto_incoming_after_s: float = 0.0,
        call_me_on_start: bool = False,
        call_me_delay_s: float = 0.8,
        echo_gain: float = 0.9,
    ):
        super().__init__()
        self._connected = False
        self._in_call = False
        self._held = False
        self._status = "online"
        self._active_call_id = ""
        self._pending_incoming_id = ""
        self._pending_incoming_name = ""
        self._auto_incoming_after_s = max(0.0, float(auto_incoming_after_s))
        self._call_me_on_start = bool(call_me_on_start)
        self._call_me_delay_s = max(0.0, float(call_me_delay_s))
        self._echo_gain = max(0.0, min(1.0, float(echo_gain)))
        self._incoming_task: Optional[asyncio.Task] = None
        self._contacts = self._build_contacts(contacts or [])

    @property
    def platform_name(self) -> str:
        return "LocalMock"

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_in_call(self) -> bool:
        return self._in_call

    async def connect(self) -> None:
        self._connected = True
        log.info("LocalMock connected")
        if self._call_me_on_start:
            self._incoming_task = asyncio.create_task(self._schedule_call_me_on_start())
            return
        if self._auto_incoming_after_s > 0:
            self._incoming_task = asyncio.create_task(self._schedule_incoming())

    async def _schedule_call_me_on_start(self):
        try:
            await asyncio.sleep(self._call_me_delay_s)
            if not self._connected or self._in_call or not self._on_incoming_call:
                return
            who = random.choice(self._contacts).name if self._contacts else "Caller"
            self._pending_incoming_id = self._new_call_id()
            self._pending_incoming_name = who
            log.info("LocalMock: call-me-on-start incoming from %s", who)
            self._on_incoming_call(self._pending_incoming_id, self._pending_incoming_name)
        except asyncio.CancelledError:
            return

    async def disconnect(self) -> None:
        if self._incoming_task:
            self._incoming_task.cancel()
            self._incoming_task = None
        self._connected = False
        self._in_call = False
        self._held = False
        self._active_call_id = ""
        self._pending_incoming_id = ""
        self._pending_incoming_name = ""
        log.info("LocalMock disconnected")

    async def place_call(self, target: str) -> None:
        if not self._connected:
            return
        if self._in_call:
            log.info("LocalMock: already in call, ignoring new dial")
            return

        self._active_call_id = self._new_call_id()
        self._in_call = True
        self._held = False
        log.info("LocalMock: placing call to %s (%s)", target, self._active_call_id)

        await asyncio.sleep(0.35)
        if self._in_call and self._on_call_answered:
            self._on_call_answered()
        if self._in_call and self._on_audio_received:
            self._on_audio_received(self._make_tone())

    async def answer_call(self, call_id: str = "") -> None:
        if not self._connected:
            return
        if self._pending_incoming_id:
            self._active_call_id = self._pending_incoming_id
            self._pending_incoming_id = ""
            self._pending_incoming_name = ""
            self._in_call = True
            self._held = False
            if self._on_call_answered:
                self._on_call_answered()
            if self._on_audio_received:
                self._on_audio_received(self._make_tone())
            return

        if call_id:
            self._active_call_id = call_id
            self._in_call = True
            self._held = False
            if self._on_call_answered:
                self._on_call_answered()
            if self._on_audio_received:
                self._on_audio_received(self._make_tone())

    async def end_call(self) -> None:
        if not self._in_call and not self._pending_incoming_id:
            return
        self._in_call = False
        self._held = False
        self._active_call_id = ""
        self._pending_incoming_id = ""
        self._pending_incoming_name = ""
        if self._on_call_ended:
            self._on_call_ended()

    async def hold_call(self) -> None:
        if not self._in_call:
            return
        self._held = not self._held
        log.info("LocalMock hold=%s", self._held)

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if not self._in_call or self._held or not self._on_audio_received:
            return

        if self._echo_gain >= 0.999:
            out = pcm_chunk
        else:
            out = self._scale_pcm16(pcm_chunk, self._echo_gain)

        self._on_audio_received(out)

    async def get_contacts(self) -> list[PlatformContact]:
        return list(self._contacts)

    async def get_status(self) -> str:
        return self._status

    async def set_status(self, status: str) -> None:
        self._status = status
        log.info("LocalMock status=%s", status)

    async def _schedule_incoming(self):
        try:
            await asyncio.sleep(self._auto_incoming_after_s)
            if not self._connected or self._in_call or not self._on_incoming_call:
                return
            who = random.choice(self._contacts).name if self._contacts else "Caller"
            self._pending_incoming_id = self._new_call_id()
            self._pending_incoming_name = who
            self._on_incoming_call(self._pending_incoming_id, self._pending_incoming_name)
        except asyncio.CancelledError:
            return

    @staticmethod
    def _new_call_id() -> str:
        return "local-" + "".join(random.choice(string.hexdigits.lower()) for _ in range(8))

    @staticmethod
    def _build_contacts(items: list[dict]) -> list[PlatformContact]:
        if not items:
            items = [
                {"id": "1", "handle": "echo", "name": "Echo Test", "status": 0},
                {"id": "2", "handle": "lab", "name": "Local Lab", "status": 3},
                {"id": "3", "handle": "support", "name": "Support Bot", "status": 0},
            ]

        out: list[PlatformContact] = []
        for i, item in enumerate(items):
            out.append(
                PlatformContact(
                    id=str(item.get("id", i)),
                    handle=str(item.get("handle", f"contact{i}")),
                    name=str(item.get("name", f"Contact {i}")),
                    status=int(item.get("status", 0)),
                )
            )
        return out

    @staticmethod
    def _scale_pcm16(data: bytes, gain: float) -> bytes:
        import array

        pcm = array.array("h")
        pcm.frombytes(data)
        for i, v in enumerate(pcm):
            nv = int(v * gain)
            if nv > 32767:
                nv = 32767
            elif nv < -32768:
                nv = -32768
            pcm[i] = nv
        return pcm.tobytes()

    @staticmethod
    def _make_tone(freq_hz: float = 440.0, ms: int = 350, sample_rate: int = 16000, amp: int = 7000) -> bytes:
        total = int(sample_rate * (ms / 1000.0))
        out = bytearray()
        for n in range(total):
            s = int(amp * math.sin(2.0 * math.pi * freq_hz * (n / sample_rate)))
            out += struct.pack("<h", s)
        return bytes(out)
