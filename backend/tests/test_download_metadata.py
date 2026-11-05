import os


for key, value in {
    "SECRET_KEY": "test-secret",
    "JWT_SECRET_KEY": "test-jwt-secret",
    "POSTGRES_DB": "test",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/test",
    "TELEGRAM_BOT_TOKEN": "123456789:test-token",
}.items():
    os.environ.setdefault(key, value)


from app.services.download import _best_thumbnail_url, _normalize_format
from app.services.download import _normalize_formats
from app.schemas.download import MediaFormat
from app.workers.tasks import download as worker
from unittest.mock import MagicMock


def test_instagram_worker_and_metadata_choose_same_complete_quality(monkeypatch):
    base = {"height": 1280, "width": 720, "fps": 30, "filesize": 12000, "ext": "mp4"}
    formats = [
        dict(base, format_id="vp9", vcodec="vp09", acodec="aac"),
        dict(base, format_id="avc", vcodec="h264", acodec="none", ext="mkv"),
        dict(base, format_id="480", height=854, width=480, vcodec="avc1", acodec="aac"),
    ]
    def selectors():
        client = MagicMock()
        client.return_value.__enter__.return_value.extract_info.return_value = {"formats": formats}
        monkeypatch.setattr(worker.yt_dlp, "YoutubeDL", client)
        selector = worker._get_non_youtube_format_selector("https://www.instagram.com/reel/test/", 720)
        normalized = [MediaFormat(**entry) for entry in _normalize_formats(formats, instagram=True)]
        marked = {f.format_id for f in normalized if f.preferred_for_quality}
        return selector, marked

    assert selectors() == ("vp9", {"vp9", "480"})  # Only audible 720p option.
    formats.append({"format_id": "audio", "vcodec": "none", "acodec": "aac", "ext": "m4a", "filesize": 1000})
    assert selectors() == ("avc+bestaudio[ext=m4a]/avc+bestaudio", {"avc", "480"})
    formats[0]["fps"] = 60
    assert selectors() == ("vp9", {"vp9", "480"})  # Don't trade 60 fps for 30.
    assert not any(f.get("preferred_for_quality") for f in _normalize_formats(formats))


def test_largest_thumbnail_and_video_fps_are_exposed():
    thumbnail = _best_thumbnail_url(
        {
            "thumbnail": "https://cdn.example.test/default.jpg",
            "thumbnails": [
                {
                    "url": "https://cdn.example.test/small.jpg",
                    "width": 320,
                    "height": 180,
                },
                {
                    "url": "https://cdn.example.test/original.jpg",
                    "width": 1920,
                    "height": 1080,
                },
            ],
        }
    )
    media_format = _normalize_format(
        {
            "format_id": "1080p",
            "width": 1920,
            "height": 1080,
            "fps": 29.97,
            "vcodec": "avc1",
            "acodec": "mp4a",
            "ext": "mp4",
        }
    )

    assert thumbnail == "https://cdn.example.test/original.jpg"
    assert media_format is not None
    assert media_format["resolution"] == "1920x1080"
    assert media_format["fps"] == 29.97
