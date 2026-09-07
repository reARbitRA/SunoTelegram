"""Formatting helpers for Telegram messages and inline keyboards."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Field order shown in the metadata editor.
METADATA_FIELDS = ["title", "artist", "album", "year", "language", "genre", "cover"]

FIELD_EMOJIS = {
    "title": "🎵",
    "artist": "👤",
    "album": "💿",
    "year": "🗓️",
    "language": "🌐",
    "genre": "🎸",
    "cover": "🖼️",
}

FIELD_NAMES = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "year": "Year",
    "language": "Language",
    "genre": "Genre",
    "cover": "Cover art",
}

# Natural language option set for genres. Users may type any custom genre.
GENRE_SUGGESTIONS = [
    "Pop", "Rock", "Hip-Hop", "R&B", "Jazz", "Blues", "Classical",
    "Electronic", "House", "Techno", "Country", "Folk", "Metal", "Punk",
    "Reggae", "Soul", "Funk", "Disco", "Indie", "Ambient", "Lo-Fi",
    "Cinematic", "Orchestral", "Acoustic", "Latin", "K-Pop", "J-Pop",
    "Soundtrack", "Trap", "Dubstep",
]

# Common ISO 639-1 languages / friendly names.
LANGUAGE_SUGGESTIONS = [
    "English", "Spanish", "French", "German", "Italian", "Portuguese",
    "Japanese", "Korean", "Chinese", "Hindi", "Arabic", "Russian",
    "Turkish", "Dutch", "Swedish", "Norwegian", "Polish", "Greek",
    "Thai", "Vietnamese", "Indonesian", "Instrumental",
]

INSTRUMENTAL = "Instrumental"


def _escape(text: str) -> str:
    """Make a string safe to render as plain inline text in HTML messages."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def field_line(field: str, value: str) -> str:
    emoji = FIELD_EMOJIS.get(field, "•")
    value = _escape(value) if value else "— not set —"
    return f"{emoji} <b>{FIELD_NAMES[field]}:</b> {value}"


def editor_caption(track, index: int, total: int) -> str:
    """Rich caption shown above the metadata editor keyboard."""
    md = track.metadata
    title = md.title or "(unknown title)"
    lines = [
        f"🎧 <b>Song {index} of {total}</b> — Edit metadata",
        "",
        f"Title: <i>{_escape(title)}</i>",
        "",
    ]
    for field in METADATA_FIELDS:
        lines.append(field_line(field, getattr(md, field)))
    return "\n".join(lines)


def build_editor_keyboard(track) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for field in METADATA_FIELDS:
        value = getattr(track.metadata, field)
        label = f"{FIELD_EMOJIS.get(field, '')} {FIELD_NAMES[field]}"
        if value:
            label += " ✓"
        elif field == "cover":
            label += " (add)"
        else:
            label += " (set)"
        rows.append([InlineKeyboardButton(label, callback_data=f"edit:{field}")])

    rows.append([
        InlineKeyboardButton("⬅️ Prev", callback_data="edit:__prev"),
        InlineKeyboardButton("Next ➡️", callback_data="edit:__next"),
    ])
    rows.append([InlineKeyboardButton("✅ Done editing", callback_data="edit:__done")])
    rows.append([InlineKeyboardButton("💾 Save & process", callback_data="edit:__save")])
    return InlineKeyboardMarkup(rows)


def song_summary(track, index: int, total: int) -> str:
    md = track.metadata
    return (
        f"🎼 <b>Song {index}/{total}</b>\n"
        + "\n".join(field_line(f, getattr(md, f)) for f in METADATA_FIELDS)
    )
