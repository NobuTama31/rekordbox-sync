"""Persistent SQLite store for fetched track metadata. Survives restarts by design."""

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .matching import MetadataLookupResult, normalize_key
from .provider import Candidate

_SCHEMA = """
CREATE TABLE IF NOT EXISTS track_metadata (
    key_title TEXT NOT NULL,
    key_artist TEXT NOT NULL,
    raw_title TEXT NOT NULL,
    raw_artist TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT,
    provider_track_id TEXT,
    title TEXT,
    artist TEXT,
    album TEXT,
    release_date TEXT,
    artwork_url TEXT,
    candidates TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (key_title, key_artist)
)
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetadataStore:
    """Thread-safe SQLite store keyed by the catalog key rule (NFC + trim)."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        with self._lock, self._connection:
            self._connection.execute(_SCHEMA)

    def get(self, title: str, artist: str) -> MetadataLookupResult | None:
        key = (normalize_key(title), normalize_key(artist))
        with self._lock:
            row = self._connection.execute(
                "SELECT status, provider, provider_track_id, title, artist, album,"
                " release_date, artwork_url, candidates, fetched_at"
                " FROM track_metadata WHERE key_title = ? AND key_artist = ?",
                key,
            ).fetchone()
        if row is None:
            return None
        (
            status,
            provider,
            track_id,
            found_title,
            found_artist,
            album,
            release_date,
            artwork_url,
            candidates_json,
            fetched_at,
        ) = row
        candidates = tuple(Candidate(**item) for item in json.loads(candidates_json))
        match = None
        if status == "matched":
            match = Candidate(
                provider=provider,
                provider_track_id=track_id,
                title=found_title,
                artist=found_artist,
                album=album,
                release_date=release_date,
                artwork_url=artwork_url,
            )
        return MetadataLookupResult(status, match=match, candidates=candidates, fetched_at=fetched_at)

    def put(self, raw_title: str, raw_artist: str, result: MetadataLookupResult) -> MetadataLookupResult:
        # fetched_at is stamped here, at write time: the refetch
        # windows measure from when the store learned the result.
        stored = replace(result, fetched_at=_utc_now())
        match = stored.match
        values = (
            normalize_key(raw_title),
            normalize_key(raw_artist),
            raw_title,
            raw_artist,
            stored.status,
            match.provider if match else None,
            match.provider_track_id if match else None,
            match.title if match else None,
            match.artist if match else None,
            match.album if match else None,
            match.release_date if match else None,
            match.artwork_url if match else None,
            json.dumps([candidate.to_dict() for candidate in stored.candidates], ensure_ascii=False),
            stored.fetched_at,
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO track_metadata (key_title, key_artist, raw_title,"
                " raw_artist, status, provider, provider_track_id, title, artist, album,"
                " release_date, artwork_url, candidates, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        return stored

    @property
    def count(self) -> int:
        with self._lock:
            return self._connection.execute("SELECT COUNT(*) FROM track_metadata").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
