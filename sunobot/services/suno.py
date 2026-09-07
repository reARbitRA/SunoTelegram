"""Discover song metadata & direct audio from the unofficial Suno studio API.

The Suno web app talks to ``https://studio-api.prod.suno.com``. A handful of
these endpoints are documented in various open-source reverse-engineered
clients and are used here purely to enrich the songs the user links with
accurate titles, cover art and (for the user's own, private songs) a direct
audio URL.

Endpoints used:
    * ``GET /api/song/{song_id}``   -> the user's own clip (returns ``audio_url``)
    * ``GET /api/clip/{song_id}?info=true`` -> a clip's public info block

Everything in this module is best-effort. If Suno changes the API or the
request requires an account cookie we do not have, the callers simply fall
back to the yt-dlp extractor, so the bot keeps working.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

STUDIO_API = "https://studio-api.prod.suno.com"
TIMEOUT_SECONDS = 15

_SONG_TOKEN = re.compile(r"/(?:song|clip)/([0-9a-fA-F-]{8,})")


class SunoError(Exception):
    """Raised when a Suno request cannot be completed."""


@dataclass
class SongInfo:
    """Normalised info about a Suno song."""

    song_id: str
    title: str = ""
    artist: str = ""
    language: str = ""
    cover_url: str = ""
    audio_url: str = ""
    duration: float | None = None
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def extract_song_id(url: str) -> str | None:
    """Return the UUID in a /song/{id} or /clip/{id} Suno URL, if any."""
    m = _SONG_TOKEN.search(url)
    return m.group(1) if m else None


def _parse_clip(payload: dict[str, Any], song_id: str, page_url: str) -> SongInfo:
    """Translate a Suno clip payload into a SongInfo."""
    title = payload.get("title") or payload.get("clip_name") or ""
    artist = (
        payload.get("artist")
        or payload.get("display_name")
        or payload.get("user", {}).get("username", "")
        or ""
    )
    if artist and not str(artist).startswith("@"):
        artist = f"@{artist}"

    language = ""
    meta = payload.get("metadata") or {}
    tags = meta.get("tags") or ""
    # Suno puts spoken language hints inside the style tags.
    lang_hint = re.search(r"language:([a-z-]+)", tags, re.IGNORECASE)
    if lang_hint:
        language = lang_hint.group(1)
    if not language and (payload.get("is_instrumental") or payload.get("instrumental")):
        language = "Instrumental"

    cover = (
        payload.get("image_l")
        or payload.get("image_url")
        or payload.get("image_prompt_large")
        or ""
    )
    audio = (
        payload.get("audio_url")
        or payload.get("audio_wav_url")
        or payload.get("audio_mp3_url")
        or ""
    )
    duration = payload.get("duration") or payload.get("metadata", {}).get("duration")

    return SongInfo(
        song_id=song_id,
        title=title,
        artist=artist,
        language=language,
        cover_url=cover,
        audio_url=audio,
        duration=float(duration) if duration else None,
        url=page_url,
        raw=payload,
    )


async def fetch_song_info(
    url: str,
    cookie: str = "",
    proxy: str = "",
) -> SongInfo:
    """Fetch rich info for a song URL.

    Works best for the caller's own songs (when ``cookie`` is provided). For
    publicly shared songs it still returns the visible title / cover art.
    """
    song_id = extract_song_id(url) or ""
    if not song_id:
        raise SunoError(f"Could not find a Suno song id in: {url}")

    page_url = url
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if cookie:
        headers["Cookie"] = cookie
    proxy_url = proxy or None

    # 1) The caller's own song endpoint (richest, includes audio_url).
    own_url = f"{STUDIO_API}/api/song/{song_id}"
    info = await _get_json(own_url, headers, proxy_url)
    if info is None or (isinstance(info, dict) and not info.get("id")):
        # 2) Public clip info block.
        clip_url = f"{STUDIO_API}/api/clip/{song_id}?info=true"
        info = await _get_json(clip_url, headers, proxy_url)

    if not info or not isinstance(info, dict):
        raise SunoError("Suno returned no data for this song.")

    return _parse_clip(info, song_id, page_url)


async def _get_json(
    url: str, headers: dict[str, str], proxy_url: str | None
) -> Any:
    """GET a JSON endpoint. Returns None on any HTTP/network problem."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    logger.info("Suno API returned HTTP %s for %s", resp.status, url)
                    return None
                try:
                    return await resp.json()
                except aiohttp.ContentTypeError:
                    return None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.debug("Suno API request failed for %s: %s", url, exc)
        return None
