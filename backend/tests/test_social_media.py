import os
import pytest


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:test-token")


from app.services import social_media  # noqa: E402
from app.services.download import _get_format_filesize  # noqa: E402


def _gallery_payload(*items):
    return [
        [2, {"id": "post-1", "description": "Public post"}],
        *[
            [
                3,
                url,
                {
                    "id": "post-1",
                    "type": media_type,
                    "extension": extension,
                    "num": index,
                    "width": width,
                    "height": height,
                },
            ]
            for index, (
                url,
                media_type,
                extension,
                width,
                height,
            ) in enumerate(items, start=1)
        ],
    ]


def test_pinterest_image_is_returned_as_single_download(monkeypatch):
    payload = _gallery_payload(
        (
            "https://i.pinimg.com/originals/aa/bb/cc/photo.jpg",
            "pin",
            "jpg",
            1200,
            1800,
        )
    )
    monkeypatch.setattr(social_media, "_run_gallery_dl", lambda *_: payload)

    result = social_media.get_social_media_info(
        "https://www.pinterest.com/pin/123456789/"
    )

    assert result is not None
    assert result["media_type"] == "image"
    assert result["is_playlist"] is False
    assert result["media_url"].startswith("https://i.pinimg.com/")
    assert result["entries"][0]["width"] == 1200


def test_tiktok_photo_carousel_ignores_soundtrack(monkeypatch):
    payload = _gallery_payload(
        (
            "https://p16-common-sign.tiktokcdn-us.com/photo-one.jpeg",
            "image",
            "jpeg",
            1080,
            1920,
        ),
        (
            "https://p19-common-sign.tiktokcdn-us.com/photo-two.jpeg",
            "image",
            "jpeg",
            1080,
            1920,
        ),
        (
            "https://v19.tiktokcdn-us.com/soundtrack.mp3",
            "audio",
            "mp3",
            0,
            0,
        ),
    )
    monkeypatch.setattr(social_media, "_run_gallery_dl", lambda *_: payload)

    result = social_media.get_social_media_info(
        "https://www.tiktok.com/@example/photo/123456789"
    )

    assert result is not None
    assert result["media_type"] == "image"
    assert result["entry_count"] == 2
    assert [entry["index"] for entry in result["entries"]] == [1, 2]
    assert all(entry["extension"] == "jpg" for entry in result["entries"])


def test_facebook_public_photo_uses_cdn_allowlist(monkeypatch):
    payload = _gallery_payload(
        (
            "https://scontent.example.fbcdn.net/v/photo.jpg",
            "photo",
            "jpg",
            1440,
            1440,
        )
    )
    monkeypatch.setattr(social_media, "_run_gallery_dl", lambda *_: payload)

    result = social_media.get_social_media_info(
        "https://www.facebook.com/photo/?fbid=123456789"
    )

    assert result is not None
    assert result["media_type"] == "image"
    assert social_media.is_allowed_social_media_url(
        "facebook",
        result["media_url"],
    )


def test_gallery_video_only_post_stays_on_ytdlp_path(monkeypatch):
    payload = _gallery_payload(
        (
            "https://v.pinimg.com/videos/example/video.m3u8",
            "video",
            "mp4",
            720,
            1280,
        )
    )
    monkeypatch.setattr(social_media, "_run_gallery_dl", lambda *_: payload)

    assert social_media.get_social_media_info(
        "https://www.pinterest.com/pin/123456789/"
    ) is None


def test_threads_parser_excludes_avatar_and_keeps_post_media():
    parser = social_media._ThreadsEmbedParser()
    parser.feed(
        """
        <img src="https://scontent.example.cdninstagram.com/v/t51.82787-19/avatar.jpg?s100x100"
             width="36" height="36" />
        <img src="https://scontent.example.cdninstagram.com/v/t51.82787-15/one.jpg"
             draggable="false" />
        <video><source src="https://scontent.example.cdninstagram.com/o1/video.mp4" /></video>
        """
    )

    assert parser.image_urls == [
        "https://scontent.example.cdninstagram.com/v/t51.82787-15/one.jpg"
    ]
    assert parser.video_urls == [
        "https://scontent.example.cdninstagram.com/o1/video.mp4"
    ]


def test_threads_video_exposes_one_real_quality(monkeypatch):
    monkeypatch.setattr(
        social_media,
        "_extract_threads_entries",
        lambda _url: [
            {
                "index": 1,
                "id": "1",
                "title": "Threads post",
                "duration": None,
                "thumbnail": None,
                "formats": [],
                "media_type": "video",
                "media_url": (
                    "https://scontent.example.cdninstagram.com/o1/video.mp4"
                ),
                "extension": "mp4",
                "width": None,
                "height": None,
            }
        ],
    )
    monkeypatch.setattr(
        social_media,
        "_probe_threads_video",
        lambda _url: {
            "width": 640,
            "height": 360,
            "duration": 18,
            "filesize": 1_812_374,
            "has_audio": True,
        },
    )

    result = social_media.get_social_media_info(
        "https://www.threads.com/@example/post/ABC123"
    )

    assert result["duration"] == 18
    assert result["formats"] == [
        {
            "format_id": "threads-direct",
            "extension": "mp4",
            "resolution": "640x360",
            "filesize": 1_812_374,
            "has_video": True,
            "has_audio": True,
            "video_codec": None,
            "audio_codec": None,
        }
    ]


def test_manifest_size_is_estimated_without_probing(monkeypatch):
    def fail_probe(_item):
        raise AssertionError("manifest URL must not be probed as a media file")

    monkeypatch.setattr(
        "app.services.download._probe_remote_filesize",
        fail_probe,
    )
    item = {
        "url": "https://v.pinimg.com/video/master.m3u8",
        "protocol": "m3u8_native",
        "tbr": 800,
        "filesize": 512,
    }

    assert _get_format_filesize(item, duration=10) == 1_030_000


def test_direct_pinterest_size_prefers_exact_cdn_probe(monkeypatch):
    monkeypatch.setattr(
        "app.services.download._probe_remote_filesize",
        lambda _item: 8_765_432,
    )
    item = {
        "url": "https://v.pinimg.com/videos/example/video.mp4",
        "protocol": "https",
        "filesize_approx": 7_000_000,
    }

    assert _get_format_filesize(item, duration=20) == 8_765_432


@pytest.mark.parametrize(
    ("platform", "url", "allowed"),
    [
        ("pinterest", "https://i.pinimg.com/originals/a.jpg", True),
        ("pinterest", "https://evil.example/a.jpg", False),
        ("tiktok", "https://p16.tiktokcdn-us.com/a.jpeg", True),
        ("threads", "https://scontent.cdninstagram.com/a.mp4", True),
        ("facebook", "http://scontent.fbcdn.net/a.jpg", False),
    ],
)
def test_media_cdn_allowlists(platform, url, allowed):
    assert social_media.is_allowed_social_media_url(platform, url) is allowed
