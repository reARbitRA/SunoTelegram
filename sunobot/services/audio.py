"""Embed metadata + artwork into audio files with mutagen.

Supported containers:
    * MP3       -> ID3v2.3 tags + APIC cover art
    * M4A / MP4 -> Apple MP4 tags + ``covr`` artwork
    * Anything else (WAV, WebM/Opus) -> best-effort; logged and skipped
"""
from __future__ import annotations

import logging
from pathlib import Path

from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TLAN,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4Tags

logger = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".mp4", ".aac", ".wav", ".webm", ".opus"}


class AudioError(Exception):
    """Raised when metadata/artwork cannot be embedded."""


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _read_cover_bytes(artwork_path: str | None) -> bytes | None:
    if not artwork_path:
        return None
    path = Path(artwork_path)
    if not path.is_file():
        logger.warning("Cover art path missing: %s", artwork_path)
        return None
    return path.read_bytes()


def _embed_mp3(path: Path, tags: dict[str, str], cover: bytes | None) -> None:
    # We control every tag we write, so install a clean ID3v2.3 block.
    frame = ID3()
    frame.version = (2, 3, 0)  # best compatibility (Windows Explorer etc.)

    if tags.get("title"):
        frame.add(TIT2(encoding=3, text=tags["title"]))
    if tags.get("artist"):
        frame.add(TPE1(encoding=3, text=tags["artist"]))
    if tags.get("album"):
        frame.add(TALB(encoding=3, text=tags["album"]))
    if tags.get("album_artist"):
        frame.add(TPE2(encoding=3, text=tags["album_artist"]))
    if tags.get("year"):
        frame.add(TDRC(encoding=3, text=tags["year"]))
    if tags.get("language"):
        frame.add(TLAN(encoding=3, text=tags["language"]))
    if tags.get("genre"):
        frame.add(TCON(encoding=3, text=tags["genre"]))
    if tags.get("track"):
        frame.add(TRCK(encoding=3, text=tags["track"]))
    if tags.get("disc"):
        frame.add(TPOS(encoding=3, text=tags["disc"]))

    if cover:
        frame.add(
            APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=cover,
            )
        )

    audio = MP3(str(path))
    audio.tags = frame
    audio.save()


def _embed_mp4(path: Path, tags: dict[str, str], cover: bytes | None) -> None:
    audio = MP4(str(path))
    if audio.tags is None:
        audio.tags = MP4Tags()
    if tags.get("title"):
        audio.tags["\xa9nam"] = [tags["title"]]
    if tags.get("artist"):
        audio.tags["\xa9ART"] = [tags["artist"]]
    if tags.get("album"):
        audio.tags["\xa9alb"] = [tags["album"]]
    if tags.get("album_artist"):
        audio.tags["aART"] = [tags["album_artist"]]
    if tags.get("year"):
        audio.tags["\xa9day"] = [tags["year"]]
    if tags.get("genre"):
        audio.tags["\xa9gen"] = [tags["genre"]]
    if tags.get("track"):
        num = tags["track"].split("/")[0] if "/" in tags["track"] else tags["track"]
        try:
            audio.tags["trkn"] = [(int(num), 0)]
        except ValueError:
            pass
    if tags.get("disc"):
        num = tags["disc"].split("/")[0] if "/" in tags["disc"] else tags["disc"]
        try:
            audio.tags["disk"] = [(int(num), 0)]
        except ValueError:
            pass

    # Language isn't a standard MP4 key; stash it as an iTunes freeform atom.
    if tags.get("language"):
        try:
            atom = "----:com.apple.iTunes:LANGUAGE"
            audio.tags[atom] = [tags["language"].encode("utf-8")]
        except Exception:  # noqa: BLE001 - freeform atoms are optional
            logger.debug("Could not write freeform language atom.", exc_info=True)

    if cover:
        try:
            audio.tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        except Exception:  # noqa: BLE001
            logger.warning("Could not embed cover into MP4.", exc_info=True)
    audio.save()


def apply_metadata(
    audio_path: str,
    *,
    title: str = "",
    artist: str = "",
    album: str = "",
    album_artist: str = "",
    year: str = "",
    genre: str = "",
    language: str = "",
    track: str = "",
    disc: str = "",
    artwork_path: str | None = None,
) -> str:
    """Write tags + artwork into the file; returns a short human summary."""
    path = Path(audio_path)
    if not path.is_file():
        raise AudioError(f"Audio file not found: {audio_path}")

    ext = path.suffix.lower()
    if ext not in AUDIO_EXTS:
        raise AudioError(f"Unsupported audio container: {ext or '(none)'}")

    tags = {
        "title": _clean(title),
        "artist": _clean(artist),
        "album": _clean(album),
        "album_artist": _clean(album_artist),
        "year": _clean(year),
        "genre": _clean(genre),
        "language": _clean(language),
        "track": _clean(track),
        "disc": _clean(disc),
    }
    cover = _read_cover_bytes(artwork_path)

    applied = [k for k in ("title", "artist", "album", "year", "language", "genre") if tags[k]]

    if ext in (".mp3",):
        _embed_mp3(path, tags, cover)
    elif ext in (".m4a", ".mp4", ".aac"):
        _embed_mp4(path, tags, cover)
    else:
        logger.warning(
            "No tag writer for %s; skipping metadata embedding for %s.", ext, path.name
        )
        raise AudioError(f"No tag writer available for {ext} files.")

    summary = ", ".join(applied) if applied else "no textual tags"
    if cover:
        summary += " + cover art"
    else:
        summary += " (no cover art)"
    return summary
