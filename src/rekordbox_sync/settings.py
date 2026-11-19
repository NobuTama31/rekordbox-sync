"""Monitor tuning. All durations are seconds; the defaults are the
values live sessions were validated with — change them deliberately."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class MonitorConfig:
    midi_port: str = "DDJ-FLX4 0"
    uia_poll_interval_seconds: float = 0.5
    midi_poll_interval_seconds: float = 0.005
    retry_interval_seconds: float = 2.0
    uia_timeout_seconds: float = 10.0
    # How long a UIA observation stays presentable after it was taken.
    # 6 s covers a track load (Rekordbox's UIA tree degrades for several
    # seconds while a track loads/analyzes); a longer hold was tried and
    # rejected — in a real set, tracks change more often than that.
    uia_stale_after_seconds: float = 6.0
    # Diagnostics are opt-in: normal monitoring persists no snapshots.
    save_suspect_captures: bool = False
    keep_suspect_history: bool = False
    # Resident (tracked-read) capture is the default since 2026-11-18;
    # False selects the one-shot capture instead.
    uia_resident: bool = True
    midi_port_check_interval_seconds: float = 1.0
    # A 14-bit fader value arrives as two MIDI messages; halves that
    # arrive further apart than this are not paired into one value.
    midi_pair_timeout_seconds: float = 0.25

    def __post_init__(self):
        if not self.midi_port.strip():
            raise ValueError("midi_port must not be empty")
        for name, value in vars(self).items():
            if name.endswith("_seconds") and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")
