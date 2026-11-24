"""Reviewed public music sites. No source is restricted by song/UI language."""
from dataclasses import dataclass
import re
from urllib.parse import quote, unquote, urlsplit


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    host: str
    media_hosts: tuple[str, ...] = ()
    post_pattern: str = r"/[^/]+/"
    brands: tuple[str, ...] = ()
    status: str = "public"
    search_path: str = "/?s={query}"

    @property
    def active(self):
        return self.status == "public" and bool(self.media_hosts)

    def search_url(self, track):
        query = " ".join((str(track.get("artist", "")), str(track.get("title", "")))).strip()
        if not track.get("artist") or not track.get("title") or len(query) > 400:
            raise ValueError("invalid_music_identity")
        return "https://" + self.host + self.search_path.format(query=quote(query, safe=""))

    def allows(self, url, media=False):
        try:
            if not isinstance(url, str):
                return False
            parsed = urlsplit(url)
            decoded = unquote(url)
            if (not self.active or len(url) > 2048 or parsed.scheme != "https"
                    or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or parsed.fragment or any(ord(c) < 32 or c == "\\" for c in decoded)
                    or ".." in unquote(parsed.path).split("/")):
                return False
            if media:
                return parsed.hostname in self.media_hosts and parsed.path.lower().endswith(".mp3") and not parsed.query
            hosts = {self.host, self.host.removeprefix("www."), "www."+self.host.removeprefix("www.")}
            if parsed.hostname not in hosts:
                return False
            if parsed.path in ("", "/"):
                return parsed.query.startswith("s=")  # A no-results redirect to home is not a search.
            if parsed.path.startswith("/search/"):
                return True
            return not parsed.query and self.is_post(url)
        except ValueError:
            return False

    def is_post(self, url):
        try:
            parsed = urlsplit(url)
            slug = unquote(parsed.path).strip("/").casefold()
            if parsed.query or parsed.fragment or slug in {"ads", "adsv", "contact", "contact-us", "about", "about-us",
                    "musics", "single-songs", "songs", "register", "login", "privacy-policy", "terms"}:
                return False
            if any(part in {"wp-admin", "wp-json", "wp-content", "category", "tag", "genre", "artists", "singer", "page"}
                   for part in slug.split("/")):
                return False
            return bool(re.fullmatch(self.post_pattern, parsed.path))
        except ValueError:
            return False


SOURCES = (
    Source("musicfa", "Music-fa", "musics-fa.com", ("dls.musics-fa.com",), r"/download-song/[0-9]+/",
           ("music-fa.com", "musics-fa.com"), search_path="/search/{query}"),
    Source("rozmusic", "RozMusic", "rozmusic.com", ("dl.rozmusic.com",), r"/[^/]+\.html", ("rozmusic.com",)),
    Source("upmusics", "UpMusics", "upmusics.com", ("irsv.upmusics.com",), brands=("upmusics", "upmusics.com")),
    Source("musicbaran", "MusicBaran", "musicbaran1.ir", ("dl.dlmusicbaran.ir",), r"/music/[^/]+/", ("musicbaran",)),
    Source("popmusic", "Pop Music", "pop-music.ir", ("dl.pop-music.ir",), brands=("pop-music.ir",)),
    Source("musicdel", "MusicDel", "musicdel.ir", ("dl.musicdel.ir",), r"/single-tracks/[0-9]+/", ("musicdel.ir",)),
    Source("sevilmusics", "SevilMusics", "sevilmusics.com", ("dl.sevilmusics.com",), r"/article/[0-9]+/", ("sevilmusic", "sevilmusics")),
    Source("birmusic", "Bir Music", "bir-music.com", ("dl.bir-music.com",), r"/music/[^/]+/", ("bir-music.com",)),
    Source("nicmusic", "NicMusic", "nicmusic.net", ("dl.nicmusic.net",), brands=("nicmusic.net",)),
    Source("worldofmusic", "World of Music", "worldofmusic.ir", status="subscription_required"),
    Source("musicdagh", "MusicDagh", "www.musicdagh.ir", status="download_route_unverified"),
    Source("dibasmusic", "DibasMusic", "dibasmusic.com", ("dl.dibasmusic.com",), brands=("dibasmusic.com",)),
    Source("radiojavan", "Radio Javan", "play.radiojavan.com", status="download_route_unverified"),
    Source("remixgram", "Remixgram", "remixgram.com", status="subscription_required"),
    Source("pinkmusics", "PinkMusics", "pinkmusics.com", ("sv.pinkmusics.com",), r"/song-[^/]+/", ("pinkmusics.com",)),
    Source("vipremix", "VIPRemix", "vipremix.ir", status="subscription_required"),
    Source("radiojavanhd", "RadioJavanHD", "radiojavanhd.com", ("dl.face1music.net",), r"/[0-9]+/[^/]+/", ("radiojavanhd.com",)),
    Source("musictag", "MusicTag", "musictag.ir", ("dl.musictag.ir",), r"/details/[0-9]+/", ("musictag.ir",)),
    Source("newsong", "NewSong", "newsong.ir", ("dl.newsong.ir",), brands=("www.newsong.ir", "newsong.ir")),
    Source("soonami", "Soonami", "soonami.ir", ("kk.soonami.ir",), brands=("soonami.ir",)),
    Source("musictops", "MusicTops", "musictops.ir", ("dl.musictops.ir",), r"/[^/]+\.html", ("musictops.ir",)),
    Source("musicviral", "MusicViral", "musicviral.ir", ("dl.musicviral.ir",), brands=("musicviral.ir",)),
    Source("melodya", "Melodya", "melodya.ir", ("dl.melodya.ir",), r"/cnt/[0-9]+/", ("melodya",)),
    Source("musicsmehr", "Musics Mehr", "musics-mehr.com", ("dl.mehrdl.top",), brands=("musics-mehr.com", "mehrdl.top")),
    Source("moonmusic", "Moon Music", "moonmusic.in", status="discord_bot_not_download_catalog"),
)
BY_KEY = {source.key: source for source in SOURCES}


def source_for_media(url):
    return next((source for source in SOURCES if source.allows(url, media=True)), None)


def is_public_candidate(candidate):
    source = BY_KEY.get(candidate.get("source"))
    return bool(source and source.allows(candidate.get("url", ""), media=True))
