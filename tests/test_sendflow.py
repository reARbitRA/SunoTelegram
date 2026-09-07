"""Tests the bot's file-sending logic against a stubbed Telegram context."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from sunobot.config import Settings
from sunobot.models import Track


class _FakeBot:
    def __init__(self):
        self.sent_groups = []
        self.sent_messages = []
        self.sent_audio = []

    async def send_media_group(self, chat_id, media):
        self.sent_groups.append([m for m in media])
        return []

    async def send_message(self, chat_id, text):
        self.sent_messages.append(text)
        return None

    async def send_audio(self, chat_id, audio, filename=None, caption=None):
        self.sent_audio.append((filename, caption))
        return None


class _FakeContext:
    def __init__(self):
        self.bot = _FakeBot()


def _make_mp3s(tmp: Path, count: int) -> list[Track]:
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe") or os.environ.get(
        "FFMPEG_LOCATION"
    )
    tracks = []
    for i in range(count):
        p = tmp / f"src{i}.mp3"
        if ffmpeg:
            os.system(
                f'"{ffmpeg}" -loglevel error -y -f lavfi -i '
                f'anullsrc=r=44100:cl=mono -t 1 -codec:a libmp3lame "{p}"'
            )
        else:
            p.write_bytes(b"0" * 100)
        t = Track(display_index=i + 1, url=f"http://suno.com/song/{i}")
        t.metadata.title = f"Song {i}"
        t.metadata.artist = "Artist"
        t.audio_path = str(p)
        t.downloaded = True
        tracks.append(t)
    return tracks


class SendFlowTests(unittest.TestCase):
    def setUp(self):
        from sunobot.bot import SunoBot

        self.tmp = Path(tempfile.mkdtemp())
        settings = Settings(
            bot_token="x", download_dir=self.tmp / "dl"
        )
        settings.prepare()
        self.bot = SunoBot(settings)
        self.ctx = _FakeContext()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def _run(self, tracks):
        from sunobot.session_state import BatchSession

        session = BatchSession(
            user_id=1,
            chat_id=999,
            tracks=tracks,
        )
        await self.bot._send_files(None, self.ctx, session, tracks, [])
        return session

    def test_sends_single_group_under_limit(self):
        import asyncio

        tracks = _make_mp3s(self.tmp, 3)
        asyncio.run(self._run(tracks))
        self.assertEqual(len(self.ctx.bot.sent_groups), 1)
        self.assertEqual(len(self.ctx.bot.sent_groups[0]), 3)
        # Confirmation message delivered.
        self.assertTrue(any("Done!" in m for m in self.ctx.bot.sent_messages))

    def test_chunks_over_ten_files(self):
        import asyncio

        tracks = _make_mp3s(self.tmp, 25)
        asyncio.run(self._run(tracks))
        # Groups of 10 + 10 + 5.
        sizes = [len(g) for g in self.ctx.bot.sent_groups]
        self.assertEqual(sum(sizes), 25)
        self.assertTrue(all(s <= 10 for s in sizes))

    def test_file_names_include_artist_and_title(self):
        import asyncio

        tracks = _make_mp3s(self.tmp, 1)
        tracks[0].metadata.artist = "My Artist"
        tracks[0].metadata.title = "Cool Song"
        asyncio.run(self._run(tracks))
        filename = self.ctx.bot.sent_groups[0][0].media.filename
        self.assertEqual(filename, "My Artist - Cool Song.mp3")


class OrchestrationTests(unittest.TestCase):
    """Verifies graceful handling when network/discovery fails (no internet)."""

    def setUp(self):
        from sunobot.bot import SunoBot

        self.tmp = Path(tempfile.mkdtemp())
        settings = Settings(bot_token="x", download_dir=self.tmp / "dl")
        settings.prepare()
        self.bot = SunoBot(settings)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prepare_one_records_error_when_download_fails(self):
        import asyncio
        from unittest.mock import patch

        from sunobot.session_state import BatchSession

        session = BatchSession(user_id=1, chat_id=1, tracks=[
            Track(display_index=1, url="http://suno.com/song/abc")
        ])
        track = session.tracks[0]

        async def go():
            with patch("sunobot.bot.fetch_song_info", side_effect=RuntimeError("no net")), \
                 patch("sunobot.bot.download_audio", side_effect=RuntimeError("dl failed")):
                await self.bot._prepare_one(session, track)
            return track

        asyncio.run(go())
        self.assertFalse(track.downloaded)
        self.assertIn("dl failed", track.error)

    def test_year_field_validates_input(self):
        import asyncio

        from sunobot.session_state import BatchSession

        session = BatchSession(user_id=1, chat_id=1, tracks=[
            Track(display_index=1, url="http://suno.com/song/abc")
        ])

        async def fake_reply(*args):
            return None

        class FakeMsg:
            reply_text = fake_reply

        class FakeUpdate:
            effective_message = FakeMsg()

        class FakeBot:
            async def delete_message(self, *a, **k):
                return None

        class FakeCtx:
            bot = FakeBot()

        async def apply_validated(raw):
            session.pending_field = "year"
            await self.bot._apply_field(FakeUpdate(), FakeCtx(), session, "year", raw)
            return session.current.metadata

        md = asyncio.run(apply_validated("20XX"))
        self.assertEqual(md.year, "")  # invalid year is dropped
        md = asyncio.run(apply_validated("2024"))
        self.assertEqual(md.year, "2024")

    def test_field_cleared_with_dash(self):
        import asyncio

        from sunobot.session_state import BatchSession

        session = BatchSession(user_id=1, chat_id=1, tracks=[
            Track(display_index=1, url="u")
        ])
        session.current.metadata.artist = "Someone"

        async def fake_reply(*args):
            return None

        class FakeMsg:
            reply_text = fake_reply

        class FakeUpdate:
            effective_message = FakeMsg()

        class FakeBot:
            async def delete_message(self, *a, **k):
                return None

        class FakeCtx:
            bot = FakeBot()

        async def go():
            await self.bot._apply_field(FakeUpdate(), FakeCtx(), session, "artist", "-")
            return session.current.metadata

        md = asyncio.run(go())
        self.assertEqual(md.artist, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
