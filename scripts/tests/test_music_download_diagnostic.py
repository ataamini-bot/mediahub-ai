import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("music_download_diagnostic", Path(__file__).parents[1] / "diagnose_music_download.py")
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def test_diagnostic_errors_never_echo_secrets_or_urls():
    assert diagnostic.safe_reason(RuntimeError("HTTP Error 403 https://private.example/?token=secret")) == "HTTP_403"
    assert diagnostic.safe_reason(RuntimeError("secret message")) == "RuntimeError"
    logger = diagnostic.DiagnosticLogger()
    logger.debug("[pot:bgutil:http] Generating a gvs PO Token for mweb secret")
    logger.warning("[pot:bgutil:http] failed token=secret")
    assert logger.flags == {"PO_TOKEN_REQUESTED", "PO_PROVIDER_ERROR"}


@pytest.mark.parametrize("message,reason", [
    ("ERROR: [youtube] AbcDefGhI12: Requested format is not available", "NO_FORMATS"),
    ("FFmpeg media conversion failed: HTTP Error 403 https://private.test/?pot=secret", "HTTP_403"),
    ("FFmpeg media conversion failed: Unknown encoder 'libmp3lame'", "ENCODER_MISSING"),
    ("FFmpeg media conversion failed: Invalid argument", "INVALID_ARGUMENT"),
    ("FFmpeg media conversion failed: No space left on device", "DISK_FULL"),
    ("Converted output exceeds the plan file-size limit", "SIZE_LIMIT"),
    ("FFmpeg conversion did not create a valid output file", "FILE_MISSING"),
    ("private URL https://example.test/403?token=secret", "RuntimeError"),
])
def test_recorded_failure_is_classified_without_a_new_extraction(message, reason):
    row = {"id": 12, "status": "FAILED", "output_format": "mp3", "error_message": message}
    result = diagnostic.recorded_failure(row)
    assert result["reason"] == reason
    assert all(word not in str(result) for word in ("secret", "https://", "AbcDefGhI12"))
    if message.startswith("FFmpeg media conversion"):
        assert result["stage"] == "conversion"


def test_local_encoder_detail_keeps_error_but_redacts_paths_urls_and_credentials():
    row = {"id": 12, "status": "FAILED", "error_message": (
        "FFmpeg media conversion failed: [mp3 @ 0x123] Error while opening encoder\n"
        "Output /app/downloads/private-name.mp3: Invalid argument\n"
        "https://r1.googlevideo.com/videoplayback?pot=private\n"
        "Authorization: Bearer private\nCookie: private\ntoken=private\n")}
    detail = diagnostic.recorded_failure(row)["encoder_detail"]
    assert "Error while opening encoder" in detail and "Invalid argument" in detail
    assert all(value not in detail for value in ("private", "googlevideo", "/app", "Bearer"))
    row["error_message"] = "ERROR: upstream response body private"
    assert "encoder_detail" not in diagnostic.recorded_failure(row)


def test_probe_reads_only_one_kib_and_carries_extracted_headers():
    class Response:
        status = 206
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            assert size == 1024
            return b"a" * size
    class Downloader:
        def urlopen(self, request):
            assert request.headers['Range'] == 'bytes=0-1023'
            assert request.headers['User-Agent'] == 'extracted-agent'
            return Response()
    result = diagnostic.probe(Downloader(), {"url": "https://r1.googlevideo.com/videoplayback?pot=private",
        "protocol": "https", "format_id": "140", "ext": "m4a", "acodec": "mp4a.40.2",
        "http_headers": {"User-Agent": "extracted-agent"}})
    assert result["result"] == "MEDIA_OK" and result["bytes"] == 1024 and result["po_token_present"]
    assert "private" not in str(result) and "googlevideo" not in str(result)


@pytest.mark.parametrize("chunk_size", [0, 10 * 1024**2])
@pytest.mark.parametrize("rejected", [False, True])
def test_transfer_probe_uses_real_downloader_request_without_writing_or_full_download(monkeypatch, chunk_size, rejected):
    from yt_dlp import YoutubeDL
    from yt_dlp.downloader.http import HttpFD
    requests, reads, closes = [], [], []
    class Response:
        status = 206
        def __enter__(self): return self
        def __exit__(self, *args): closes.append(True)
        def read(self, size):
            reads.append(size)
            return b"x" * size
    def opener(request):
        requests.append(request)
        if rejected:
            raise RuntimeError("HTTP Error 403 https://private.example/?pot=secret")
        return Response()
    monkeypatch.setattr(HttpFD, "sanitize_open", lambda *a, **k: pytest.fail("Probe tried to write a file"))
    with YoutubeDL({"quiet": True, "cachedir": False, "logger": diagnostic.DiagnosticLogger()}) as downloader:
        monkeypatch.setattr(downloader, "urlopen", opener)
        result = diagnostic.probe(downloader, {
            "url": "https://r1.googlevideo.com/videoplayback?pot=private",
            "protocol": "https", "format_id": "140", "ext": "m4a", "acodec": "mp4a.40.2",
            "http_headers": {"User-Agent": "extracted-agent"},
            "downloader_options": {"http_chunk_size": chunk_size},
        }, transfer=True)
        assert downloader.urlopen is opener
    assert len(requests) == 1
    assert requests[0].headers["Accept-Encoding"] == "identity"
    assert requests[0].headers["User-Agent"] == "extracted-agent"
    if chunk_size:
        value = result["request_range"]
        assert value.startswith("bytes=0-")
        assert int(chunk_size * .95) <= int(value.removeprefix("bytes=0-")) + 1 <= chunk_size
    else:
        assert result["request_range"] == "NONE"
    assert result["result"] == ("HTTP_403" if rejected else "MEDIA_OK")
    assert reads == ([] if rejected else [1024])
    assert closes == ([] if rejected else [True])
    assert not any(private in str(result) for private in ("secret", "private", "https://", "extracted-agent"))
