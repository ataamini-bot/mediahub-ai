"""Synthetic public catalogue/MP3 fixtures; no real song or user data."""
import html
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import quote

import httpx
import pytest

from app.services import musicfa
from app.services import media_tool_providers as providers

TRACK = {"artist": "Example Artist", "title": "Original Song", "duration": 90}
PAGE = "https://musics-fa.com/download-song/123/"
MEDIA = "https://dls.musics-fa.com/test/" + quote("Example Artist - Original Song (320).mp3")


def article(*, ident=123, artist="Example Artist", title="Original Song", links=(), persian=False):
    labels = 'data-artist="هنرمند نمونه" data-song="آهنگ اصلی"' if persian else ''
    anchors = ''.join(f'<a class="mf_mp3" href="{html.escape(u)}">320</a>' for u in links)
    return f'''<article class="mf_pst" data-id="{ident}" {labels}>
        <p>Download New Music BY : {html.escape(artist)} | {html.escape(title)} With Text And 2 Quality 320 And 128 On Music-fa</p>
        {anchors}</article>'''


@pytest.fixture(scope="module")
def mp3(tmp_path_factory):
    path = tmp_path_factory.mktemp("musicfa") / "synthetic.mp3"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=90",
                    "-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", str(path)], check=True)
    return path.read_bytes()


def transport(monkeypatch, handle):
    calls = []
    def run(request):
        calls.append(request)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return handle(request)
    monkeypatch.setattr(musicfa, "_client", lambda: httpx.Client(transport=httpx.MockTransport(run), follow_redirects=False))
    return calls


def catalogue(monkeypatch, mp3, *, search=None, post=None, media_status=206):
    search = article() if search is None else search
    post = article(links=[MEDIA]) if post is None else post
    def handle(request):
        if request.url.host == "musics-fa.com":
            return httpx.Response(200, text=post if request.url.path.startswith("/download-song/") else search,
                                  headers={"Content-Type": "text/html", "Set-Cookie": "tracking=discard"})
        assert str(request.url) == MEDIA
        if request.headers.get("range") and media_status == 206:
            return httpx.Response(206, content=mp3[:musicfa.PROBE_LIMIT], headers={
                "Content-Range": f"bytes 0-{musicfa.PROBE_LIMIT-1}/{len(mp3)}", "Content-Type": "audio/mpeg"})
        return httpx.Response(media_status, content=mp3, headers={"Content-Type": "audio/mpeg"})
    return transport(monkeypatch, handle)


def test_search_matches_identity_probes_320_and_preserves_source_attribution(mp3, monkeypatch):
    calls = catalogue(monkeypatch, mp3, search=article() + article(ident=456, artist="Someone Else"))
    candidates = musicfa.find_tracks(TRACK)
    assert len(candidates) == 1
    item = candidates[0]
    assert item["duration"] == pytest.approx(90, abs=1)
    assert {k: item[k] for k in ("source", "title", "channel", "url", "page_url")} == {
        "source": "musicfa", "title": TRACK["title"], "channel": TRACK["artist"], "url": MEDIA, "page_url": PAGE}
    assert calls[0].url.path == "/search/Example Artist Original Song"
    assert len(calls) == 3 and calls[2].headers["range"] == "bytes=0-524287"


@pytest.mark.parametrize("reference_duration,expected", [(90, True), (180, False)])
def test_public_catalogue_resolves_real_mp3_prefix_and_checks_length(mp3, monkeypatch, reference_duration, expected):
    import time
    from app.services import music_sources
    from app.services.music_source_catalog import BY_KEY
    source = BY_KEY["musicviral"]
    page = "https://musicviral.ir/synthetic-song/"
    media = "https://dl.musicviral.ir/test/Example%20Artist%20-%20Original%20Song.mp3"
    def handle(request):
        if request.url.host == source.host:
            return httpx.Response(200, text=f'<article><a href="{media}">Download MP3 320</a></article>',
                                  headers={"Content-Type": "text/html"})
        assert str(request.url) == media and request.headers["range"] == "bytes=0-524287"
        return httpx.Response(206, content=mp3[:musicfa.PROBE_LIMIT], headers={
            "Content-Range": f"bytes 0-{musicfa.PROBE_LIMIT-1}/{len(mp3)}", "Content-Type": "audio/mpeg"})
    calls = transport(monkeypatch, handle)
    item = music_sources._resolve(source, {**TRACK, "duration": reference_duration}, page, time.monotonic()+5, lambda:None)
    assert bool(item) is expected and len(calls) == 2
    if expected:
        assert item["source"] == "musicviral" and item["source_type"] == "public_mp3"
        assert item["url"] == media and item["duration"] == pytest.approx(90, abs=1)


