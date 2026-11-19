import logging
import time

from ...models import MixerObservation
from .flx4_decoder import Flx4Decoder

_LOGGER = logging.getLogger(__name__)


class MidiSource:
    """Fader messages arrive on the port callback; read() only snapshots and checks the connection."""

    def __init__(self, port_name: str, *, check_interval_seconds: float = 1.0, pair_timeout_seconds: float = 0.25):
        self._port_name = port_name
        self._check_interval_seconds = check_interval_seconds
        self._decoder = Flx4Decoder(pair_timeout_seconds)
        self._port = None
        self._next_check = 0.0

    def _on_message(self, message) -> None:
        if message.type != "control_change":
            return
        try:
            self._decoder.feed(message.channel, message.control, message.value)
        except Exception:
            _LOGGER.exception("Failed to decode MIDI message")

    def read(self) -> MixerObservation:
        import mido

        try:
            if self._port is None:
                self._port = mido.open_input(self._port_name, callback=self._on_message)
                self._next_check = time.monotonic() + self._check_interval_seconds
            # The disconnect check enumerates port names, so it runs on
            # its own slower interval, not on every 5 ms poll. Any
            # failure closes the port; the next read reopens it.
            if time.monotonic() >= self._next_check:
                if self._port_name not in mido.get_input_names():
                    raise RuntimeError("MIDI input disconnected")
                self._next_check = time.monotonic() + self._check_interval_seconds
            return MixerObservation(self._decoder.snapshot(), True, self._decoder.control_event_count)
        except Exception as error:
            self.close()
            return MixerObservation(control_event_count=self._decoder.control_event_count, error=str(error))

    def close(self) -> None:
        port, self._port = self._port, None
        try:
            if port is not None:
                port.callback = None
                port.close()
        finally:
            self._decoder.reset()
