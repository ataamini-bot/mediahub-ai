"""Validate individual Pinterest pins and resolve only reviewed short-link hops."""
import re
from urllib.parse import urljoin, urlsplit

import requests

# Mirrors the country domains supported by the installed yt-dlp Pinterest
# extractor. Matching suffixes such as pinterest.example.com is not allowed.
COUNTRIES = {"com", "fr", "de", "ch", "jp", "cl", "ca", "it", "co.uk", "nz", "ru", "com.au",
    "at", "pt", "co.kr", "es", "com.mx", "dk", "ph", "th", "com.uy", "co", "nl", "info", "kr",
    "ie", "vn", "com.vn", "ec", "mx", "in", "pe", "co.at", "hu", "co.in", "co.nz", "id",
    "com.ec", "com.py", "tw", "be", "uk", "com.bo", "com.pe"}


def is_pinterest_host(host):
    return host == "pin.it" or any(host == "pinterest."+country or host.endswith(".pinterest."+country)
                                    for country in COUNTRIES)


def pin_id(url):
    try:
        parts = urlsplit(url)
        if not is_pinterest_host(parts.hostname or "") or parts.hostname == "pin.it":
            return None
        match = re.fullmatch(r"/pin/(?:[\w-]+--)?([0-9]{5,30})(?:/sent)?/?", parts.path)
        return match[1] if match else None
    except ValueError:
        return None


def valid_link(url):
    try:
        parts = urlsplit(url)
        return bool(parts.scheme in {"http", "https"} and is_pinterest_host(parts.hostname or "")
            and not parts.username and not parts.password and parts.port in (None, 80, 443)
            and (pin_id(url) or (parts.hostname == "pin.it" and re.fullmatch(r"/[A-Za-z0-9_-]{3,64}/?", parts.path))))
    except ValueError:
        return False


def resolve_pin(url):
    if not valid_link(url):
        raise ValueError("pinterest_invalid_link")
    if pin_id(url):
        return f"https://www.pinterest.com/pin/{pin_id(url)}/"
    current = url
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(5):
            with session.get(current, allow_redirects=False, stream=True, timeout=(5, 10),
                             headers={"User-Agent": "Mozilla/5.0"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = urljoin(current, response.headers.get("Location", ""))
                    parts = urlsplit(target)
                    if (parts.scheme not in {"http", "https"} or not is_pinterest_host(parts.hostname or "")
                            or parts.username or parts.password or parts.port not in (None, 80, 443)):
                        raise ValueError("pinterest_invalid_redirect")
                    if pin_id(target):
                        return f"https://www.pinterest.com/pin/{pin_id(target)}/"
                    current = target
                    continue
                response.raise_for_status()
                raise ValueError("pinterest_pin_not_found")
    raise ValueError("pinterest_redirect_limit")
