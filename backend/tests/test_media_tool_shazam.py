import asyncio
import io
import json
import subprocess
import wave
from array import array
from types import SimpleNamespace

import httpx
import pytest

from app.services import media_tool_providers as providers
from app.services import media_tool_shazam as shazam
from app.services.media_tool_config import ToolConfiguration, public_config


RESPONSE = {"matches": [{"id": "123"}], "track": {
    "title": "Original Song", "subtitle": "Example Artist",
    "hub": {"actions": [{"uri": "https://example.org/preview.m4a"}]},
    "duration": 30,
}}
URL = "https://amp.shazam.com/discovery/v5/en-US/GB/iphone/-/tag/example"


def test_shazam_is_default_and_does_not_need_saved_credentials():
    config = ToolConfiguration(kind="music", enabled=True).model_dump()
    assert config["provider"] == "shazamio"
    public = public_config(config)
    assert public["available"] and public["configured"] and not public["token_configured"]
    assert not public_config({**config, "provider": "audd"})["available"]
    assert not public_config({**config, "provider": "unknown", "token": "private"})["available"]
    # Pre-provider settings switch to the free default, even with an old paid key.
    assert public_config({"enabled": True, "token": "private"})["provider"] == "shazamio"


def test_shazam_identity_excludes_preview_and_unverified_duration():
    assert shazam.track_identity(RESPONSE) == {
        "title": "Original Song", "artist": "Example Artist", "album": "", "duration": None}


@pytest.mark.parametrize("response,code", [
    ({"matches": []}, "music_not_found"),
    ({}, "invalid_provider_result"),
    ({"matches": [], "error": "unavailable"}, "invalid_provider_result"),
    ({"matches": [1]}, "invalid_provider_result"),
    ({"matches": [], "track": RESPONSE["track"]}, "invalid_provider_result"),
    ({"matches": [1], "track": {"title": "", "subtitle": "Artist"}}, "invalid_provider_result"),
    ([], "invalid_provider_result"),
])
def test_shazam_no_match_and_bad_metadata_are_explicit(response, code):
    with pytest.raises(providers.ToolError, match=code):
        shazam.track_identity(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(429, "provider_busy"), (403, "provider_auth"),
    (500, "provider_rejected"), (302, "provider_rejected")])
async def test_shazam_http_errors_are_bounded_sanitized_and_not_retried(status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private-provider-body", headers={"Location": "https://elsewhere.invalid"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(providers.ToolError) as error:
            await shazam.RecognitionClient(client).request("POST", URL, json={})
    assert str(error.value) == code and len(calls) == 1


@pytest.mark.asyncio
async def test_shazam_response_limit_and_fixed_destination(monkeypatch):
    monkeypatch.setattr(shazam, "MAX_RESPONSE_BYTES", 16)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * 17)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = shazam.RecognitionClient(client)
        with pytest.raises(providers.ToolError, match="invalid_provider_result"):
            await adapter.request("POST", URL, json={})
        with pytest.raises(providers.ToolError, match="provider_rejected"):
            await adapter.request("POST", "http://127.0.0.1/private", json={})
    assert len(calls) == 1


