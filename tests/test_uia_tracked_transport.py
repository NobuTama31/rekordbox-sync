"""Run the actual PowerShell tracked path against a deterministic UIA provider."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path

from test_tracked_selection import FIXTURES, tracked_subset

from rekordbox_sync.sources.uia.deck_parser import parse_snapshot


def fixture(name="tempo_changed"):
    snapshot = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["input"]
    for index, element in enumerate(snapshot["elements"]):
        element["index"] = index
    return snapshot


def frame(snapshot, **options):
    return dict(elements=copy.deepcopy(snapshot["elements"]), **options)


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell required")
class TrackedCaptureProtocolTests(unittest.TestCase):
    def run_frames(self, frames, *, serve=True):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "frames.json").write_text(json.dumps(frames), encoding="utf-8")
            script = files("rekordbox_sync.sources.uia").joinpath("capture.ps1").read_text(encoding="utf-8")
            provider = (FIXTURES / "fake_uia_provider.ps1").read_text(encoding="utf-8")
            # Keep Get-UiaCapture intact, replacing only access to the OS provider.
            script = script.replace("function Get-UiaCapture {", provider + "\nfunction Get-UiaCapture {", 1)
            script = script.replace(
                "Get-Process rekordbox | Select-Object -First 1", "[pscustomobject]@{ Id = 123; Path = 'fake.exe' }"
            )
            root_lookup = "$root = Get-UiaRoot -ProcessId $rbProcess.Id"
            self.assertIn(root_lookup, script)
            script = script.replace(root_lookup, "$root = $script:fakeRoot")
            request = "$result = Get-ResidentUiaCapture -CaptureLabel $requestLabel"
            self.assertIn(request, script)
            script = script.replace(request, "Set-FakeFrame -Index ([int]$requestLabel)\n            " + request)
            path = directory / "capture.ps1"
            path.write_text(script, encoding="utf-8-sig")
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", str(path), "-Serve" if serve else "-StdoutJson"],
                input="".join(json.dumps({"label": str(i)}) + "\n" for i in range(len(frames))),
                capture_output=True,
                encoding="utf-8",
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=directory,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payloads = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(payloads), len(frames) if serve else 1, result.stderr)
            self.assertFalse((directory / "logs").exists())
            return payloads, result.stderr

    def assert_modes(self, payloads, modes):
        self.assertEqual([p.get("capture_mode") for p in payloads], modes, payloads)

    def assert_walks(self, payloads, counts):
        self.assertEqual([int(p["window_name"].split("walks=")[1]) for p in payloads], counts)

    def test_powershell_selection_and_parser_match_every_recorded_capture(self):
        for path in sorted(FIXTURES.glob("*.json")):
            with self.subTest(fixture=path.name):
                snapshot = fixture(path.stem)
                payloads, stderr = self.run_frames([frame(snapshot), frame(snapshot)])
                self.assertEqual(stderr, "")
                self.assert_modes(payloads, ["full", "tracked"])
                self.assert_walks(payloads, [1, 1])
                self.assertEqual(
                    [e["index"] for e in payloads[1]["elements"]], [e["index"] for e in tracked_subset(snapshot)]
                )
                for payload in payloads:
                    payload["received_at"] = snapshot["received_at"]
                    self.assertEqual(parse_snapshot(payload), parse_snapshot(snapshot))
                # Tracked rows have the same fields and ordering as the full rows.
                self.assertEqual(payloads[1]["elements"], tracked_subset(payloads[0]))

    def test_stable_text_change_rewalks_and_refreshes_references(self):
        snapshot = fixture()
        changed = frame(snapshot)
        title = next(e for e in tracked_subset(changed) if e["type"] == "ControlType.Text" and e["rect"]["width"] > 150)
        title["name"] = "New title 日本語"
        payloads, stderr = self.run_frames([frame(snapshot), frame(snapshot), changed, changed])
        self.assertEqual(stderr, "")
        self.assert_modes(payloads, ["full", "tracked", "full", "tracked"])
        self.assert_walks(payloads, [1, 1, 2, 2])
        self.assertIn("New title 日本語", [e["name"] for e in payloads[3]["elements"]])

    def test_volatile_updates_stay_tracked_and_values_are_fresh(self):
        snapshot = fixture()
        base = frame(snapshot)
        selected = tracked_subset(base)
        texts = [e for e in selected if e["type"] == "ControlType.Text"][:4]
        for element, text in zip(texts, ["1:23", "-4:56.7", ".8", " 128.00 "]):
            element["name"] = text
        edit = next(e for e in selected if e["type"] == "ControlType.Edit")
        edit["name"], edit["value"] = "BPM", "128.00"
        edit["toggle_state"] = "Off"
        changed = copy.deepcopy(base)
        for element, text in zip(texts, ["2:34", "-3:45.6", ".9", " 130.00 "]):
            changed["elements"][element["index"]]["name"] = text
        new_edit = changed["elements"][edit["index"]]
        new_edit.update(value="130.00", toggle_state="On", automation_id="fresh-id")
        new_edit["rect"]["x"] += 1
        payloads, stderr = self.run_frames([base, changed, base])
        self.assertEqual(stderr, "")
        self.assert_modes(payloads, ["full", "tracked", "tracked"])
        self.assert_walks(payloads, [1, 1, 1])
        actual = next(e for e in payloads[1]["elements"] if e["index"] == edit["index"])
        for key in ("name", "value", "toggle_state", "automation_id", "rect"):
            self.assertEqual(actual[key], new_edit[key])

    def test_stale_reference_falls_back_in_same_request(self):
        snapshot = fixture()
        index = tracked_subset(snapshot)[0]["index"]
        payloads, stderr = self.run_frames(
            [frame(snapshot), frame(snapshot), frame(snapshot, stale_index=index), frame(snapshot)]
        )
        self.assertEqual(stderr, "")
        self.assert_modes(payloads, ["full", "tracked", "full", "tracked"])
        self.assert_walks(payloads, [1, 1, 2, 2])

    def test_stable_and_volatile_transitions_are_conservative(self):
        snapshot = fixture()
        index = next(e["index"] for e in tracked_subset(snapshot) if e["type"] == "ControlType.Text")
        frames = []
        for text in ("TITLE", "title", "128.00", "130.00", "", "1:23", "2:34", "2:34 extra"):
            changed = frame(snapshot)
            changed["elements"][index]["name"] = text
            frames.append(changed)
        payloads, stderr = self.run_frames(frames)
        self.assertEqual(stderr, "")
        self.assert_modes(payloads, ["full", "full", "full", "tracked", "full", "full", "tracked", "full"])
        self.assert_walks(payloads, [1, 2, 3, 3, 4, 5, 5, 6])

    def test_offscreen_change_and_resync_deadline(self):
        snapshot = fixture()
        changed = frame(snapshot)
        next(e for e in tracked_subset(changed) if e["type"] == "ControlType.Text")["offscreen"] = True
        payloads, stderr = self.run_frames(
            [frame(snapshot), changed, dict(changed, elapsed_seconds=59.9), dict(changed, elapsed_seconds=60), changed]
        )
        self.assertEqual(stderr, "")
        self.assert_modes(payloads, ["full", "full", "tracked", "full", "tracked"])
        self.assert_walks(payloads, [1, 2, 2, 3, 3])

    def test_no_anchors_rewalks_then_tracks_second_walk_references(self):
        snapshot = fixture()
        payloads, stderr = self.run_frames([frame(snapshot, empty_walks=1), frame(snapshot)])
        self.assertEqual(stderr, "")
        self.assert_modes(payloads, ["full", "tracked"])
        self.assert_walks(payloads, [2, 2])
        self.assertEqual(payloads[1]["elements"], tracked_subset(payloads[0]))

    def test_unlocated_keeps_full_walking_and_recovers(self):
        snapshot = fixture()
        payloads, stderr = self.run_frames([{"elements": []}, {"elements": []}, frame(snapshot), frame(snapshot)])
        self.assertEqual(stderr, "")
        self.assert_modes(payloads, ["full", "full", "full", "tracked"])
        self.assert_walks(payloads, [2, 4, 5, 5])

    def test_failed_fallback_is_error_and_next_request_relocates(self):
        snapshot = fixture()
        index = tracked_subset(snapshot)[0]["index"]
        payloads, stderr = self.run_frames(
            [frame(snapshot), frame(snapshot, stale_index=index, fail_full=True), frame(snapshot), frame(snapshot)]
        )
        self.assertIn("full walk failed", stderr)
        self.assert_modes(payloads, ["full", "full", "full", "tracked"])
        self.assertEqual(payloads[1]["elements"], [])
        self.assertIn("full walk failed", payloads[1]["error"])
        self.assert_walks([payloads[i] for i in (0, 2, 3)], [1, 3, 3])

    def test_one_shot_still_returns_full_capture_without_mode(self):
        snapshot = fixture()
        payloads, stderr = self.run_frames([frame(snapshot)], serve=False)
        self.assertEqual(stderr, "")
        self.assertNotIn("capture_mode", payloads[0])
        self.assertEqual(len(payloads[0]["elements"]), len(snapshot["elements"]))