def test_persian_fields_and_latin_metadata_both_work(mp3, monkeypatch):
    catalogue(monkeypatch, mp3, search=article(persian=True), post=article(persian=True, links=[MEDIA]))
    assert musicfa.find_tracks({"artist": "هنرمند نمونه", "title": "آهنگ اصلي", "duration": 90})
    assert musicfa.find_tracks(TRACK)


def test_search_follows_sites_canonical_search_redirect(monkeypatch):
    def handle(request):
        if not request.url.path.endswith("/"):
            return httpx.Response(301, headers={"Location": "/search/Example%20Artist%20Original%20Song/"})
        assert request.url.path == "/search/Example Artist Original Song/"
        return httpx.Response(200, text="<html>No matches</html>", headers={"Content-Type": "text/html"})
    calls = transport(monkeypatch, handle)
    assert musicfa.find_tracks(TRACK) == [] and len(calls) == 2


@pytest.mark.parametrize("bad", [article(artist="Other Artist"), article(title="Original Song Remix"),
    article(title="Original Song Teaser"), article(title="Other Song"),
    '<aside><a class="mf_mp3" href="' + MEDIA + '">Example Artist Original Song</a></aside>'])
def test_wrong_artists_variants_and_sidebar_do_not_become_candidates(mp3, monkeypatch, bad):
    calls = catalogue(monkeypatch, mp3, search=bad)
    assert musicfa.find_tracks(TRACK) == [] and len(calls) == 1


@pytest.mark.parametrize("links", [[MEDIA.replace("320", "128")],
    [MEDIA, MEDIA.replace("Original", "Other")], [MEDIA.replace("Original", "Other")],
    ["https://evil.example/song(320).mp3"]])
def test_only_one_native_320_download_from_song_article_is_accepted(mp3, monkeypatch, links):
    calls = catalogue(monkeypatch, mp3, post=article(links=links))
    assert musicfa.find_tracks(TRACK) == [] and len(calls) == 2


def test_320_label_cannot_disguise_128_audio(mp3, monkeypatch, tmp_path):
    path = tmp_path / "lower.mp3"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=duration=90",
                    "-c:a", "libmp3lame", "-b:a", "128k", str(path)], check=True)
    catalogue(monkeypatch, path.read_bytes())
    assert musicfa.find_tracks(TRACK) == []


def test_reference_duration_mismatch_is_not_auto_delivered(mp3, monkeypatch):
    catalogue(monkeypatch, mp3)
    assert musicfa.find_tracks({**TRACK, "duration": 180}) == []


def test_large_embedded_cover_is_skipped_with_a_bounded_second_audio_range(mp3, monkeypatch):
    tag_size = 2 * 1024**2
    existing = 10 + sum((mp3[6+i]&127) << (7*(3-i)) for i in range(4)) if mp3[:3] == b"ID3" else 0
    tag = b"ID3\x04\0\0" + bytes((tag_size >> (7*(3-i))) & 127 for i in range(4)) + b"\0"*tag_size
    content = tag + mp3[existing:]
    transferred = []
    def handle(request):
        start, end = map(int,request.headers["range"].removeprefix("bytes=").split("-"))
        chunk = content[start:end+1]
        transferred.append(len(chunk))
        return httpx.Response(206,content=chunk,headers={"Content-Range":f"bytes {start}-{start+len(chunk)-1}/{len(content)}"})
    calls = transport(monkeypatch,handle)
    with musicfa._client() as client:duration,size=musicfa._probe(client,MEDIA,time.monotonic()+5)
    assert duration == pytest.approx(90,abs=1) and size == len(content)
    assert len(calls) == 2 and sum(transferred) == 2*musicfa.PROBE_LIMIT
    assert calls[1].headers["range"].startswith(f"bytes={len(tag)}-")


