"""Explicit matching policy between Rekordbox strings and provider candidates.

Mirrors the song_catalog vocabulary: a lookup is matched, ambiguous or not_found.
Ambiguous results are never silently reduced to one candidate.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from .provider import Candidate

_OPEN = "[\\[\\(\uff08\u3010\u3014{]"
_CLOSE_CHARS = "\\]\\)\uff09\u3011\u3015}"
_BRACKETS = re.compile(_OPEN + "[^" + _CLOSE_CHARS + "]*[" + _CLOSE_CHARS + "]")
_FEAT_TAIL = re.compile(r"\s+(?:feat\.?|featuring)\s+.*$", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def normalize_key(value: str) -> str:
    """Store key form: NFC + trim, same rule as the song catalog keys."""
    return unicodedata.normalize("NFC", value).strip()


def _basic(value: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _squash(value: str) -> str:
    """Notation-insensitive form: punctuation, symbols and whitespace removed.

    'Q & A' and 'Q&A', or a curly vs straight apostrophe, compare equal here.
    Letters, digits and CJK characters are kept; this is still exact equality
    after normalization, not similarity scoring.
    """
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return "".join(char for char in text if unicodedata.category(char)[0] in ("L", "N"))


def _stripped(value: str) -> str:
    text = _FEAT_TAIL.sub("", _BRACKETS.sub(" ", _basic(value)))
    return _WHITESPACE.sub(" ", text).strip(" -\u2013\u2014")


_BRACKET_CONTENT = re.compile(_OPEN + "([^" + _CLOSE_CHARS + "]*)[" + _CLOSE_CHARS + "]")
_VERSION_WORDS = (
    "instrumental",
    "off vocal",
    "karaoke",
    "\u30ab\u30e9\u30aa\u30b1",
    "\u30a4\u30f3\u30b9\u30c8",
    "live",
    "remix",
)


def _has_version_marker(value: str) -> bool:
    basic = _basic(value)
    removed = _BRACKET_CONTENT.findall(basic) + _FEAT_TAIL.findall(basic)
    return any(word in part for part in removed for word in _VERSION_WORDS)


@dataclass(frozen=True)
class _Forms:
    """The query side's normalized forms, computed once per lookup
    instead of once per candidate (audit #10). Every field is the same
    pure function of the raw string the per-candidate code used."""

    squash: str
    stripped: str
    stripped_squash: str
    has_version_marker: bool


def _forms(value: str) -> _Forms:
    stripped = _stripped(value)
    return _Forms(
        squash=_squash(value),
        stripped=stripped,
        stripped_squash=_squash(stripped),
        has_version_marker=_has_version_marker(value),
    )


def _title_matches(query: _Forms, candidate: str) -> bool:
    if query.squash == _squash(candidate):
        return True
    if query.has_version_marker or _has_version_marker(candidate):
        return False
    return bool(query.stripped) and query.stripped_squash == _squash(_stripped(candidate))


def _artist_matches(query: _Forms, candidate: str) -> bool:
    if query.squash == _squash(candidate):
        return True
    return bool(query.stripped) and query.stripped_squash == _squash(_stripped(candidate))


def query_terms(title: str, artist: str) -> tuple[str, str] | None:
    """Search-term form of a lookup, or None when it adds nothing.

    Bracketed decorations and feat. tails poison provider term search (a
    long voice-cast parenthetical can return zero results), so a not_found
    lookup is retried once with them removed. Matching itself always uses
    the raw strings; this only shapes the search query.
    """
    stripped = (_stripped(title), _stripped(artist))
    if stripped == (_basic(title), _basic(artist)):
        return None
    return (stripped[0] or _basic(title), stripped[1] or _basic(artist))


@dataclass(frozen=True)
class MetadataLookupResult:
    status: Literal["matched", "ambiguous", "not_found"]
    match: Candidate | None = None
    candidates: tuple[Candidate, ...] = ()
    fetched_at: str | None = None

    def to_dict(self) -> dict:
        value = {"status": self.status, "match_count": len(self.candidates), "fetched_at": self.fetched_at}
        if self.match is not None:
            chosen = self.match.to_dict()
            for name in ("title", "artist", "album", "release_date", "artwork_url", "provider", "provider_track_id"):
                value[name] = chosen[name]
        if self.status == "ambiguous":
            value["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return value


def match_candidates(title: str, artist: str, candidates: tuple[Candidate, ...]) -> MetadataLookupResult:
    title_forms = _forms(title)
    artist_forms = _forms(artist)
    strict = {}
    loose = {}
    for candidate in candidates:
        if title_forms.squash == _squash(candidate.title) and artist_forms.squash == _squash(candidate.artist):
            strict[candidate.provider_track_id] = candidate
        elif _title_matches(title_forms, candidate.title) and _artist_matches(artist_forms, candidate.artist):
            loose[candidate.provider_track_id] = candidate
    selected = tuple((strict or loose).values())
    if len(selected) == 1:
        return MetadataLookupResult("matched", match=selected[0], candidates=selected)
    if len(selected) > 1:
        return MetadataLookupResult("ambiguous", candidates=selected)
    return MetadataLookupResult("not_found")
