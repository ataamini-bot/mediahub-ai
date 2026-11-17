"""Read-only worker diagnostic: fresh metadata and bounded media requests.

Run: python - JOB_ID [PREVIOUSLY_SUCCESSFUL_JOB_ID] < this_file.py
Or:  python - --latest --stored-only < this_file.py
Or:  python - --latest --transfer-check < this_file.py
Transfer check inspects only the newest failed music download (or latest if none
failed), reading at most 1 KiB using each of two request styles per audio format.
No files, downloads, queue changes, Telegram sends, recognition calls, or raw URLs.
"""
import json
import re
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
    http = re.search(r"(?:http(?:\s+error)?|status(?:\s+code)?)\s*[:=]?\s*([45]\d\d)\b", message)
    if http:
        return "HTTP_" + http[1]
    if "sign in" in message or "login" in message:
        return "LOGIN_REQUIRED"
    if isinstance(error, TimeoutError) or "timeout" in message or "timed out" in message:
        return "TIMEOUT"
    for needles, reason in (
        (("requested format is not available", "no video formats", "no usable formats"), "NO_FORMATS"),
        (("video unavailable", "video is unavailable", "private video", "has been removed"), "SOURCE_UNAVAILABLE"),
        (("confirm your age", "age-restricted"), "AGE_RESTRICTED"),
        (("no space left", "disk quota exceeded"), "DISK_FULL"),
        (("file-size limit", "file size limit", "size limit exceeded"), "SIZE_LIMIT"),
        (("permission denied", "read-only file system"), "FILE_PERMISSION"),
        (("unknown encoder", "encoder not found"), "ENCODER_MISSING"),
        (("ffmpeg is not available", "unable to start ffmpeg"), "FFMPEG_MISSING"),
        (("channel layout", "invalid number of channels"), "AUDIO_CHANNEL_LAYOUT"),
        (("invalid data found", "moov atom not found", "not a readable audio/video"), "INVALID_MEDIA"),
        (("input file has no audio", "matches no streams"), "AUDIO_STREAM_MISSING"),
        (("error while opening encoder", "error initializing output stream"), "ENCODER_INITIALIZATION"),
        (("unable to choose an output format", "requested output format", "not a suitable output format"), "OUTPUT_FORMAT"),
        (("invalid argument", "unrecognized option", "option not found"), "INVALID_ARGUMENT"),
        (("no such file", "no output files", "no valid output", "did not create a valid output"), "FILE_MISSING"),
        (("ffmpeg", "conversion"), "CONVERSION_FAILED"),
        (("connection reset", "connection refused", "network is unreachable"), "CONNECTION_FAILED"),
    ):
        if any(needle in message for needle in needles):
            return reason
    return type(error).__name__


def recorded_failure(job):
    """Classify the failure that actually happened, before any new network test."""
    message = str(job.get("error_message") or "")
    lower = message.lower()
    stage = "unknown"
    if "unable to download video data" in lower:
        stage = "media_transfer"
    elif "ffmpeg media conversion" in lower or "ffmpeg conversion" in lower or "converted output" in lower:
        stage = "conversion"
    elif "requested format" in lower or "[youtube]" in lower:
        stage = "extraction"
    result = {"id": job["id"], "status": job["status"], "format": job.get("output_format"),
            "time": str(job.get("created_at") or ""), "stage": stage,
            "reason": safe_reason(RuntimeError(message)) if message else "NONE"}
    prefix = "FFmpeg media conversion failed:"
    if message.startswith(prefix):
        # Only our local encoder's bounded stderr, never extractor/provider bodies.
        detail = message[len(prefix):].strip()
        detail = re.sub(r"\x1b\[[0-9;]*m", "", detail)
        detail = re.sub(r"(?i)\b(?:https?|rtmps?)://\S+", "[url]", detail)
        detail = re.sub(r"(?im)^.*(?:authorization|cookie|api[_-]?(?:key|hash)|password|secret|token)\b.*$", "[redacted]", detail)
        detail = re.sub(r"\b\d{5,20}:[A-Za-z0-9_-]{20,}", "[redacted]", detail)
        detail = re.sub(r"(?<!\w)/[^\s\]\"']+", "[path]", detail)
        result["encoder_detail"] = detail[-1500:]
    return result


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


