"""Lifecycle and polling orchestration, independent of HTTP."""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from .models import DeckObservation, LiveStateSnapshot, MixerObservation
from .settings import MonitorConfig
from .sources.midi.source import MidiSource
from .sources.protocols import DeckSource, MixerSource
from .sources.uia.resident import UiaResidentSource
from .sources.uia.source import UiaSource
from .state_store import StateStore

_LOGGER = logging.getLogger(__name__)


def _write_suspect_capture(raw: dict) -> None:
    """Diagnostic opt-in: keep the most recent suspect raw snapshot at the
    conventional diagnostic path (logs/latest_uia.json), replacing the
    previous one, so a transient failure can be inspected after the fact."""
    try:
        path = Path("logs") / "latest_uia.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("latest_uia.json.tmp")
        temporary.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except Exception:  # a diagnostic must never break monitoring
        _LOGGER.warning("Could not save suspect UIA capture", exc_info=True)


def _write_suspect_history(raw: dict) -> None:
    """Diagnostic opt-in (keep-history): keep every suspect raw snapshot
    under logs/history/ with a timestamped name, plus a one-line-per-event
    index (index.jsonl), so a failure's whole arc — when it started, which
    capture path produced it, how many elements the tree had — survives
    the session. At most one snapshot file per second; the index still
    records every event."""
    try:
        directory = Path("logs") / "history"
        directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        path = directory / f"uia_{now:%Y-%m-%dT%H-%M-%S}.json"
        if not path.exists():
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        entry = {
            "at": now.isoformat(timespec="seconds"),
            "capture_mode": raw.get("capture_mode") or "oneshot",
            "elements": len(raw.get("elements") or []),
            "window_name": raw.get("window_name"),
            "window_minimized": raw.get("window_minimized"),
            "error": raw.get("error"),
        }
        with (directory / "index.jsonl").open("a", encoding="utf-8") as index:
            index.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # a diagnostic must never break monitoring
        _LOGGER.warning("Could not save suspect UIA capture history", exc_info=True)


def _suspect_sink(config: MonitorConfig):
    """The on_suspect hook for the deck source, or None when diagnostics
    are off (normal monitoring persists no snapshots)."""
    if not config.save_suspect_captures:
        return None
    keep_history = config.keep_suspect_history

    def sink(raw: dict) -> None:
        _write_suspect_capture(raw)
        if keep_history:
            _write_suspect_history(raw)

    return sink


class RekordboxMonitor:
    """Owns the two polling threads (decks via UIA, mixer via MIDI) and
    the StateStore they feed. A source failure never kills a thread: it
    becomes an observation carrying the error, and the store decides
    how long the last good state stays presentable."""

    def __init__(
        self,
        config: MonitorConfig | None = None,
        *,
        deck_source: DeckSource | None = None,
        mixer_source: MixerSource | None = None,
    ):
        self._config = config or MonitorConfig()
        if deck_source is not None:
            self._deck_source = deck_source
        else:
            on_suspect = _suspect_sink(self._config)
            source_type = UiaResidentSource if self._config.uia_resident else UiaSource
            self._deck_source = source_type(self._config.uia_timeout_seconds, on_suspect=on_suspect)
        self._mixer_source = (
            mixer_source
            if mixer_source is not None
            else MidiSource(
                self._config.midi_port,
                check_interval_seconds=self._config.midi_port_check_interval_seconds,
                pair_timeout_seconds=self._config.midi_pair_timeout_seconds,
            )
        )
        self._store = StateStore(self._config.uia_stale_after_seconds)
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._threads = []

    def start(self) -> None:
        with self._lifecycle_lock:
            if any(thread.is_alive() for thread in self._threads):
                # Already running is a no-op; running-but-stopping is a
                # caller error, because the old threads still own the
                # sources the new run would share.
                if self._stop_event.is_set():
                    raise RuntimeError("Previous monitor workers have not stopped")
                return
            self._store.update_decks(DeckObservation())
            self._store.update_mixer(MixerObservation())
            self._stop_event.clear()
            self._threads = [
                threading.Thread(target=self._run_decks, name="rekordbox-uia", daemon=True),
                threading.Thread(target=self._run_mixer, name="rekordbox-midi", daemon=True),
            ]
            for thread in self._threads:
                thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            if self._threads:
                # Cancel a pending UIA retry first: an in-flight read then
                # ends after its current capture instead of starting a
                # second one this stop would have to wait for.
                try:
                    self._deck_source.close()
                except Exception:
                    _LOGGER.exception("Failed to close deck source")
            deadline = time.monotonic() + self._config.uia_timeout_seconds + 2
            for thread in self._threads:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError("Source did not stop within its configured timeout")
            self._threads.clear()

    def snapshot(self) -> LiveStateSnapshot:
        return self._store.snapshot()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_args):
        self.stop()

    def _run_decks(self):
        try:
            while not self._stop_event.is_set():
                try:
                    observation = self._deck_source.read()
                except Exception as error:
                    observation = DeckObservation(error=str(error))
                self._store.update_decks(observation)
                # Back off while the source is failing instead of
                # hammering a Rekordbox that is already struggling.
                interval = (
                    self._config.retry_interval_seconds if observation.error else self._config.uia_poll_interval_seconds
                )
                self._stop_event.wait(interval)
        finally:
            try:
                self._deck_source.close()
            except Exception:
                _LOGGER.exception("Failed to close deck source")
            self._store.clear_decks("stopped")

    def _run_mixer(self):
        try:
            while not self._stop_event.is_set():
                try:
                    observation = self._mixer_source.read()
                except Exception as error:
                    observation = MixerObservation(error=str(error))
                self._store.update_mixer(observation)
                interval = (
                    self._config.midi_poll_interval_seconds
                    if observation.connected
                    else self._config.retry_interval_seconds
                )
                self._stop_event.wait(interval)
        finally:
            try:
                self._mixer_source.close()
            except Exception:
                _LOGGER.exception("Failed to close mixer source")
            self._store.update_mixer(MixerObservation(error="stopped"))
