"""Background fetching of external track metadata for loaded decks.

The HTTP path never searches: it reads the persistent store only. A watcher
thread notices track changes on monitor snapshots and fetches in the background.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from track_metadata import MetadataStore, match_candidates, normalize_key, query_terms

_LOGGER = logging.getLogger(__name__)
REFETCH_AFTER_SECONDS = 30 * 24 * 3600
# A not_found verdict is weak evidence: term search can return nothing
# transiently, and one bad fetch must not hide a findable track for a month.
NOT_FOUND_REFETCH_AFTER_SECONDS = 600
ERROR_RETRY_SECONDS = 60.0
DEFAULT_DB_PATH = Path.home() / ".rekordbox_sync" / "track_metadata.db"
METADATA_DB_ENV_VAR = "REKORDBOX_SYNC_METADATA_DB"


def resolve_metadata_db_path(explicit=None) -> Path:
    """DB location: explicit argument, then $REKORDBOX_SYNC_METADATA_DB, then the default."""
    if explicit is not None:
        return Path(explicit)
    from_env = os.environ.get(METADATA_DB_ENV_VAR)
    if from_env:
        return Path(from_env)
    return DEFAULT_DB_PATH


def _is_stale(result, now: datetime) -> bool:
    # A match is trusted indefinitely; only misses expire, on the
    # status-specific windows above.
    if result.status == "matched" or result.fetched_at is None:
        return False
    try:
        fetched = datetime.fromisoformat(result.fetched_at)
    except ValueError:
        return True
    limit = NOT_FOUND_REFETCH_AFTER_SECONDS if result.status == "not_found" else REFETCH_AFTER_SECONDS
    return (now - fetched).total_seconds() > limit


class TrackMetadataService:
    def __init__(self, store: MetadataStore, provider, *, poll_interval_seconds: float = 1.0):
        self._store = store
        self._provider = provider
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread = None
        self._closed = False
        self._close_requested = False
        self._lifecycle_lock = threading.RLock()
        self._active_lookups = 0
        self._attempts = {}
        self._attempts_lock = threading.Lock()

    def lookup(self, title: str, artist: str) -> dict:
        with self._lifecycle_lock:
            if self._close_requested:
                raise RuntimeError("Track metadata service is closing")
            self._active_lookups += 1
        try:
            result = self._store.get(title, artist)
            if result is None:
                return {"status": "pending", "match_count": 0, "fetched_at": None}
            return result.to_dict()
        finally:
            with self._lifecycle_lock:
                self._active_lookups -= 1
                self._finish_close()

    def start(self, snapshot) -> None:
        with self._lifecycle_lock:
            if self._close_requested:
                raise RuntimeError("Track metadata service is closed")
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, args=(snapshot,), name="rekordbox-track-metadata", daemon=True
            )
            self._thread.start()

    def _request_stop(self) -> None:
        self._stop_event.set()

    def stop(self) -> None:
        self._request_stop()
        with self._lifecycle_lock:
            thread = self._thread
        if thread:
            thread.join(timeout=self._poll_interval_seconds + 15)
            if thread.is_alive():
                # Keep the reference: start() must not spawn a second watcher
                # while this one is still running (e.g. stuck in a search).
                _LOGGER.warning(
                    "Track metadata thread did not stop within %.1f seconds", self._poll_interval_seconds + 15
                )
            else:
                with self._lifecycle_lock:
                    if self._thread is thread:
                        self._thread = None

    def close(self) -> None:
        """Final teardown: stop the watcher and close the store.

        The service must not be started again after close(). If the watcher
        or an HTTP lookup outlives the wait, its exit closes the store rather
        than closing the connection under running work.
        """
        with self._lifecycle_lock:
            self._close_requested = True
        self.stop()
        with self._lifecycle_lock:
            self._finish_close()

    def _finish_close(self) -> None:
        # Called under the lifecycle lock, including from the final worker or
        # HTTP lookup to exit after close() exhausted its bounded wait.
        if (
            self._close_requested
            and not self._closed
            and not self._active_lookups
            and (self._thread is None or not self._thread.is_alive())
        ):
            self._store.close()
            self._closed = True

    def _run(self, snapshot) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    for deck in snapshot().decks:
                        if self._stop_event.is_set():
                            break
                        if deck.loaded and deck.title and deck.artist:
                            self._fetch_if_needed(deck.title, deck.artist)
                except Exception:
                    _LOGGER.exception("Track metadata watch failed")
                self._stop_event.wait(self._poll_interval_seconds)
        finally:
            with self._lifecycle_lock:
                self._thread = None
                self._finish_close()

    def _fetch_if_needed(self, title: str, artist: str) -> None:
        if self._stop_event.is_set():
            return
        key = (normalize_key(title), normalize_key(artist))
        stored = self._store.get(title, artist)
        if stored is not None and not _is_stale(stored, datetime.now(timezone.utc)):
            return
        with self._attempts_lock:
            last_attempt = self._attempts.get(key)
            if last_attempt is not None and time.monotonic() - last_attempt < ERROR_RETRY_SECONDS:
                return
            self._attempts[key] = time.monotonic()
        try:
            candidates = self._provider.search(title, artist)
        except Exception:
            _LOGGER.exception("Track metadata search failed for %s / %s", title, artist)
            return
        result = match_candidates(title, artist, candidates)
        if self._stop_event.is_set():
            with self._attempts_lock:
                self._attempts.pop(key, None)
            return
        if result.status == "not_found":
            try:
                result = self._retry_with_query_terms(title, artist) or result
            except Exception:
                # A failed follow-up search is a provider error, not a
                # negative result: store nothing and keep the attempt
                # record, so the 60-second error backoff applies
                # instead of the not-found refetch window (audit #7).
                _LOGGER.exception("Track metadata retry search failed for %s / %s", title, artist)
                return
        with self._attempts_lock:
            self._attempts.pop(key, None)
        if self._stop_event.is_set():
            return
        self._store.put(title, artist, result)

    def _retry_with_query_terms(self, title: str, artist: str):
        if self._stop_event.is_set():
            return None
        terms = query_terms(title, artist)
        if terms is None:
            return None
        # Provider errors propagate: the caller treats them as a
        # failed search, never as a negative result (audit #7).
        candidates = self._provider.search(*terms)
        result = match_candidates(title, artist, candidates)
        return result if result.status != "not_found" else None
