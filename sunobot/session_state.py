"""In-memory per-user conversation state and shared state stores."""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import time
from dataclasses import dataclass, field

from .config import Settings
from .models import Track

logger = logging.getLogger(__name__)


class ConversationState:
    """Integer constants used as python-telegram-bot conversation states."""

    (AWAITING_URLS, AWAITING_EDIT_FIELD, AWAITING_CONFIRM_PHOTO) = range(3)


@dataclass
class BatchSession:
    """Everything the bot knows about the current download/edit run."""

    user_id: int
    chat_id: int
    tracks: list[Track] = field(default_factory=list)
    current_index: int = 0  # 0-based index of the song being edited
    edit_message_id: int = 0
    processing: bool = False  # True while the final send is in flight
    started_at: float = field(default_factory=time.time)

    # --- live UI state ---
    pending_field: str | None = None  # text field currently being captured
    pending_cover: bool = False  # whether we await a cover photo
    field_prompt_message_id: int | None = None
    cover_prompt_message_id: int | None = None

    @property
    def current(self) -> Track | None:
        if not self.tracks:
            return None
        if not (0 <= self.current_index < len(self.tracks)):
            self.current_index = 0
        return self.tracks[self.current_index]

    @property
    def total(self) -> int:
        return len(self.tracks)

    def index_in_batch(self, index0: int) -> int:
        return index0 + 1

    def move(self, delta: int) -> Track:
        """Move the editor cursor by delta (wraps around) and return the track."""
        n = len(self.tracks)
        self.current_index = (self.current_index + delta) % n
        return self.tracks[self.current_index]


class BatchStore:
    """Keeps one active batch per user and one 'pending edit' marker per message."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._by_user: dict[int, BatchSession] = {}
        self._edit_owner: dict[int, int] = {}  # message_id -> user_id
        self._lock = asyncio.Lock()
        self._seq = itertools.count(1)

    async def create(self, user_id: int, chat_id: int, urls: list[str]) -> BatchSession:
        """Register a new batch of songs and return its session."""
        async with self._lock:
            seq = next(self._seq)
            tracks = [
                Track(
                    display_index=seq * 1000 + idx + 1,
                    url=url,
                )
                for idx, url in enumerate(urls)
            ]
            session = BatchSession(
                user_id=user_id,
                chat_id=chat_id,
                tracks=tracks,
                current_index=0,
            )
            # Normalise display indexes to a clean 1..N numbering.
            for i, track in enumerate(session.tracks, start=1):
                track.display_index = i
            self._by_user[user_id] = session
            return session

    def get_for_user(self, user_id: int) -> BatchSession | None:
        return self._by_user.get(user_id)

    def drop(self, user_id: int) -> None:
        self._by_user.pop(user_id, None)

    def get_for_message(self, message_id: int) -> BatchSession | None:
        user_id = self._edit_owner.get(message_id)
        if user_id is None:
            return None
        return self._by_user.get(user_id)

    def owns_message(self, message_id: int, user_id: int) -> bool:
        return self._edit_owner.get(message_id) == user_id

    def claim_message(self, message_id: int, user_id: int) -> None:
        self._edit_owner[message_id] = user_id

    def release_message(self, message_id: int) -> None:
        self._edit_owner.pop(message_id, None)

    def __len__(self) -> int:
        return len(self._by_user)


def cleanup_dir(path: str, max_age_seconds: int = 3600) -> None:
    """Best-effort removal of temp files/directories older than max_age."""
    try:
        if not os.path.isdir(path):
            return
        now = time.time()
        for name in os.listdir(path):
            full = os.path.join(path, name)
            try:
                if os.path.isfile(full) and (now - os.path.getmtime(full)) > max_age_seconds:
                    os.remove(full)
                elif os.path.isdir(full):
                    cleanup_dir(full, max_age_seconds)
            except OSError:
                continue
    except OSError:
        logger.warning("Could not clean temp dir %s", path, exc_info=True)
