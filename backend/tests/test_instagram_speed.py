import copy
import os

for key, value in {
    "SECRET_KEY": "test-secret", "JWT_SECRET_KEY": "test-secret",
    "POSTGRES_DB": "test", "POSTGRES_USER": "test", "POSTGRES_PASSWORD": "test",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/test",
    "TELEGRAM_BOT_TOKEN": "123456789:test-token",
}.items():
    os.environ.setdefault(key, value)

import pytest
from yt_dlp import YoutubeDL

from app.services.download import DownloadService
from app.workers.tasks import download as worker

URL = "https://www.instagram.com/reel/PublicTest/"


def video():
    base = {"ext": "mp4", "protocol": "https", "filesize": 65536, "fps": 30, "acodec": "none"}
    return {
        "_type": "video", "id": "test-video", "title": "Example",
        "duration": 34, "extractor": "Instagram", "extractor_key": "Instagram", "webpage_url": URL,
        "formats": [
            dict(base, format_id="720", width=720, height=1280, vcodec="avc1.64001f", url="https://cdn.example.test/720.mp4"),
            dict(base, format_id="1080", width=1080, height=1920, vcodec="vp09", url="https://cdn.example.test/1080.mp4"),
            {"format_id": "audio", "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a",
             "protocol": "https", "filesize": 8192, "url": "https://cdn.example.test/audio.m4a"},
        ],
    }


def reject_network(*_args, **_kwargs):
    raise AssertionError("Unexpected extra network request")


def test_single_reel_metadata_extracts_once_then_normalizes_locally(monkeypatch):
    requests = []
    def extract(_self, url, **kwargs):
        requests.append(url)
        assert kwargs == {"download": False, "process": False}
        return video()
    monkeypatch.setattr(YoutubeDL, "extract_info", extract)
    monkeypatch.setattr(YoutubeDL, "urlopen", reject_network)
    result = DownloadService.get_media_info(URL)
    assert requests == [URL]
    assert not result["is_playlist"]
    choices = {f["resolution"] for f in result["formats"] if f.get("preferred_for_quality")}
    assert choices == {"720x1280", "1080x1920"}


@pytest.mark.parametrize("item", [1, 2])
def test_album_entries_are_reusable_and_selection_keeps_its_index(monkeypatch, item):
    photo = {"id": "photo", "thumbnails": [{"url": "https://cdn.example.test/photo.jpg", "width": 1080, "height": 1080}]}
    raw = {"_type": "playlist", "id": "album", "title": "Mixed post", "extractor": "Instagram",
           "extractor_key": "Instagram", "webpage_url": URL, "entries": iter([photo, video()])}
    requests = []
    def extract(_self, url, **kwargs):
        requests.append(url)
        return raw
    monkeypatch.setattr(YoutubeDL, "extract_info", extract)
    monkeypatch.setattr(YoutubeDL, "urlopen", reject_network)
    result = DownloadService.get_media_info(URL, playlist_index=item)
    assert requests == [URL]
    assert len(result["entries"]) == 1 and result["entries"][0]["index"] == item
    assert result["media_type"] == ("image" if item == 1 else "video")
    if item == 2:
        assert result["formats"] and result["entries"][0]["id"] == "test-video"


def test_worker_selects_1080_with_sound_inside_the_existing_extraction(monkeypatch):
    monkeypatch.setattr(YoutubeDL, "extract_info", reject_network)
    monkeypatch.setattr(YoutubeDL, "urlopen", reject_network)
    selector = worker._get_format_selector(URL, None, "1080p", "video")
    assert callable(selector)
    with YoutubeDL({"format": selector, "quiet": True, "no_warnings": True}) as downloader:
        selected = downloader.process_ie_result(copy.deepcopy(video()), download=False)
    assert selected["format_id"] == "1080+audio"
    assert selected["width"] == 1080 and selected["height"] == 1920
    assert [s["format_id"] for s in selected["requested_formats"]] == ["1080", "audio"]


def test_single_silent_video_remains_a_valid_source():
    source = video()["formats"][1]
    chosen = list(worker._instagram_format_selector(1080)({"formats": [source]}))
    assert chosen == [source]
