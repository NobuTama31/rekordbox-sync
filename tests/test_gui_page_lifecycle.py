"""Exercise page ownership without starting Tk or a network worker."""

import importlib.util
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch


@unittest.skipUnless(importlib.util.find_spec("customtkinter"), "Optional GUI dependency")
class PageLifecycleTests(unittest.TestCase):
    def setUp(self):
        from rekordbox_sync.gui import app

        self.module = app
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.factories = []
        for name in ("CTkFrame", "CTkButton", "CTkTextbox"):
            self.factories.append(
                self.stack.enter_context(patch.object(app.ctk, name, side_effect=lambda *a, **k: Mock()))
            )
        for name in ("font", "label", "DeckCard", "MixerCard"):
            self.stack.enter_context(patch.object(app, name, side_effect=lambda *a, **k: Mock()))
        self.stack.enter_context(patch.object(app, "_package_version", return_value=""))
        self.window = SimpleNamespace(
            _api_url="http://localhost:8765", _artwork=None, _payload={"state": {"decks": {}}}, _closing=False
        )
        for name in (
            "grid_columnconfigure",
            "grid_rowconfigure",
            "_start_api",
            "_stop_api",
            "_open_settings",
            "_toggle_page",
            "_copy_json",
            "_render_json",
            "_status_segment",
            "_status_separator",
            "clipboard_clear",
            "clipboard_append",
            "after",
        ):
            setattr(self.window, name, Mock())

    def test_build_owns_one_json_page_through_repeated_notices_and_switches(self):
        app, window = self.module.MonitorApp, self.window
        app._build(window)
        self.assertTrue(hasattr(window, "_json_page"), "JSON page must exist before any notice")
        page, textbox = window._json_page, window._json_text
        counts = [factory.call_count for factory in self.factories]
        app._show_page(window, "json")
        for _ in range(101):
            app._set_notice(window, "updated")
        self.assertEqual([factory.call_count for factory in self.factories], counts)
        self.assertIs(window._json_page, page)
        self.assertIs(window._json_text, textbox)
        app._show_page(window, "monitor")
        page.grid_remove.assert_called()
        app._show_page(window, "json")
        page.grid.assert_called()
        window._render_json.assert_called()

    def test_initial_json_render_and_copy_use_the_built_widgets(self):
        app, window = self.module.MonitorApp, self.window
        app._build(window)
        self.assertTrue(hasattr(window, "_json_text"))
        window._json_text.yview.return_value = (0.2, 0.8)
        window._json_text.xview.return_value = (0.1, 0.9)
        app._render_json(window)
        window._json_text.insert.assert_called_once()
        window._json_text.yview.assert_called_with("moveto", 0.2)
        app._copy_json(window)
        window.clipboard_append.assert_called_once()
        self.assertIn('"decks"', window.clipboard_append.call_args.args[0])
