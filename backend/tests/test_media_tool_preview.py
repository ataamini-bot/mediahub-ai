import json
import subprocess

from app.services.media_tool_preview import prepare_preview, MAX_PREVIEW_BYTES
from app.services.media_tool_files import folder, remove_files


def test_preview_is_short_small_playable_and_does_not_change_input(tmp_path):
    source = tmp_path / "input"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=640x360:rate=24", "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", "13", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-f", "matroska", str(source)], check=True)
    size = source.stat().st_size
    preview = prepare_preview(source)
    assert preview == {"width": 480, "height": 270, "duration": 12}
    target = tmp_path / "preview.mp4"
    assert 0 < target.stat().st_size < MAX_PREVIEW_BYTES
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(target)]))
    assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
    assert source.stat().st_size == size
    assert not (tmp_path / "preview.tmp.mp4").exists()


def test_audio_and_unreadable_inputs_skip_preview(tmp_path):
    source = tmp_path / "input"
    source.write_bytes(b"invalid")
    assert prepare_preview(source) is None
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-f", "wav", str(source)], check=True)
    assert prepare_preview(source) is None
    assert set(tmp_path.iterdir()) == {source}


def test_preview_cleanup_retains_only_preview_until_delivery(tmp_path, monkeypatch):
    from uuid import uuid4
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    request_id = uuid4()
    path = folder(42, request_id)
    path.mkdir(parents=True)
    for name in ("input", "sample.wav", "preview.tmp.mp4", "preview.mp4"):
        (path / name).write_bytes(b"sample")
    remove_files(42, request_id, keep_preview=True)
    assert [p.name for p in path.iterdir()] == ["preview.mp4"]
    remove_files(42, request_id)
    assert not path.exists()
