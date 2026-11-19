"""Stage 4 of the 2026-11-18 audit: small optimizations.

#9: Fader.set() must not redraw when the clamped value and the lit
state are unchanged; resize (<Configure>) redraws still happen because
they call _draw() directly.
"""

import unittest
from unittest.mock import Mock

try:
    from rekordbox_sync.gui.widgets import Fader
except ImportError:  # Optional GUI dependency
    Fader = None


@unittest.skipUnless(Fader is not None, "Optional GUI dependency")
class FaderRedrawTests(unittest.TestCase):
    def _fader(self):
        fader = Fader.__new__(Fader)
        fader._orientation = "vertical"
        fader._value = 0.0
        fader._lit = False
        fader.delete = Mock()
        fader.winfo_width = lambda: 44
        fader.winfo_height = lambda: 200
        fader.create_line = Mock()
        fader.create_polygon = Mock()
        return fader

    def test_set_skips_redraw_when_value_and_lit_are_unchanged(self):
        fader = self._fader()
        fader.set(0.5)
        self.assertEqual(fader.delete.call_count, 1)
        for _ in range(100):
            fader.set(0.5)
        self.assertEqual(fader.delete.call_count, 1)
        fader.set(0.5, lit=False)  # lit change redraws
        self.assertEqual(fader.delete.call_count, 2)
        fader.set(0.7)  # value change redraws
        self.assertEqual(fader.delete.call_count, 3)
        fader._draw()  # the <Configure> path always redraws
        self.assertEqual(fader.delete.call_count, 4)
        fader.set(0.7)  # still settled after the configure redraw
        self.assertEqual(fader.delete.call_count, 4)


if __name__ == "__main__":
    unittest.main()
