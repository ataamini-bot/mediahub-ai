import base64
import json
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
import pytest

from app.services import media_tool_providers as providers
from app.services.media_tool_files import folder, input_file, remove_files


def image_bytes():
    output = BytesIO()
    photo = Image.new("RGB", (240, 320), "grey")
    exif = Image.Exif()
    exif[270] = "private-metadata"
    photo.save(output, "JPEG", exif=exif)
    return output.getvalue()


def test_photo_metadata_removed_before_provider(tmp_path):
    source = tmp_path / "input"
    source.write_bytes(image_bytes())
    sanitized = providers.normalized_photo(source)
    assert b"private-metadata" not in sanitized
    assert Image.open(BytesIO(sanitized)).size == (240, 320)


@pytest.mark.parametrize("clothing", list(providers.CLOTHES))
@pytest.mark.parametrize("background", list(providers.BACKGROUNDS))
def test_portrait_edit_preserves_identity_and_mild_corrections(clothing, background):
    prompt = providers.portrait_prompt(clothing, background)
    assert "SAME person" in prompt and "mild exposure" in prompt and "Do not reshape" in prompt
    assert providers.CLOTHES[clothing] in prompt and providers.BACKGROUNDS[background] in prompt


def test_portrait_posts_original_image_and_only_uses_returned_image(monkeypatch, tmp_path):
    source = tmp_path / "input"
    source.write_bytes(image_bytes())
    calls = []
    def fake(url, **kwargs):
        calls.append((url, kwargs))
        return {"data": [{"b64_json": base64.b64encode(image_bytes()).decode()}]}
    monkeypatch.setattr(providers, "provider_json", fake)
    result = providers.create_portrait(source, {"token": "secret", "model": "gpt-image-2.6-flare"},
                                        {"clothing": "natural", "background": "blue"})
    assert result["output"] == "result.jpg"
    assert Image.open(tmp_path / "result.jpg").format == "JPEG"
    url, kwargs = calls[0]
    assert url == "https://api.openai.com/v1/images/edits"
    assert kwargs["files"]["image"][0] == "portrait.png"
    assert kwargs["data"]["n"] == "1" and "input_fidelity" not in kwargs["data"]
    assert b"private-metadata" not in kwargs["files"]["image"][1]


def test_provider_url_response_is_not_fetched(monkeypatch, tmp_path):
    source = tmp_path / "input"
    source.write_bytes(image_bytes())
    monkeypatch.setattr(providers, "provider_json", lambda *a, **k: {"data": [{"url": "http://127.0.0.1/secret"}]})
    with pytest.raises(providers.ToolError, match="invalid_provider_result"):
        providers.create_portrait(source, {"token": "x", "model": "gpt-image-2.6-flare"}, {"clothing": "suit", "background": "white"})
    assert not (tmp_path / "result.jpg").exists()


@pytest.mark.parametrize("status,code", [(401, "provider_auth"), (429, "provider_busy"), (500, "provider_rejected")])
def test_provider_errors_never_leak_body_or_key(monkeypatch, status, code):
    client = httpx.Client
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="secret-provider-key and private photo data")
    monkeypatch.setattr(providers.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(providers.ToolError) as error:
        providers.provider_json("https://api.audd.io/", token="secret-provider-key", files={}, data={})
    assert str(error.value) == code and len(calls) == 1


def test_provider_timeout_is_uncertain_and_not_retried(monkeypatch):
    client = httpx.Client
    calls = []
    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("contains secret", request=request)
    monkeypatch.setattr(providers.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(providers.ToolError, match="provider_uncertain"):
        providers.provider_json("https://api.audd.io/", token="private", files={}, data={})
    assert len(calls) == 1


def test_recognition_discards_preview_urls_and_only_returns_identity(monkeypatch, tmp_path):
    source = tmp_path / "input"
    source.write_bytes(b"audio")
    monkeypatch.setattr(providers, "recognition_sample", lambda a, b: b.write_bytes(b"sample"))
    monkeypatch.setattr(providers, "provider_json", lambda *a, **k: {"status": "success", "result": {
        "artist": "Example Artist", "title": "Original Song", "apple_music": {"durationInMillis": 180000,
        "previews": [{"url": "https://example.org/preview.m4a"}]}}})
    track = providers.recognize_music(source, "secret")
    assert track == {"artist": "Example Artist", "title": "Original Song", "album": "", "duration": 180}
    assert not (tmp_path / "sample.wav").exists()


def test_full_track_candidates_exclude_clips_covers_wrong_artists_and_unknown_durations():
    track = {"title": "Original Song", "artist": "Example Artist", "duration": 180}
    def item(id, **kwargs):
        return {"id": id, "title": "Example Artist Original Song", "duration": 180, **kwargs}
    entries = [item("AbcDefGhI12"), item("AbcDefGhI12"), item("AAAAAAAAAAA", duration=30),
        item("BBBBBBBBBBB", title="Example Artist Original Song cover"),
        item("CCCCCCCCCCC", title="Other Artist Original Song"),
        item("DDDDDDDDDDD", duration=None), item("EEEEEEEEEEE", duration=float("nan")),
        item("http://evil"), item("FFFFFFFFFFF", duration=600)]
    assert providers.song_candidates(track, entries) == [{"title": entries[0]["title"], "channel": "",
        "duration": 180, "url": "https://www.youtube.com/watch?v=AbcDefGhI12"}]
    assert providers.song_candidates({**track, "duration": None}, entries) == []


def test_sample_is_bounded_pcm_and_has_no_video(tmp_path):
    import subprocess
    source = tmp_path / "input"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=15",
                    "-f", "wav", str(source)], check=True)
    target = tmp_path / "sample.wav"
    providers.recognition_sample(source, target)
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)]))
    assert float(probe["format"]["duration"]) <= 12.01
    assert len(probe["streams"]) == 1 and probe["streams"][0]["codec_name"] == "pcm_s16le"


def test_invalid_photo_never_calls_provider(monkeypatch, tmp_path):
    source = tmp_path / "input"
    source.write_bytes(b"not an image")
    monkeypatch.setattr(providers, "provider_json", lambda *a, **k: pytest.fail("provider called"))
    with pytest.raises(providers.ToolError, match="invalid_photo"):
        providers.create_portrait(source, {}, {})


def test_cleanup_does_not_follow_symlinks_and_checks_upload_limit(monkeypatch, tmp_path):
    from uuid import uuid4
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    request_id = uuid4()
    path = folder(123, request_id)
    path.mkdir(parents=True)
    outside = tmp_path / "private"
    outside.write_text("keep")
    (path / "input").symlink_to(outside)
    with pytest.raises(ValueError):
        input_file(123, request_id, "music")
    remove_files(123, request_id)
    assert outside.read_text() == "keep" and not path.exists()
