"""One private directory per attempt; no user paths or URLs are opened here."""
import os
from pathlib import Path
from uuid import UUID

MAX_INPUT_BYTES = {"music": 25 * 1024**2, "portrait": 10 * 1024**2}


def root():
    return Path(os.environ.get("DOWNLOAD_DIR", "/app/downloads")).resolve() / "tools"


def folder(telegram_id, request_id):
    base = root()
    name = f"{int(telegram_id)}-{UUID(str(request_id)).hex}"
    path = base / name
    if base.is_symlink() or path.is_symlink() or path.resolve().parent != base.resolve():
        raise ValueError("Unsafe media path")
    return path


def input_file(telegram_id, request_id, kind):
    path = folder(telegram_id, request_id) / "input"
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT_BYTES[kind]:
        raise ValueError("Invalid or expired input")
    return path


def remove_files(telegram_id, request_id, *, keep_result=False):
    path = folder(telegram_id, request_id)
    # Never follow user symlinks or recurse into unexpected subdirectories.
    if path.is_dir():
        for temporary in path.glob("link-source.*"):
            if temporary.is_file() or temporary.is_symlink():
                temporary.unlink(missing_ok=True)
        for name in ("input", "sample.wav", "photo.png", "result.jpg", "result.tmp"):
            if keep_result and name == "result.jpg":
                continue
            (path / name).unlink(missing_ok=True)
        try:
            path.rmdir()
        except OSError:
            pass