@pytest.mark.parametrize("mismatch", ["ignored_range","changed_size","oversized_tag"])
def test_invalid_second_range_or_cover_size_is_not_accepted(monkeypatch, mismatch):
    tag_size = 17*1024**2 if mismatch == "oversized_tag" else 2*1024**2
    data = b"ID3\x04\0\0" + bytes((tag_size >> (7*(3-i)))&127 for i in range(4)) + b"\0"*(musicfa.PROBE_LIMIT-10)
    def handle(request):
        start = int(request.headers["range"].split("=")[1].split("-")[0])
        if start and mismatch == "ignored_range":return httpx.Response(200,content=data,headers={"Content-Length":str(30*1024**2)})
        size = 30*1024**2 + int(bool(start))
        return httpx.Response(206,content=data,headers={"Content-Range":f"bytes {start}-{start+len(data)-1}/{size}"})
    calls=transport(monkeypatch,handle)
    with musicfa._client() as client,pytest.raises(musicfa.MusicFaError):
        musicfa._probe(client,MEDIA,time.monotonic()+5)
    assert len(calls) == (1 if mismatch == "oversized_tag" else 2)


@pytest.mark.parametrize("verified",[True,False])
def test_missing_version_label_needs_a_successful_audio_identity_check(mp3,monkeypatch,verified):
    from app.services import music_sources
    from app.services.music_source_catalog import BY_KEY
    source=BY_KEY["musicviral"]
    page="https://musicviral.ir/synthetic-song/"
    media="https://dl.musicviral.ir/test/Example%20Artist%20-%20Original%20Song.mp3"
    def handle(request):
        if request.url.host == source.host:
            return httpx.Response(200,text=f'<article><a href="{media}">Download 320</a></article>',headers={"Content-Type":"text/html"})
        return httpx.Response(206,content=mp3[:musicfa.PROBE_LIMIT],headers={"Content-Range":f"bytes 0-{musicfa.PROBE_LIMIT-1}/{len(mp3)}"})
    transport(monkeypatch,handle)
    checks=[]
    def verify(path):
        assert path.is_file() and path.stat().st_size <= musicfa.PROBE_LIMIT
        checks.append(path)
        return verified
    track={**TRACK,"title":"Original Song (Acoustic)","recognition_provider":"shazamio","recognition_key":"12345"}
    result=music_sources._resolve(source,track,page,time.monotonic()+5,lambda:None,verify)
    assert bool(result) is verified and len(checks) == 1 and not checks[0].exists()
    if verified:assert result["identity_match"] == "audio" and result["title"] == track["title"]


@pytest.mark.parametrize("location", ["http://dls.musics-fa.com/a(320).mp3", "https://evil.example/a.mp3",
    "https://127.0.0.1/a.mp3", "https://dls.musics-fa.com:8443/a.mp3", "https://user:pass@dls.musics-fa.com/a.mp3"])
def test_redirect_is_rejected_before_contacting_other_host(monkeypatch, tmp_path, location):
    calls = transport(monkeypatch, lambda request: httpx.Response(302, headers={"location": location}))
    with pytest.raises(musicfa.MusicFaError, match="invalid_source"):
        musicfa.download_track(MEDIA, tmp_path / "1.mp3", max_bytes=10**7,
                               check_cancelled=lambda: None, progress=lambda data: None)
    assert len(calls) == 1 and not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", [403, 429, 503])
def test_source_rejection_is_safe_and_not_retried(monkeypatch, caplog, status):
    calls = transport(monkeypatch, lambda request: httpx.Response(status, text="private content"))
    assert musicfa.find_tracks(TRACK) == [] and len(calls) == 1
    assert "private content" not in caplog.text


def test_oversized_search_page_is_bounded(monkeypatch):
    calls = transport(monkeypatch, lambda request: httpx.Response(200, text="a" * (musicfa.HTML_LIMIT+1),
                                                               headers={"Content-Type": "text/html"}))
    assert musicfa.find_tracks(TRACK) == [] and len(calls) == 1


def test_source_search_keeps_youtube_alternatives_and_prefers_verified_musicfa(monkeypatch):
    from app.services import music_sources
    site = {"source": "musicfa", "title": TRACK["title"], "channel": TRACK["artist"], "duration": 90, "url": MEDIA}
    youtube = {"title": "Original Song (Official Audio)", "channel": "Example Artist - Topic", "duration": 90}
    monkeypatch.setattr(music_sources, "find_tracks", lambda track: [site])
    monkeypatch.setattr(providers, "_find_youtube_tracks", lambda track: [youtube]*3)
    result = providers.find_full_tracks(TRACK)
    assert result == [site, youtube, youtube]
    assert providers.automatic_song_candidate(TRACK, result) == 0
    monkeypatch.setattr(music_sources, "find_tracks", lambda track: [])
    assert providers.find_full_tracks(TRACK) == [youtube]*3


