import csv
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from rekordbox_sync import DeckObservation, DeckState
from rekordbox_sync.api.http import HttpServer
from rekordbox_sync.state_store import StateStore
from song_catalog import CatalogError, CsvSongCatalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "songs.csv"
        self.write([["曲名", "fhána", "作品, OP\nコメント", "001"]])
        self.catalog = CsvSongCatalog(self.path)

    def write(self, rows, headers=None):
        with self.path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers or ["タイトル", "歌手", "TVA", "識別番号"])
            writer.writerows(rows)

    def test_lookup_projection_and_unicode(self):
        result = self.catalog.lookup(" 曲名 ", "fha\u0301na", fields=["TVA"])
        self.assertEqual(result.status, "matched")
        self.assertEqual(dict(result.fields), {"TVA": "作品, OP\nコメント"})
        self.assertEqual(self.catalog.row_count, 1)
        self.assertEqual(dict(self.catalog.lookup("曲名", "fhána").fields)["識別番号"], "001")

    def test_no_fuzzy_match_and_ambiguous_duplicates(self):
        self.assertEqual(self.catalog.lookup("曲名 (Remix)", "fhána").status, "not_found")
        self.assertEqual(self.catalog.lookup("曲名", "FHÁNA").status, "not_found")
        self.write([["曲名", "fhána", "A", "1"], ["曲名", "fhána", "B", "2"]])
        result = self.catalog.lookup("曲名", "fhána")
        self.assertEqual((result.status, result.match_count, result.fields), ("ambiguous", 2, ()))

    def test_reload_and_failed_reload_never_returns_cached_match(self):
        self.catalog.lookup("曲名", "fhána")
        self.write([["曲名", "fhána", "Updated value", "001"]])
        self.assertEqual(dict(self.catalog.lookup("曲名", "fhána").fields)["TVA"], "Updated value")
        self.write([["wrong"]], headers=["missing"])
        with self.assertRaises(CatalogError):
            self.catalog.lookup("曲名", "fhána")
        self.path.unlink()
        with self.assertRaises(CatalogError):
            self.catalog.lookup("曲名", "fhána")
        self.write([["曲名", "fhána", "recovered", "001"]])
        self.assertEqual(self.catalog.lookup("曲名", "fhána").status, "matched")

    def test_invalid_fields_and_rows(self):
        for fields in (["missing"], ["TVA", "TVA"], "TVA"):
            with self.assertRaises(ValueError):
                self.catalog.lookup("曲名", "fhána", fields=fields)
        for rows in ([["曲名", "fhána"]], [["", "fhána", "A", "1"]]):
            self.write(rows)
            with self.assertRaises(CatalogError):
                self.catalog.reload()

    def test_http_lookup_and_enrichment_preserve_original_state(self):
        store = StateStore(60)
        store.update_decks(
            DeckObservation(
                (DeckState("1", True, "曲名", "fhána"), DeckState("2", True, "missing", "artist")), observed_at="test"
            )
        )
        with HttpServer(store, port=0, catalog=self.catalog) as server:
            base = f"http://127.0.0.1:{server.address[1]}"
            query = urlencode({"title": "曲名", "artist": "fhána", "field": "TVA"})
            with urlopen(base + "/song-info?" + query) as response:
                self.assertEqual(json.load(response)["status"], "matched")
            with urlopen(base + "/state/enriched?field=TVA") as response:
                result = json.load(response)
            self.assertEqual(result["song_info"]["1"]["fields"], {"TVA": "作品, OP\nコメント"})
            self.assertEqual(result["song_info"]["2"]["status"], "not_found")
            with urlopen(base + "/state") as response:
                self.assertNotIn("song_info", json.load(response))
            with self.assertRaises(HTTPError) as raised:
                urlopen(base + "/song-info")
            self.assertEqual(raised.exception.code, 400)
            self.path.unlink()
            with self.assertRaises(HTTPError) as raised:
                urlopen(base + "/state/enriched")
            self.assertEqual(raised.exception.code, 503)

    def test_no_catalog_is_explicitly_unavailable(self):
        with HttpServer(StateStore(60), port=0) as server:
            with self.assertRaises(HTTPError) as raised:
                urlopen(f"http://127.0.0.1:{server.address[1]}/state/enriched")
            self.assertEqual(raised.exception.code, 503)
