"""Download + normalise cover art (squares it, resizes, re-encodes to JPEG)."""
from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

import aiohttp
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Covers are stored as square JPEGs ~1000px, plenty for audio tags.
TARGET_SIZE = 1000
JPEG_QUALITY = 90
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MB sanity cap
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


class ArtworkError(Exception):
    """Raised when cover art cannot be fetched or decoded."""


async def fetch_bytes(url: str, cookie: str = "", proxy: str = "") -> bytes:
    """Download a remote image into memory."""
    headers = {"User-Agent": _USER_AGENT}
    if cookie:
        headers["Cookie"] = cookie
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                proxy=proxy or None,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise ArtworkError(
                        f"Could not download cover art (HTTP {resp.status})."
                    )
                data = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ArtworkError(f"Cover art download failed: {exc}") from exc
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ArtworkError("Cover art file is unexpectedly large.")
    if not data:
        raise ArtworkError("Cover art came back empty.")
    return data


def normalise_bytes(data: bytes) -> bytes:
    """Normalise raw image bytes into a square JPEG suitable for audio tags."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ArtworkError("The image could not be read.") from exc

    img = ImageOps.exif_transpose(img)  # honour EXIF orientation
    # Fill-transparent -> white then convert to RGB.
    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")

    width, height = img.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((TARGET_SIZE, TARGET_SIZE), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


async def ensure_artwork(source: str, dest_dir: Path, cookie: str = "", proxy: str = "") -> Path:
    """Turn ``source`` (URL or local file) into a normalised JPEG file in ``dest_dir``.

    Returns the path to the produced file. Raises :class:`ArtworkError` on any
    failure so callers can decide whether the error is fatal or just means the
    cover is skipped.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    source = (source or "").strip()

    if source.lower().startswith(("http://", "https://")):
        data = await fetch_bytes(source, cookie=cookie, proxy=proxy)
    else:
        path = Path(source)
        if not path.is_file():
            raise ArtworkError("The artwork file could not be found on disk.")
        data = path.read_bytes()

    jpeg = normalise_bytes(data)

    # Keep a deterministic-ish name: hash of content keeps it stable & unique.
    import hashlib

    digest = hashlib.sha1(jpeg).hexdigest()[:12]
    out_path = dest_dir / f"cover_{digest}.jpg"
    if not out_path.exists():
        out_path.write_bytes(jpeg)
    return out_path
