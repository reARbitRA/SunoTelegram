"""Unit tests for pure / offline-testable logic."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from sunobot.models import Track, extract_suno_urls
from sunobot.services.suno import SongInfo, _parse_clip, extract_song_id
from sunobot.utils import sanitise_filename


class UrlExtractionTests(unittest.TestCase):
    def test_extracts_song_urls(self):
        text = (
            "https://suno.com/song/aaBBccDD-1122-3344-5566-778899aabbcc and "
            "https://www.suno.com/@artist/song/11111111-2222-3333-4444-555555555555."
        )
        urls = extract_suno_urls(text)
        self.assertEqual(len(urls), 2)
        self.assertIn("song/aaBBccDD", urls[0])

    def test_ignores_non_suno(self):
        self.assertEqual(extract_suno_urls("https://example.com/x"), [])

    def test_deduplicates(self):
        u = "https://suno.com/song/abc"
        self.assertEqual(extract_suno_urls(f"{u} and {u}"), [u])


class SunoHelpersTests(unittest.TestCase):
    def test_extract_song_id(self):
        self.assertEqual(
            extract_song_id("https://suno.com/@a/song/aa-bb-cc-dd"),
            "aa-bb-cc-dd",
        )
        self.assertIsNone(extract_song_id("https://example.com/x"))

    def test_parse_clip(self):
        payload = {
            "id": "x",
            "title": "Neon Rain",
            "display_name": "wavecraft",
            "image_l": "http://cdn/img.png",
            "audio_url": "http://cdn/audio.mp3",
            "metadata": {"tags": "synthwave, language:en", "duration": 180},
        }
        info: SongInfo = _parse_clip(payload, "x", "http://suno.com/song/x")
        self.assertEqual(info.title, "Neon Rain")
        self.assertEqual(info.artist, "@wavecraft")
        self.assertEqual(info.language, "en")
        self.assertEqual(info.duration, 180)
        self.assertTrue(info.cover_url.startswith("http"))
        self.assertTrue(info.audio_url.endswith(".mp3"))

    def test_instrumental_detection(self):
        payload = {"id": "y", "is_instrumental": True, "title": "Beat"}
        info = _parse_clip(payload, "y", "u")
        self.assertEqual(info.language, "Instrumental")


class ModelTests(unittest.TestCase):
    def test_track_seeds_metadata(self):
        t = Track(
            display_index=1, url="u", title="T", artist="A",
            cover_url="http://c", language="en",
        )
        self.assertEqual(t.metadata.title, "T")
        self.assertEqual(t.metadata.cover, "http://c")
        self.assertTrue(t.ready)

    def test_track_not_ready_when_empty(self):
        t = Track(display_index=1, url="u")
        self.assertFalse(t.ready)


class SanitiseTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(sanitise_filename('A/B:C*?"|<>  .mp3'), "A_B_C")
        self.assertEqual(sanitise_filename(""), "untitled")
        self.assertEqual(sanitise_filename("My Song.mp3"), "My Song")

    def test_reserved_names(self):
        self.assertTrue(sanitise_filename("CON").startswith("_"))

    def test_long(self):
        long = "x" * 500
        self.assertLessEqual(len(sanitise_filename(long)), 120)


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        Image  # imported for its side effect of being available
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normalises_image(self):
        from PIL import Image

        src = self.tmp / "in.png"
        Image.new("RGBA", (400, 200), (10, 20, 30, 255)).save(src)
        from sunobot.services import artwork as art

        out = art.normalise_bytes(src.read_bytes())
        from PIL import Image as I

        f = self.tmp / "out.jpg"
        f.write_bytes(out)
        im = I.open(f)
        self.assertEqual(im.size, (art.TARGET_SIZE, art.TARGET_SIZE))
        self.assertEqual(im.mode, "RGB")


class AudioTests(unittest.TestCase):
    """End-to-end tag embedding. Requires ffmpeg on PATH (else skipped)."""

    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg") or shutil.which(
            "ffmpeg.exe"
        ) or os.environ.get("FFMPEG_LOCATION")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_mp3(self, name="t.mp3"):
        p = self.tmp / name
        if self.ffmpeg:
            os.system(
                f'"{self.ffmpeg}" -loglevel error -y -f lavfi -i '
                f'anullsrc=r=44100:cl=mono -t 1 -codec:a libmp3lame {p}'
            )
        else:
            # Fallback: craft nothing — test will be skipped by caller.
            p.write_bytes(b"")
        return p

    def test_embed_mp3(self):
        if not self.ffmpeg:
            self.skipTest("ffmpeg not available")
        p = self._make_mp3()
        from mutagen.mp3 import MP3
        from sunobot.services.audio import apply_metadata

        apply_metadata(
            str(p), title="Title", artist="Artist", album="Album",
            year="2024", genre="Pop", language="eng",
        )
        tags = MP3(str(p)).tags
        self.assertEqual(tags.getall("TIT2")[0].text, ["Title"])
        self.assertEqual(tags.getall("TPE1")[0].text, ["Artist"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
