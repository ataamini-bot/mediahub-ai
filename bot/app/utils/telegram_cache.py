"""Remove media cached by getFile; leave the Bot API session/database intact."""
import logging
import asyncio
import os
import time
from pathlib import Path

MEDIA_DIRS = {"documents", "photos", "videos", "video_notes", "voice", "music", "audio", "animations", "thumbnails"}


def owned_media(path):
    root = Path(os.getenv("TELEGRAM_BOT_API_DATA_DIR", "/var/lib/telegram-bot-api")).resolve()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return None
    path = Path(path)
    try:
        relative = path.resolve().relative_to(root)
    except (ValueError, OSError):
        return None
    # getFile returns <root>/<bot-token>/<media-kind>/<file>. Session and
    # database files are never candidates, including through symlinks.
    if len(relative.parts) != 3 or relative.parts[0] != token or relative.parts[1] not in MEDIA_DIRS:
        return None
    if path.is_symlink() or not path.is_file():
        return None
    return path


def release_cached_file(path):
    try:
        target = owned_media(path)
        if target:
            target.unlink(missing_ok=True)
    except OSError as exc:
        logging.getLogger(__name__).warning("Telegram media cache cleanup failed: %s", type(exc).__name__)


def sweep_cache():
    root = Path(os.getenv("TELEGRAM_BOT_API_DATA_DIR", "/var/lib/telegram-bot-api"))
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    cutoff = time.time() - 24*3600
    for folder in MEDIA_DIRS:
        for path in (root / token / folder).glob("*"):
            try:
                if max(path.stat().st_mtime, path.stat().st_atime) < cutoff:
                    release_cached_file(path)
            except OSError:
                continue


async def cleanup_loop():
    while True:
        try:
            await asyncio.to_thread(sweep_cache)
        except Exception as exc:
            logging.getLogger(__name__).warning("Telegram cache cleanup unavailable: %s", type(exc).__name__)
        await asyncio.sleep(60)
