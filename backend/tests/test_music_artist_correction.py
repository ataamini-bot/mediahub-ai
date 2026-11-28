"""Synthetic public pages/audio; no production song or provider requests."""
import io
import subprocess
import time
import wave
from urllib.parse import quote, unquote_plus

import httpx
import pytest

from app.services import media_tool_providers as providers, media_tool_shazam as shazam
from app.services import music_artist_correction as correction, music_source_cache as cache
from app.services import music_sources as sources, musicfa, recognition_access
from app.services.music_source_catalog import BY_KEY

TRACK = {"title": "Original Song", "artist": "Reported Artist", "duration": None,
         "recognition_provider": "shazamio", "recognition_key": "12345"}
SITES = [BY_KEY[key] for key in correction.FALLBACK_SOURCES]


def page(source):
    path = "/music/synthetic/" if source.key == "birmusic" else (
        "/synthetic.html" if source.key == "rozmusic" else "/synthetic/")
    return "https://" + source.host + path


def media(source, artist="Actual Artist", title="Original Song"):
    brand = " UpMusics" if source.key == "upmusics" else ""
    return "https://" + source.media_hosts[0] + "/test/" + quote(f"{artist} - {title}{brand}.mp3")


def post(source, *, artist="Actual Artist", title="Original Song", label="Download 320", heading=True):
    names = f"<h1>{artist} {title}</h1>" if heading else ""
    return f'<article>{names}<div><a href="{media(source, artist, title)}">{label}</a></div></article>'


def candidate(source, artist="Actual Artist"):
    return {"source": source.key, "source_name": source.label, "source_type": "public_mp3",
            "channel": artist, "title": TRACK["title"], "url": media(source, artist), "page_url": page(source)}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(cache, "_remote", lambda operation: None)
    monkeypatch.setattr(recognition_access, "check", lambda provider: None)
    monkeypatch.setattr(sources, "SOURCES", SITES)
    cache._local.clear()
    yield
    cache._local.clear()


@pytest.fixture(scope="module")
def mp3(tmp_path_factory):
    path = tmp_path_factory.mktemp("artist-audio") / "synthetic.mp3"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=duration=90",
                    "-c:a", "libmp3lame", "-b:a", "320k", str(path)], check=True)
    return path.read_bytes()


