import json
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rekordbox_sync.api.http import HttpServer
from rekordbox_sync.metadata_service import (
    DEFAULT_DB_PATH,
    TrackMetadataService,
    resolve_metadata_db_path,
)
from rekordbox_sync.models import DeckObservation, DeckState
from rekordbox_sync.state_store import StateStore
from track_metadata import (
    ItunesSearchProvider,
    MetadataStore,
    match_candidates,
    query_terms,
)
from track_metadata.provider import Candidate, parse_itunes_results


def _candidate(track_id, title, artist, **kwargs):
    return Candidate(provider="itunes", provider_track_id=str(track_id), title=title, artist=artist, **kwargs)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self):
        return self._payload


ITUNES_JSON = json.dumps(
    {
        "resultCount": 3,
        "results": [
            {
                "wrapperType": "track",
                "trackId": 111,
                "trackName": "リスポーン!!",
                "artistName": "角巻わため",
                "collectionName": "リスポーン!! - Single",
                "releaseDate": "2024-05-01T07:00:00Z",
                "artworkUrl100": "https://example.com/image/100x100bb.jpg",
            },
            {
                "wrapperType": "track",
                "trackId": 222,
                "trackName": "別の曲",
                "artistName": "別人",
                "collectionName": "X",
                "releaseDate": "2020-01-01T07:00:00Z",
                "artworkUrl100": "https://example.com/y/100x100bb.jpg",
            },
            {"wrapperType": "collection", "collectionId": 999},
        ],
    }
).encode()


class ProviderTests(unittest.TestCase):
    def test_parse_results_and_artwork_upscale(self):
        candidates = parse_itunes_results(json.loads(ITUNES_JSON))
        self.assertEqual(len(candidates), 2)
        first = candidates[0]
        self.assertEqual(first.provider_track_id, "111")
        self.assertEqual(first.release_date, "2024-05-01")
        self.assertEqual(first.artwork_url, "https://example.com/image/600x600bb.jpg")

    def test_search_uses_injected_opener(self):
        seen = {}

        def opener(url, timeout=None):
            seen["url"] = url
            return _Response(ITUNES_JSON)

        provider = ItunesSearchProvider(opener=opener)
        candidates = provider.search("リスポーン!!", "角巻わため")
        self.assertEqual(len(candidates), 2)
        self.assertIn("country=JP", seen["url"])


