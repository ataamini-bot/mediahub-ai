"""A small local video preview; failure must never block recognition."""
import json
import math
import subprocess
from pathlib import Path

MAX_PREVIEW_BYTES = 5 * 1024**2


def prepare_preview(source: Path) -> dict | None:
    target = source.parent / "preview.mp4"
    temporary = source.parent / "preview.tmp.mp4"
    try:
        if target.exists() or target.is_symlink() or temporary.is_symlink():
            return None
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams",
             "-of", "json", str(source)], capture_output=True, timeout=5, check=True)
        video = next((s for s in json.loads(probe.stdout).get("streams", [])
                      if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
        if video is None:
            return None
        # Reuse the already-fetched sample. Never download the clip a second time.
        subprocess.run([
            "ffmpeg", "-nostdin", "-y", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-i", str(source), "-t", "12", "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=480:480:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-r", "24", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart", "-fs", str(MAX_PREVIEW_BYTES), str(temporary),
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=True)
        if not 0 < temporary.stat().st_size < MAX_PREVIEW_BYTES:
            return None
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams",
             "-show_format", "-of", "json", str(temporary)], capture_output=True, timeout=5, check=True)
        info = json.loads(result.stdout)
        stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        duration = float(info["format"]["duration"])
        if not math.isfinite(duration) or not 0 < duration <= 12.2:
            return None
        temporary.replace(target)
        return {"width": int(stream["width"]), "height": int(stream["height"]),
                "duration": max(1, round(duration))}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, subprocess.SubprocessError):
        return None
    finally:
        temporary.unlink(missing_ok=True)