def test_pinned_shazamio_fingerprints_audio_and_never_uses_paid_key(monkeypatch, tmp_path):
    source = tmp_path / "input.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=12", str(source)], check=True)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == "amp.shazam.com"
        payload = json.loads(request.content)
        assert payload["signature"]["uri"].startswith("data:audio/vnd.shazam.sig;base64,")
        assert b"saved-audd-key" not in request.content
        assert "authorization" not in request.headers
        return httpx.Response(200, json=RESPONSE)
    client = httpx.AsyncClient
    monkeypatch.setattr(shazam.httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(providers, "provider_json", lambda *a, **k: pytest.fail("AudD called"))
    result = providers.recognize_music(source, "saved-audd-key")
    assert result["title"] == "Original Song" and len(calls) == 1
    assert source.exists() and not (tmp_path / "sample.wav").exists()


def test_shazam_timeout_cancels_request_cleans_sample_and_never_falls_back(monkeypatch, tmp_path):
    source = tmp_path / "input"
    source.write_bytes(b"fixture")
    cancelled = []
    async def pending(sample):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(shazam, "recognition_sample", lambda a, b, **kwargs: write_windows(b))
    monkeypatch.setattr(shazam, "recognize_sample", pending)
    monkeypatch.setattr(shazam, "RECOGNITION_TIMEOUT", .01)
    monkeypatch.setattr(providers, "provider_json", lambda *a, **k: pytest.fail("Paid fallback called"))
    with pytest.raises(providers.ToolError, match="provider_uncertain"):
        providers.recognize_music(source, "saved-audd-key")
    assert cancelled == [True] and not (tmp_path / "sample.wav").exists()


def test_unknown_provider_never_silently_calls_audd(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "provider_json", lambda *a, **k: pytest.fail("Unexpected paid call"))
    with pytest.raises(providers.ToolError, match="provider_auth"):
        providers.recognize_music(tmp_path / "input", "saved-audd-key", provider="unknown")


def write_windows(target, seconds=36):
    with wave.open(str(target), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        # Distinct waveform per window, no silence or repeated content.
        for second in range(seconds):
            audio.writeframes(array("h", [1000 * (second // 12 + 1)] * 16000).tobytes())


def test_shazam_tries_distinct_windows_and_stops_at_first_match(monkeypatch, tmp_path):
    source = tmp_path / "input.wav"
    write_windows(source)
    calls = []
    async def recognize(content):
        with wave.open(io.BytesIO(content)) as audio:
            assert audio.getnframes() == 12 * 16000
            calls.append(int.from_bytes(audio.readframes(1), "little", signed=True))
        return RESPONSE if len(calls) == 2 else {"matches": []}
    monkeypatch.setattr(shazam, "recognize_sample", recognize)
    assert shazam.recognize_shazam(source)["title"] == "Original Song"
    assert calls == [1000, 2000]
    assert source.exists() and not (tmp_path / "sample.wav").exists()


@pytest.mark.parametrize("seconds,expected", [(12, 1), (18, 1), (24, 2), (36, 3), (60, 3)])
def test_shazam_no_match_is_bounded_and_does_not_send_short_tails(monkeypatch, tmp_path, seconds, expected):
    source = tmp_path / "input.wav"
    write_windows(source, seconds)
    calls = []
    async def recognize(content):
        calls.append(content)
        return {"matches": []}
    monkeypatch.setattr(shazam, "recognize_sample", recognize)
    with pytest.raises(providers.ToolError, match="music_not_found"):
        shazam.recognize_shazam(source)
    assert len(calls) == expected
    assert not (tmp_path / "sample.wav").exists()


@pytest.mark.parametrize("code", ["provider_auth", "provider_busy", "provider_uncertain", "invalid_provider_result"])
def test_service_failure_does_not_try_another_window(monkeypatch, tmp_path, code):
    source = tmp_path / "input.wav"
    write_windows(source)
    calls = []
    async def recognize(content):
        calls.append(content)
        raise providers.ToolError(code)
    monkeypatch.setattr(shazam, "recognize_sample", recognize)
    with pytest.raises(providers.ToolError, match=code):
        shazam.recognize_shazam(source)
    assert len(calls) == 1
    assert not (tmp_path / "sample.wav").exists()


def test_cancel_between_windows_stops_before_the_next_request(monkeypatch, tmp_path):
    source = tmp_path / "input.wav"
    write_windows(source)
    calls = []
    async def recognize(content):
        calls.append(content)
        return {"matches": []}
    def check():
        if calls:
            raise providers.ToolError("request_cancelled")
    monkeypatch.setattr(shazam, "recognize_sample", recognize)
    with pytest.raises(providers.ToolError, match="request_cancelled"):
        providers.recognize_music(source, check_cancelled=check)
    assert len(calls) == 1 and not (tmp_path / "sample.wav").exists()


def test_all_windows_share_one_recognition_deadline(monkeypatch, tmp_path):
    source = tmp_path / "input.wav"
    source.write_bytes(b"input")
    monkeypatch.setattr(shazam, "recognition_sample", lambda a, b, **kwargs: write_windows(b))
    # Advance only the recognition clock; no wall-clock sleeps or CI timing race.
    clock = iter([100, 100, 150, 191])
    monkeypatch.setattr(shazam, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    calls, timeouts = [], []
    async def recognize(content):
        calls.append(1)
        return {"matches": []}
    async def bounded(coroutine, *, timeout):
        timeouts.append(timeout)
        return await coroutine
    monkeypatch.setattr(shazam.asyncio, "wait_for", bounded)
    monkeypatch.setattr(shazam, "recognize_sample", recognize)
    with pytest.raises(providers.ToolError, match="provider_uncertain"):
        shazam.recognize_shazam(source)
    assert calls == [1, 1] and timeouts == [45, 40]
    assert not (tmp_path / "sample.wav").exists()
