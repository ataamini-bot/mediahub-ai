from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.services import pinterest_links as pins
from app.services import media_tool_links as links


@pytest.mark.parametrize("url", ["https://www.pinterest.com/pin/123456789/", "https://pin.it/TestLink",
    "https://de.pinterest.com/pin/123456789/", "https://www.pinterest.co.uk/pin/example-video--123456789/"])
def test_recognition_accepts_pins_and_short_links(url):
    assert links.normalize_music_link(url).startswith("https://")


@pytest.mark.parametrize("url", ["https://pinterest.example.com/pin/123456789/",
    "https://pinterest.com/private-board/", "https://pin.it/", "https://pin.it:444/TestLink",
    "https://user:secret@pin.it/TestLink", "http://127.0.0.1/pin/123456789/"])
def test_recognition_rejects_boards_spoofed_domains_and_private_hosts(url):
    with pytest.raises(ValueError):
        links.normalize_music_link(url)


def session(monkeypatch, target):
    calls = []
    class Response:
        status_code, headers = 302, {"Location": target}
        def __enter__(self): return self
        def __exit__(self, *_): pass
    class Session:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, url, **kwargs):
            assert not kwargs["allow_redirects"] and kwargs["stream"]
            calls.append(url)
            return Response()
    monkeypatch.setattr(pins.requests, "Session", Session)
    return calls


def test_short_pin_resolves_without_fetching_an_external_redirect(monkeypatch):
    calls = session(monkeypatch, "https://www.pinterest.com/pin/123456789/?tracking=test")
    assert pins.resolve_pin("https://pin.it/TestLink") == "https://www.pinterest.com/pin/123456789/"
    assert len(calls) == 1
    calls = session(monkeypatch, "http://127.0.0.1/private")
    with pytest.raises(ValueError, match="invalid_redirect"):
        pins.resolve_pin("https://pin.it/TestLink")
    assert len(calls) == 1


def test_pin_video_uses_the_bounded_recognition_sample_and_cleans_source_name(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(links, "resolve_pin", lambda _: "https://www.pinterest.com/pin/123456789/")
    from app.services import download
    monkeypatch.setattr(download, "_get_media_info_options", lambda _: {})
    @contextmanager
    def downloader(url, options):
        captured.update(url=url, options=options)
        def extract(value, download):
            assert value == url and download
            (tmp_path / "link-source.mp4").write_bytes(b"synthetic clip with audio")
        yield SimpleNamespace(extract_info=extract)
    monkeypatch.setattr(links, "media_downloader", downloader)
    target = tmp_path / "input"
    links.download_link_sample("https://pin.it/TestLink", target, seconds=36)
    assert captured["url"] == "https://www.pinterest.com/pin/123456789/"
    assert "pinterest" in captured["options"]["allowed_extractors"]
    assert captured["options"]["external_downloader_args"]["ffmpeg_o"][-2:] == ["-t", "36"]
    assert list(tmp_path.iterdir()) == [target]
