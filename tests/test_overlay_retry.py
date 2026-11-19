"""Node is optional; no browser or JavaScript package dependencies required."""

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class OverlayRetryTests(unittest.TestCase):
    def test_artwork_retry_with_conditional_http_responses(self):
        tests = Path(__file__).parent
        result = subprocess.run(
            [
                shutil.which("node"),
                str(tests / "fixtures" / "overlay_retry.cjs"),
                str(tests.parent / "src/rekordbox_sync/api/overlay/overlay.js"),
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
