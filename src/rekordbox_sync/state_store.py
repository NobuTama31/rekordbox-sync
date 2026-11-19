"""Single owner of synchronized observations and freshness policy."""

import threading
import time
from collections.abc import Callable

from .models import (
    DeckObservation,
    LiveStateSnapshot,
    MixerObservation,
    MixerState,
    SourceStatus,
    utc_now,
)


class StateStore:
    def __init__(self, stale_after_seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._stale_after_seconds = stale_after_seconds
        self._deck_observation = DeckObservation()
        self._deck_error: str | None = None
        self._mixer_observation = MixerObservation()
        self._deck_received_at = None

    def update_decks(self, observation: DeckObservation) -> None:
        with self._lock:
            if observation.error:
                # A failed capture is not evidence that the decks emptied:
                # keep the last good decks until they age out, and surface
                # the error alongside them.
                self._deck_error = observation.error
                return
            self._deck_observation = observation
            self._deck_error = None
            self._deck_received_at = self._clock() if observation.observed_at else None

    def clear_decks(self, error: str | None = None) -> None:
        """Drop the decks immediately (monitor stopping); unlike a failed
        read, this is not a transient state worth holding."""
        with self._lock:
            self._deck_observation = DeckObservation()
            self._deck_error = error
            self._deck_received_at = None

    def update_mixer(self, observation: MixerObservation) -> None:
        with self._lock:
            self._mixer_observation = observation

    def snapshot(self) -> LiveStateSnapshot:
        with self._lock:
            deck = self._deck_observation
            midi = self._mixer_observation
            age = None if self._deck_received_at is None else max(0.0, self._clock() - self._deck_received_at)
            fresh = age is not None and age <= self._stale_after_seconds
            valid = fresh
            return LiveStateSnapshot(
                served_at=utc_now(),
                decks=deck.decks if valid else (),
                mixer=midi.mixer if midi.connected else MixerState(),
                uia_status=SourceStatus(
                    status="ok" if valid else "unavailable",
                    observed_at=deck.observed_at,
                    age_seconds=round(age, 3) if age is not None else None,
                    window_minimized=deck.window_minimized,
                    error=self._deck_error or ("stale" if age is not None and not fresh else None),
                ),
                midi_status=SourceStatus(
                    status="connected" if midi.connected else "unavailable",
                    control_event_count=midi.control_event_count,
                    error=midi.error,
                ),
            )
