"""All catalogue fixtures are synthetic; no public song or production data."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from urllib.parse import quote, unquote_plus

import httpx
import pytest

from app.services import musicfa, music_sources as sources, music_source_cache as cache
from app.services.music_source_catalog import SOURCES, BY_KEY, source_for_media

ACTIVE = [s for s in SOURCES if s.active]
TRACK = {"artist": "Example Artist", "title": "Original Song", "duration": 90}


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(cache, "_remote", lambda operation: None)
    cache._local.clear()
    yield
    cache._local.clear()


def page_url(source):
    prefix = next((p for p in ("download-song", "single-tracks", "article", "details", "cnt") if "/"+p+"/" in source.post_pattern), None)
    if prefix:
        path = f"/{prefix}/123/"
    elif source.key in {"musicbaran", "birmusic"}:
        path = "/music/test-song/"
    elif source.key == "radiojavanhd":
        path = "/123/test-song/"
    elif source.key == "pinkmusics":
        path = "/song-test-song/"
    else:
        path = "/test-song.html" if "html" in source.post_pattern else "/test-song/"
    return "https://" + source.host + path


def media_url(source, track=TRACK, *, suffix=" (320)"):
    brand = " ["+source.brands[0]+"]" if source.brands else ""
    return "https://" + source.media_hosts[0] + "/test/" + quote(track["artist"] + " - " + track["title"] + brand + suffix + ".mp3")


@pytest.mark.parametrize("source", ACTIVE, ids=lambda s:s.key)
@pytest.mark.parametrize("track", [TRACK, {"artist": "هنرمند نمونه", "title": "آهنگ اصلی", "duration": 90}], ids=["foreign", "persian"])
def test_every_active_catalogue_searches_both_languages_and_matches_same_track(source, track):
    page, media = page_url(source), media_url(source, track)
    query = source.search_url(track)
    assert track["artist"] in unquote_plus(query) and track["title"] in unquote_plus(query)
    search = f'<article><h2><a rel="bookmark" href="{page}">{track["artist"]} {track["title"]}</a></h2></article>'
    assert sources.search_pages(source, track, search) == [page]
    body = f'<main><article><h1>{track["title"]}</h1><a href="{media}">دانلود 320</a></article></main>'
    assert sources.download_links(source, track, body, page) == [media]
    assert source_for_media(media) == source


def test_every_requested_site_is_accounted_for_without_enabling_unverified_downloads():
    assert len(SOURCES) == 25 and len({s.host for s in SOURCES}) == 25
    assert len(ACTIVE) == 19
    for key in ("worldofmusic", "radiojavan", "remixgram", "vipremix", "musicdagh", "moonmusic"):
        assert not BY_KEY[key].active


@pytest.mark.parametrize("url", ["http://dl.rozmusic.com/file.mp3", "https://dl.rozmusic.com.evil.example/file.mp3",
    "https://user:pass@dl.rozmusic.com/file.mp3", "https://dl.rozmusic.com:8443/file.mp3",
    "https://dl.rozmusic.com/%2e%2e/private.mp3", "https://dl.rozmusic.com/file.mp3?token=secret",
    "https://127.0.0.1/file.mp3", None])
def test_media_destination_is_never_inferred_from_an_untrusted_host_or_url(url):
    assert source_for_media(url) is None


@pytest.mark.parametrize("label,box", [("پخش آنلاین", ""), ("دانلود نسخه دمو", ""),
    ("Download preview 320", ""), ("دانلود 320", "برای دانلود باید اشتراک داشته باشید"),
    ("دانلود 320", "با خرید اشتراک لینک فعال می‌شود")])
def test_player_preview_and_locked_download_boxes_are_not_full_song_sources(label, box):
    source = BY_KEY["rozmusic"]
    body = f'<article><div>{box}<a href="{media_url(source)}">{label}</a></div></article>'
    assert sources.download_links(source, TRACK, body, page_url(source)) == []


@pytest.mark.parametrize("change", ["Other Artist - Original Song", "Example Artist - Other Song", "Example Artist - Original Song Remix",
                                   "Example Artist - Original Song Cover"])
def test_variant_and_identity_mismatch_cannot_replace_full_track(change):
    source = BY_KEY["rozmusic"]
    url = "https://dl.rozmusic.com/test/" + quote(change + ".mp3")
    assert not sources._file_identity(source, url, TRACK)


def test_brand_suffix_and_newsongs_quality_annotation_are_supported():
    url = "https://dl.newsong.ir/test/Example%20Artist%20Original%20Song%20320_(www.newsong.ir).mp3"
    assert sources._file_identity(BY_KEY["newsong"], url, TRACK)


def test_best_offered_quality_is_selected_but_lower_quality_is_not_a_missing_song():
    source = BY_KEY["rozmusic"]
    lower, higher = media_url(source, suffix=" (128)"), media_url(source)
    body = f'<article><a href="{lower}">Download 128</a><a href="{higher}">Download 320</a></article>'
    assert sources.download_links(source, TRACK, body, page_url(source)) == [higher]
    assert sources.download_links(source, TRACK, f'<a href="{lower}">Download 128</a>', page_url(source)) == [lower]


def test_versionless_discovery_is_only_enabled_with_a_valid_shazam_identity():
    source = BY_KEY["upmusics"]
    track = {**TRACK, "title":"Original Song (Acoustic)", "recognition_provider":"shazamio", "recognition_key":"12345"}
    body = f'<article><a href="{page_url(source)}">Example Artist Original Song</a></article>'
    assert "Acoustic" not in source.search_url(track)
    assert sources.search_pages(source, track, body) == [page_url(source)]
    for other in ({**track,"recognition_provider":"audd"}, {**track,"recognition_key":"https://invalid.example"}):
        assert "Acoustic" in source.search_url(other)
        assert sources.search_pages(source, other, body) == []
    literal = {**track, "title":"Original Song (Part Two)"}
    assert "Part Two" in unquote_plus(source.search_url(literal))
    assert sources._cache_key(track) != sources._cache_key({**track,"recognition_key":"67890"})


@pytest.mark.parametrize("failure,expected_calls", [(False,2),(True,1)])
def test_source_audio_checks_are_serial_bounded_and_stop_on_provider_failure(monkeypatch, failure, expected_calls):
    from app.services import music_source_match
    calls=[]
    def match(*args):
        calls.append(1)
        if failure:raise httpx.ReadTimeout("private upstream body")
        return False
    monkeypatch.setattr(music_source_match,"matches_sample",match)
    monkeypatch.setattr(sources,"_search",lambda source,*args:[page_url(source)])
    def resolve(source, track, page, deadline, control, verify):
        verify(None)
        return None
    monkeypatch.setattr(sources,"_resolve",resolve)
    track={**TRACK,"recognition_provider":"shazamio","recognition_key":"12345"}
    assert sources.find_tracks(track,sources=[BY_KEY[k] for k in ("upmusics","rozmusic","musicviral","nicmusic")]) == []
    assert len(calls) == expected_calls


def test_search_ignores_sidebar_and_irrelevant_articles_before_applying_page_budget():
    source = BY_KEY["musicviral"]
    correct = page_url(source)
    body = f'''<aside><a href="{correct}">Example Artist Original Song</a></aside>
      <article><h2><a href="https://musicviral.ir/unrelated/">Other Artist Other Song</a></h2></article>
      <article><h2><a href="{correct}">Example Artist Original Song</a></h2></article>'''
    assert sources.search_pages(source, TRACK, body) == [correct]


def test_article_image_metadata_can_connect_persian_heading_to_latin_identity():
    source = BY_KEY["musicbaran"]
    url = page_url(source)
    body = f'<article><a href="{url}"><img alt="Example Artist Original Song">هنرمند نمونه آهنگ اصلی</a></article>'
    assert sources.search_pages(source, TRACK, body) == [url]


def test_offsite_redirect_is_rejected_before_following_it(monkeypatch):
    real = httpx.Client
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location":"https://127.0.0.1/private"})
    monkeypatch.setattr(musicfa, "_client", lambda: real(transport=httpx.MockTransport(handler)))
    assert sources._search(BY_KEY["rozmusic"], TRACK, time.monotonic()+5, lambda:None) == []
    assert len(calls) == 1


def test_rate_limit_is_shared_and_retry_after_is_respected(monkeypatch):
    real = httpx.Client
    def handler(request):
        return httpx.Response(429, headers={"Retry-After":"900"}, text="private upstream body")
    monkeypatch.setattr(musicfa, "_client", lambda:real(transport=httpx.MockTransport(handler)))
    source = BY_KEY["rozmusic"]
    assert sources._search(source, TRACK, time.monotonic()+5, lambda:None) == []
    assert cache.read(["cooldown:rozmusic"])["cooldown:rozmusic"]
    assert cache._local["cooldown:rozmusic"][0] > time.monotonic()+890
    monkeypatch.setattr(sources, "_search", lambda *a:pytest.fail("Cooling source was contacted"))
    assert sources.find_tracks(TRACK, sources=[source]) == []


def test_all_sources_enter_the_pool_for_foreign_music_and_cached_metadata_avoids_repeat_search(monkeypatch):
    calls = []
    lock = threading.Lock()
    def search(source, track, deadline, control):
        control()
        with lock:calls.append(source.key)
        return []
    def legacy(track, deadline, control):
        with lock:calls.append("musicfa")
        return []
    monkeypatch.setattr(sources, "_search", search)
    monkeypatch.setattr(sources, "_musicfa", legacy)
    assert sources.find_tracks(TRACK) == []
    assert set(calls) == {s.key for s in ACTIVE}
    assert sources.find_tracks(TRACK) == [] and len(calls) == len(ACTIVE)


def test_total_budget_cancels_queued_and_inflight_source_searches(monkeypatch):
    monkeypatch.setattr(sources, "SEARCH_SECONDS", .08)
    stopped = []
    def search(source, track, deadline, control):
        try:
            while True:
                control()
                time.sleep(.005)
        except sources.SearchStopped:
            stopped.append(source.key)
            return []
    monkeypatch.setattr(sources, "_search", search)
    started = time.monotonic()
    assert sources.find_tracks(TRACK, sources=[s for s in ACTIVE if s.key != "musicfa"]) == []
    assert stopped and time.monotonic()-started < .8


def test_fast_matches_are_verified_while_other_catalogues_are_still_searching(monkeypatch):
    monkeypatch.setattr(sources, "SEARCH_SECONDS", .4)
    matching = {"upmusics", "rozmusic"}
    waiting = threading.Event()
    def search(source, track, deadline, control):
        if source.key in matching:
            return [page_url(source)]
        waiting.set()
        try:
            while True:
                control()
                time.sleep(.005)
        except sources.SearchStopped:
            return []
    def resolve(source, track, page, deadline, control, verify):
        assert waiting.wait(.2)
        control()
        return {"source":source.key,"url":media_url(source),"page_url":page,"duration":90}
    monkeypatch.setattr(sources, "_search", search)
    monkeypatch.setattr(sources, "_resolve", resolve)
    selected = [BY_KEY[k] for k in ("upmusics", "rozmusic", "musicviral", "nicmusic", "musicdel", "newsong", "melodya", "musicsmehr")]
    result = sources.find_tracks(TRACK, sources=selected)
    assert {item["source"] for item in result} == matching


def test_a_broken_source_does_not_discard_another_verified_candidate(monkeypatch):
    source = BY_KEY["musicviral"]
    item = {"source":source.key,"url":media_url(source),"page_url":page_url(source),"duration":90}
    def search(site, *args):
        if site.key == "rozmusic":
            raise RuntimeError("unexpected upstream structure")
        return [page_url(site)]
    monkeypatch.setattr(sources, "_search", search)
    monkeypatch.setattr(sources, "_resolve", lambda *args:item)
    assert sources.find_tracks(TRACK, sources=[BY_KEY["rozmusic"], source]) == [item]


def test_verified_site_candidates_are_cached_without_any_media_or_user_identifiers(monkeypatch):
    source = BY_KEY["musicviral"]
    item = {"source":source.key,"url":media_url(source),"page_url":page_url(source),"duration":90,"title":"Original Song","channel":"Example Artist"}
    monkeypatch.setattr(sources, "_search", lambda *a:[page_url(source)])
    monkeypatch.setattr(sources, "_resolve", lambda *a:item)
    assert sources.find_tracks(TRACK, sources=[source]) == [item]
    monkeypatch.setattr(sources, "_search", lambda *a:pytest.fail("Cached song searched again"))
    assert sources.find_tracks(TRACK, sources=[source]) == [item]
    for key, (_, value) in cache._local.items():
        assert "Example" not in key and "Original" not in key
        assert not any(term in json.dumps(value) for term in ("telegram_id", "file_path", "file_id"))


def test_shared_cache_uses_redis_ttl_and_bounded_local_fallback(monkeypatch):
    store = {}
    class Client:
        def set(self,key,value,ex):store[key]=(value,ex)
        def mget(self,keys):return [store.get(k,(None,0))[0] for k in keys]
    monkeypatch.setattr(cache, "_remote", lambda operation:operation(Client()))
    cache.write("cooldown:rozmusic", True, 120)
    cache._local.clear()  # Another worker can still observe the Redis value.
    assert cache.read(["cooldown:rozmusic"])["cooldown:rozmusic"] is True
    assert store[cache.PREFIX+"cooldown:rozmusic"][1] == 120
    for i in range(150):cache.write("test:"+str(i), [], 30)
    assert len(cache._local) == 128
