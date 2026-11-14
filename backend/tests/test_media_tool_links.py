import signal
import subprocess
import sys

import pytest

from app.services import media_tool_links as links
from app.services.media_tool_providers import ToolError


@pytest.mark.parametrize("url", [
    "http://localhost/video", "https://127.0.0.1/", "file:///etc/passwd",
    "https://youtube.com.evil.test/video", "https://youtube.com@127.0.0.1/",
    "https://www.youtube.com:8443/watch?v=AbcDefGhI12", "https://user:pass@youtube.com/",
    "https://youtube.com\\@localhost/", "https://youtube.com/\nprivate", "ytsearch:music",
])
def test_music_link_rejects_local_arbitrary_and_credential_urls(url):
    with pytest.raises(ValueError):
        links.normalize_music_link(url)


@pytest.mark.parametrize("url", [
    "https://www.instagram.com/reel/Example/", "https://m.youtube.com/watch?v=AbcDefGhI12",
    "https://youtu.be/AbcDefGhI12?si=example", "https://vm.tiktok.com/example/",
    "https://x.com/example/status/123", "https://www.threads.com/@example/post/ABC",
])
def test_music_link_accepts_public_supported_hosts(url):
    assert links.normalize_music_link(url) == url


def test_link_sample_uses_existing_profile_and_only_twelve_seconds(monkeypatch, tmp_path):
    import yt_dlp
    seen = {}
    class Downloader:
        def __init__(self, options):
            seen.update(options)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, url, download):
            assert url == "https://youtu.be/AbcDefGhI12" and download
            (tmp_path / "link-source.webm").write_bytes(b"independent-link-sample")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    links.download_link_sample("https://youtu.be/AbcDefGhI12", tmp_path / "input")
    assert (tmp_path / "input").read_bytes() == b"independent-link-sample"
    assert not (tmp_path / "link-source.webm").exists()
    assert list(seen["download_ranges"]({}, None)) == [{"start_time": 0, "end_time": 12}]
    assert seen["playlist_items"] == "1" and seen["noplaylist"]
    assert seen["extractor_args"]["youtube"]["player_client"] == ["mweb"]
    assert seen["extractor_args"]["youtubepot-bgutilhttp"]["base_url"]
    assert "file" not in seen["external_downloader_args"]["ffmpeg_i"][1].split(",")
    assert seen["external_downloader_args"]["ffmpeg_o"][-2:] == ["-t", "12"]


def test_failed_link_child_cleans_partial_sample(monkeypatch, tmp_path):
    class Process:
        def wait(self, **kwargs):
            (tmp_path / "link-source.mp4.part").write_bytes(b"partial")
            return 1
    monkeypatch.setattr(links.subprocess, "Popen", lambda *a, **k: Process())
    with pytest.raises(ToolError, match="link_unavailable"):
        links.fetch_music_link("https://youtu.be/AbcDefGhI12", tmp_path / "input")
    assert list(tmp_path.iterdir()) == []


def test_link_timeout_kills_the_extractor_process_group(monkeypatch, tmp_path):
    popen = subprocess.Popen
    children = []
    def start(*args, **kwargs):
        process = popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(links.subprocess, "Popen", start)
    with pytest.raises(ToolError, match="link_timeout"):
        links.fetch_music_link("https://youtu.be/AbcDefGhI12", tmp_path / "input", timeout=.1)
    assert len(children) == 1 and children[0].returncode == -signal.SIGKILL
    assert list(tmp_path.iterdir()) == []
