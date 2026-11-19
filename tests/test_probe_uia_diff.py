"""Tests for tools/probe_uia_diff.py (run as a subprocess, as users do)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "tools" / "probe_uia_diff.py"


def element(name, **overrides):
    base = {
        "index": 0,
        "runtime_id": [1, 2],
        "parent_runtime_id": [1],
        "name": name,
        "type": "ControlType.Button",
        "automation_id": "",
        "offscreen": False,
        "rect": {"x": 10, "y": 20, "width": 30, "height": 40},
        "patterns": [],
    }
    base.update(overrides)
    return base


def capture(elements):
    return {"received_at": "2026-11-17T00:00:00+00:00", "elements": elements}


class ProbeUiaDiffTests(unittest.TestCase):
    def run_probe(self, capture_a, capture_b):
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "a.json"
            path_b = Path(directory) / "b.json"
            path_a.write_text(json.dumps(capture_a), encoding="utf-8")
            path_b.write_text(json.dumps(capture_b), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(SCRIPT), str(path_a), str(path_b)],
                capture_output=True,
                text=True,
                encoding="utf-8-sig",
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_reports_toggle_state_change(self):
        before = capture([element("プレイ/ポーズ", toggle_state="Off")])
        after = capture([element("プレイ/ポーズ", toggle_state="On")])
        output = self.run_probe(before, after)
        self.assertIn("プレイ/ポーズ", output)
        self.assertIn("toggle_state", output)
        self.assertIn("'Off'", output)
        self.assertIn("'On'", output)

    def test_identical_captures_report_no_differences(self):
        snapshot = capture([element("BEAT SYNC"), element("プレイ/ポーズ")])
        output = self.run_probe(snapshot, snapshot)
        self.assertIn("no differences", output)

    def test_runtime_id_changes_are_ignored(self):
        before = capture([element("BEAT SYNC", runtime_id=[1])])
        after = capture([element("BEAT SYNC", runtime_id=[9, 9])])
        output = self.run_probe(before, after)
        self.assertIn("no differences", output)

    def test_element_only_in_second_capture_is_reported(self):
        before = capture([element("BEAT SYNC")])
        after = capture([element("BEAT SYNC"), element("CUE", rect={"x": 1, "y": 2, "width": 3, "height": 4})])
        output = self.run_probe(before, after)
        self.assertIn("CUE", output)
        self.assertIn("only in second", output)


if __name__ == "__main__":
    unittest.main()
