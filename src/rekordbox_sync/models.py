"""Immutable values shared by sources, the monitor and consumers.

Conventions that matter when reading everything downstream:
- ``None`` always means *unknown* — never a fabricated default. A deck
  whose BPM could not be read has ``bpm_current is None``, not 0.
- ``playing`` / ``master`` stay ``None``: UI Automation cannot confirm
  PLAY or MASTER state, and inferred motion is never presented as
  confirmed state. Anything inferred carries an explicit ``*_estimate``
  name (see ``LiveStateSnapshot.on_air_estimate``).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DeckState:
    deck_id: str
    loaded: bool
    title: str | None = None
    artist: str | None = None
    bpm_original: float | None = None
    bpm_current: float | None = None
    position_seconds: float | None = None
    remaining_seconds: float | None = None
    playing: bool | None = None
    master: bool | None = None


@dataclass(frozen=True)
class FaderState:
    value: float | None = None
    raw_14bit: int | None = None
    observed_at: str | None = None


@dataclass(frozen=True)
class MixerState:
    hardware_channel_1: FaderState = field(default_factory=FaderState)
    hardware_channel_2: FaderState = field(default_factory=FaderState)
    hardware_crossfader: FaderState = field(default_factory=FaderState)


@dataclass(frozen=True)
class DeckObservation:
    decks: tuple[DeckState, ...] = ()
    observed_at: str | None = None
    window_minimized: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class MixerObservation:
    mixer: MixerState = field(default_factory=MixerState)
    connected: bool = False
    control_event_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class SourceStatus:
    status: Literal["ok", "connected", "unavailable"] = "unavailable"
    observed_at: str | None = None
    age_seconds: float | None = None
    window_minimized: bool | None = None
    control_event_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class LiveStateSnapshot:
    served_at: str
    decks: tuple[DeckState, ...]
    mixer: MixerState
    uia_status: SourceStatus
    midi_status: SourceStatus
    on_air_estimate: tuple[int, ...] | None = None
