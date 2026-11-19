"""External catalog search providers. Standard library only; no API key for iTunes Search."""

import json
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import urlopen


@dataclass(frozen=True)
class Candidate:
    provider: str
    provider_track_id: str
    title: str
    artist: str
    album: str | None = None
    release_date: str | None = None
    artwork_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "provider_track_id": self.provider_track_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "release_date": self.release_date,
            "artwork_url": self.artwork_url,
        }


def _artwork_url(value):
    if not value:
        return None
    # iTunes returns a 100x100 URL; the same path serves larger sizes.
    return value.replace("100x100bb.jpg", "600x600bb.jpg")


def parse_itunes_results(payload: dict) -> tuple[Candidate, ...]:
    candidates = []
    for item in payload.get("results", []):
        if item.get("wrapperType") != "track" or not item.get("trackName") or not item.get("artistName"):
            continue
        release = item.get("releaseDate") or ""
        candidates.append(
            Candidate(
                provider="itunes",
                provider_track_id=str(item.get("trackId", "")),
                title=item["trackName"],
                artist=item["artistName"],
                album=item.get("collectionName"),
                release_date=release[:10] or None,
                artwork_url=_artwork_url(item.get("artworkUrl100")),
            )
        )
    return tuple(candidates)


class ItunesSearchProvider:
    """Search the iTunes catalog. The opener is injectable for tests and replay."""

    def __init__(self, country: str = "JP", *, timeout_seconds: float = 10.0, opener=None):
        self._country = country
        self._timeout_seconds = timeout_seconds
        self._opener = opener or urlopen

    def search(self, title: str, artist: str) -> tuple[Candidate, ...]:
        query = urlencode(
            {"term": f"{title} {artist}", "country": self._country, "media": "music", "entity": "song", "limit": 25}
        )
        with self._opener(f"https://itunes.apple.com/search?{query}", timeout=self._timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return parse_itunes_results(payload)
