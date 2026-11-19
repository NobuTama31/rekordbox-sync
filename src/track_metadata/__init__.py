"""Persistent external track metadata: provider search, explicit matching, SQLite store.

Independent of Rekordbox, Windows and HTTP, like song_catalog. Fetched results are
kept on disk so a track is searched once, not on every state read.
"""

from .matching import MetadataLookupResult, match_candidates, normalize_key, query_terms
from .provider import Candidate, ItunesSearchProvider
from .store import MetadataStore

__all__ = [
    "Candidate",
    "ItunesSearchProvider",
    "MetadataLookupResult",
    "MetadataStore",
    "match_candidates",
    "normalize_key",
    "query_terms",
]
