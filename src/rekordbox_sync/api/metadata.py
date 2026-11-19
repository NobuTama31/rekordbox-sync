"""Integration of independent CSV lookup with monitor snapshots."""

from collections.abc import Sequence
from typing import Protocol

from song_catalog import SongLookupResult

from ..models import LiveStateSnapshot
from .serialization import to_api_dict


class SongCatalog(Protocol):
    def lookup(self, title: str, artist: str, *, fields: Sequence[str] | None = None) -> SongLookupResult: ...


def enrich_snapshot(snapshot: LiveStateSnapshot, catalog: SongCatalog | None, fields=None, track_metadata=None) -> dict:
    # Two independent lookups ride alongside the state, both keyed by
    # deck id: ``song_info`` from the local CSV catalog, and
    # ``track_info`` from the external metadata store (present only
    # when that service is attached). Unloaded decks get an explicit
    # track_unavailable placeholder so consumers can index every deck
    # blindly. Both lookups are local reads — the HTTP path never
    # searches the network (see metadata_service).
    state = to_api_dict(snapshot)
    metadata = {}
    external = {}
    for deck in snapshot.decks:
        if not deck.loaded or not deck.title or not deck.artist:
            metadata[deck.deck_id] = {"status": "track_unavailable", "match_count": 0, "fields": {}}
            external[deck.deck_id] = {"status": "track_unavailable", "match_count": 0, "fetched_at": None}
        else:
            metadata[deck.deck_id] = (
                catalog.lookup(deck.title, deck.artist, fields=fields).to_dict()
                if catalog is not None
                else {"status": "track_unavailable", "match_count": 0, "fields": {}}
            )
            if track_metadata is not None:
                external[deck.deck_id] = track_metadata.lookup(deck.title, deck.artist)
    payload = {"schema_version": 1, "state": state, "song_info": metadata}
    if track_metadata is not None:
        payload["track_info"] = external
    return payload
