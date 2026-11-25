"""Synthetic source fingerprints; never contact a recognition service."""
import io
import subprocess
import time
import wave

import pytest

from app.services import music_source_match as matcher, media_tool_shazam as shazam
from app.services.media_tool_providers import ToolError

TRACK={"title":"Original Song (Acoustic)","artist":"Example Artist",
       "recognition_provider":"shazamio","recognition_key":"12345"}


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    path=tmp_path_factory.mktemp("source-match")/'sample.mp3'
    subprocess.run(["ffmpeg","-nostdin","-v","error","-f","lavfi","-i","sine=duration=15",
                    "-c:a","libmp3lame","-b:a","320k",str(path)],check=True)
    return path


@pytest.mark.parametrize("key,expected",[("12345",True),("67890",False)])
def test_only_the_same_shazam_recording_can_validate_missing_version(sample,monkeypatch,tmp_path,key,expected):
    calls=[]
    async def recognize(content):
        with wave.open(io.BytesIO(content)) as audio:
            assert audio.getnframes() <= 12*16000
            assert audio.getnchannels() == 1 and audio.getframerate() == 16000
        calls.append(1)
        return {"matches":[{"id":"match"}],"track":{"title":"Original Song","subtitle":"Example Artist","key":key}}
    monkeypatch.setattr(shazam,"recognize_sample",recognize)
    monkeypatch.setattr(matcher.tempfile,"tempdir",str(tmp_path))
    assert matcher.matches_sample(TRACK,sample,time.monotonic()+10,lambda:None) is expected
    assert len(calls)==1 and not list(tmp_path.iterdir()) and sample.exists()


def test_other_provider_or_expired_budget_never_sends_source_audio(monkeypatch):
    monkeypatch.setattr(matcher.subprocess,"run",lambda *a,**k:pytest.fail("Unexpected audio processing"))
    assert not matcher.matches_sample({**TRACK,"recognition_provider":"audd"},None,time.monotonic()+10,lambda:None)
    assert not matcher.matches_sample(TRACK,None,time.monotonic()-1,lambda:None)


@pytest.mark.parametrize("service_error",[False,True])
def test_valid_no_match_is_distinct_from_service_error(sample,monkeypatch,tmp_path,service_error):
    async def recognize(content):
        if service_error:
            raise ToolError("provider_busy")
        return {"matches":[]}
    monkeypatch.setattr(shazam,"recognize_sample",recognize)
    monkeypatch.setattr(matcher.tempfile,"tempdir",str(tmp_path))
    if service_error:
        with pytest.raises(ToolError,match="provider_busy"):
            matcher.matches_sample(TRACK,sample,time.monotonic()+10,lambda:None)
    else:
        assert not matcher.matches_sample(TRACK,sample,time.monotonic()+10,lambda:None)
    assert not list(tmp_path.iterdir())
