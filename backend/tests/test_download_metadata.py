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