class MatchingTests(unittest.TestCase):
    def test_exact_match(self):
        result = match_candidates(
            "リスポーン!!", "角巻わため", (_candidate(1, "リスポーン!!", "角巻わため"), _candidate(2, "別の曲", "別人"))
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.match.provider_track_id, "1")

    def test_decorated_query_title_matches(self):
        result = match_candidates(
            "リスポーン!! (TV Size)", "角巻わため", (_candidate(1, "リスポーン!!", "角巻わため"),)
        )
        self.assertEqual(result.status, "matched")

    def test_ambiguous_is_not_reduced(self):
        result = match_candidates("曲", "歌手", (_candidate(1, "曲", "歌手"), _candidate(2, "曲", "歌手")))
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.match)
        self.assertEqual(len(result.candidates), 2)

    def test_instrumental_candidate_is_a_different_track(self):
        result = match_candidates("曲 (TV Size)", "歌手", (_candidate(1, "曲 (Instrumental)", "歌手"),))
        self.assertEqual(result.status, "not_found")
        result = match_candidates(
            "曲", "歌手", (_candidate(1, "曲", "歌手"), _candidate(2, "曲 (Instrumental)", "歌手"))
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.match.provider_track_id, "1")

    def test_notation_variants_match(self):
        cases = [
            ("Q & A", "B'z", "Q&A", "B'z"),
            ("LEVEL5 -judgelight-", "fripSide", "LEVEL5-judgelight-", "fripSide"),
            ("Takin' my Heart", "RAISE A SUILEN", "Takin\u2019 my Heart", "RAISE A SUILEN"),
            ("君の神話 ～ アクエリオン第二章", "AKINO", "君の神話~アクエリオン第二章", "AKINO"),
        ]
        for title, artist, found_title, found_artist in cases:
            result = match_candidates(title, artist, (_candidate(1, found_title, found_artist),))
            self.assertEqual(result.status, "matched", (title, found_title))

    def test_artist_mismatch_is_not_found(self):
        result = match_candidates("曲", "歌手A", (_candidate(1, "曲", "歌手B"),))
        self.assertEqual(result.status, "not_found")

    def test_artist_substring_is_not_a_match(self):
        # Regression: containment ("ai" in "aimer") used to loose-match and the
        # result was then cached permanently as matched.
        result = match_candidates("同じ曲名", "AI", (_candidate(1, "同じ曲名", "Aimer"),))
        self.assertEqual(result.status, "not_found")
        result = match_candidates("同じ曲名", "Aimer", (_candidate(1, "同じ曲名", "AI"),))
        self.assertEqual(result.status, "not_found")

    def test_artist_decoration_still_loose_matches(self):
        result = match_candidates("曲", "歌手 feat. 別人", (_candidate(1, "曲", "歌手"),))
        self.assertEqual(result.status, "matched")
        result = match_candidates("曲", "歌手", (_candidate(1, "曲", "歌手 feat. 別人"),))
        self.assertEqual(result.status, "matched")
        result = match_candidates("曲", "歌手 (Band)", (_candidate(1, "曲", "歌手"),))
        self.assertEqual(result.status, "matched")

    def test_query_terms_strip_decorations(self):
        self.assertEqual(
            query_terms("Secret Dawn", "Morfonica (進藤あまね, 直田姫奈, 西尾夕香, mika, Ayasa)"),
            ("secret dawn", "morfonica"),
        )
        self.assertEqual(query_terms("曲 feat. 別人", "歌手"), ("曲", "歌手"))

    def test_query_terms_none_when_nothing_to_strip(self):
        self.assertIsNone(query_terms("曲", "歌手"))
        self.assertIsNone(query_terms("HALO", "NOMELON NOLEMON"))


class StoreTests(unittest.TestCase):
    def test_roundtrip_and_key_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "meta.db")
            result = match_candidates("曲", "歌手", (_candidate(7, "曲", "歌手", album="盤"),))
            store.put(" 曲 ", "歌手", result)
            loaded = store.get("曲", "歌手")
            self.assertEqual(loaded.status, "matched")
            self.assertEqual(loaded.match.album, "盤")
            self.assertIsNotNone(loaded.fetched_at)
            self.assertEqual(store.count, 1)
            self.assertIsNone(store.get("無い", "誰"))
            store.close()


class _FakeProvider:
    def __init__(self, candidates=(), error=None):
        self._candidates = candidates
        self._error = error
        self.calls = 0

    def search(self, title, artist):
        self.calls += 1
        if self._error:
            raise self._error
        return self._candidates


class _FakeStore:
    def __init__(self):
        self.closed = False
        self._data = {}

    def get(self, title, artist):
        return self._data.get((title, artist))

    def put(self, title, artist, result):
        self._data[(title, artist)] = result
        return result

    def close(self):
        self.closed = True


class _AliveThread:
    """Stands in for a watcher stuck in a provider search past the join timeout."""

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return True


class ResolvePathTests(unittest.TestCase):
    def test_explicit_argument_wins_over_env(self):
        with patch.dict(os.environ, {"REKORDBOX_SYNC_METADATA_DB": "/env/meta.db"}):
            self.assertEqual(resolve_metadata_db_path("/cli/meta.db"), Path("/cli/meta.db"))

    def test_env_var_overrides_default(self):
        with patch.dict(os.environ, {"REKORDBOX_SYNC_METADATA_DB": "/env/meta.db"}):
            self.assertEqual(resolve_metadata_db_path(), Path("/env/meta.db"))

    def test_default_without_env_var(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_metadata_db_path(), DEFAULT_DB_PATH)


