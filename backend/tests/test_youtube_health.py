import io
import json
from urllib.error import URLError

import pytest

from app.services import youtube_health


@pytest.mark.parametrize("payload,healthy,error", [
    ({"version": "2.0.2", "server_uptime": 10}, True, None),
    ({"version": "1.3.2"}, False, "ProviderPluginVersionMismatch"),
    ({"server_uptime": 10}, False, "ValueError"),
    ([], False, "ValueError"),
])
def test_only_a_reachable_matching_provider_is_healthy(monkeypatch, payload, healthy, error):
    monkeypatch.setattr(youtube_health, "version", lambda name: "2.0.2" if name.startswith("bgutil") else "2026.08.19")
    monkeypatch.setenv("BGUTIL_POT_BASE_URL", "http://provider.internal:4416/")
    def respond(endpoint, *, timeout):
        assert endpoint == "http://provider.internal:4416/ping"
        assert timeout == 5
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(youtube_health, "urlopen", respond)
    result = youtube_health.provider_health()
    assert result["healthy"] is healthy
    assert result.get("error") == error


def test_provider_failure_does_not_disclose_endpoint_credentials(monkeypatch):
    monkeypatch.setattr(youtube_health, "version", lambda name: "2.0.2")
    monkeypatch.setenv("BGUTIL_POT_BASE_URL", "http://private:password@provider.internal:4416")
    def fail(*args, **kwargs):
        raise URLError("Cannot connect to http://private:password@provider.internal:4416")
    monkeypatch.setattr(youtube_health, "urlopen", fail)
    result = youtube_health.provider_health()
    assert result["healthy"] is False
    assert result["error"] == "URLError"
    assert "password" not in json.dumps(result)
