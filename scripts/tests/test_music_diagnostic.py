import importlib.util
import io
from pathlib import Path
import sys
from types import ModuleType
import wave

import pytest


spec = importlib.util.spec_from_file_location("diagnose_music", Path(__file__).parents[1] / "diagnose_music.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def make_audio(path):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        for value in (100, 200, 300):
            output.writeframes(value.to_bytes(2, "little", signed=True) * (12 * 16000))


def test_windows_use_distinct_audio_and_bound_the_sample(tmp_path):
    path = tmp_path / "audio.wav"
    make_audio(path)
    found = list(probe.windows(path))
    assert [summary["start_s"] for summary, _ in found] == [0, 12, 24]
    for (summary, data), expected in zip(found, (100, 200, 300)):
        assert summary["seconds"] == 12
        with wave.open(io.BytesIO(data)) as audio:
            assert audio.getnframes() == 12 * 16000
            assert int.from_bytes(audio.readframes(1), "little", signed=True) == expected


@pytest.mark.asyncio
async def test_diagnostic_stops_at_provider_error_without_more_requests(tmp_path, monkeypatch, capsys):
    path = tmp_path / "audio.wav"
    make_audio(path)
    calls = []
    class ToolError(RuntimeError):
        code = "provider_busy"
    async def recognize(content):
        calls.append(content)
        raise ToolError("private provider response")
    module = ModuleType("app.services.media_tool_shazam")
    module.recognize_sample = recognize
    errors = ModuleType("app.services.media_tool_providers")
    errors.ToolError = ToolError
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, errors.__name__, errors)
    await probe.compare(path)
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "provider_busy" in output and "private provider response" not in output
