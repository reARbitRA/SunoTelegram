"""Download a Suno track at the highest available quality.

Strategy
--------
1. ``yt-dlp`` against the Suno page (or a direct audio URL discovered through
   the studio API). yt-dlp picks the best audio stream and keeps it **lossless
   relative to the source** (no generational re-encode).
2. If that produced a non-MP3 container and ffmpeg is available we convert to
   MP3 ``V0`` (the highest-quality variable MP3). Otherwise we deliver the
   native container (M4A/AAC can still be tagged fully).
3. As a last resort a plain HTTP stream downloader is used for a direct URL.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def ffmpeg_location() -> str | None:
    """Return an ffmpeg binary path if one is available."""
    override = os.environ.get("FFMPEG_LOCATION") or os.environ.get("PATH_TO_FFMPEG")
    if override:
        return override
    return shutil.which("ffmpeg")


def _make_cookie_file(cookie: str) -> str | None:
    """Write a Netscape cookie file yt-dlp accepts for a raw cookie header."""
    if not cookie:
        return None
    from tempfile import NamedTemporaryFile

    f = NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    f.write("# Netscape HTTP Cookie File\n")
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        f.write(f"\t{suno_domain()}\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")
    f.close()
    return f.name


def suno_domain() -> str:
    return ".suno.com"


async def run_ytdlp(url: str, dest_dir: Path, cookie: str, proxy: str) -> Path:
    """Download ``url`` with yt-dlp; return the produced audio file path."""
    out_dir = dest_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = _make_cookie_file(cookie) if cookie else None

    opts = {
        "format": "bestaudio[ext=mp3]/bestaudio/best",
        "outtmpl": str(out_dir / "%(title).200B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 6,
        "fragment_retries": 6,
        "continuedl": True,
        "restrictfilenames": False,
        "overwrites": True,
        "ignoreerrors": False,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if proxy:
        opts["proxy"] = proxy

    loop = asyncio.get_running_loop()
    result_path: Path | None = None

    def _run() -> None:
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return
            # The actual downloaded file name is computed from the template.
            request = {"id": info.get("id"), "title": info.get("title")}
            filename = ydl.prepare_filename(request)
            nonlocal result_path
            result_path = Path(filename)
            # Postprocessors can change the extension; find the newest matching
            # file in the output dir as a robust fallback.
            if not result_path.exists():
                candidates = sorted(
                    out_dir.iterdir(),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    result_path = candidates[0]

    try:
        await loop.run_in_executor(None, _run)
    except Exception as exc:  # noqa: BLE001 - surface any downloader failure
        logger.warning("yt-dlp failed for %s: %s", url, exc)
        raise RuntimeError(f"yt-dlp could not download the track: {exc}") from exc
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except OSError:
                pass

    if not result_path or not result_path.exists():
        raise RuntimeError("yt-dlp finished without producing an audio file.")

    return _finalise(result_path, dest_dir)


def _finalise(raw_path: Path, dest_dir: Path) -> Path:
    """Optionally transcode to MP3 V0 and return a cleaned up final file."""
    ext = raw_path.suffix.lower().lstrip(".")

    if ext != "mp3":
        ffmpeg = ffmpeg_location()
        if ffmpeg:
            final_path = raw_path.with_suffix(".mp3")
            cmd = [
                ffmpeg, "-y", "-i", str(raw_path),
                "-vn", "-codec:a", "libmp3lame", "-qscale:a", "0",
                str(final_path),
            ]
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    timeout=600,
                )
                raw_path.unlink(missing_ok=True)
                return final_path
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                logger.warning(
                    "MP3 conversion failed (%s); keeping original container.", exc
                )
                return raw_path
        # No ffmpeg: deliver native container (M4A/AAC still taggable).
        return raw_path

    return raw_path


async def download_audio(
    candidates: list[str],
    dest_dir: Path,
    cookie: str = "",
    proxy: str = "",
) -> Path:
    """Download from the first URL that works, returning the final audio path."""
    errors: list[str] = []
    for url in candidates:
        if not url:
            continue
        try:
            logger.info("Downloading audio from %s", url)
            return await run_ytdlp(url, dest_dir, cookie, proxy)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
            logger.info("Candidate failed, trying next.")
    raise RuntimeError(
        "All download attempts failed.\n" + "\n".join(errors[-3:])
    )
