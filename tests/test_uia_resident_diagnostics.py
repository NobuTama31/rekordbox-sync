"""Opt-in resident diagnostics preserve observations and process lifetime."""

import copy
import json
import unittest

from test_uia_resident import ERROR_LINE, FIXTURE, FakeFactory, FakeProcess, line

from rekordbox_sync.sources.uia.resident import UiaResidentSource
from rekordbox_sync.sources.uia.source import decode_observation


class ResidentDiagnosticsTests(unittest.TestCase):
    def test_suspect_snapshots_reach_hook_and_process_is_reused(self):
        missing_bpm = copy.deepcopy(FIXTURE)
        for element in missing_bpm["elements"]:
            if element["type"] == "ControlType.Edit":
                element["value"] = ""
        missing_bpm["capture_mode"] = "tracked"
        error = json.loads(ERROR_LINE)
        error["capture_mode"] = "full"
        saved = []
        process = FakeProcess([line(json.dumps(error)), line(json.dumps(missing_bpm)), line()])
        factory = FakeFactory(process)
        source = UiaResidentSource(process_factory=factory, on_suspect=saved.append)
        self.addCleanup(source.close)
        for snapshot in (error, missing_bpm, FIXTURE):
            self.assertEqual(source.read(), decode_observation(snapshot))
        self.assertEqual(saved, [error, missing_bpm])
        self.assertFalse(process.terminated)
        self.assertEqual(factory.spawned, 1)

    def test_hook_failure_does_not_break_read_or_terminate_process(self):
        def fail(snapshot):
            raise OSError("diagnostic destination unavailable")

        process = FakeProcess([line(ERROR_LINE), line()])
        source = UiaResidentSource(process_factory=FakeFactory(process), on_suspect=fail)
        self.addCleanup(source.close)
        self.assertEqual(source.read(), decode_observation(json.loads(ERROR_LINE)))
        self.assertFalse(process.terminated)
        self.assertEqual(source.read(), decode_observation(FIXTURE))

    def test_transport_failure_does_not_call_hook(self):
        saved = []
        process = FakeProcess([line("invalid json")])
        source = UiaResidentSource(process_factory=FakeFactory(process), on_suspect=saved.append)
        self.addCleanup(source.close)
        with self.assertRaises(RuntimeError):
            source.read()
        self.assertEqual(saved, [])
        self.assertTrue(process.terminated)
