"""
Abstract base class for voice platforms (Telegram, Discord, etc.)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class PlatformContact:
    """A contact from the voice platform."""
    id: str             # Platform-specific user ID
    handle: str         # Username / handle
    name: str           # Display name
    status: int = 0     # Maps to cit200.Status values (0=ONLINE, 1=OFFLINE, ...)
    avatar_url: str = ""

    @property
    def online(self) -> bool:
        """Status 0 = ONLINE in linksysphone's Status enum."""
        return self.status == 0

    def as_ui_dict(self) -> dict:
        """Return a dict suitable for SkypeUI.update_contacts()."""
        return {"name": self.name or self.handle, "online": self.online}


class VoicePlatform(ABC):
    """
    Abstract interface for a voice call platform.

    Implementations must handle:
    - Authentication and connection
    - Placing and receiving voice calls
    - Bidirectional PCM audio streaming
    - Contact list retrieval
    - Online status management
    """

    def __init__(self):
        # Callbacks — set by main.py to receive platform events
        self._on_incoming_call: Optional[Callable[[str, str], None]] = None  # (call_id, caller_name)
        self._on_call_ended: Optional[Callable[[], None]] = None
        self._on_audio_received: Optional[Callable[[bytes], None]] = None   # PCM int16 data
        self._on_call_answered: Optional[Callable[[], None]] = None

    # ── Callback Registration ───────────────────────────────────

    def on_incoming_call(self, callback: Callable[[str, str], None]):
        """Register callback for incoming calls. Args: (call_id, caller_name)"""
        self._on_incoming_call = callback

    def on_call_ended(self, callback: Callable[[], None]):
        """Register callback for when a call ends (remote side hung up)."""
        self._on_call_ended = callback

    def on_audio_received(self, callback: Callable[[bytes], None]):
        """Register callback for received audio. Args: (pcm_int16_bytes,)"""
        self._on_audio_received = callback

    def on_call_answered(self, callback: Callable[[], None]):
        """Register callback for when an outgoing call is answered."""
        self._on_call_answered = callback

    # ── Connection ──────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Authenticate and connect to the platform."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the platform."""
        ...

    # ── Call Control ────────────────────────────────────────────

    @abstractmethod
    async def place_call(self, target: str) -> None:
        """
        Place an outgoing voice call.
        target: platform-specific identifier (username, user ID, channel ID, etc.)
        """
        ...

    @abstractmethod
    async def answer_call(self, call_id: str = "") -> None:
        """Answer an incoming call."""
        ...

    @abstractmethod
    async def end_call(self) -> None:
        """End the current active call."""
        ...

    @abstractmethod
    async def hold_call(self) -> None:
        """Toggle hold on the current call."""
        ...

    # ── Audio ───────────────────────────────────────────────────

    @abstractmethod
    async def send_audio(self, pcm_chunk: bytes) -> None:
        """
        Send a PCM audio chunk to the active call.
        pcm_chunk: int16 little-endian mono PCM data.
        """
        ...

    # ── Contacts & Status ───────────────────────────────────────

    @abstractmethod
    async def get_contacts(self) -> list[PlatformContact]:
        """Retrieve the contact/friend list from the platform."""
        ...

    @abstractmethod
    async def get_status(self) -> str:
        """Get the current user's online status."""
        ...

    @abstractmethod
    async def set_status(self, status: str) -> None:
        """Set the current user's online status."""
        ...

    async def get_contact_bio(self, contact: PlatformContact) -> str:
        """Optional contact bio/about text for handset detail pages."""
        return ""

    def get_last_incoming_target(self) -> str:
        """Optional real dial target for latest incoming call (for callback flows)."""
        return ""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Human-readable platform name."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the platform is currently connected."""
        ...

    @property
    @abstractmethod
    def is_in_call(self) -> bool:
        """Whether there is an active call."""
        ...
