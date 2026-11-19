"""Contract tests for UiaResidentSource (docs/resident_uia.md).

These tests are the specification for the resident capture implementation.
They inject a fake process factory, so they run on any platform. Against
the skeleton they fail; the implementation is done when they pass without
modifying this file.
"""

import json
import threading
import unittest
from pathlib import Path

from rekordbox_sync.sources.uia.resident import UiaResidentSource
from rekordbox_sync.sources.uia.source import decode_observation

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tempo_changed.json").read_text(encoding="utf-8-sig"))[
    "input"
]
FIXTURE_LINE = json.dumps(FIXTURE)
ERROR_LINE = json.dumps(
    {
        "received_at": "2026-11-17T00:00:00+00:00",
        "elements": [],
        "error": "Rekordbox window not found",
    }
)


class FakeProcess:
    """Process handle double: replays one scripted behaviour per read."""

    def __init__(self, behaviours, read_delay_seconds: float = 0.0):
        self._behaviours = list(behaviours)
        self._read_delay_seconds = read_delay_seconds
        self.written = []
        self.read_timeouts = []
        self.terminated = False
        self._in_flight = False

    def write_line(self, text):
        if self._in_flight:
            raise AssertionError("overlapping request: reads are not serialized")
        self._in_flight = True
        self.written.append(text)

    def read_line(self, timeout_seconds):
        self.read_timeouts.append(timeout_seconds)
        if self._read_delay_seconds:
            import time

            time.sleep(self._read_delay_seconds)
        behaviour = self._behaviours.pop(0)
        self._in_flight = False
        kind = behaviour[0]
        if kind == "line":
            return behaviour[1]
        if kind == "timeout":
            return None
        if kind == "eof":
            raise EOFError("stream closed")
        raise AssertionError(f"unknown behaviour {kind!r}")

    def is_alive(self):
        return not self.terminated

    def terminate(self):
        self.terminated = True


class FakeFactory:
    def __init__(self, *processes):
        self._queue = list(processes)
        self.spawned = 0

    def __call__(self):
        self.spawned += 1
        return self._queue.pop(0)


def line(text=FIXTURE_LINE):
    return ("line", text)


class UiaResidentSourceTests(unittest.TestCase):
    def test_first_read_spawns_once_and_reuses_process(self):
        process = FakeProcess([line(), line()])
        factory = FakeFactory(process)
        source = UiaResidentSource(process_factory=factory)
        expected = decode_observation(FIXTURE)
        self.assertEqual(source.read(), expected)
        self.assertEqual(source.read(), expected)
        self.assertEqual(factory.spawned, 1)

    def test_request_is_one_json_line_with_label(self):
        process = FakeProcess([line()])
        source = UiaResidentSource(process_factory=FakeFactory(process))
        source.read()
        self.assertEqual(len(process.written), 1)
        self.assertEqual(json.loads(process.written[0]), {"label": "live-api"})

    def test_configured_timeout_is_passed_to_read_line(self):
        process = FakeProcess([line()])
        source = UiaResidentSource(timeout_seconds=3.5, process_factory=FakeFactory(process))
        source.read()
        self.assertEqual(process.read_timeouts, [3.5])

    def test_timeout_raises_terminates_and_next_read_respawns(self):
        first = FakeProcess([("timeout",)])
        second = FakeProcess([line()])
        factory = FakeFactory(first, second)
        source = UiaResidentSource(process_factory=factory)
        with self.assertRaises(RuntimeError):
            source.read()
        self.assertTrue(first.terminated)
        self.assertEqual(source.read(), decode_observation(FIXTURE))
        self.assertEqual(factory.spawned, 2)

    def test_process_death_raises_terminates_and_next_read_respawns(self):
        first = FakeProcess([("eof",)])
        second = FakeProcess([line()])
        factory = FakeFactory(first, second)
        source = UiaResidentSource(process_factory=factory)
        with self.assertRaises(RuntimeError):
            source.read()
        self.assertTrue(first.terminated)
        self.assertEqual(source.read(), decode_observation(FIXTURE))
        self.assertEqual(factory.spawned, 2)

    def test_malformed_response_raises_terminates_and_next_read_respawns(self):
        first = FakeProcess([line("this is not json")])
        second = FakeProcess([line()])
        factory = FakeFactory(first, second)
        source = UiaResidentSource(process_factory=factory)
        with self.assertRaises(RuntimeError):
            source.read()
        self.assertTrue(first.terminated)
        self.assertEqual(source.read(), decode_observation(FIXTURE))
        self.assertEqual(factory.spawned, 2)

    def test_error_payload_flows_through_and_process_survives(self):
        process = FakeProcess([line(ERROR_LINE), line()])
        factory = FakeFactory(process)
        source = UiaResidentSource(process_factory=factory)
        observation = source.read()
        self.assertEqual(observation.decks, ())
        self.assertTrue(observation.error)
        self.assertFalse(process.terminated)
        self.assertEqual(source.read(), decode_observation(FIXTURE))
        self.assertEqual(factory.spawned, 1)

    def test_close_terminates_and_is_idempotent(self):
        process = FakeProcess([line()])
        source = UiaResidentSource(process_factory=FakeFactory(process))
        source.close()  # before any read: no spawn, no error
        source.read()
        source.close()
        self.assertTrue(process.terminated)
        source.close()

    def test_read_after_close_respawns(self):
        first = FakeProcess([line()])
        second = FakeProcess([line()])
        factory = FakeFactory(first, second)
        source = UiaResidentSource(process_factory=factory)
        source.read()
        source.close()
        self.assertEqual(source.read(), decode_observation(FIXTURE))
        self.assertEqual(factory.spawned, 2)

    def test_reads_are_serialized(self):
        process = FakeProcess([line(), line()], read_delay_seconds=0.05)
        source = UiaResidentSource(process_factory=FakeFactory(process))
        errors = []

        def read_once():
            try:
                source.read()
            except Exception as error:  # collected for assertion
                errors.append(error)

        threads = [threading.Thread(target=read_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_non_windows_without_factory_raises(self):
        source = UiaResidentSource(platform="linux")
        with self.assertRaises(RuntimeError):
            source.read()


if __name__ == "__main__":
    unittest.main()
