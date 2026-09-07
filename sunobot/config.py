"""Application configuration loaded from the environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - python-dotenv is optional
    pass


def _csv_ints(value: str | None) -> list[int]:
    if not value:
        return []
    result: list[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            continue
    return result


@dataclass
class Settings:
    """Typed, validated configuration for the bot."""

    bot_token: str
    allowed_users: list[int] = field(default_factory=list)
    allowed_chat_ids: list[int] = field(default_factory=list)
    download_dir: Path = field(default_factory=lambda: Path("downloads"))
    max_songs_per_batch: int = 20
    suno_cookie: str = ""
    http_proxy: str = ""
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = env or dict(os.environ)
        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is not set. Please create a bot with "
                "@BotFather and put its token in your .env file."
            )
        download_dir = Path(env.get("DOWNLOAD_DIR") or "downloads")
        try:
            max_batch = int(env.get("MAX_SONGS_PER_BATCH") or 20)
        except ValueError:
            max_batch = 20
        return cls(
            bot_token=token,
            allowed_users=_csv_ints(env.get("ALLOWED_USERS")),
            allowed_chat_ids=_csv_ints(env.get("ALLOWED_CHAT_IDS")),
            download_dir=download_dir,
            max_songs_per_batch=max(min(max_batch, 50), 1),
            suno_cookie=(env.get("SUNO_COOKIE") or "").strip(),
            http_proxy=(env.get("HTTP_PROXY") or "").strip(),
            log_level=(env.get("LOG_LEVEL") or "INFO").upper(),
        )

    def prepare(self) -> None:
        """Create the download directory if it does not exist yet."""
        self.download_dir.mkdir(parents=True, exist_ok=True)