class ServiceTests(unittest.TestCase):
    def test_fetch_on_demand_and_no_refetch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "meta.db")
            provider = _FakeProvider((_candidate(3, "曲", "歌手"),))
            service = TrackMetadataService(store, provider)
            self.assertEqual(service.lookup("曲", "歌手")["status"], "pending")
            service._fetch_if_needed("曲", "歌手")
            self.assertEqual(service.lookup("曲", "歌手")["status"], "matched")
            service._fetch_if_needed("曲", "歌手")
            self.assertEqual(provider.calls, 1)
            store.close()

    def test_not_found_retries_with_stripped_query_terms(self):
        class CastProvider:
            def __init__(self):
                self.queries = []

            def search(self, title, artist):
                self.queries.append((title, artist))
                if artist == "morfonica":
                    return (_candidate(1, "Secret Dawn", "Morfonica"),)
                return ()

        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "meta.db")
            provider = CastProvider()
            service = TrackMetadataService(store, provider)
            artist = "Morfonica (進藤あまね, 直田姫奈)"
            service._fetch_if_needed("Secret Dawn", artist)
            self.assertEqual(service.lookup("Secret Dawn", artist)["status"], "matched")
            self.assertEqual(provider.queries, [("Secret Dawn", artist), ("secret dawn", "morfonica")])
            store.close()

    def test_stale_windows_by_status(self):
        from datetime import datetime, timedelta, timezone

        from rekordbox_sync.metadata_service import _is_stale
        from track_metadata import MetadataLookupResult

        now = datetime.now(timezone.utc)

        def result(status, age):
            return MetadataLookupResult(status=status, match=None, candidates=(), fetched_at=(now - age).isoformat())

        self.assertFalse(_is_stale(result("matched", timedelta(days=365)), now))
        self.assertFalse(_is_stale(result("ambiguous", timedelta(days=29)), now))
        self.assertTrue(_is_stale(result("ambiguous", timedelta(days=31)), now))
        self.assertFalse(_is_stale(result("not_found", timedelta(minutes=9)), now))
        self.assertTrue(_is_stale(result("not_found", timedelta(minutes=11)), now))

    def test_provider_error_stores_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "meta.db")
            service = TrackMetadataService(store, _FakeProvider(error=OSError("offline")))
            service._fetch_if_needed("曲", "歌手")
            self.assertIsNone(store.get("曲", "歌手"))
            store.close()

    def test_close_closes_store_once_thread_has_stopped(self):
        store = _FakeStore()
        service = TrackMetadataService(store, _FakeProvider(), poll_interval_seconds=0.05)
        service.start(lambda: SimpleNamespace(decks=()))
        service.close()
        self.assertIsNone(service._thread)
        self.assertTrue(store.closed)

    def test_stop_keeps_live_thread_and_close_leaves_store_open(self):
        store = _FakeStore()
        service = TrackMetadataService(store, _FakeProvider(), poll_interval_seconds=0.05)
        alive = _AliveThread()
        service._thread = alive
        service.stop()
        self.assertIs(service._thread, alive)
        service.close()
        self.assertIs(service._thread, alive)
        self.assertFalse(store.closed)


class HttpTests(unittest.TestCase):
    def test_enriched_includes_track_info_without_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "meta.db")
            store.put("曲", "歌手", match_candidates("曲", "歌手", (_candidate(5, "曲", "歌手"),)))
            service = TrackMetadataService(store, _FakeProvider())
            state = StateStore(60)
            state.update_decks(
                DeckObservation(
                    decks=(DeckState(deck_id="1", loaded=True, title="曲", artist="歌手"),),
                    observed_at="2026-11-16T00:00:00+00:00",
                )
            )
            with HttpServer(state, port=0, track_metadata=service) as server:
                url = f"http://127.0.0.1:{server.address[1]}/state/enriched"
                with urllib.request.urlopen(url, timeout=5) as response:
                    payload = json.load(response)
            self.assertEqual(payload["track_info"]["1"]["status"], "matched")
            self.assertEqual(payload["song_info"]["1"]["status"], "track_unavailable")
            store.close()

    def test_enriched_still_503_without_any_metadata_source(self):
        with HttpServer(StateStore(60), port=0) as server:
            url = f"http://127.0.0.1:{server.address[1]}/state/enriched"
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(caught.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
