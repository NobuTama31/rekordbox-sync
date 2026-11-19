"""Decodes the DDJ-FLX4's fader control changes into 14-bit values.

Each fader sends its position as a pair of CC messages — MSB first,
then LSB — on the (channel, control) pairs in ``_CONTROLS``. The
values describe the *hardware* control positions; Rekordbox's
effective (software) values may differ.
"""

import threading
import time
from collections.abc import Callable

from ...models import FaderState, MixerState, utc_now

# (channel, control) -> (fader, is_msb)
_CONTROLS = {
    (0, 19): ("hardware_channel_1", True),
    (0, 51): ("hardware_channel_1", False),
    (1, 19): ("hardware_channel_2", True),
    (1, 51): ("hardware_channel_2", False),
    (6, 31): ("hardware_crossfader", True),
    (6, 63): ("hardware_crossfader", False),
}


class Flx4Decoder:
    def __init__(self, pair_timeout_seconds: float = 0.25, *, clock: Callable[[], float] = time.monotonic):
        self._pair_timeout_seconds = pair_timeout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._pending = {}
        self._faders = {}
        self._control_event_count = 0

    @property
    def control_event_count(self) -> int:
        with self._lock:
            return self._control_event_count

    def feed(self, channel: int, control: int, value: int) -> None:
        if not 0 <= value <= 127:
            raise ValueError("MIDI CC value must be in 0..127")
        item = _CONTROLS.get((channel, control))
        if item is None:
            return
        key, msb = item
        with self._lock:
            self._control_event_count += 1
            if msb:
                self._pending[key] = (value, self._clock())
            else:
                # An LSB only counts paired with a recent MSB; on its
                # own the high bits are unknowable, so it is dropped.
                previous = self._pending.pop(key, None)
                if previous is not None and self._clock() - previous[1] < self._pair_timeout_seconds:
                    raw = (previous[0] << 7) | value
                    self._faders[key] = FaderState(raw / 16383, raw, utc_now())

    def snapshot(self) -> MixerState:
        with self._lock:
            return MixerState(**self._faders)

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
            self._faders.clear()
