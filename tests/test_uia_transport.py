"""Exercise real pipes and PowerShell framing without a Rekordbox window."""

import json
import subprocess
import sys
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from rekordbox_sync.sources.uia.resident import UiaResidentSource, _ResidentProcess


class ResidentTransportTests(unittest.TestCase):
    def make_process(self, program):
        popen = subprocess.Popen

        def spawn(command, **kwargs):
            self.assertIn("-Serve", command)
            self.assertTrue(Path(command[command.index("-File") + 1]).is_file())
            return popen([sys.executable, "-u", "-c", program], **kwargs)

        with patch("rekordbox_sync.sources.uia.resident.subprocess.Popen", side_effect=spawn):
            process = _ResidentProcess()
        self.addCleanup(process.terminate)
        return process

    def test_utf8_lines_and_eof(self):
        process = self.make_process(
            "import sys; sys.stdin.reconfigure(encoding='utf-8'); "
            "sys.stdout.reconfigure(encoding='utf-8'); "
            "print(sys.stdin.readline().strip()); print('second')"
        )
        process.write_line("日本語の曲")
        self.assertEqual(process.read_line(5), "日本語の曲")
        self.assertEqual(process.read_line(5), "second")
        with self.assertRaises(EOFError):
            process.read_line(5)

    def test_timeout_reaps_child_and_reader(self):
        process = self.make_process("import time; time.sleep(60)")
        source = UiaResidentSource(timeout_seconds=0.05, process_factory=lambda: process)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            source.read()
        self.assertFalse(process.is_alive())
        self.assertFalse(process._reader.is_alive())
        source.close()

    def test_invalid_utf8_is_failure_and_reaps_child(self):
        process = self.make_process(
            "import sys, time; sys.stdin.readline(); "
            "sys.stdout.buffer.write(b'\\xff\\n'); sys.stdout.buffer.flush(); time.sleep(60)"
        )
        source = UiaResidentSource(process_factory=lambda: process)
        with self.assertRaises(RuntimeError):
            source.read()
        self.assertFalse(process.is_alive())
        self.assertFalse(process._reader.is_alive())


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell required")
class CaptureProtocolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self._directory = Path(directory.name)
        self._script = self._directory / "capture.ps1"
        # Replace only the UI enumeration with a deterministic fake. The real
        # script's serving, serialization and diagnostic-file paths run intact.
        script = files("rekordbox_sync.sources.uia").joinpath("capture.ps1").read_text(encoding="utf-8")
        start = script.index("function Get-UiaCapture {")
        end = script.index("if ($Serve) {", start)
        fake = """function Get-UiaCapture {
    param([string]$CaptureLabel)
    if ($CaptureLabel -eq 'fail') { throw 'capture failed' }
    return [ordered]@{
        received_at = '2026-11-17T00:00:00Z'
        label = $CaptureLabel
        elements = @(@{ name = '日本語の曲'; index = 0 })
    }
}

"""
        self._script.write_text(script[:start] + fake + script[end:], encoding="utf-8-sig")

    def run_capture(self, *args, request=None):
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", str(self._script), *args],
            input=request,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=self._directory,
        )

    def test_serve_recovers_and_matches_stdout_without_writing_files(self):
        result = self.run_capture("-Serve", request='{"label":"fail"}\ninvalid\n{"label":"日本語"}\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines[:2]:
            payload = json.loads(line)
            self.assertTrue(payload["error"])
            self.assertEqual(payload["elements"], [])
            self.assertEqual(payload["capture_mode"], "full")
        single = self.run_capture("-StdoutJson", "-Label", "日本語")
        self.assertEqual(single.returncode, 0, single.stderr)
        served = json.loads(lines[2])
        self.assertEqual(served.pop("capture_mode"), "full")
        self.assertEqual(served, json.loads(single.stdout))
        self.assertEqual(json.loads(lines[2])["elements"][0]["name"], "日本語の曲")
        self.assertFalse((self._directory / "logs").exists())

    def test_diagnostics_overwrite_latest_and_history_is_opt_in(self):
        output = self._directory / "logs"
        for label in ("first", "second"):
            result = self.run_capture("-Label", label)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Saved 1 elements:", result.stdout)
        self.assertEqual([p.name for p in output.iterdir()], ["latest_uia.json"])
        latest = json.loads((output / "latest_uia.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(latest["label"], "second")
        result = self.run_capture("-KeepHistory")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(output.glob("*.json"))), 2)
