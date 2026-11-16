import importlib.util
from pathlib import Path


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