def probe(downloader, item, *, transfer=False):
    from yt_dlp.networking import Request
    url = item.get("url") or ""
    parts = urlsplit(url)
    result = {"format": str(item.get("format_id", "")), "ext": item.get("ext"),
              "codec": item.get("acodec"), "po_token_present": bool(parse_qs(parts.query).get("pot"))}
    if parts.scheme != "https" or not (parts.hostname or "").endswith(".googlevideo.com") or item.get("protocol") != "https":
        return {**result, "result": "NOT_DIRECT_HTTPS_AUDIO"}
    headers = {**(item.get("http_headers") or {}), "Range": "bytes=0-1023"}
    try:
        if transfer:
            from yt_dlp.downloader.http import HttpFD
            original_open = downloader.urlopen

            class ProbeComplete(Exception):
                pass

            def bounded_open(request):
                # Use the *actual* production HTTP downloader's Range, headers
                # and extensions. Stop before it can write any media bytes.
                value = str(request.headers.get("Range", ""))
                result["request_range"] = value if re.fullmatch(r"bytes=\d+-\d*", value) else "NONE"
                try:
                    with original_open(request) as response:
                        size = len(response.read(1024))
                        result.update(result="MEDIA_OK" if size else "EMPTY_RESPONSE",
                                      status=response.status, bytes=size)
                except Exception as error:
                    result["result"] = safe_reason(error)
                raise ProbeComplete()

            downloader.urlopen = bounded_open
            try:
                http = HttpFD(downloader, {**downloader.params, "quiet": True,
                    "noprogress": True, "continuedl": False, "retries": 0})
                http.real_download("-", item)
                result.setdefault("result", "TRANSFER_NOT_STARTED")
            except ProbeComplete:
                pass
            finally:
                downloader.urlopen = original_open
        else:
            with downloader.urlopen(Request(url, headers=headers)) as response:
                size = len(response.read(1024))
                result.update(result="MEDIA_OK" if size else "EMPTY_RESPONSE", status=response.status, bytes=size)
    except Exception as error:
        result["result"] = safe_reason(error)
    return result


def inspect_job(job_id, url, *, transfer_check=False):
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
                for transfer in ((True, False) if transfer_check else (False,)):
                    result = probe(downloader, item, transfer=transfer)
                    emit("AUDIO", {"job": job_id, "probe": "downloader" if transfer else "range_1k", **result})
                    if result["result"] in {"HTTP_429", "HTTP_401", "LOGIN_REQUIRED", "TIMEOUT"}:
                        return result["result"]
    except Exception as error:
        reason = safe_reason(error)
        emit("JOB", {"id": job_id, "stage": "fresh_extraction", "result": reason})
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
    args = sys.argv[1:]
    stored_only = "--stored-only" in args
    transfer_check = "--transfer-check" in args
    args = [arg for arg in args if arg not in {"--stored-only", "--transfer-check"}]
    latest = args == ["--latest"]
    if ((stored_only and transfer_check) or (transfer_check and not latest and len(args) != 1)
            or (not latest and (not 1 <= len(args) <= 2 or any(not arg.isdecimal() for arg in args)))):
        raise SystemExit("Usage: python - JOB_ID [CONTROL_JOB_ID] | --latest [--stored-only | --transfer-check]")
    versions = {}
    for package in ("yt-dlp", "yt-dlp-ejs", "bgutil-ytdlp-pot-provider"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "NOT_INSTALLED"
    emit("VERSIONS", versions)
    with WorkerSessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        columns = "SELECT j.id, j.source_url, j.status::text, j.output_format, j.created_at, j.error_message FROM download_jobs j "
        if latest:
            jobs = db.execute(text(columns + """
                WHERE j.media_type='audio' AND EXISTS (
                    SELECT 1 FROM media_tool_requests r
                    WHERE r.kind='music' AND (r.download_job_id=j.id OR EXISTS (
                        SELECT 1 FROM json_each(coalesce(r.downloads, '{}'::json)) entry
                        WHERE entry.value->>'job_id'=j.id::text)))
                ORDER BY j.id DESC LIMIT 3""")).mappings().all()
        else:
            jobs = [db.execute(text(columns + "WHERE j.id=:id AND j.media_type='audio'"),
                               {"id": int(arg)}).mappings().first() for arg in args]
    found = [job for job in jobs if job]
    if transfer_check and found:
        found = [next((job for job in found if job["status"] == "FAILED"), found[0])]
    if not found:
        emit("JOB", {"result": "AUDIO_JOB_NOT_FOUND"})
    for job in found:
        emit("RECORDED", recorded_failure(job))
    if stored_only or not found:
        return
    emit("YOUTUBE_PROVIDER", provider_health())
    signal.signal(signal.SIGALRM, deadline)
    for job in found:
        if inspect_job(job["id"], job["source_url"], transfer_check=transfer_check) in {"HTTP_429", "HTTP_401", "LOGIN_REQUIRED", "TIMEOUT"}:
            break


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit("CHECK_FAILED", {"error": safe_reason(error)})
        raise SystemExit(1) from None
