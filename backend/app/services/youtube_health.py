"""Check the local PO-token provider without contacting YouTube or Telegram."""
import json
import os
from importlib.metadata import version
from urllib.request import urlopen


def provider_health():
    result = {"healthy": False}
    try:
        plugin = version("bgutil-ytdlp-pot-provider")
        result.update(plugin_version=plugin, ytdlp_version=version("yt-dlp"))
        endpoint = os.environ.get("BGUTIL_POT_BASE_URL", "http://bgutil-provider:4416")
        with urlopen(endpoint.rstrip("/") + "/ping", timeout=5) as response:
            data = json.loads(response.read(4096))
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise ValueError("Invalid provider response")
        result["provider_version"] = data["version"]
        result["healthy"] = data["version"] == plugin
        if not result["healthy"]:
            result["error"] = "ProviderPluginVersionMismatch"
    except Exception as exc:
        # Endpoint URLs can contain credentials. Never echo them or raw errors.
        result["error"] = type(exc).__name__
    return result


if __name__ == "__main__":
    status = provider_health()
    print("YOUTUBE_PROVIDER=" + json.dumps(status))
    raise SystemExit(0 if status["healthy"] else 1)
