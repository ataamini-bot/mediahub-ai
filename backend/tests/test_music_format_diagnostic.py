"""Inspect raw formats without downloading, exposing metadata, or changing cache."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yt_dlp
from yt_dlp.cache import Cache
from yt_dlp.extractor.common import InfoExtractor


spec = importlib.util.spec_from_file_location("music_formats_diagnostic",
    Path(__file__).parents[2] / "scripts/diagnose_music_download.py")
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)
URL = "https://www.youtube.com/watch?v=AAAAAAAAAAA"


def reports(capsys):
    output = capsys.readouterr().out
    assert not any(secret in output for secret in ("googlevideo", "private-token", "Private title", "AAAAAAAAAAA"))
    return [json.loads(line.split("=", 1)[1]) for line in output.splitlines()
            if line.startswith("FORMATS_CHECK=")]


def test_format_comparison_bypasses_selector_and_never_transfers_or_writes_cache(monkeypatch, capsys):
    real_ydl = yt_dlp.YoutubeDL
    parameters, alarms = [], []
    original_store = Mock(side_effect=AssertionError("Diagnostic changed extractor cache"))
    monkeypatch.setattr(Cache, "store", original_store)
    monkeypatch.setattr(diagnostic.signal, "alarm", alarms.append)

    class SyntheticIE(InfoExtractor):
        _VALID_URL = r"https://www\.youtube\.com/watch\?v=(?P<id>AAAAAAAAAAA)"

        def _real_extract(self, url):
            self._downloader.cache.store("youtube-sigfuncs", "example", {"secret": "data"})
            cached = self._downloader.params.get("cachedir") is not False
            return {"id": "AAAAAAAAAAA", "title": "Private title", "formats": [{
                "format_id": "18" if cached else "140", "ext": "mp4" if cached else "m4a",
                "vcodec": "h264" if cached else "none", "acodec": "aac", "protocol": "https",
                "url": "https://r1.googlevideo.com/videoplayback?pot=private-token"}]}

    def factory(params):
        parameters.append(params)
        downloader = real_ydl(params, auto_init=False)
        downloader.add_info_extractor(SyntheticIE())
        downloader.urlopen = Mock(side_effect=AssertionError("Unexpected network transfer"))
        # Even a download=False call to this would hide combined formats when
        # bestaudio cannot match; the diagnostic must stop before processing.
        downloader.process_ie_result = Mock(side_effect=AssertionError("Applied format selector"))
        return downloader

    monkeypatch.setattr(yt_dlp, "YoutubeDL", factory)
    diagnostic.inspect_formats({"id": 12, "status": "FAILED", "source_url": URL})
    rows = reports(capsys)
    assert len(rows) == 2 and all(row["result"] == "EXTRACTED" for row in rows)
    assert (rows[0]["audio_only"], rows[0]["combined"]) == (0, 1)
    assert (rows[1]["audio_only"], rows[1]["combined"]) == (1, 0)
    assert [row["cache"] for row in rows] == ["worker_read_only", "disabled"]
    assert "cachedir" not in parameters[0] and parameters[1]["cachedir"] is False
    assert all(p["retries"] == p["fragment_retries"] == p["extractor_retries"] == 0 for p in parameters)
    assert alarms == [90, 0]
    original_store.assert_not_called()


@pytest.mark.parametrize("reason", ["HTTP Error 429 private-token", "Sign in private-token", "TIMEOUT"])
def test_format_comparison_stops_on_rate_limit_login_or_deadline(monkeypatch, capsys, reason):
    calls = []
    class Downloader:
        cache = Mock()
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def extract_info(self, *_args, **_kwargs):
            calls.append(True)
            if reason == "TIMEOUT":
                raise diagnostic.DownloadCheckStopped("TIMEOUT")
            raise RuntimeError(reason)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda _options: Downloader())
    diagnostic.inspect_formats({"id": 12, "status": "FAILED", "source_url": URL})
    rows = reports(capsys)
    assert len(calls) == len(rows) == 1
    assert rows[0]["result"] in {"HTTP_429", "LOGIN_REQUIRED", "TIMEOUT"}


def test_format_inventory_excludes_images_drm_missing_urls_and_unknown_codecs():
    formats = [
        {"url": "private-token", "protocol": "mhtml", "acodec": "none", "vcodec": "none"},
        {"url": "private-token", "acodec": "aac", "vcodec": "none", "has_drm": True},
        {"acodec": "aac", "vcodec": "none"},
        {"url": "private-token"},
        {"url": "private-token", "acodec": "none", "vcodec": "h264"},
    ]
    result = diagnostic.format_inventory({"formats": formats})
    assert result == {"total": 5, "with_url": 4, "drm": 1, "audio_only": 0,
                      "combined": 0, "video_only": 1, "images": 1}
    assert "private-token" not in str(result)
