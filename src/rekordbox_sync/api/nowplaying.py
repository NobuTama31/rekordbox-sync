"""Presentation conversion for the OBS overlay.

Turns an enriched snapshot into the flat ``/nowplaying.json`` contract the
overlay page polls. Deck selection is a display choice only and never feeds
back into ``/state`` or the schema v1 semantics. Nothing here claims
confirmed PLAY or ON AIR. Selection: when every loaded deck has a channel
fader value, the loudest deck wins (ties go to the most recently changed
track); otherwise — no or partial fader data — the most recently changed
deck wins, as before. One refinement: while the displayed deck's fader is
parked at the top, the selection is locked to it. DJs mix with EQ while
both channel faders stay at 100%, so a tie at the top must not flip the
overlay to the incoming track; the lock releases when that fader leaves
the top, and the normal loudest-wins rule takes over.

A track load makes the UIA snapshot fail or the decks read empty for a
moment, which would flash the overlay to hidden mid-load. The presenter
therefore holds the last visible payload for a short grace period before
letting a hidden result through.
"""

import threading
import time
from collections.abc import Callable

from ..models import LiveStateSnapshot
from .metadata import SongCatalog, enrich_snapshot

_WORK_FIELD = "TVA"
_TOP_FADER_VALUE = 0.999


def _hidden() -> dict:
    return {"status": "hidden", "title": "", "artist": "", "artwork": "", "work": "", "manual_revision": ""}


def _artwork(track_info: dict) -> str:
    status = track_info.get("status")
    if status == "matched":
        return track_info.get("artwork_url") or ""
    if status == "ambiguous":
        candidates = track_info.get("candidates") or []
        if candidates:
            return candidates[0].get("artwork_url") or ""
    return ""


def _work(song_info: dict) -> str:
    if song_info.get("status") != "matched":
        return ""
    return song_info.get("fields", {}).get(_WORK_FIELD) or ""


class NowPlayingPresenter:
    """Stateful converter: remembers each deck's last (title, artist) and
    the sequence number of its most recent change."""

    def __init__(self, *, hidden_grace_seconds: float = 3.0, clock: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._track_keys: dict[str, tuple[str, str] | None] = {}
        self._change_sequences: dict[str, int] = {}
        self._sequence = 0
        self._hidden_grace_seconds = hidden_grace_seconds
        self._clock = clock
        self._last_visible: dict | None = None
        self._last_visible_at: float | None = None
        self._selected_id: str | None = None
        # Concurrent requests take a ticket on entry. Only the newest
        # ticket may mutate the selection state or the held payload, so
        # a request delayed in enrichment cannot roll either back after
        # a newer request has committed (audit #5). Enrichment itself
        # stays outside the lock.
        self._next_ticket = 0
        self._applied_ticket = 0
        self._hold_ticket = 0

    def present(self, snapshot: LiveStateSnapshot, catalog: SongCatalog | None, track_metadata=None) -> dict:
        with self._lock:
            self._next_ticket += 1
            ticket = self._next_ticket
        enriched = enrich_snapshot(snapshot, catalog, track_metadata=track_metadata)
        with self._lock:
            result = self._present_current(snapshot, enriched, track_metadata, ticket)
            now = self._clock()
            if result["status"] != "hidden":
                if ticket >= self._hold_ticket:
                    self._last_visible = result
                    self._last_visible_at = now
                    self._hold_ticket = ticket
                return result
            if (
                self._last_visible is not None
                and self._last_visible_at is not None
                and now - self._last_visible_at <= self._hidden_grace_seconds
            ):
                return self._last_visible
            return result

    def _present_current(
        self, snapshot: LiveStateSnapshot, enriched: dict, track_metadata=None, ticket: int = 0
    ) -> dict:
        state = enriched["state"]
        decks = state["decks"]
        faders = {"1": snapshot.mixer.hardware_channel_1.value, "2": snapshot.mixer.hardware_channel_2.value}
        if ticket < self._applied_ticket:
            # A newer request already committed the selection state;
            # answer from it without mutating anything.
            selected_id = self._selected_id
        else:
            selected_id = self._select(decks, faders)
            self._applied_ticket = ticket
        if selected_id is None or state["sources"]["uia"]["status"] != "ok":
            return _hidden()
        deck = decks[selected_id]
        if not deck["loaded"] or not deck["title"] or not deck["artist"]:
            return _hidden()
        track_info = enriched.get("track_info", {}).get(selected_id, {})
        if track_metadata is not None and track_info.get("status") == "pending":
            return {"status": "loading", "title": "", "artist": "", "artwork": "", "work": "", "manual_revision": ""}
        return {
            "status": "playing",
            "title": deck["title"],
            "artist": deck["artist"],
            "artwork": _artwork(track_info),
            "work": _work(enriched["song_info"].get(selected_id, {})),
            "manual_revision": "",
        }

    def _select(self, decks: dict, faders: dict) -> str | None:
        selected_id = None
        selected_sequence = -1
        for deck_id, deck in decks.items():
            key = (deck["title"], deck["artist"]) if deck["loaded"] else None
            if deck_id not in self._track_keys:
                self._track_keys[deck_id] = None
                self._change_sequences[deck_id] = 0
            if self._track_keys[deck_id] != key:
                self._sequence += 1
                self._track_keys[deck_id] = key
                self._change_sequences[deck_id] = self._sequence
            if self._change_sequences[deck_id] > selected_sequence:
                selected_sequence = self._change_sequences[deck_id]
                selected_id = deck_id
        loaded = [deck_id for deck_id, deck in decks.items() if deck["loaded"] and deck["title"] and deck["artist"]]
        if loaded and all(faders.get(deck_id) is not None for deck_id in loaded):
            previous = self._selected_id
            if previous in loaded and faders[previous] >= _TOP_FADER_VALUE:
                selected_id = previous
            else:
                selected_id = max(loaded, key=lambda deck_id: (faders[deck_id], self._change_sequences[deck_id]))
        self._selected_id = selected_id
        return selected_id
