"""Stage 3 of the 2026-11-18 audit: display and retry semantics.

#5: a delayed older /nowplaying request must not roll the presenter's
held payload or selection state back after a newer request committed.
#7: a provider failure in the follow-up search is a provider error,
not a negative result — nothing is stored and the 60-second error
backoff applies.
"""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_nowplaying import _deck, _matched_catalog, _matched_metadata, _snapshot

import rekordbox_sync.api.nowplaying as nowplaying
from rekordbox_sync.api.nowplaying import NowPlayingPresenter
from rekordbox_sync.metadata_service import TrackMetadataService
from track_metadata import MetadataStore


class NowPlayingRaceTests(unittest.TestCase):
    def test_delayed_older_request_cannot_roll_back_hold_or_selection(self):
        presenter = NowPlayingPresenter()
        catalog = _matched_catalog(("OLD", "A"), ("NEW", "A"))
        metadata = _matched_metadata(("OLD", "A"), ("NEW", "A"))
        old_snapshot = _snapshot([_deck("1", "OLD", "A"), _deck("2")])
        new_snapshot = _snapshot([_deck("1", "NEW", "A"), _deck("2")])
        real_enrich = nowplaying.enrich_snapshot
        entered, release = threading.Event(), threading.Event()
        calls = []

        def gated_enrich(snapshot, catalog_arg, track_metadata=None):
            result = real_enrich(snapshot, catalog_arg, track_metadata=track_metadata)
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                release.wait(5)
            return result

        results = {}

        def run_old():
            results["old"] = presenter.present(old_snapshot, catalog, metadata)

        with patch.object(nowplaying, "enrich_snapshot", gated_enrich):
            worker = threading.Thread(target=run_old)
            worker.start()
            self.assertTrue(entered.wait(2))
            results["new"] = presenter.present(new_snapshot, catalog, metadata)
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results["new"]["title"], "NEW")
        # Shared state belongs to the newer request, not the delayed one.
        self.assertEqual(presenter._last_visible["title"], "NEW")
        self.assertEqual(presenter._track_keys["1"], ("NEW", "A"))
        hidden = presenter.present(_snapshot([_deck("1")], uia_status="error"), catalog, metadata)
        self.assertEqual(hidden["title"], "NEW")


class MetadataRetryFailureTests(unittest.TestCase):
    ARTIST = "Morfonica (進藤あまね, 直田姫奈)"

    def test_followup_search_failure_stores_nothing_and_keeps_error_backoff(self):
        class FlakyProvider:
            def __init__(self):
                self.calls = 0

            def search(self, title, artist):
                self.calls += 1
                if self.calls == 1:
                    return ()
                raise OSError("offline")

        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "meta.db")
            provider = FlakyProvider()
            service = TrackMetadataService(store, provider)
            service._fetch_if_needed("Secret Dawn", self.ARTIST)
            self.assertEqual(provider.calls, 2)
            self.assertIsNone(store.get("Secret Dawn", self.ARTIST))
            # The provider-error backoff (60 s) still applies: an
            # immediate refetch must not search again.
            service._fetch_if_needed("Secret Dawn", self.ARTIST)
            self.assertEqual(provider.calls, 2)
            store.close()

    def test_followup_search_zero_results_still_stores_not_found(self):
        class EmptyProvider:
            def __init__(self):
                self.calls = 0

            def search(self, title, artist):
                self.calls += 1
                return ()

        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "meta.db")
            provider = EmptyProvider()
            service = TrackMetadataService(store, provider)
            service._fetch_if_needed("Secret Dawn", self.ARTIST)
            self.assertEqual(provider.calls, 2)
            self.assertEqual(store.get("Secret Dawn", self.ARTIST).status, "not_found")
            store.close()


if __name__ == "__main__":
    unittest.main()
