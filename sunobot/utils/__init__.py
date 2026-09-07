"""Small helpers for sanitising filenames, sizes and Telegram text."""
from __future__ import annotations

import re

# Characters forbidden in Windows filenames.
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_EXT = re.compile(
    r"\.(mp3|m4a|mp4|wav|flac|aac|ogg|opus|webm)$", re.IGNORECASE
)
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitise_filename(name: str, default: str = "untitled") -> str:
    """Return a safe, filesystem-friendly title (without an extension)."""
    value = _INVALID_FILENAME_CHARS.sub("_", str(name or ""))
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"_+", "_", value)
    value = _TRAILING_EXT.sub("", value)
    value = value.strip().strip(" ._").strip("_")
    if not value:
        return default
    if value.upper() in _RESERVED_NAMES:
        value = f"_{value}"
    return value[:120] or default


def format_bytes(num: float | int | None) -> str:
    """Format a byte count into a human readable string."""
    if num is None:
        return "unknown"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def format_duration(seconds: float | int | None) -> str:
    """Format seconds into mm:ss or hh:mm:ss."""
    if not seconds:
        return "--:--"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
