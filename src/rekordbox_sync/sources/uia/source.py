"""One-shot UIA capture: spawn capture.ps1 per read, decode its JSON.

Also home to the pieces both capture paths share — ``decode_observation``
(raw snapshot → typed observation) and ``_quality`` (the suspect ranking
the diagnostic hook keys on). The resident source imports them from here
so the two paths can never drift apart in what they consider a good read.
"""

import json
import subprocess
import sys
from importlib.resources import as_file, files

from ...models import DeckObservation, DeckState
from .deck_parser import parse_snapshot


def decode_observation(raw: dict) -> DeckObservation:
    parsed = parse_snapshot(raw)
    return DeckObservation(
        decks=tuple(DeckState(deck_id=key, **value) for key, value in parsed["decks"].items()),
        observed_at=parsed["received_at"],
        window_minimized=raw.get("window_minimized"),
        error=parsed.get("error"),
    )


def _quality(observation: DeckObservation) -> int:
    """2 = complete, 1 = parsed but a loaded deck's current BPM is missing,
    0 = error. A loaded deck always shows a current BPM on screen, so a
    missing one means the tree was incomplete even though it parsed."""
    if observation.error:
        return 0
    if any(deck.loaded and deck.bpm_current is None for deck in observation.decks):
        return 1
    return 2


class UiaSource:
    def __init__(self, timeout_seconds: float = 10.0, on_suspect=None):
        self._timeout_seconds = timeout_seconds
        # Optional diagnostic hook: called with the raw snapshot whenever a
        # completed capture decodes to a suspect observation (error, or a
        # loaded deck's BPM missing). Normal monitoring passes None and
        # never persists snapshots.
        self._on_suspect = on_suspect
        self._last_read_failed = False
        self._closed = False

    def read(self) -> DeckObservation:
        if self._closed:
            # A close since the last read (a monitor stop) ends the
            # failure streak: this read retries like a first failure.
            self._closed = False
            self._last_read_failed = False
        try:
            observation = self._capture_once()
            if _quality(observation) < 2 and not self._last_read_failed and not self._closed:
                # A completed capture whose tree does not parse — or parses
                # with a loaded deck's BPM missing — is often a transient
                # partial enumeration (the walk is not atomic): one
                # immediate second look usually sees the full tree. Only
                # the first failure of a streak retries, so a state that
                # genuinely persists does not double every poll's cost.
                retry = self._capture_once()
                if _quality(retry) > _quality(observation):
                    observation = retry
        except RuntimeError:
            self._last_read_failed = True
            raise
        self._last_read_failed = _quality(observation) < 2
        return observation

    def _capture_once(self) -> DeckObservation:
        if sys.platform != "win32":
            raise RuntimeError("Live UI Automation capture requires Windows")
        with as_file(files(__package__).joinpath("capture.ps1")) as script:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", str(script), "-Label", "live-api", "-StdoutJson"],
                capture_output=True,
                timeout=self._timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        if result.returncode:
            raise RuntimeError("Rekordbox UI Automation capture failed")
        raw = json.loads(result.stdout.decode("utf-8-sig"))
        observation = decode_observation(raw)
        if self._on_suspect is not None and _quality(observation) < 2:
            self._on_suspect(raw)
        return observation

    def close(self) -> None:
        # Capture subprocesses are bounded and reaped by subprocess.run, so
        # there is nothing to release; closing only cancels the retry of a
        # read currently in progress (a stop must not wait for a second
        # capture) and marks the failure streak as ended for the next read.
        self._closed = True
