"""Read-only worker diagnostic: fresh metadata and at most 1 KiB per audio format.

Run: python - JOB_ID [PREVIOUSLY_SUCCESSFUL_JOB_ID] < this_file.py
No files, downloads, queue changes, Telegram sends, recognition calls, or raw URLs.
"""
import json
import signal
import sys
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import parse_qs, urlsplit


def emit(key, value):
    print(key + "=" + json.dumps(value, ensure_ascii=False), flush=True)


def safe_reason(error):
    status = getattr(error, "status", None) or getattr(error, "code", None)
    if isinstance(status, int):
        return "HTTP_" + str(status)
    message = str(error).lower()
    for code in (401, 403, 429):
        if str(code) in message:
            return "HTTP_" + str(code)
    if "sign in" in message or "login" in message:
        return "LOGIN_REQUIRED"
    if isinstance(error, TimeoutError) or "timeout" in message or "timed out" in message:
        return "TIMEOUT"
    return type(error).__name__


class DiagnosticLogger:
    def __init__(self):
        self.flags = set()

    def debug(self, message, *args):
        message = str(message).lower()
        if "generating a" in message and "po token" in message:
            self.flags.add("PO_TOKEN_REQUESTED")
        if "[pot:bgutil:http]" in message and any(word in message for word in ("error", "failed")):
            self.flags.add("PO_PROVIDER_ERROR")
        if "po token" in message and "not provided" in message:
            self.flags.add("PO_TOKEN_MISSING")
        if "challenge" in message and ("failed" in message or "not solved" in message):
            self.flags.add("JS_CHALLENGE_FAILED")

    warning = error = debug


def probe(downloader, item):
    from yt_dlp.networking import Request
    url = item.get("url") or ""
    parts = urlsplit(url)
    result = {"format": str(item.get("format_id", "")), "ext": item.get("ext"),
              "codec": item.get("acodec"), "po_token_present": bool(parse_qs(parts.query).get("pot"))}
    if parts.scheme != "https" or not (parts.hostname or "").endswith(".googlevideo.com") or item.get("protocol") != "https":
        return {**result, "result": "NOT_DIRECT_HTTPS_AUDIO"}
    headers = {**(item.get("http_headers") or {}), "Range": "bytes=0-1023"}
    try:
        with downloader.urlopen(Request(url, headers=headers)) as response:
            size = len(response.read(1024))
            result.update(result="MEDIA_OK" if size else "EMPTY_RESPONSE", status=response.status, bytes=size)
    except Exception as error:
        result["result"] = safe_reason(error)
    return result


def inspect_job(job_id, url):
    from yt_dlp import YoutubeDL
    from app.services.media_tool_links import normalize_music_link
    from app.workers.tasks.download import _get_format_selector, _get_yt_dlp_options
    normalized = normalize_music_link(url)
    if urlsplit(normalized).hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
        emit("JOB", {"id": job_id, "result": "NOT_YOUTUBE"})
        return
    logger = DiagnosticLogger()
    options = _get_yt_dlp_options(normalized,
        format=_get_format_selector(normalized, None, None, "audio"),
        skip_download=True, noplaylist=True, cachedir=False, logger=logger, verbose=True,
        socket_timeout=12, retries=0, fragment_retries=0, extractor_retries=0,
        quiet=True, no_warnings=False)
    emit("PROFILE", {"job": job_id, "clients": options.get("extractor_args", {}).get("youtube", {}).get("player_client"),
                     "selector": options["format"]})
    try:
        signal.alarm(55)
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(normalized, download=False)
            chosen = (info.get("requested_downloads") or [info])[0]
            candidates = [chosen]
            # Compare one other audio encoding from the same extraction/client.
            alternative = next((f for f in reversed(info.get("formats") or [])
                if f.get("vcodec") == "none" and f.get("acodec") not in {None, "none"}
                and f.get("ext") != chosen.get("ext") and f.get("protocol") == "https"), None)
            if alternative:
                candidates.append(alternative)
            for item in candidates:
                result = probe(downloader, item)
                emit("AUDIO", {"job": job_id, **result})
                if result["result"] in {"HTTP_429", "HTTP_401", "LOGIN_REQUIRED", "TIMEOUT"}:
                    return result["result"]
    except Exception as error:
        reason = safe_reason(error)
        emit("JOB", {"id": job_id, "result": reason})
        return reason
    finally:
        signal.alarm(0)
        emit("CHECKS", {"job": job_id, "flags": sorted(logger.flags)})


def deadline(*_):
    raise TimeoutError("Diagnostic deadline")


def main():
    from sqlalchemy import text
    from app.db.worker_session import WorkerSessionLocal
    from app.services.youtube_health import provider_health
    if not 1 <= len(sys.argv[1:]) <= 2 or any(not arg.isdecimal() for arg in sys.argv[1:]):
        raise SystemExit("Usage: python - JOB_ID [CONTROL_JOB_ID]")
    versions = {}
    for package in ("yt-dlp", "yt-dlp-ejs", "bgutil-ytdlp-pot-provider"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "NOT_INSTALLED"
    emit("VERSIONS", versions)
    emit("YOUTUBE_PROVIDER", provider_health())
    signal.signal(signal.SIGALRM, deadline)
    with WorkerSessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        jobs = [(int(arg), db.scalar(text("SELECT source_url FROM download_jobs WHERE id=:id AND media_type='audio'"),
                                     {"id": int(arg)})) for arg in sys.argv[1:]]
    for job_id, url in jobs:
        if not url:
            emit("JOB", {"id": job_id, "result": "AUDIO_JOB_NOT_FOUND"})
        elif inspect_job(job_id, url) in {"HTTP_429", "HTTP_401", "LOGIN_REQUIRED", "TIMEOUT"}:
            break


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit("CHECK_FAILED", {"error": safe_reason(error)})
        raise SystemExit(1) from None
