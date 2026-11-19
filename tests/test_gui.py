import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rekordbox_sync.api.http import HttpServer
from rekordbox_sync.gui.artwork import ArtworkLoader
from rekordbox_sync.gui.client import ApiClient, validate_api_url
from rekordbox_sync.gui.controller import LocalApiController
from rekordbox_sync.gui.presentation import (
    artwork_url,
    format_bpm,
    format_tempo_percent,
    metadata_text,
)
from rekordbox_sync.state_store import StateStore


class PresentationTests(unittest.TestCase):
    def test_unknown_is_not_zero(self):
        self.assertEqual(format_bpm(None), "—")
        self.assertEqual(format_bpm(0), "0.00")

    def test_tempo_percent(self):
        self.assertEqual(format_tempo_percent(182.0, 175.0), "+4.00%")
        self.assertEqual(format_tempo_percent(171.5, 175.0), "-2.00%")
        self.assertEqual(format_tempo_percent(175.0, 175.0), "±0.00%")
        self.assertEqual(format_tempo_percent(None, 175.0), "—")
        self.assertEqual(format_tempo_percent(182.0, None), "—")
        self.assertEqual(format_tempo_percent(182.0, 0), "—")

    def test_metadata_and_invalid_url(self):
        self.assertEqual(metadata_text({"status": "matched", "fields": {"TVA": "作品OP"}}), "作品OP")
        self.assertIn("一致", metadata_text({"status": "not_found"}))
        self.assertEqual(validate_api_url(" http://127.0.0.1:8765/ "), "http://127.0.0.1:8765")
        for url in ("file:///tmp", "http://user:pass@localhost", "http://localhost/state", "http://localhost:bad"):
            with self.assertRaises(ValueError):
                validate_api_url(url)

    def test_artwork_url(self):
        self.assertEqual(artwork_url({"status": "matched", "artwork_url": "https://x/a.jpg"}), "https://x/a.jpg")
        self.assertEqual(
            artwork_url(
                {
                    "status": "ambiguous",
                    "candidates": [{"artwork_url": "https://x/first.jpg"}, {"artwork_url": "https://x/second.jpg"}],
                }
            ),
            "https://x/first.jpg",
        )
        self.assertIsNone(artwork_url({"status": "ambiguous", "candidates": []}))
        self.assertIsNone(artwork_url({"status": "pending"}))
        self.assertIsNone(artwork_url({"status": "not_found"}))
        self.assertIsNone(artwork_url(None))


class ArtworkLoaderTests(unittest.TestCase):
    def test_fetch_cache_and_failure(self):
        import io
        import time

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (10, 6), (255, 0, 0)).save(buffer, format="PNG")
        png = buffer.getvalue()
        calls = []

        def opener(url, timeout=None):
            calls.append(url)
            if "bad" in url:
                raise OSError("offline")
            return io.BytesIO(png)

        loader = ArtworkLoader(opener=opener)
        try:
            self.assertIsNone(loader.image_for("https://x/a.jpg"))
            deadline = time.monotonic() + 5
            image = None
            while image is None and time.monotonic() < deadline:
                image = loader.image_for("https://x/a.jpg")
                time.sleep(0.02)
            self.assertIsNotNone(image)
            self.assertEqual(image.size, (184, 184))
            self.assertIs(loader.image_for("https://x/a.jpg"), image)
            self.assertEqual(calls, ["https://x/a.jpg"])
            self.assertIsNone(loader.image_for("https://x/bad.jpg"))
            time.sleep(0.3)
            self.assertIsNone(loader.image_for("https://x/bad.jpg"))
            self.assertEqual(calls.count("https://x/bad.jpg"), 1)
            self.assertIsNone(loader.image_for("file:///etc/passwd"))
        finally:
            loader.stop()


class ClientTests(unittest.TestCase):
    def test_catalog_fallback_and_external_server_ownership(self):
        store = StateStore(60)
        with HttpServer(store, port=0) as server:
            url = f"http://127.0.0.1:{server.address[1]}"
            client = ApiClient(url)
            update = client.fetch(url)
            self.assertIsNotNone(update.payload)
            self.assertIsNotNone(update.metadata_error)
            controller = LocalApiController()
            self.assertFalse(controller.start(url))
            self.assertFalse(controller.owned)
            controller.stop()
            self.assertIsNotNone(client.fetch(url).payload)

    def test_offline_clears_payload(self):
        client = ApiClient("http://127.0.0.1:1")
        with patch.object(client, "_read", side_effect=OSError("offline")):
            update = client.fetch("http://127.0.0.1:1")
        self.assertIsNone(update.payload)
        self.assertIn("offline", update.error)

    def test_background_client_stops(self):
        with HttpServer(StateStore(60), port=0) as server:
            client = ApiClient(f"http://127.0.0.1:{server.address[1]}")
            client.start()
            client.stop()
            self.assertFalse(client._thread.is_alive())

    def test_owned_api_lifecycle(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        controller = LocalApiController()
        with patch("rekordbox_sync.gui.controller.RekordboxMonitor") as monitor_type:
            monitor_type.return_value.snapshot.side_effect = StateStore(60).snapshot
            try:
                self.assertTrue(controller.start(f"http://127.0.0.1:{port}"))
                self.assertTrue(controller.owned)
                monitor_type.return_value.start.assert_called_once()
            finally:
                controller.stop()
            monitor_type.return_value.stop.assert_called_once()
            self.assertFalse(controller.owned)

    def test_owned_api_uses_metadata_db_env_var(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "nested" / "meta.db"
            controller = LocalApiController()
            with (
                patch.dict(os.environ, {"REKORDBOX_SYNC_METADATA_DB": str(db_path)}),
                patch("rekordbox_sync.gui.controller.RekordboxMonitor") as monitor_type,
            ):
                monitor_type.return_value.snapshot.side_effect = StateStore(60).snapshot
                try:
                    self.assertTrue(controller.start(f"http://127.0.0.1:{port}"))
                finally:
                    controller.stop()
            self.assertTrue(db_path.exists())
