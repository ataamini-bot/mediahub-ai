from app.services import music_link_cache as cache
from app.services import source_capacity
from .test_source_capacity import storage


def configure(monkeypatch):
    monkeypatch.setattr(cache, "configuration", source_capacity.configuration)
    monkeypatch.setattr(cache, "PREFIX", source_capacity.PREFIX + "music:")


def test_link_cache_contains_only_identity_sources_and_telegram_preview_reference(storage, monkeypatch):
    configure(monkeypatch)
    key = cache.key("https://www.instagram.com/reel/PublicTest/", "shazamio", 123)
    assert key == cache.key("https://instagram.com/p/PublicTest/", "shazamio", 456)
    assert cache.key(None, "shazamio", 123) is None
    assert cache.key("upload://upload-private.mp4", "shazamio", 123) is None
    owner = cache.acquire(key)
    assert owner and cache.acquire(key) is False
    cache.save(key, {"title": "Test song", "artist": "Test artist", "candidates": [{"url": "https://example.invalid/song"}],
                     "file_path": "/private/file", "raw_audio": "must not persist", "preview": {"file_path": "/private/clip"}})
    cache.save_preview(key, {"width": 320, "height": 240, "duration": 12, "file_path": "/private/clip"}, "telegram-preview-ref")
    result, preview = cache.read(key)
    assert set(result) == {"title", "artist", "candidates"}
    assert preview == {"width": 320, "height": 240, "duration": 12, "telegram_file_id": "telegram-preview-ref"}
    cache.release(key, "wrong-owner")
    assert cache.acquire(key) is False
    cache.release(key, owner)
    assert cache.acquire(key)


def test_unknown_visibility_is_per_user_and_failures_are_not_cached(storage, monkeypatch):
    configure(monkeypatch)
    url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert cache.key(url, "shazamio", 123) != cache.key(url, "shazamio", 456)
    key = cache.key(url, "shazamio", 123)
    cache.save(key, None)
    cache.save(key, {"title": "Unresolved", "candidates": []})
    assert cache.read(key) is None
