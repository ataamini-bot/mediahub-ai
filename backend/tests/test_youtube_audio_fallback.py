"""Select audible full-source formats and encode the combined fallback as MP3."""
import copy
import io
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking.common import Response
from yt_dlp.utils import ExtractorError

from app.workers.tasks import download as worker
from app.models.download_job import DownloadJobStatus


URL = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
MEDIA = "https://r1.googlevideo.com/videoplayback?itag=18"
FORMATS = {
    "140": {"format_id": "140", "ext": "m4a", "acodec": "aac", "vcodec": "none", "abr": 128},
    "140-drc": {"format_id": "140-drc", "ext": "m4a", "acodec": "aac", "vcodec": "none", "abr": 128},
    "251": {"format_id": "251", "ext": "webm", "acodec": "opus", "vcodec": "none", "abr": 160},
    "18": {"format_id": "18", "ext": "mp4", "acodec": "aac", "vcodec": "h264", "height": 360},
    "137": {"format_id": "137", "ext": "mp4", "acodec": "none", "vcodec": "h264", "height": 1080},
    "sb": {"format_id": "sb", "ext": "mhtml", "acodec": "none", "vcodec": "none", "protocol": "mhtml"},
}


def formats(names):
    return [{"url": MEDIA, "protocol": "https", **copy.deepcopy(FORMATS[name])} for name in names]


@pytest.mark.parametrize("available,selected", [
    (["18", "251", "140-drc", "140"], "140"),
    (["18", "251", "140-drc"], "140-drc"),
    (["18", "251"], "251"),
    (["18", "sb", "137"], "18"),
    (["137", "sb"], None),
])
def test_real_selector_prefers_audio_and_uses_only_audible_combined_fallback(available, selected):
    options = worker._get_yt_dlp_options(URL, format=worker._get_format_selector(URL, None, None, "audio", allow_combined_audio=True),
                                       quiet=True, cachedir=False)
    info = {"id": "AAAAAAAAAAA", "extractor": "synthetic", "title": "Synthetic full song", "formats": formats(available)}
    with YoutubeDL(options) as downloader:
        if selected is None:
            with pytest.raises(ExtractorError, match="Requested format is not available"):
                downloader.process_ie_result(info, download=False)
        else:
            result = downloader.process_ie_result(info, download=False)
            assert result["format_id"] == selected


def test_explicit_format_choice_and_unconverted_audio_are_preserved():
    assert worker._get_format_selector(URL, "251", None, "audio", allow_combined_audio=True) == "251"
    # A raw audio request without a conversion target must not return a video.
    selector = worker._get_format_selector(URL, None, None, "audio")
    with YoutubeDL({"format": selector, "quiet": True}) as downloader:
        with pytest.raises(ExtractorError, match="Requested format is not available"):
            downloader.process_ie_result({"id": "AAAAAAAAAAA", "extractor": "synthetic",
                "title": "Synthetic full song", "formats": formats(["18"])}, download=False)


def test_combined_full_source_download_becomes_mp3_320_without_video_or_truncation(tmp_path, monkeypatch):
    source = tmp_path / "fixture.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=size=32x32:rate=1",
        "-f", "lavfi", "-i", "sine=sample_rate=8000", "-t", "20", "-c:v", "libx264", "-threads", "1",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)
    body = source.read_bytes()
    source.unlink()
    calls = []
    job_id = 12
    monkeypatch.setattr(worker, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(worker, "_check_job_control", lambda _id: None)
    monkeypatch.setattr(worker, "_update_download_stats", lambda **_values: None)
    monkeypatch.setattr(worker, "_normalize_video_for_compatibility", lambda *_a: pytest.fail("Re-encoded video for audio job"))
    job = SimpleNamespace(status=DownloadJobStatus.PENDING, source_url=URL, format_id=None,
        quality=None, media_type="audio", output_format="mp3", playlist_index=None,
        plan_limits_snapshot={"max_file_size_mb": 2})
    session = MagicMock()
    session.__enter__.return_value.execute.return_value.scalar_one_or_none.return_value = job
    monkeypatch.setattr(worker, "WorkerSessionLocal", lambda: session)
    monkeypatch.setattr(worker, "_set_processing", lambda _id: True)
    monkeypatch.setattr(worker, "_get_job_status", lambda _id: DownloadJobStatus.PROCESSING)
    completed = MagicMock()
    monkeypatch.setattr(worker, "_set_completed", completed)
    monkeypatch.setattr(worker.download_task, "retry", lambda **_kwargs: pytest.fail("Unexpected retry"))

    class SyntheticIE(InfoExtractor):
        _VALID_URL = r"https://www\.youtube\.com/watch\?v=(?P<id>AAAAAAAAAAA)"
        def _real_extract(self, url):
            assert url == URL  # The selected independent source, never a submitted clip.
            return {"id": "AAAAAAAAAAA", "title": "Synthetic full song", "duration": 20,
                    "formats": formats(["18", "sb"])}

    def opener(request):
        assert request.url == MEDIA
        calls.append(request.url)
        return Response(io.BytesIO(body), MEDIA, {"Content-Length": str(len(body))}, status=200)

    def factory(options):
        downloader = YoutubeDL({**options, "cachedir": False, "fixup": "never"}, auto_init=False)
        downloader.add_info_extractor(SyntheticIE())
        downloader.urlopen = opener
        return downloader
    monkeypatch.setattr(worker.yt_dlp, "YoutubeDL", factory)
    assert worker.download_task.run(job_id) == f"Download job {job_id} completed"
    assert len(calls) == 1
    completed.assert_called_once()
    output = tmp_path / f"{job_id}.mp3"
    assert completed.call_args.kwargs == {"job_id": job_id, "file_path": str(output), "file_size": output.stat().st_size}
    probe = worker._probe_media_file(output)
    assert [stream["codec_type"] for stream in probe["streams"]] == ["audio"]
    audio = probe["streams"][0]
    assert (audio["codec_name"], int(audio["bit_rate"]), int(audio["sample_rate"])) == ("mp3", 320000, 44100)
    assert float(probe["format"]["duration"]) == pytest.approx(20, abs=.5)
    assert list(tmp_path.iterdir()) == [output]  # Intermediate combined media removed.
