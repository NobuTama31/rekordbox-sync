"""Pin locale-independent test I/O, including child output with a UTF-8 BOM."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_probe_uia_diff as probe_tests
import test_regression


class TestEncodingTests(unittest.TestCase):
    def test_packaged_resource_test_uses_explicit_encoding(self):
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path.name == "capture.ps1":
                self.assertIn("encoding", kwargs)
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            test_regression.ParserTests("test_resources_packaged").test_resources_packaged()

    def test_probe_harness_decodes_japanese_with_and_without_bom(self):
        expected = "\u30d7\u30ec\u30a4/\u30dd\u30fc\u30ba\n"
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "output.py"
            for encoding in ("utf-8", "utf-8-sig"):
                with self.subTest(encoding=encoding):
                    script.write_text(
                        "import sys\nsys.stdout.buffer.write(bytes.fromhex('"
                        + expected.encode(encoding).hex()
                        + "'))\n",
                        encoding="utf-8",
                    )
                    with patch.object(probe_tests, "SCRIPT", script):
                        actual = probe_tests.ProbeUiaDiffTests().run_probe({}, {})
                    self.assertEqual(actual, expected)
