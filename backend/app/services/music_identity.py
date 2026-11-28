"""Discovery can omit a version suffix; accepting that file still needs proof."""
import re


def discovery_title(title):
    value = str(title)
    # Only a known version suffix, not arbitrary parentheses in a song name.
    return re.sub(r"\s*[\[(](?:acoustic|unplugged|live|instrumental|radio edit|"
                  r"acoustic version|live version|آکوستیک|اکوستیک|اجرای زنده)[\])]\s*$",
                  "", value, flags=re.I).strip()


def can_verify_audio(track):
    return (track.get("recognition_provider") == "shazamio"
            and isinstance(track.get("recognition_key"), str)
            and re.fullmatch(r"[0-9]{1,20}", track["recognition_key"]) is not None)


def discovery_track(track):
    if can_verify_audio(track):
        return {**track, "title": discovery_title(track.get("title", ""))}
    return track
