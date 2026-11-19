"""Pin the tracked-element selection rule of docs/resident_uia.md (v2).

The resident capture's fast path re-reads only a positional superset of
the elements the parser interprets. This reference implementation of the
selection rule must, on every recorded capture, produce a subset whose
parse is identical to the full capture's parse. capture.ps1 implements
the same rule on the PowerShell side; if the rule ever changes, change
both together.
"""

import json
import unittest
from pathlib import Path

from rekordbox_sync.sources.uia.deck_parser import parse_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def tracked_subset(snapshot):
    elements = snapshot["elements"]
    visible = [e for e in elements if not e["offscreen"]]
    anchors = sorted(
        [e for e in visible if (e.get("name") or "").replace("\n", " ") == "BEAT SYNC"], key=lambda e: e["rect"]["x"]
    )
    plays = sorted([e for e in visible if e["name"] in ("プレイ/ポーズ", "Play/Pause")], key=lambda e: e["rect"]["x"])
    if len(anchors) != 2 or len(plays) != 2:
        return None
    top = min(a["rect"]["y"] for a in anchors) - 16
    bottom = max(p["rect"]["y"] + p["rect"]["height"] for p in plays) + 40
    keep = []
    for element in visible:
        if element in anchors or element in plays:
            keep.append(element)
        elif element["type"] in ("ControlType.Text", "ControlType.Edit") and top <= element["rect"]["y"] <= bottom:
            keep.append(element)
    return keep


class TrackedSelectionTests(unittest.TestCase):
    def test_tracked_subset_parses_identically_on_fixtures(self):
        for path in sorted(FIXTURES.glob("*.json")):
            sample = json.loads(path.read_text(encoding="utf-8"))
            snapshot = sample["input"]
            subset = tracked_subset(snapshot)
            self.assertIsNotNone(subset, path.name)
            self.assertLess(len(subset), len(snapshot["elements"]) // 3, path.name)
            self.assertEqual(parse_snapshot(dict(snapshot, elements=subset)), parse_snapshot(snapshot), path.name)


if __name__ == "__main__":
    unittest.main()