def catalogues(monkeypatch, mp3, *, missing=(), blocked=(), changed_size=False):
    calls = []
    def handle(request):
        calls.append(request)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        site = next(s for s in SITES if request.url.host in (s.host, *s.media_hosts))
        if request.url.host == site.host:
            if site.key in blocked:
                return httpx.Response(429, headers={"Retry-After": "120"})
            url = str(request.url)
            if url == page(site):
                body = post(site)
            elif "Reported Artist" in unquote_plus(url) or site.key in missing:
                body = "<html>No matching posts</html>"
            else:
                assert "Original Song" in unquote_plus(url)
                body = f'<article><h2><a href="{page(site)}">Actual Artist Original Song</a></h2></article>'
            return httpx.Response(200, text=body, headers={"Content-Type": "text/html"})
        assert str(request.url) == media(site)
        start, end = map(int, request.headers["range"].removeprefix("bytes=").split("-"))
        assert end - start + 1 == musicfa.PROBE_LIMIT
        size = len(mp3) + (1 if changed_size and start else 0)
        data = mp3[start:end+1]
        return httpx.Response(206, content=data, headers={
            "Content-Range": f"bytes {start}-{start+len(data)-1}/{size}", "Content-Type": "audio/mpeg"})
    monkeypatch.setattr(musicfa, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    return calls


@pytest.mark.parametrize("site", SITES, ids=lambda s:s.key)
def test_title_discovery_preserves_version_and_requires_primary_page_corroboration(site):
    query = unquote_plus(site.search_url({**TRACK, "title": "Original Song (Acoustic)"}, title_only=True))
    assert "Reported Artist" not in query and "Original Song (Acoustic)" in query
    search = f'<article><a href="{page(site)}">Actual Artist Original Song</a></article>'
    assert sources.search_pages(site, TRACK, search) == []
    assert sources.search_pages(site, TRACK, search, title_only=True) == [page(site)]
    result = correction.page_candidates(site, TRACK, post(site), page(site))
    assert result == [candidate(site)]
    assert not correction.page_candidates(site, TRACK, post(site, heading=False), page(site))


@pytest.mark.parametrize("kind", ["sidebar", "preview", "locked", "different_title", "remix", "offsite"])
def test_artist_correction_rejects_unsupported_identity_or_download_offers(kind):
    site = SITES[0]
    body = post(site)
    if kind == "sidebar":
        body = post(site, heading=False) + "<aside><h1>Actual Artist Original Song</h1></aside>"
    elif kind == "preview":
        body = post(site, label="Download preview 320")
    elif kind == "locked":
        body = body.replace("<div>", "<div>login to download")
    elif kind == "different_title":
        body = post(site, title="Another Song")
    elif kind == "remix":
        body = post(site, title="Original Song Remix")
    elif kind == "offsite":
        body = body.replace(site.media_hosts[0], "127.0.0.1")
    assert correction.page_candidates(site, TRACK, body, page(site)) == []


@pytest.mark.parametrize("response,checks", [("same_key", 2), ("other_key", 1), ("no_match", 1), ("busy", 1)])
def test_complete_discovery_requires_two_audio_proofs_and_cleans_all_samples(mp3, monkeypatch, tmp_path, response, checks):
    calls = catalogues(monkeypatch, mp3)
    fingerprints = []
    async def recognize(content):
        with wave.open(io.BytesIO(content)) as audio:
            assert 8*16000 <= audio.getnframes() <= 12*16000
        fingerprints.append(content)
        if response == "busy":
            raise providers.ToolError("provider_busy")
        if response == "no_match":
            return {"matches": []}
        # Even identical wrong names cannot substitute for the recording key.
        return {"matches": [{"id": "match"}], "track": {"title": TRACK["title"], "subtitle": TRACK["artist"],
                "key": "12345" if response == "same_key" else "67890"}}
    monkeypatch.setattr(shazam, "recognize_sample", recognize)
    monkeypatch.setattr(musicfa.tempfile, "tempdir", str(tmp_path))
    old_youtube = {"title": "Reported Artist Original Song", "channel": "Reported Artist", "url": "https://youtu.be/AAAAAAAAAAA"}
    monkeypatch.setattr(providers, "_find_youtube_tracks", lambda track:[old_youtube])
    track = dict(TRACK)
    results = providers.find_full_tracks(track)
    assert len(fingerprints) == checks and not list(tmp_path.iterdir())
    media_calls = [r for r in calls if r.url.host.startswith(("dl.", "irsv."))]
    assert len(media_calls) == checks*2
    assert sum(r.headers["range"].startswith("bytes=0-") for r in media_calls) == checks
    if response != "same_key":
        assert track == TRACK and results == [old_youtube]
        return
    assert len(results) == 2 and old_youtube not in results
    assert track["artist"] == "Actual Artist" and track["recognized_artist"] == "Reported Artist"
    assert track["artist_correction"]["sources"] == ["rozmusic", "upmusics"]
    assert all(item["verified_recording_key"] == "12345" for item in results)
    assert providers.automatic_song_candidate(track, results) == 0
    before = len(calls)
    repeated = dict(TRACK)
    assert providers.find_full_tracks(repeated) == results and repeated == track
    assert len(calls) == before and len(fingerprints) == 2
    # Proof belongs to this recording, never a global artist alias.
    other = {**TRACK, "recognition_key": "99999"}
    assert providers.find_full_tracks(other) == [old_youtube] and other["artist"] == TRACK["artist"]


@pytest.mark.parametrize("scenario", ["one_site", "conflicting_artists", "different_duration", "one_failed_audio"])
def test_partial_or_conflicting_proof_cannot_correct_attribution(monkeypatch, scenario):
    def discover(site, *args):
        if scenario == "one_site" and site != SITES[0]:
            return []
        values = [candidate(site)]
        if scenario == "conflicting_artists":
            values.append(candidate(site, artist="Another Artist"))
        return values
    monkeypatch.setattr(correction, "_discover", discover)
    probes = []
    def probe(client, url, deadline, **kwargs):
        probes.append(url)
        if scenario == "one_failed_audio" and len(probes) == 2:
            raise musicfa.MusicFaError("music_source_identity_mismatch")
        return {"duration": 180 if scenario == "different_duration" and len(probes) == 2 else 90,
                "size_bytes": 3600000, "source_bitrate": 320000}
    monkeypatch.setattr(musicfa, "_probe_details", probe)
    result = correction.find_corrected_tracks(TRACK, SITES, set(), {}, time.monotonic()+5, lambda:None, lambda p:True)
    assert result == []
    assert len(probes) == (2 if scenario in {"different_duration", "one_failed_audio"} else 0)
    track = dict(TRACK)
    assert not correction.apply_verified_artist(track, result) and track == TRACK


@pytest.mark.parametrize("skip", ["audd", "generic_title", "provider_busy", "source_cooldown"])
def test_ineligible_or_cooling_sources_do_not_start_broad_searches(monkeypatch, skip):
    monkeypatch.setattr(correction, "_discover", lambda *a:pytest.fail("Unexpected fallback network call"))
    track = dict(TRACK)
    if skip == "audd":
        track["recognition_provider"] = "audd"
    elif skip == "generic_title":
        track["title"] = "Love"
    elif skip == "provider_busy":
        def busy(provider): raise providers.ToolError("provider_busy")
        monkeypatch.setattr(recognition_access, "check", busy)
    else:
        for site in SITES[:2]: cache.write("cooldown:"+site.key, True, 120)
    assert correction.find_corrected_tracks(track, SITES, set(), {}, time.monotonic()+5, lambda:None, lambda p:True) == []


def test_rejected_catalogues_are_not_retried_in_title_fallback(mp3, monkeypatch):
    calls = catalogues(monkeypatch, mp3, blocked={"rozmusic", "upmusics"})
    monkeypatch.setattr(shazam, "recognize_sample", lambda *a:pytest.fail("No supported source consensus"))
    assert sources.find_tracks(TRACK) == []
    assert len(calls) == 3


def test_normal_artist_match_does_not_start_correction_queries(monkeypatch):
    monkeypatch.setattr(sources, "_search", lambda site, *args:[page(site)])
    monkeypatch.setattr(sources, "_resolve", lambda site, *args:{**candidate(site, TRACK["artist"]), "duration":90})
    monkeypatch.setattr(correction, "find_corrected_tracks", lambda *a:pytest.fail("Strict source already found"))
    assert sources.find_tracks(TRACK)


def test_broad_search_respects_one_deadline_for_all_catalogues(monkeypatch):
    def discover(site, track, deadline, control):
        while True:
            control()
            time.sleep(.005)
    monkeypatch.setattr(correction, "_discover", discover)
    started = time.monotonic()
    deadline = started + .06
    def control():
        if time.monotonic() >= deadline:
            raise sources.SearchStopped()
    assert correction.find_corrected_tracks(TRACK, SITES, set(), {}, deadline, control, lambda p:True) == []
    assert time.monotonic() - started < .5


def test_changed_middle_range_size_stops_before_audio_is_sent(mp3, monkeypatch, tmp_path):
    catalogues(monkeypatch, mp3, changed_size=True)
    monkeypatch.setattr(musicfa.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(shazam, "recognize_sample", lambda *a:pytest.fail("Invalid range reached recognition"))
    assert sources.find_tracks(TRACK) == [] and not list(tmp_path.iterdir())
