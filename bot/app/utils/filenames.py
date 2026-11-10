"""User-visible filenames; internal download paths remain unique and unchanged."""
import re
import unicodedata


def media_filename(title: object, suffix: str, *, cover: bool = False) -> str:
    suffix = suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    name = unicodedata.normalize("NFC", str(title or ""))
    # Keep Persian half-spaces and emoji joiners, but remove control/bidi marks.
    name = "".join(
        (" " if ch.isspace() else ch) for ch in name
        if ch.isspace() or not unicodedata.category(ch).startswith("C") or ch in "\u200c\u200d"
    )
    name = re.sub(r'[<>:"/\\|?*]', " ", name)
    name = " ".join(name.split()).strip(" .")
    if not name or name.lower() in {"none", "null", "n/a", "na"}:
        name = "MediaHub"
    if name.lower().endswith(suffix):
        name = name[:-len(suffix)].rstrip(" .") or "MediaHub"
    if re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name, re.I):
        name = "MediaHub-" + name
    ending = (" - cover" if cover else "") + suffix
    # Leave ample room below common 255-byte filesystem component limits.
    name = name.encode("utf-8")[:180 - len(ending)].decode("utf-8", errors="ignore").rstrip(" .")
    return (name or "MediaHub") + ending
