"""Search reviewed public catalogues in a bounded pool, independent of language."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import json
import logging
import math
import re
import threading
import time
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.services import musicfa, music_source_cache as cache
from app.services.music_source_catalog import SOURCES, BY_KEY, source_for_media
from app.services.music_identity import discovery_track, can_verify_audio

logger = logging.getLogger(__name__)
SEARCH_SECONDS = 30
SEARCH_WORKERS = 4
RESOLVE_WORKERS = 2
MAX_PAGE_CHECKS = 18


class SearchStopped(Exception):
    pass


class SearchPages(list):
    """Distinguish an empty search from a source that must not be retried."""
    def __init__(self, values=(), *, available=True):
        super().__init__(values)
        self.available = available


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: object = field(default=None, repr=False)

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.nodes()

    def text(self):
        if self.tag in {"script", "style", "nav", "footer"}:
            return ""
        return " ".join(child.text() if isinstance(child, Node) else child for child in self.children)

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        # Ignore a structurally pathological page rather than recursing forever.
        if len(self.stack) > 80:
            raise ValueError("music_source_html_depth")
        node = Node(tag, dict(attrs), parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack[-1].tag not in {"script", "style"}:
            self.stack[-1].children.append(data)


def _excluded(node):
    return any(n.tag in {"nav", "footer", "aside"} or any(token in (n.attrs.get("id", "") + " " + n.attrs.get("class", "")).lower()
               for token in ("comment", "related", "sidebar", "advert", "suggest")) for n in node.ancestors())


def search_pages(source, track, body, *, title_only=False):
    """Rank observed search-result links, ignoring menus and related sections."""
    root = Page(body).root
    wanted = (musicfa._artist(track["title"]) if title_only else
              musicfa._artist(track["artist"] + " " + discovery_track(track)["title"]))
    ranked = {}
    for node in root.nodes():
        if node.tag != "a" or _excluded(node):
            continue
        url = urljoin("https://" + source.host + "/", node.attrs.get("href", ""))
        if not source.allows(url) or not source.is_post(url):
            continue
        parents = list(node.ancestors())
        article = next((n for n in parents if n.tag == "article"), None)
        text = node.text() + " " + node.attrs.get("title", "")
        label_words = musicfa._words(text)
        if (label_words & musicfa.VARIANTS) - musicfa._words(track["title"]):
            continue
        if article:
            text += " " + article.text()[:4000]
        for child in (article or node).nodes():
            if child.tag == "img":
                text += " " + child.attrs.get("alt", "") + " " + unquote(urlsplit(child.attrs.get("src", "")).path).rsplit("/", 1)[-1]
        if not wanted or not wanted <= musicfa._artist(text):
            continue
        score = (int(wanted <= musicfa._artist(node.text() + " " + node.attrs.get("title", ""))),
                 int("bookmark" in node.attrs.get("rel", "") or any(n.tag in {"h1", "h2", "h3", "h4", "h5"} for n in parents)))
        ranked[url] = max(score, ranked.get(url, (-1, -1)))
    return sorted(ranked, key=ranked.get, reverse=True)[:2 if title_only else 1]


def _file_name(source, url):
    name = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    if not name.lower().endswith(".mp3"):
        return ""
    name = name[:-4]
    # Strip only known site branding and quality annotations, not arbitrary words.
    for brand in sorted(source.brands, key=len, reverse=True):
        name = re.sub(r"(?i)(?<![a-z0-9])" + re.escape(brand) + r"(?![a-z0-9])", " ", name)
    name = re.sub(r"[\[(]\s*[\])]", " ", name).replace("_", " ").strip()
    name = re.sub(r"(?i)(?:[\[(]\s*(?:128|192|256|320)(?:\s*kbps)?\s*[\])]|(?:[-_ ]+)(?:128|192|256|320)(?:\s*kbps)?$)", " ", name)
    return name.strip()


def _file_identity(source, url, track):
    actual = musicfa._artist(_file_name(source, url))
    wanted = musicfa._artist(track["artist"] + " " + track["title"])
    return bool(wanted and actual == wanted)


def download_links(source, track, body, page_url):
    root = Page(body).root
    links = {}
    for node in root.nodes():
        if node.tag != "a" or _excluded(node):
            continue
        url = urljoin(page_url, node.attrs.get("href", ""))
        label = node.text() + " " + node.attrs.get("title", "")
        if not source.allows(url, media=True) or not _file_identity(source, url, track):
            continue
        if not re.search(r"دانلود|download|320|۳۲۰", label, re.I):
            continue  # Player/preview links are not download offers.
        if re.search(r"preview|demo|snippet|teaser|پیش.?نمایش|دمو", label, re.I):
            continue
        # Never take a preview or a premium link from a locked download box.
        context = " ".join(n.text()[:3000] for n in list(node.ancestors())[:2])
        if re.search(r"نسخه\s*دمو|نیاز.*اشتراک|با خرید اشتراک|برای.*(?:دانلود|دریافت).*اشتراک|premium.only|login.to.download", context, re.I):
            continue
        normalized_label = label.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        advertised = max([int(q) for q in re.findall(r"\b(?:128|192|256|320)\b", normalized_label)] or [0])
        links[url] = max(advertised, links.get(url, -1))
    best = [url for url, quality in links.items() if quality == max(links.values())] if links else []
    return best if len(best) == 1 else []


def _cooldown(source, error):
    message = str(error)
    if "rate_limited" in message:
        cache.write("cooldown:" + source.key, True, getattr(error, "retry_after", 300))
    elif "source_denied" in message:
        cache.write("cooldown:" + source.key, True, 300)
    logger.warning("Music catalogue unavailable: source=%s, error=%s", source.key,
                   message if isinstance(error, musicfa.MusicFaError) else type(error).__name__)


def _search(source, track, deadline, control, *, title_only=False):
    try:
        with musicfa._client() as client:
            body = musicfa._html(client, source.search_url(track, title_only=title_only), deadline, allowed=source.allows, check_cancelled=control)
        return SearchPages(search_pages(source, track, body, title_only=title_only))
    except SearchStopped:
        return SearchPages(available=False)
    except (musicfa.MusicFaError, httpx.HTTPError, ValueError, TypeError) as error:
        _cooldown(source, error)
        return SearchPages(available=False)


def _resolve(source, track, page_url, deadline, control, verify_sample=None):
    try:
        with musicfa._client() as client:
            body = musicfa._html(client, page_url, deadline, allowed=source.allows, check_cancelled=control)
            urls = download_links(source, track, body, page_url)
            needs_audio_match = False
            broader = discovery_track(track)
            if not urls and verify_sample is not None and broader["title"] != track["title"]:
                urls = download_links(source, broader, body, page_url)
                needs_audio_match = bool(urls)
            if not urls:
                return None
            details = musicfa._probe_details(client, urls[0], deadline, allowed=source.allows,
                check_cancelled=control, strict_320=False,
                verify_sample=verify_sample if needs_audio_match else None)
            duration, size = details["duration"], details["size_bytes"]
            expected = track.get("duration")
            if expected is not None and (not math.isfinite(float(expected)) or
                    abs(duration-float(expected)) > max(5, float(expected)*.03)):
                return None
            return {"source": source.key, "source_name": source.label, "source_type": "public_mp3",
                    "title": str(track["title"])[:200], "channel": str(track["artist"])[:150],
                    "url": urls[0], "page_url": page_url, "duration": duration, "size_bytes": size,
                    "source_bitrate": details["source_bitrate"],
                    "identity_match": "audio" if needs_audio_match else "metadata"}
    except SearchStopped:
        return SearchPages(available=False)
    except (musicfa.MusicFaError, httpx.HTTPError, ValueError, TypeError) as error:
        _cooldown(source, error)
        return SearchPages(available=False)


def _musicfa(track, deadline, control):
    try:
        found = musicfa.find_tracks(track, deadline=deadline, check_cancelled=control)
        return [{**item, "source_name": "Music-fa", "source_type": "public_mp3"} for item in found]
    except SearchStopped:
        return SearchPages(available=False)
    except (musicfa.MusicFaError, httpx.HTTPError) as error:
        _cooldown(BY_KEY["musicfa"], error)
        return SearchPages(available=False)


def _cache_key(track):
    identity = ["artist-proof-v1", sorted(musicfa._artist(track.get("artist", ""))), sorted(musicfa._words(track.get("title", ""))),
                track.get("duration"), track.get("recognition_key"), track.get("recognition_provider")]
    return "track:" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def _valid_cached(items):
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        source = BY_KEY.get(item.get("source"))
        if not source or not source.allows(item.get("url", ""), True) or not source.allows(item.get("page_url", "")):
            return False
        if not isinstance(item.get("duration"), (int, float)) or not 60 <= item["duration"] <= 3600:
            return False
    return True


def find_tracks(track, *, sources=None):
    """Submit all active sources, bound the total wait and stop after two matches."""
    active = [s for s in (SOURCES if sources is None else sources) if s.active]
    key = _cache_key(track)
    stored = cache.read([key] + ["cooldown:"+s.key for s in active])
    cached = stored.get(key)
    if _valid_cached(cached) and not any(stored.get("cooldown:"+item["source"]) for item in cached):
        return [{**item, "title": str(track["title"])[:200],
                 "channel": item["channel"] if item.get("identity_match") == "audio_recording_key" else str(track["artist"])[:150]}
                for item in cached]
    deadline = time.monotonic() + SEARCH_SECONDS
    stopped = threading.Event()
    def control():
        if stopped.is_set() or time.monotonic() >= deadline:
            raise SearchStopped()
    verification_lock = threading.Lock()
    verification_count, verification_failed = 0, False

    def verify(path, *, require_key=False):
        nonlocal verification_count, verification_failed
        # At most two short source fingerprints, serialized. A service error
        # disables further attempts; AudD never triggers these free calls.
        with verification_lock:
            control()
            if verification_failed or verification_count >= 2 or not can_verify_audio(track):
                return False
            verification_count += 1
            from app.services.music_source_match import matches_sample
            try:
                return matches_sample(track, path, deadline, control, **({"require_key": True} if require_key else {}))
            except SearchStopped:
                raise
            except Exception as error:
                verification_failed = True
                logger.warning("Music source verification unavailable: error=%s", type(error).__name__)
                return False
    results, checked, seen, unavailable = [], 0, set(), set()
    executor = ThreadPoolExecutor(max_workers=SEARCH_WORKERS, thread_name_prefix="music-source")
    resolver = ThreadPoolExecutor(max_workers=RESOLVE_WORKERS, thread_name_prefix="music-match")
    futures = {}
    try:
        # Submit every search, but reserve two of the six workers for checking
        # results. Slow unrelated sites cannot keep a fast match at the back
        # of the search queue until its verification budget has expired.
        for source in active:
            if stored.get("cooldown:"+source.key):
                continue
            if source.key == "musicfa":
                future = executor.submit(_musicfa, track, deadline, control)
                futures[future] = ("musicfa", source)
            else:
                future = executor.submit(_search, source, track, deadline, control)
                futures[future] = ("search", source)
        while futures and time.monotonic() < deadline and len(results) < 2:
            done, _ = wait(futures, timeout=max(0, deadline-time.monotonic()), return_when=FIRST_COMPLETED)
            for future in done:
                kind, source = futures.pop(future)
                if future.exception() is not None:
                    unavailable.add(source.key)
                    logger.warning("Music catalogue task failed: source=%s, error=%s", source.key, type(future.exception()).__name__)
                    continue
                value = future.result()
                if getattr(value, "available", True) is False:
                    unavailable.add(source.key)
                if kind == "search":
                    for url in value:
                        if checked >= MAX_PAGE_CHECKS:
                            break
                        checked += 1
                        futures[resolver.submit(_resolve, source, track, url, deadline, control, verify)] = ("resolve", source)
                else:
                    for item in (value if kind == "musicfa" else [value]):
                        if item and item["url"] not in seen and source.key not in {r["source"] for r in results}:
                            seen.add(item["url"])
                            results.append(item)
    finally:
        stopped.set()
        executor.shutdown(wait=True, cancel_futures=True)
        resolver.shutdown(wait=True, cancel_futures=True)
        # Searches that ended during shutdown must not be retried as a fallback.
        for future, (_, source) in futures.items():
            if future.cancelled() or future.exception() is not None or getattr(future.result(), "available", True) is False:
                unavailable.add(source.key)
    if not results and not verification_failed and verification_count == 0 and can_verify_audio(track):
        # A wrong provider artist can otherwise make every catalogue search
        # empty. Keep the original audio identity and require independent proof.
        from app.services.music_artist_correction import find_corrected_tracks
        deadline = time.monotonic() + 25
        stopped.clear()
        try:
            results = find_corrected_tracks(track, active, unavailable, stored, deadline, control,
                lambda path: verify(path, require_key=True))
        finally:
            stopped.set()
    results = results[:2]
    cache.write(key, results, 300 if results else 30)
    logger.warning("Music catalogue search complete: sources=%s, matches=%s", len(active), len(results))
    return results


def download_track(url, target, **kwargs):
    source = source_for_media(url)
    if not source:
        raise musicfa.MusicFaError("musicfa_invalid_source")
    try:
        return musicfa.download_track(url, target, allowed=source.allows, strict_320=False, **kwargs)
    except musicfa.MusicFaError as error:
        _cooldown(source, error)
        raise


if __name__ == "__main__":
    # Read-only inventory; does not search, recognize, download or send anything.
    for source in SOURCES:
        print(json.dumps({"source": source.key, "site": source.host, "enabled": source.active,
                          "status": source.status, "language_filter": None}))
