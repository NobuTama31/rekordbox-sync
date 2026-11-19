"""Session diagnostics: the suspect-capture sink's keep-history mode."""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import rekordbox_sync.service as service
from rekordbox_sync import MonitorConfig


class _FixedClock:
    def __init__(self, moment):
        self.moment = moment

    def now(self):
        return self.moment


class SuspectHistoryTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._directory.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._directory.cleanup()

    def history_dir(self):
        return Path("logs") / "history"

    def test_history_keeps_snapshot_and_index_entry(self):
        raw = {"capture_mode": "tracked", "elements": [{}, {}], "window_name": "rekordbox"}
        clock = _FixedClock(datetime(2026, 11, 18, 9, 51, 56))
        with mock.patch.object(service, "datetime", clock):
            service._write_suspect_history(raw)
        names = sorted(p.name for p in self.history_dir().iterdir())
        self.assertEqual(names, ["index.jsonl", "uia_2026-11-18T09-51-56.json"])
        saved = json.loads((self.history_dir() / "uia_2026-11-18T09-51-56.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, raw)
        entry = json.loads((self.history_dir() / "index.jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(entry["at"], "2026-11-18T09:51:56")
        self.assertEqual(entry["capture_mode"], "tracked")
        self.assertEqual(entry["elements"], 2)
        self.assertEqual(entry["window_name"], "rekordbox")
        self.assertIsNone(entry["error"])

    def test_same_second_keeps_one_file_but_indexes_every_event(self):
        clock = _FixedClock(datetime(2026, 11, 18, 9, 51, 56))
        with mock.patch.object(service, "datetime", clock):
            service._write_suspect_history({"elements": []})
            service._write_suspect_history({"elements": [{}]})
        files = [p.name for p in self.history_dir().iterdir() if p.suffix == ".json"]
        self.assertEqual(len(files), 1)
        lines = (self.history_dir() / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["capture_mode"], "oneshot")
        self.assertEqual(json.loads(lines[1])["elements"], 1)

    def test_sink_modes(self):
        self.assertIsNone(service._suspect_sink(MonitorConfig()))
        latest_only = service._suspect_sink(MonitorConfig(save_suspect_captures=True))
        latest_only({"elements": []})
        self.assertTrue((Path("logs") / "latest_uia.json").exists())
        self.assertFalse(self.history_dir().exists())
        with_history = service._suspect_sink(MonitorConfig(save_suspect_captures=True, keep_suspect_history=True))
        with_history({"elements": []})
        self.assertTrue(self.history_dir().exists())

    def test_history_writer_never_raises_on_garbage(self):
        service._write_suspect_history({})
        service._write_suspect_history({"elements": None})


if __name__ == "__main__":
    unittest.main()
