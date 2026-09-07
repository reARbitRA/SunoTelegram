"""Pure data models used across the bot."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SUNO_URL_RE = re.compile(
    r"https?://(?:(?:www|app|studio)\.)?(suno\.com|suno\.ai)/[^\s]+", re.IGNORECASE
)


def extract_suno_urls(text: str) -> list[str]:
    """Extract every Suno URL from a chunk of user text."""
    seen: list[str] = []
    for match in _SUNO_URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:)]}")
        if url not in seen:
            seen.append(url)
    return seen


@dataclass
class Metadata:
    """User editable metadata for one track."""

    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    language: str = ""
    genre: str = ""
    # Path (or remote URL) to the artwork that should be embedded.
    cover: str = ""

    def is_ready(self) -> bool:
        """A track is ready to process once it has a title and artist."""
        return bool(self.title) or bool(self.artist)

    @property
    def cover_filename(self) -> str:
        if not self.cover:
            return ""
        import os

        return os.path.basename(self.cover)


@dataclass
class Track:
    """A single song being processed in the current batch."""

    display_index: int  # 1-based position in the batch
    url: str
    title: str = ""           # title discovered from Suno/yt-dlp
    artist: str = ""          # artist discovered from Suno/yt-dlp
    language: str = ""        # language discovered from Suno/yt-dlp
    cover_url: str = ""       # remote artwork discovered from Suno/yt-dlp
    audio_path: str = ""      # local file after download
    downloaded: bool = False
    error: str = ""
    metadata: Metadata = field(default_factory=Metadata)
    embed_ok: bool = False
    embed_error: str = ""

    def __post_init__(self) -> None:
        # Seed editable metadata from anything we discovered.
        self.metadata.title = self.title
        self.metadata.artist = self.artist
        self.metadata.cover = self.cover_url
        if not self.metadata.language:
            self.metadata.language = self.language

    @property
    def ready(self) -> bool:
        """True once the user has confirmed this track (title or artist)."""
        return self.metadata.is_ready()
