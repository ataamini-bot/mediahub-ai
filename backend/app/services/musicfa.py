"""Bounded, public Music-fa lookup and MP3 delivery; no credentials or crawling."""
from contextlib import contextmanager
from html.parser import HTMLParser
import json
import logging
import math
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unicodedata
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

logger = logging.getLogger(__name__)
SITE = "https://musics-fa.com"
MEDIA_HOST = "dls.musics-fa.com"
HTML_LIMIT = 1024 * 1024
PROBE_LIMIT = 512 * 1024
SOURCE_LIMIT = 150 * 1024 * 1024
VARIANTS = {"remix", "ریمیکس", "cover", "کاور", "live", "زنده", "karaoke", "instrumental",
            "slowed", "sped", "nightcore", "preview", "teaser", "snippet", "demo", "دمو", "تیزر",
            "گلچین", "مجموعه", "medley", "mashup", "loop"}


class MusicFaError(RuntimeError):
    """Stable errors only: never include response bodies or remote file URLs."""
    def __init__(self, code, retry_after=300):
        super().__init__(code)
        self.retry_after = retry_after


def _words(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold().translate(str.maketrans("يك", "یک"))
    return set(re.findall(r"[^\W_]+", value))


def _artist(value):
    return _words(value) - {"and", "feat", "ft", "featuring", "و"}


def _identity_matches(track, artist, title):
    wanted, found = _words(track.get("title", "")), _words(title)
    return bool(wanted and _artist(track.get("artist", "")) and wanted == found
                and _artist(track["artist"]) == _artist(artist))


def is_musicfa_source(url):
    try:
        return urlsplit(url).hostname == MEDIA_HOST
    except ValueError:
        return False


def _allowed(url, media=False):
    try:
        parsed = urlsplit(url)
        decoded = unquote(url)
        if (len(url) > 2048 or parsed.scheme != "https" or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment
                or any(ord(c) < 32 or c == "\\" for c in decoded)
                or ".." in unquote(parsed.path).split("/")):
            return False
        if media:
            return parsed.hostname == MEDIA_HOST and parsed.path.lower().endswith(".mp3") and not parsed.query
        return parsed.hostname in {"musics-fa.com", "www.musics-fa.com"} and (
            parsed.path == "/" or bool(re.fullmatch(r"/download-song/[0-9]+/", parsed.path))
            or bool(re.fullmatch(r"/search/[^/]+/?", parsed.path)))
    except ValueError:
        return False


@contextmanager
def _response(client, url, deadline, *, media=False, headers=None, allowed=None, check_cancelled=None):
    # Validate every redirect before following it, with no automatic retries.
    for redirect in range(3):
        if check_cancelled:
            check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MusicFaError("musicfa_timeout")
        if not (allowed or _allowed)(url, media):
            raise MusicFaError("musicfa_invalid_source")
        client.cookies.clear()
        with client.stream("GET", url, headers=headers, timeout=min(5, remaining)) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code == 429:
                from email.utils import parsedate_to_datetime
                from datetime import datetime, timezone
                value = response.headers.get("retry-after", "300")
                try:
                    seconds = int(value) if value.isdigit() else int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    seconds = 300
                raise MusicFaError("musicfa_rate_limited", retry_after=max(60, min(3600, seconds)))
            if response.status_code in (401, 403):
                raise MusicFaError("musicfa_source_denied")
            if response.status_code not in (200, 206):
                raise MusicFaError("musicfa_unavailable")
            yield response
            return
    raise MusicFaError("musicfa_invalid_redirect")


def _client():
    return httpx.Client(trust_env=False, follow_redirects=False,
                        headers={"User-Agent": "MediaHub/1.0 (public music lookup)", "Accept-Encoding": "identity"})


def _html(client, url, deadline, *, allowed=None, check_cancelled=None):
    with _response(client, url, deadline, allowed=allowed, check_cancelled=check_cancelled) as response:
        if "text/html" not in response.headers.get("content-type", "").lower():
            raise MusicFaError("musicfa_invalid_page")
        data = bytearray()
        for chunk in response.iter_bytes(16384):
            if check_cancelled:
                check_cancelled()
            data.extend(chunk)
            if len(data) > HTML_LIMIT or time.monotonic() > deadline:
                raise MusicFaError("musicfa_page_limit")
        return data.decode("utf-8", errors="replace")


class _Articles(HTMLParser):
    """Only song articles, never sidebar, comment or advertisement links."""
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.articles, self.current = [], None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "article" and "mf_pst" in values.get("class", "").split():
            ident = values.get("data-id", "")
            if re.fullmatch(r"[0-9]+", ident):
                self.current = {"id": ident, "artist": values.get("data-artist", ""),
                                "title": values.get("data-song", ""), "text": [], "links": []}
        if self.current is not None and tag == "a" and "mf_mp3" in values.get("class", "").split():
            self.current["links"].append(values.get("href", ""))

    def handle_data(self, data):
        if self.current is not None and sum(map(len, self.current["text"])) < 8192:
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "article" and self.current is not None:
            self.articles.append(self.current)
            self.current = None


def _names(article):
    text = " ".join(article["text"])
    english = re.search(r"Download\s+(?:New\s+)?(?:Music|Song)\s+BY\s*:\s*(.*?)\s*\|\s*(.*?)\s+With\s+Text", text, re.I)
    names = [(article["artist"], article["title"])]
    if english:
        names.append(english.groups())
    return names


def _matches(track, article):
    names = _names(article)
    # Reject variants even when only the other-language title mentions them.
    labels = " ".join(artist + " " + title for artist, title in names)
    if (_words(labels) & VARIANTS) - _words(track.get("title", "")):
        return False
    return any(_identity_matches(track, artist, title) for artist, title in names)


def _file_matches(article, url):
    stem = re.sub(r"\s*\(320\)\.mp3$", "", unquote(urlsplit(url).path).rsplit("/", 1)[-1], flags=re.I)
    return any(artist and title and _artist(stem) == _artist(artist + " " + title)
               for artist, title in _names(article))


def _media_size(response, *, partial):
    try:
        if response.status_code == 206:
            if not partial:
                raise ValueError
            match = re.fullmatch(r"bytes 0-([0-9]+)/([0-9]+)", response.headers.get("content-range", ""))
            if not match or int(match[1]) >= int(match[2]):
                raise ValueError
            size = int(match[2])
        else:
            size = int(response.headers.get("content-length", "0"))
        if not 1 <= size <= SOURCE_LIMIT:
            raise ValueError
        return size
    except ValueError:
        raise MusicFaError("musicfa_invalid_size") from None


def _audio_info(path):
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,bit_rate:format=duration", "-of", "json", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
        info = json.loads(result.stdout)
        stream = info["streams"][0]
        if stream["codec_name"] != "mp3" or int(stream.get("bit_rate", 0)) != 320000:
            raise ValueError
        return info
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        raise MusicFaError("musicfa_invalid_mp3_320") from None


def _probe(client, url, deadline, *, allowed=None, check_cancelled=None):
    with _response(client, url, deadline, media=True, headers={"Range": f"bytes=0-{PROBE_LIMIT-1}"},
                   allowed=allowed, check_cancelled=check_cancelled) as response:
        size = _media_size(response, partial=True)
        data = bytearray()
        for chunk in response.iter_bytes(16384):
            if check_cancelled:
                check_cancelled()
            data.extend(chunk[:PROBE_LIMIT-len(data)])
            if time.monotonic() > deadline:
                raise MusicFaError("musicfa_timeout")
            if len(data) >= PROBE_LIMIT:
                break
    with tempfile.NamedTemporaryFile(suffix=".mp3") as sample:
        sample.write(data)
        sample.flush()
        _audio_info(Path(sample.name))
    if time.monotonic() > deadline:
        raise MusicFaError("musicfa_timeout")
    # Native 320 kbps CBR; subtract the ID3 tag rather than treating cover art as audio.
    tag_size = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        tag_size = 10 + sum((data[6+i] & 127) << (7*(3-i)) for i in range(4))
    duration = (size - tag_size) / 40000
    if not 60 <= duration <= 3600:
        raise MusicFaError("musicfa_not_full_track")
    return int(duration), size


def find_tracks(track, *, deadline=None, check_cancelled=None):
    """One user-triggered query, at most two matching pages, one verified source."""
    deadline = min(deadline or float("inf"), time.monotonic() + 20)
    query = " ".join((str(track.get("artist", "")), str(track.get("title", "")))).strip()
    if not track.get("artist") or not track.get("title") or len(query) > 400:
        return []
    try:
        with _client() as client:
            # Use the site's observed canonical search route, avoiding an extra
            # redirect from WordPress's ?s= form on every recognition.
            articles = _Articles(_html(client, SITE + "/search/" + quote(query, safe=""), deadline,
                                       check_cancelled=check_cancelled)).articles
            matches = [a for a in articles[:12] if _matches(track, a)][:2]
            for item in matches:
                page = SITE + "/download-song/" + item["id"] + "/"
                details = _Articles(_html(client, page, deadline, check_cancelled=check_cancelled)).articles
                for article in details:
                    if article["id"] != item["id"] or not _matches(track, article):
                        continue
                    urls = [u for u in article["links"] if _allowed(u, True)
                            and re.search(r"\(320\)\.mp3$", unquote(urlsplit(u).path), re.I)]
                    # Never silently choose one track from a compilation/multi-version post.
                    if len(set(urls)) != 1:
                        continue
                    url = urls[0]
                    if not _file_matches(article, url):
                        continue
                    duration, size = _probe(client, url, deadline, check_cancelled=check_cancelled)
                    expected = track.get("duration")
                    if expected is not None and (not math.isfinite(float(expected))
                            or abs(duration - float(expected)) > max(5, float(expected) * .03)):
                        continue
                    logger.warning("Music-fa source ready: bitrate=320, duration_s=%s, bytes=%s", duration, size)
                    return [{"source": "musicfa", "title": str(track["title"])[:200],
                             "channel": str(track["artist"])[:150], "duration": duration,
                             "size_bytes": size, "url": url, "page_url": page}]
    except (MusicFaError, httpx.HTTPError, ValueError, TypeError) as exc:
        if check_cancelled and isinstance(exc, MusicFaError) and str(exc) in {"musicfa_rate_limited", "musicfa_source_denied"}:
            raise
        logger.warning("Music-fa lookup unavailable: error=%s", str(exc) if isinstance(exc, MusicFaError) else type(exc).__name__)
    return []


def download_track(url, target, *, max_bytes, check_cancelled, progress, allowed=None):
    """Use the normal job's allowance/control, validating the complete MP3 before publish."""
    part = target.with_suffix(".mp3.part")
    deadline = time.monotonic() + 180
    started, received = time.monotonic(), 0
    try:
        check_cancelled()
        with _client() as client, _response(client, url, deadline, media=True, allowed=allowed,
                                           check_cancelled=check_cancelled) as response:
            total = _media_size(response, partial=False)
            if total > max_bytes:
                raise MusicFaError("Downloaded file exceeds the plan file-size limit")
            with part.open("wb") as output:
                for chunk in response.iter_bytes(65536):
                    check_cancelled()
                    received += len(chunk)
                    if received > min(max_bytes, total):
                        raise MusicFaError("musicfa_invalid_size")
                    if time.monotonic() > deadline:
                        raise MusicFaError("musicfa_timeout")
                    output.write(chunk)
                    progress({"status": "downloading", "downloaded_bytes": received,
                              "total_bytes": total, "filename": str(target),
                              "speed": received / max(.01, time.monotonic()-started)})
        if received != total:
            raise MusicFaError("musicfa_incomplete_download")
        info = _audio_info(part)
        duration = float(info.get("format", {}).get("duration", 0))
        if not 60 <= duration <= 3600:
            raise MusicFaError("musicfa_not_full_track")
        check_cancelled()
        part.replace(target)
        return target
    except httpx.HTTPError:
        raise MusicFaError("musicfa_connection_failed") from None
    finally:
        part.unlink(missing_ok=True)
