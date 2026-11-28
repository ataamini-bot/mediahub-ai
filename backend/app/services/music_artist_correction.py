"""Recover provider artist errors only with catalogue consensus and audio proof."""
from concurrent.futures import ThreadPoolExecutor
import logging
import math
import re
from urllib.parse import urljoin

import httpx

from app.services import musicfa
from app.services.music_source_catalog import BY_KEY, is_public_candidate
from app.services.music_identity import can_verify_audio

logger = logging.getLogger(__name__)
# Reviewed public search/download layouts. No guessed media URL is requested.
FALLBACK_SOURCES = ("rozmusic", "upmusics", "birmusic")


def _identity_key(artist, title):
    return tuple(sorted(musicfa._artist(artist))), tuple(sorted(musicfa._words(title)))


def page_candidates(source, track, body, page_url):
    from app.services.music_sources import Page, _excluded, _file_name, download_links
    root = Page(body).root
    candidates = {}
    for node in root.nodes():
        if node.tag != "a" or _excluded(node):
            continue
        url = urljoin(page_url, node.attrs.get("href", ""))
        if not source.allows(url, media=True):
            continue
        parts = re.split(r"\s+[-–—]\s+", _file_name(source, url), maxsplit=1)
        if len(parts) != 2:
            continue
        artist, title = [part.strip() for part in parts]
        if (not 1 <= len(artist) <= 150 or not musicfa._artist(artist)
                or musicfa._artist(artist) == musicfa._artist(track["artist"])
                or musicfa._words(title) != musicfa._words(track["title"])):
            continue
        wanted = musicfa._artist(artist + " " + title)
        # A filename alone cannot change attribution. The primary page must
        # independently name the artist and full title, outside related links.
        evidence = False
        for label in root.nodes():
            if _excluded(label):
                continue
            text = label.attrs.get("alt", "") if label.tag == "img" else (
                label.text()[:4000] if label.tag in {"h1", "h2", "h3", "h4", "p", "strong"} else "")
            if wanted <= musicfa._artist(text) and not ((musicfa._words(text) & musicfa.VARIANTS) - musicfa._words(track["title"])):
                evidence = True
                break
        if not evidence:
            continue
        identity = {"artist": artist, "title": title}
        urls = download_links(source, identity, body, page_url)
        if len(urls) == 1:
            candidates[_identity_key(artist, title)] = {"source": source.key, "source_name": source.label,
                "source_type": "public_mp3", "title": title, "channel": artist,
                "url": urls[0], "page_url": page_url}
    return list(candidates.values())[:2]


def _discover(source, track, deadline, control):
    from app.services.music_sources import _search, _cooldown, SearchStopped
    pages = _search(source, track, deadline, control, title_only=True)
    found = []
    for page in pages[:2]:
        control()
        try:
            with musicfa._client() as client:
                body = musicfa._html(client, page, deadline, allowed=source.allows, check_cancelled=control)
            found.extend(page_candidates(source, track, body, page))
        except SearchStopped:
            raise
        except (musicfa.MusicFaError, httpx.HTTPError, ValueError, TypeError) as exc:
            _cooldown(source, exc)
            break
    return found


def find_corrected_tracks(track, active, unavailable, stored, deadline, control, verify):
    from app.services.music_sources import SearchStopped, _cooldown
    from app.services.music_source_cache import read
    from app.services.recognition_access import check
    from app.services.media_tool_providers import ToolError
    # Short generic titles would create ambiguous and expensive broad searches.
    if not can_verify_audio(track) or len(musicfa._words(track.get("title", ""))) < 2:
        return []
    try:
        check("shazamio")
    except ToolError:
        return []
    stored = {**stored, **read(["cooldown:" + key for key in FALLBACK_SOURCES])}
    chosen = [s for key in FALLBACK_SOURCES for s in active if s.key == key
              and s.key not in unavailable and not stored.get("cooldown:" + s.key)]
    if len(chosen) < 2:
        return []
    grouped = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="music-attribution") as pool:
        futures = {pool.submit(_discover, s, track, deadline, control): s for s in chosen}
        # Every network/decode operation also observes this same deadline.
        for future in futures:
            try:
                values = future.result()
            except Exception as exc:
                logger.warning("Music artist discovery unavailable: source=%s, error=%s", futures[future].key, type(exc).__name__)
                continue
            for item in values:
                grouped.setdefault(_identity_key(item["channel"], item["title"]), {})[item["source"]] = item
    supported = [list(items.values()) for items in grouped.values() if len(items) >= 2]
    if len(supported) != 1:
        return []  # One source, conflicting artists or ambiguous titles need review.
    verified = []
    for item in supported[0][:2]:
        source = BY_KEY[item["source"]]
        try:
            control()
            with musicfa._client() as client:
                details = musicfa._probe_details(client, item["url"], deadline, allowed=source.allows,
                    check_cancelled=control, strict_320=False, verify_sample=verify, verify_middle=True)
            duration = details["duration"]
            expected = track.get("duration")
            if expected is not None and (not math.isfinite(float(expected)) or abs(duration-float(expected)) > max(5, float(expected)*.03)):
                return []
            if verified and abs(duration-verified[0]["duration"]) > max(5, duration*.03):
                return []
            verified.append({**item, **details, "identity_match": "audio_recording_key",
                             "verified_recording_key": track["recognition_key"]})
        except SearchStopped:
            return []
        except (musicfa.MusicFaError, httpx.HTTPError, ValueError, TypeError) as exc:
            _cooldown(source, exc)
            return []
    logger.warning("Music artist verified: independent_sources=%s", len(verified))
    return verified


def apply_verified_artist(track, candidates):
    """Keep original provider metadata for audit; change only this recording."""
    if not can_verify_audio(track) or len(candidates) < 2 or any(not is_public_candidate(item)
            or item.get("identity_match") != "audio_recording_key"
            or item.get("verified_recording_key") != track.get("recognition_key") for item in candidates):
        return False
    if len({item["source"] for item in candidates}) < 2:
        return False
    identities = {_identity_key(item["channel"], item["title"]) for item in candidates}
    if len(identities) != 1 or musicfa._words(candidates[0]["title"]) != musicfa._words(track["title"]):
        return False
    track["recognized_artist"] = track["artist"]
    track["artist"] = candidates[0]["channel"]
    track["artist_correction"] = {"method": "audio_and_catalogue_agreement",
                                  "sources": sorted({item["source"] for item in candidates})}
    track["album"] = ""
    return True
