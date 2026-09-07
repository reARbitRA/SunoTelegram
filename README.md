# 🎵 Suno Music Downloader Telegram Bot

A production-ready **Telegram bot** (Python, `python-telegram-bot` v20, fully
async) that downloads your **Suno.com** songs at the highest available quality,
lets you **edit each song's metadata & cover art**, **embeds** it into the audio
file, and finally **sends all finished tracks to your Saved Messages**.

## ✨ Features

- 🔗 Accepts **one or many Suno song URLs** in a single message.
- ⬇️ Downloads each track at the **highest available quality**:
  - Prefers the lossless-relative best audio stream (no generational re-encode).
  - Converts to **MP3 V0** when needed (requires `ffmpeg`), otherwise keeps the
    native container (M4A/AAC are fully taggable too).
- 🎛️ **Interactive metadata editor** per song, with prev/next navigation across
  the whole batch:
  - Title, Artist, Album, Year, Language, Genre
  - **Cover art**: send a photo and the bot embeds it (auto-square + resized)
    or keep Suno's artwork.
- 🏷️ Embeds the edited tags + cover into the file with `mutagen`
  (ID3v2 tags + `APIC` for MP3, Apple MP4 tags + `covr` for M4A/AAC).
- 📤 Sends **all finished files together** to the user's private chat
  (Saved Messages), grouped in media albums (Telegram caps albums at 10 files).
- 🧹 Auto-cleans its temp/download folders.
- 🛡️ Optional allow-listing of users and chats.

## 🏗️ Architecture

```
main.py                      CLI entry point (async runloop + graceful shutdown)
sunobot/
├── bot.py                   Central orchestrator (wiring + full download/edit/send flow)
├── config.py                Typed config loaded from .env / environment
├── formatting.py            Message captions, keyboards, human text
├── models.py                Data models + Suno URL extraction
├── session_state.py         Per-user batch store + conversation state
├── services/
│   ├── suno.py              Best-effort metadata discovery (studio-api.prod.suno.com)
│   ├── downloader.py        yt-dlp primary downloader + quality handling
│   ├── artwork.py           Fetch/normalise cover art (square JPEG)
│   └── audio.py             Embed tags + artwork via mutagen (MP3 / M4A / MP4)
└── utils/
    └── __init__.py          Filename sanitising, byte/duration formatting
```

### Why yt-dlp?
Suno actively fights third-party scrapers, so any hand-rolled direct API is
brittle. The bot therefore uses **yt-dlp as its engine** (it ships a dedicated
Suno extractor and handles auth cookies, retries and format selection) and uses
the unofficial Suno API **only to enrich metadata** (accurate titles, artist,
language, cover) and, when available, to obtain a direct audio URL that yt-dlp
can consume. If discovery fails the bot still works — it just shows empty
fields for you to fill in.

## 🚀 Quick start

### 1. Create your bot
Talk to [@BotFather](https://t.me/BotFather) and copy the token.

### 2. Configure
```bash
cp .env.example .env
# edit .env and set TELEGRAM_BOT_TOKEN=...
```

### 3. Install & run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ffmpeg is optional but recommended (needed for MP3 conversion)
#   Debian/Ubuntu : sudo apt-get install ffmpeg
#   macOS         : brew install ffmpeg

python main.py
```

Or with Docker:

```bash
docker build -t sunobot .
docker run -d --env-file .env -v "$(pwd)/downloads:/app/downloads" sunobot
```

### 4. Use it
1. Open your bot and press **Start**.
2. Paste one or more `suno.com/song/…` links.
3. Walk each song with the **⬅️ / ➡️** buttons, set **Title / Artist / Album /
   Year / Language / Genre / Cover art**.
4. Press **💾 Save & process all**.
5. Receive every finished file together in your chat (Saved Messages).

## ⚙️ Configuration

See [`.env.example`](.env.example) for all variables:

| Variable              | Required | Description                                            |
|-----------------------|----------|--------------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`  | ✅       | Token from @BotFather.                                 |
| `ALLOWED_USERS`       | –        | Comma-separated Telegram user ids allowed to use it.   |
| `ALLOWED_CHAT_IDS`    | –        | Comma-separated chat ids allowed to use it.            |
| `DOWNLOAD_DIR`        | –        | Folder for downloads/temp (default `downloads`).       |
| `MAX_SONGS_PER_BATCH` | –        | Cap songs per message (default 20).                    |
| `SUNO_COOKIE`         | –        | `_session` cookie to fetch your **private** songs.     |
| `HTTP_PROXY`          | –        | Proxy for all outbound HTTP.                           |
| `LOG_LEVEL`           | –        | `DEBUG`/`INFO`/… (default `INFO`).                     |

## 🧪 Tests

```bash
source .venv/bin/activate
pip install pytest
pytest -q
```

(Unit tests that need real audio/ffmpeg are skipped automatically if `ffmpeg`
isn't on the `PATH`.)

## ⚠️ Notes & disclaimer

- **Suno's public audio is protected.** Reliable, full-quality downloads require
  your own Suno account cookie (`SUNO_COOKIE`) for *private* creations — which is
  the intended, legitimate use case (downloading your own generated music).
- Please respect Suno's [Terms of Service](https://suno.com/terms) and download
  only content you are entitled to.
- Telegram limits albums to 10 files; larger batches are sent as multiple albums.
