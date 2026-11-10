"""Use the same Instagram rendition for quality estimates and the worker."""

import math


def _number(value) -> float:
    try:
        number = float(value or 0)
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def instagram_video_choices(formats: list) -> dict[int, tuple[dict, str]]:
    """Map the short-side resolution to a source format and a yt-dlp selector."""
    entries = [f for f in formats if isinstance(f, dict) and f.get("format_id") and not f.get("has_drm")]
    separate_audio = any(
        f.get("vcodec") == "none" and f.get("acodec") not in (None, "none") for f in entries
    )
    groups = {}
    for f in entries:
        quality = int(min(_number(f.get("width")), _number(f.get("height"))))
        if quality > 0 and f.get("vcodec") not in ("none", "images") and f.get("protocol") != "mhtml":
            groups.setdefault(quality, []).append(f)

    def score(f):
        codec = str(f.get("vcodec") or "").lower()
        audio = f.get("acodec")
        # Avoid selecting a silent or lower-FPS AVC alternative over audible
        # or higher-FPS footage. Unknown codec metadata isn't evidence of AVC.
        sound = 2 if separate_audio or audio not in (None, "none") else int(audio is None)
        return (
            sound, round(_number(f.get("fps"))),
            codec.startswith(("avc1", "avc3", "h264", "h.264")),
            f.get("ext") == "mp4", audio not in (None, "none"),
            str(f.get("protocol") or "").startswith("http"),
            _number(f.get("tbr")), _number(f.get("filesize") or f.get("filesize_approx")),
        )

    choices = {}
    for quality, candidates in groups.items():
        f = max(candidates, key=score)
        selector = str(f["format_id"])
        if f.get("acodec") in (None, "none") and separate_audio:
            selector = f"{selector}+bestaudio[ext=m4a]/{selector}+bestaudio"
        choices[quality] = (f, selector)
    return choices
