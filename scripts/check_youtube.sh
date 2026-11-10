#!/usr/bin/env bash
# Metadata only. No media download, account cookies or Telegram messages.
set -Eeuo pipefail
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"
[[ $# -eq 1 ]] || { printf 'Usage: bash scripts/check_youtube.sh YOUTUBE_URL\n'; exit 2; }
docker compose exec -T backend python -m app.services.youtube_health
docker compose exec -T backend python - "$1" <<'PY'
import signal
import sys
from urllib.parse import urlsplit
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from app.services.download import _get_media_info_options

url = sys.argv[1]
host = (urlsplit(url).hostname or "").lower()
if urlsplit(url).scheme != "https" or not any(
    host == domain or host.endswith("." + domain)
    for domain in ("youtube.com", "youtu.be", "youtube-nocookie.com")
):
    raise SystemExit("Expected an HTTPS YouTube URL")

def timeout(*_):
    raise SystemExit("YOUTUBE_EXTRACTION=TIMEOUT")

signal.signal(signal.SIGALRM, timeout)
signal.alarm(60)
options = _get_media_info_options(url)
options.update(noplaylist=True, cachedir=False, socket_timeout=15,
               retries=0, extractor_retries=0, no_warnings=False)
try:
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    print("YOUTUBE_EXTRACTION=OK VIDEO_ID=" + str(info.get("id"))
          + " FORMATS=" + str(len(info.get("formats") or [])))
except DownloadError as exc:
    if "Sign in to confirm" in str(exc):
        raise SystemExit("YOUTUBE_EXTRACTION=LOGIN_REQUIRED") from None
    raise SystemExit("YOUTUBE_EXTRACTION=FAILED") from None
finally:
    signal.alarm(0)
PY
