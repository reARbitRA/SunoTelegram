"""Central bot object: wiring + all orchestration logic.

This module keeps the entire multi-step download / edit / embed / send flow in
one readable place and registers every Telegram handler on the application.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

from .config import Settings
from .formatting import (
    GENRE_SUGGESTIONS,
    LANGUAGE_SUGGESTIONS,
    METADATA_FIELDS,
    editor_caption,
)
from .models import Track, extract_suno_urls
from .services import artwork as artwork_service
from .services.audio import apply_metadata
from .services.downloader import download_audio
from .services.suno import fetch_song_info
from .session_state import BatchSession, BatchStore, cleanup_dir
from .utils import sanitise_filename

logger = logging.getLogger(__name__)

WELCOME = (
    "🎵 <b>Suno Music Downloader</b>\n\n"
    "Send me one or more <b>suno.com</b> song links and I'll:\n"
    "1️⃣ Download each song at the highest available quality\n"
    "2️⃣ Let you edit the metadata & cover art\n"
    "3️⃣ Embed everything into the audio file\n"
    "4️⃣ Send all the finished files to your Saved Messages\n\n"
    "Just paste a link to get started 👇"
)

FIELD_HINTS = {
    "title": "Send the new <b>title</b>.",
    "artist": "Send the <b>artist / band</b> name.",
    "album": "Send the <b>album</b> name.",
    "year": "Send the release <b>year</b> (e.g. <code>2024</code>).",
    "language": "Send a language (or tap one below):",
    "genre": "Send a genre (or tap one below):",
}

_SUGGESTION_ROWS = {
    "language": LANGUAGE_SUGGESTIONS,
    "genre": GENRE_SUGGESTIONS,
}


class SunoBot:
    """High level orchestrator bound to one python-telegram-bot application."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.prepare()
        self.store = BatchStore(settings)
        self._stop_event: Optional[asyncio.Event] = None
        self.app: Optional[Application] = None

    # ------------------------------------------------------------------ utils
    def is_allowed(self, user_id: Optional[int], chat_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        if not self.settings.allowed_users and not self.settings.allowed_chat_ids:
            return True
        if user_id in self.settings.allowed_users:
            return True
        if chat_id is not None and chat_id in self.settings.allowed_chat_ids:
            return True
        return False

    def session_for_user(self, user_id: int) -> Optional[BatchSession]:
        return self.store.get_for_user(user_id)

    # ------------------------------------------------------------------ paths
    def user_dir(self, session: BatchSession) -> Path:
        return self.settings.download_dir / f"u{session.user_id}"

    def covers_dir(self, session: BatchSession) -> Path:
        return self.user_dir(session) / "covers"

    def covers_work(self, session: BatchSession) -> Path:
        return self.user_dir(session) / "cover_work"

    # ------------------------------------------------------------ lifecycle
    async def build_application(self) -> Application:
        defaults = Defaults(parse_mode=ParseMode.HTML)
        builder = ApplicationBuilder().token(self.settings.bot_token).defaults(defaults)
        if self.settings.http_proxy:
            builder = builder.proxy(self.settings.http_proxy)
        app = builder.build()
        app.bot_data["sunobot"] = self
        self.app = app

        private = filters.ChatType.PRIVATE

        app.add_handler(CommandHandler("start", self.h_start, filters=private))
        app.add_handler(CommandHandler("help", self.h_start, filters=private))
        app.add_handler(CommandHandler("new", self.h_start, filters=private))
        app.add_handler(CommandHandler("cancel", self.h_cancel, filters=private))

        app.add_handler(MessageHandler(filters.PHOTO & private, self.h_photo))
        app.add_handler(MessageHandler(filters.TEXT & private, self.h_text))
        app.add_handler(CallbackQueryHandler(self.h_callback))
        return app

    async def run(self) -> None:
        self._stop_event = asyncio.Event()
        app = await self.build_application()
        async with app:
            await app.start()
            logger.info("Suno bot is running via long polling.")
            try:
                await self._stop_event.wait()
            finally:
                await app.stop()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    # ----------------------------------------------------------- /start /help
    async def h_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_allowed(_uid(update), update.effective_chat.id):
            await update.effective_message.reply_text(
                "⛔ Sorry, you are not allowed to use this bot."
            )
            return
        await update.effective_message.reply_text(WELCOME)

    async def h_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = _uid(update)
        session = self.session_for_user(uid)
        if session:
            try:
                if session.edit_message_id:
                    await context.bot.delete_message(
                        session.chat_id, session.edit_message_id
                    )
            except Exception:  # noqa: BLE001
                pass
            await self._dismiss_prompts(context, session)
            self.store.drop(uid)
            await update.effective_message.reply_text(
                "🚫 Batch cancelled. Send a new Suno link whenever you're ready."
            )
        else:
            await update.effective_message.reply_text(
                "Nothing is running — send a Suno link to get started."
            )

    # ------------------------------------------------------------ text router
    async def h_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        uid = _uid(update)
        if not self.is_allowed(uid, update.effective_chat.id):
            await message.reply_text("⛔ You are not allowed to use this bot.")
            return
        text = (message.text or "").strip()
        if not text:
            return

        session = self.session_for_user(uid)
        urls = extract_suno_urls(text)

        # New song links always start/add a batch — they should never be
        # captured as a metadata value, even if a field is currently pending.
        if urls:
            # Drop any dangling pending capture state and start fresh.
            if session:
                session.pending_field = None
                session.pending_cover = False
                await self._dismiss_prompts(context, session)
            await self._handle_urls(update, context, urls)
            return

        # A pending editable field captures whatever the user types next.
        if session and session.pending_field:
            if await self._apply_field(update, context, session, session.pending_field, text):
                return

        if session:
            await message.reply_text(
                "🤔 I don't see a suno.com link there. Send one like\n"
                "<code>https://suno.com/song/xxxxxxxx</code>\n"
                "or press <code>/cancel</code> to start over."
            )
        else:
            await message.reply_text(WELCOME)

    # ------------------------------------------------------------ photo (cover)
    async def h_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        uid = _uid(update)
        if not self.is_allowed(uid, update.effective_chat.id):
            await message.reply_text("⛔ You are not allowed to use this bot.")
            return
        session = self.session_for_user(uid)
        if session is None or not session.pending_cover:
            await message.reply_text(
                "I wasn't expecting a photo right now. To use one as cover art, "
                "tap <b>Cover art</b> in the editor, then send the photo.\n"
                "Send a Suno link to get started, or /cancel to abort."
            )
            return

        track = session.current
        photo = message.photo[-1]  # largest size
        file = await context.bot.get_file(photo.file_id)
        out_dir = self.user_dir(session) / "user_cover"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"raw_{photo.file_unique_id}.jpg"
        await file.download_to_drive(dest)

        try:
            jpg = await artwork_service.ensure_artwork(str(dest), self.covers_dir(session))
            track.metadata.cover = str(jpg)
        except artwork_service.ArtworkError as exc:
            await message.reply_text(f"⚠️ Could not use that image: {exc}")
            return

        session.pending_cover = False
        await self._dismiss_prompts(context, session)
        await message.reply_text("🖼️ Cover art set ✓")
        await self._refresh_editor(context, session)

    # ------------------------------------------------------- add URLs / batch
    async def _handle_urls(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, urls: list[str]
    ):
        uid = _uid(update)
        chat = update.effective_chat
        cap = self.settings.max_songs_per_batch
        urls = urls[:cap]
        if not urls:
            return

        session = self.session_for_user(uid)
        if session is None:
            session = await self.store.create(uid, chat.id, urls)
            await update.effective_message.reply_text(
                f"✨ Got {len(urls)} song(s). Preparing them now…"
            )
        else:
            base = len(session.tracks)
            session.tracks.extend(
                Track(display_index=base + i + 1, url=u)
                for i, u in enumerate(urls)
            )
            await update.effective_message.reply_text(
                f"➕ Added {len(urls)} more → {len(session.tracks)} total."
            )

        # Discovery + download run in the background.
        asyncio.create_task(self._prepare_tracks(session))

        session.current_index = next(
            (i for i, t in enumerate(session.tracks) if not t.downloaded),
            0,
        )
        session.pending_field = None
        session.pending_cover = False
        await self._show_editor(update, context, session)

    # ------------------------------------------------------- background prep
    async def _prepare_tracks(self, session: BatchSession) -> None:
        tasks = [
            asyncio.create_task(self._prepare_one(session, t))
            for t in session.tracks
            if not t.downloaded and not t.error
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _prepare_one(self, session: BatchSession, track: Track) -> None:
        # 1) Best-effort metadata discovery (fills nice defaults).
        direct = ""
        try:
            info = await fetch_song_info(
                track.url,
                cookie=self.settings.suno_cookie,
                proxy=self.settings.http_proxy,
            )
            if info:
                if not track.metadata.title:
                    track.metadata.title = info.title
                if not track.metadata.artist:
                    track.metadata.artist = info.artist
                if not track.metadata.language:
                    track.metadata.language = info.language
                if not track.metadata.cover:
                    track.metadata.cover = info.cover_url
                direct = info.audio_url
        except Exception as exc:  # noqa: BLE001 - discovery is best-effort
            logger.info("Discovery skipped for %s: %s", track.url, exc)

        # 2) Download at the highest available quality. Use a per-track folder
        # so parallel background downloads never collide on file names.
        try:
            track_dir = self.user_dir(session) / f"track{track.display_index}"
            track_dir.mkdir(parents=True, exist_ok=True)
            path = await download_audio(
                [direct, track.url],
                dest_dir=track_dir,
                cookie=self.settings.suno_cookie,
                proxy=self.settings.http_proxy,
            )
            track.audio_path = str(path)
            track.downloaded = True
        except Exception as exc:  # noqa: BLE001
            track.error = str(exc)
            logger.error("Download failed for %s: %s", track.url, exc)

    # --------------------------------------------------------------- editor
    async def _show_editor(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, session: BatchSession
    ):
        message = update.effective_message or (
            update.callback_query.message if update.callback_query else None
        )
        track = session.current
        caption = editor_caption(track, session.current_index + 1, session.total)
        kb = self._editor_keyboard(session, track)

        if session.edit_message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=session.chat_id,
                    message_id=session.edit_message_id,
                    text=caption,
                    reply_markup=kb,
                )
                return
            except Exception:  # noqa: BLE001
                pass
        sent = await message.reply_text(caption, reply_markup=kb)
        session.edit_message_id = sent.message_id

    async def _refresh_editor(self, context: ContextTypes.DEFAULT_TYPE, session) -> None:
        if not session.edit_message_id:
            return
        track = session.current
        try:
            await context.bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.edit_message_id,
                text=editor_caption(track, session.current_index + 1, session.total),
                reply_markup=self._editor_keyboard(session, track),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not refresh editor: %s", exc)

    def _editor_keyboard(self, session: BatchSession, track: Track):
        rows: list[list[InlineKeyboardButton]] = []
        for field in METADATA_FIELDS:
            value = getattr(track.metadata, field)
            if field == "cover":
                label = "🖼️ Cover art" + (" ✓" if value else " (add)")
            else:
                label = f"✏️ {field.capitalize()}" + (" ✓" if value else "")
            rows.append([InlineKeyboardButton(label, callback_data=f"edit:{field}")])

        rows.append([
            InlineKeyboardButton("⬅️", callback_data="edit:__prev"),
            InlineKeyboardButton(
                f"🎼 {session.current_index + 1}/{len(session.tracks)}",
                callback_data="edit:__noop",
            ),
            InlineKeyboardButton("➡️", callback_data="edit:__next"),
        ])
        if len(session.tracks) > 1:
            idx = [
                InlineKeyboardButton(
                    ("•" if i == session.current_index + 1 else str(i)),
                    callback_data=f"edit:__idx:{i}",
                )
                for i in range(1, len(session.tracks) + 1)
            ]
            rows.append(idx)

        rows.append([
            InlineKeyboardButton("💾 Save & process all", callback_data="edit:__save")
        ])
        return InlineKeyboardMarkup(rows)

    # ----------------------------------------------------------- callbacks
    async def h_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        uid = query.from_user.id
        data = query.data or ""
        session = self.session_for_user(uid)
        if session is None:
            await query.answer(text="This batch is no longer active.", show_alert=True)
            return

        if data.startswith("edit:"):
            await self._callback_edit(update, context, session, data[len("edit:"):])
        elif data.startswith("field:"):
            await self._callback_suggestion(update, context, session, data[len("field:"):])

    async def _callback_edit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, session, action
    ):
        query = update.callback_query
        if action in ("__prev", "__next", "__idx:") or action.startswith("__idx:"):
            await query.answer()
            session.pending_field = None
            session.pending_cover = False
            await self._dismiss_prompts(context, session)
            if action == "__prev":
                session.move(-1)
            elif action == "__next":
                session.move(1)
            elif action.startswith("__idx:"):
                idx = int(action.split(":")[2]) - 1
                session.current_index = max(0, min(len(session.tracks) - 1, idx))
            await self._refresh_editor(context, session)
            return

        if action == "__noop":
            await query.answer()
            return

        if action == "__save":
            await query.answer()
            session.pending_field = None
            session.pending_cover = False
            await self._dismiss_prompts(context, session)
            await self._process_and_send(update, context, session)
            return

        # Otherwise it's a field name to edit.
        field = action
        await query.answer()

        if field == "cover":
            # Cover is captured via a *photo*, not text.
            session.pending_field = None
            session.pending_cover = True
            prompt = await query.message.reply_text(
                "🖼️ Send the cover photo you'd like to embed.\n"
                "(You can cancel with <code>/cancel</code> or just navigate away.)"
            )
            session.cover_prompt_message_id = prompt.message_id
            return

        session.pending_field = field
        session.pending_cover = False
        session.current_index = session.current_index

        if field in _SUGGESTION_ROWS:
            labels = _SUGGESTION_ROWS[field]
            buttons = [
                InlineKeyboardButton(label, callback_data=f"field:{label}")
                for label in labels
            ]
            rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
            rows.append([
                InlineKeyboardButton("➖ Clear", callback_data="field:__clear"),
                InlineKeyboardButton("✖️ Cancel", callback_data="field:__cancel"),
            ])
            kb = InlineKeyboardMarkup(rows)
            prompt = await query.message.reply_text(
                FIELD_HINTS[field], reply_markup=kb
            )
        else:
            prompt = await query.message.reply_text(
                FIELD_HINTS[field] + "\n(Send <code>-</code> to clear.)"
            )
        session.field_prompt_message_id = prompt.message_id

    async def _callback_suggestion(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, session, action
    ):
        query = update.callback_query
        field = session.pending_field
        if action == "__cancel":
            session.pending_field = None
            session.pending_cover = False
            await self._dismiss_prompts(context, session)
            await query.answer("Cancelled")
            await self._refresh_editor(context, session)
            return
        await query.answer()
        if action == "__clear":
            value = ""
        else:
            value = action
        if field:
            await self._set_field(context, session, field, value)
        await self._dismiss_prompts(context, session)
        await self._refresh_editor(context, session)

    # ----------------------------------------------------------- set a field
    async def _apply_field(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        session: BatchSession,
        field: str,
        raw: str,
    ) -> bool:
        value = raw.strip()
        if value == "-":
            value = ""
        if field == "year" and value:
            if not (value.isdigit() and 1900 <= int(value) <= 2100):
                value = ""
        await self._set_field(context, session, field, value)
        await self._dismiss_prompts(context, session)
        await update.effective_message.reply_text(
            f"✓ <b>{field.capitalize()}</b> updated."
        )
        await self._refresh_editor(context, session)
        return True

    async def _set_field(
        self, context: ContextTypes.DEFAULT_TYPE, session: BatchSession, field, value
    ):
        track = session.current
        setattr(track.metadata, field, value)
        session.pending_field = None
        session.pending_cover = False

    async def _dismiss_prompts(
        self, context: ContextTypes.DEFAULT_TYPE, session: BatchSession
    ) -> None:
        for message_id in (session.field_prompt_message_id,
                           session.cover_prompt_message_id):
            if message_id:
                try:
                    await context.bot.delete_message(session.chat_id, message_id)
                except Exception:  # noqa: BLE001 - already gone is fine
                    pass
        session.field_prompt_message_id = None
        session.cover_prompt_message_id = None

    # -------------------------------------------------------- process & send
    async def _process_and_send(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, session: BatchSession
    ):
        if session.processing:
            await update.callback_query.answer(
                text="Already processing…", show_alert=True
            )
            return
        session.processing = True
        origin = update.callback_query.message if update.callback_query else (
            update.effective_message
        )
        status = await origin.reply_text("💾 Processing your songs…")
        try:
            # Ensure every song is downloaded.
            for i, track in enumerate(session.tracks, start=1):
                if track.error or track.downloaded:
                    continue
                await status.edit_text(f"⏳ Downloading {i}/{session.total}…")
                await self._prepare_one(session, track)

            ok: list[Track] = []
            failed: list[Track] = []
            for i, track in enumerate(session.tracks, start=1):
                name = track.metadata.title or track.title
                if track.error or not track.downloaded or not os.path.exists(track.audio_path):
                    failed.append(track)
                    continue
                await status.edit_text(
                    f"🎨 Tagging {i}/{session.total}: {name or 'unknown'}"
                )
                try:
                    await self._embed_one(session, track)
                    ok.append(track)
                except Exception as exc:  # noqa: BLE001
                    track.embed_error = str(exc)
                    logger.error("Embedding failed: %s", exc)
                    failed.append(track)

            await status.edit_text("📤 Sending files to your Saved Messages…")
            await self._send_files(update, context, session, ok, failed)
            try:
                await status.delete()
            except Exception:  # noqa: BLE001
                pass
        finally:
            session.processing = False

    async def _embed_one(self, session: BatchSession, track: Track) -> None:
        md = track.metadata
        cover_path = None
        cover_source = md.cover
        if cover_source and cover_source != "-":
            try:
                cover_path = await artwork_service.ensure_artwork(
                    cover_source,
                    self.covers_work(session),
                    cookie=self.settings.suno_cookie,
                    proxy=self.settings.http_proxy,
                )
            except artwork_service.ArtworkError as exc:
                logger.warning("Cover skipped for %s: %s", track.title, exc)
                cover_path = None

        apply_metadata(
            track.audio_path,
            title=md.title,
            artist=md.artist,
            album=md.album,
            year=md.year,
            genre=md.genre,
            language=md.language,
            artwork_path=str(cover_path) if cover_path else None,
        )
        track.embed_ok = True

    async def _send_files(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        session: BatchSession,
        ok: list[Track],
        failed: list[Track],
    ):
        # Build (filepath, filename, caption) tuples.
        payloads: list[tuple[str, str, str]] = []
        for track in ok:
            path = Path(track.audio_path)
            md = track.metadata
            title = sanitise_filename(md.title or track.title or path.stem) or "track"
            ext = path.suffix.lower() or ".mp3"
            artist = md.artist or "Suno"
            filename = f"{artist} - {title}{ext}"
            caption = f"{artist} — {md.title or track.title}"
            payloads.append((str(path), filename, caption))

        if payloads:
            chunk = 10  # Telegram media-group limit
            for start in range(0, len(payloads), chunk):
                part = payloads[start:start + chunk]
                media = [
                    InputMediaAudio(media=open(p, "rb"), filename=f, caption=c)
                    for p, f, c in part
                ]
                try:
                    await context.bot.send_media_group(chat_id=session.chat_id, media=media)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Media group failed, sending individually: %s", exc)
                    for p, f, c in part:
                        with open(p, "rb") as fh:
                            await context.bot.send_audio(
                                chat_id=session.chat_id,
                                audio=fh,
                                filename=f,
                                caption=c,
                            )
        else:
            await context.bot.send_message(
                session.chat_id, "😕 No finished files were available to send."
            )

        lines = ["✅ <b>Done!</b>"]
        if ok:
            lines.append(f"🎼 Sent <b>{len(ok)}</b> song(s) to your Saved Messages:")
            for t in ok:
                lines.append(f"  • {t.metadata.title or t.title}")
        if failed:
            lines.append(f"\n⚠️ <b>{len(failed)}</b> song(s) could not be processed:")
            for t in failed:
                reason = t.error or t.embed_error or "unknown error"
                lines.append(f"  • {t.metadata.title or t.title or t.url} — {reason}")
        await context.bot.send_message(session.chat_id, "\n".join(lines))

        self.store.drop(session.user_id)
        asyncio.get_running_loop().create_task(
            self._cleanup_later(session)
        )

    async def _cleanup_later(self, session: BatchSession) -> None:
        await asyncio.sleep(120)
        try:
            cleanup_dir(str(self.user_dir(session)))
        except Exception:  # noqa: BLE001
            pass


def _uid(update: Update) -> int:
    return update.effective_user.id