@pytest.mark.parametrize("stop", ["size", "cancel", "pause", "short"])
def test_interrupted_or_oversized_transfer_removes_partial_file(mp3, monkeypatch, tmp_path, stop):
    from app.workers.tasks.download import DownloadCancelledError, DownloadPausedError
    total = len(mp3) + (1000 if stop == "short" else 0)
    transport(monkeypatch, lambda request: httpx.Response(200, content=mp3, headers={"Content-Length": str(total)}))
    count = 0
    def control():
        nonlocal count
        count += 1
        if count == 4 and stop in {"cancel", "pause"}:
            raise DownloadCancelledError() if stop == "cancel" else DownloadPausedError()
    error = DownloadCancelledError if stop == "cancel" else DownloadPausedError if stop == "pause" else musicfa.MusicFaError
    with pytest.raises(error):
        musicfa.download_track(MEDIA, tmp_path / "2.mp3", max_bytes=1 if stop == "size" else total,
                               check_cancelled=control, progress=lambda data: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("output_format,status,source_bitrate", [("mp3", 200,320), ("m4a", 200,320), ("mp3", 429,320), ("mp3",200,128)])
@pytest.mark.parametrize("source_url", [MEDIA, "https://dl.musicviral.ir/test/Example%20Artist%20-%20Original%20Song.mp3"])
def test_real_worker_delivers_or_fails_source_without_repeating_transfer(mp3, monkeypatch, tmp_path, output_format, status, source_url, source_bitrate):
    from app.workers.tasks import download as worker
    from app.models.download_job import DownloadJobStatus
    if source_bitrate == 128:
        low=tmp_path/'lower.mp3'
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","sine=duration=90","-c:a","libmp3lame","-b:a","128k",str(low)],check=True)
        mp3=low.read_bytes()
        low.unlink()
    calls = transport(monkeypatch, lambda request: httpx.Response(status, content=mp3))
    job_id = 123
    monkeypatch.setattr(worker, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(worker, "_check_job_control", lambda _: None)
    monkeypatch.setattr(worker, "_update_download_stats", lambda **_: None)
    monkeypatch.setattr(worker, "_set_processing", lambda _: True)
    monkeypatch.setattr(worker, "_get_job_status", lambda _: DownloadJobStatus.PROCESSING)
    monkeypatch.setattr(worker.yt_dlp, "YoutubeDL", lambda *a, **k: pytest.fail("Music-fa must not use YouTube"))
    job = SimpleNamespace(status=DownloadJobStatus.PENDING, source_url=source_url, format_id=None, quality=None,
        media_type="audio", output_format=output_format, playlist_index=None, plan_limits_snapshot={"max_file_size_mb": 8})
    session = MagicMock()
    session.__enter__.return_value.execute.return_value.scalar_one_or_none.return_value = job
    monkeypatch.setattr(worker, "WorkerSessionLocal", lambda: session)
    completed = MagicMock()
    monkeypatch.setattr(worker, "_set_completed", completed)
    failed = MagicMock()
    monkeypatch.setattr(worker, "_set_failed", failed)
    monkeypatch.setattr(worker.download_task, "retry", lambda **_: pytest.fail("Unexpected retry"))
    if status == 429:
        with pytest.raises(musicfa.MusicFaError, match="rate_limited"):
            worker.download_task.run(job_id)
        assert len(calls) == 1 and not list(tmp_path.iterdir())
        completed.assert_not_called()
        failed.assert_called_once_with(job_id=job_id, error_message="musicfa_rate_limited")
        return
    assert worker.download_task.run(job_id) == f"Download job {job_id} completed"
    path = tmp_path / f"{job_id}.{output_format}"
    assert list(tmp_path.iterdir()) == [path]
    completed.assert_called_once_with(job_id=job_id, file_path=str(path), file_size=path.stat().st_size)
    if output_format == "mp3" and source_bitrate == 320:
        assert path.read_bytes() == mp3  # No second lossy encode.
    probe = worker._probe_media_file(path)
    assert float(probe["format"]["duration"]) == pytest.approx(90, abs=.5)
    assert {s["codec_type"] for s in probe["streams"]} == {"audio"}
    if output_format == "mp3":assert int(probe["streams"][0]["bit_rate"]) == 320000


def test_search_deadline_expires_before_network(monkeypatch):
    ticks = iter([0, 21])
    monkeypatch.setattr(musicfa.time, "monotonic", lambda: next(ticks))
    calls = transport(monkeypatch, lambda request: pytest.fail("Expired search contacted site"))
    assert musicfa.find_tracks(TRACK) == [] and not calls
