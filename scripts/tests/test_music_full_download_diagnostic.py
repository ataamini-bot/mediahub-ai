"""Exercise the actual yt-dlp transfer pipeline against synthetic HTTP responses."""
import importlib.util
import io
from pathlib import Path
import tempfile
from unittest.mock import Mock

import pytest
import yt_dlp
from yt_dlp.cache import Cache
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import HTTPError


spec = importlib.util.spec_from_file_location(
    "music_full_download_diagnostic", Path(__file__).parents[1] / "diagnose_music_download.py")
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


@pytest.mark.parametrize("outcome", ["complete", "initial_403", "later_403", "byte_limit", "timeout", "large_file", "hls"])
def test_actual_download_is_bounded_observed_and_cleaned(monkeypatch, tmp_path, outcome):
    source = b"abcdefghijkl"
    created, responses, calls, parameters = [], [], [], []
    real_ydl = yt_dlp.YoutubeDL
    real_tempdir = tempfile.TemporaryDirectory
    load = Mock(return_value={"existing": "signature"})
    store = Mock(side_effect=AssertionError("Diagnostic wrote extractor cache"))
    monkeypatch.setattr(Cache, "load", load)
    monkeypatch.setattr(Cache, "store", store)
    monkeypatch.setattr("yt_dlp.downloader.http.random.randint", lambda _low, high: high)
    if outcome in {"byte_limit", "large_file"}:
        real_trace = diagnostic.DownloadTrace
        monkeypatch.setattr(diagnostic, "DownloadTrace", lambda: real_trace(limit=7))

    class SyntheticIE(InfoExtractor):
        _VALID_URL = r"https://www\.youtube\.com/watch\?v=(?P<id>AAAAAAAAAAA)"

        def _real_extract(self, url):
            assert self._downloader.cache.load("youtube-sigfuncs", "example") == {"existing": "signature"}
            self._downloader.cache.store("youtube-sigfuncs", "example", {"new": "signature"})
            return {"id": "AAAAAAAAAAA", "title": "Synthetic track", "formats": [{
                "format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
                "filesize": len(source) if outcome == "large_file" else None,
                "protocol": "m3u8_native" if outcome == "hls" else "https",
                "url": "https://r1.googlevideo.com/videoplayback?itag=140&pot=private-token",
                "downloader_options": {"http_chunk_size": 0 if outcome == "byte_limit" else 4},
            }]}

    def opener(request):
        assert request.url.startswith("https://r1.googlevideo.com/")
        calls.append(request)
        if outcome == "initial_403" or (outcome == "later_403" and len(calls) == 2):
            raise HTTPError(Response(io.BytesIO(b"secret upstream body"), request.url, {}, status=403))
        value = request.headers.get("Range")
        if outcome == "byte_limit":
            body, headers, status = source, {}, 200
        else:
            start, end = map(int, value.removeprefix("bytes=").split("-"))
            end = min(end, len(source) - 1)
            body = source[start:end + 1]
            headers = {"Content-Length": str(len(body)), "Content-Range": f"bytes {start}-{end}/{len(source)}"}
            status = 206
        response = Response(io.BytesIO(body), request.url, headers, status=status)
        if outcome == "timeout":
            def timeout(_size):
                raise diagnostic.DownloadCheckStopped("TIMEOUT")
            response.read = timeout
        responses.append(response)
        return response

    def factory(params):
        parameters.append(params)
        downloader = real_ydl(params, auto_init=False)
        downloader.add_info_extractor(SyntheticIE())
        downloader.urlopen = opener
        return downloader

    def temporary(**kwargs):
        directory = real_tempdir(dir=tmp_path, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(diagnostic.tempfile, "TemporaryDirectory", temporary)
    result = diagnostic.download_check("https://www.youtube.com/watch?v=AAAAAAAAAAA", {"format": "bestaudio"})

    assert result["temporary_files"] == "REMOVED"
    assert created and all(not p.exists() for p in created)
    store.assert_not_called()
    load.assert_called_once_with("youtube-sigfuncs", "example")
    assert "cachedir" not in parameters[0]
    assert parameters[0]["continuedl"] is True and parameters[0]["nopart"] is False
    assert parameters[0]["retries"] == parameters[0]["fragment_retries"] == parameters[0]["extractor_retries"] == 0
    assert all(secret not in str(result) for secret in ("googlevideo", "private-token", "secret upstream", "AAAAAAAAAAA", str(tmp_path)))
    assert all(response.closed for response in responses)
    assert len(result["requests"]) == len(calls)
    if outcome == "complete":
        assert result["result"] == "COMPLETE"
        assert result["file_bytes"] == result["bytes_read"] == len(source)
        assert [r["range"] for r in result["requests"]] == ["bytes=0-3", "bytes=4-7", "bytes=8-11"]
    elif "403" in outcome:
        assert result["result"] == "HTTP_403"
        expected = 0 if outcome == "initial_403" else 4
        assert result["bytes_read"] == expected
        assert result["requests"][-1]["bytes_before_request"] == expected
        assert result["requests"][-1]["result"] == "HTTP_403"
    elif outcome == "timeout":
        assert result["result"] == "TIMEOUT" and result["bytes_read"] == 0
    elif outcome == "hls":
        assert result["result"] == "NOT_DIRECT_HTTPS_AUDIO" and not calls
    else:
        assert result["result"] == "BYTE_LIMIT"
        assert result["bytes_read"] <= 7
        if outcome == "byte_limit":
            assert result["bytes_read"] == 7


def test_request_limit_prevents_unbounded_small_fragment_requests():
    trace = diagnostic.DownloadTrace()
    response = Mock(status=206)
    opener = Mock(return_value=response)
    from yt_dlp.networking import Request
    request = Request("https://r1.googlevideo.com/videoplayback?itag=140&pot=private")
    for _ in range(12):
        trace.open(request, opener)
    with pytest.raises(diagnostic.DownloadCheckStopped, match="REQUEST_LIMIT"):
        trace.open(request, opener)
    assert opener.call_count == 12
