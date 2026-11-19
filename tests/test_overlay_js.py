"""Run the Node harness for the overlay's artwork retry (audit #6).

Skips cleanly when Node.js is not available."""

import shutil
import subprocess
import unittest
from pathlib import Path

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is not available")
class OverlayArtworkRetryTests(unittest.TestCase):
    def test_artwork_retry_survives_304_responses(self):
        harness = Path(__file__).with_name("overlay_artwork_retry.js")
        result = subprocess.run([NODE, str(harness)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
