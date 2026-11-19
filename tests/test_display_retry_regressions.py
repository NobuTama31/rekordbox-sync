import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rekordbox_sync.api.nowplaying import NowPlayingPresenter
from rekordbox_sync.metadata_service import TrackMetadataService
from rekordbox_sync.models import DeckState, LiveStateSnapshot, MixerState, SourceStatus


def snapshot(title=None):
    decks = (DeckState("1", True, title, "Artist"),) if title else ()
    return LiveStateSnapshot("now", decks, MixerState(), SourceStatus(status="ok"), SourceStatus())


class DisplayRetryTests(unittest.TestCase):
    def test_selection_and_held_display_commit_together(self):
        released, resume = threading.Event(), threading.Event()

        class PauseFirstRelease:
            def __init__(self):
                self.lock = threading.Lock()
                self.paused = False

            def __enter__(self):
                self.lock.acquire()

            def __exit__(self, *args):
                pause = threading.current_thread().name == "old-request" and not self.paused
                if pause:
                    self.paused = True
                self.lock.release()
                if pause:
                    released.set()
                    resume.wait(3)

        presenter = NowPlayingPresenter()
        presenter._lock = PauseFirstRelease()
        old = threading.Thread(name="old-request", target=lambda: presenter.present(snapshot("OLD"), None))
        old.start()
        try:
            self.assertTrue(released.wait(1))
            self.assertEqual(presenter.present(snapshot("NEW"), None)["title"], "NEW")
        finally:
            resume.set()
            old.join(2)
        self.assertEqual(presenter.present(snapshot(), None)["title"], "NEW")

    def test_metadata_io_does_not_hold_presenter_lock(self):
        entered, release = threading.Event(), threading.Event()
        metadata = SimpleNamespace(lookup=lambda *args: (entered.set(), release.wait(2), {"status": "not_found"})[-1])
        presenter = NowPlayingPresenter()
        worker = threading.Thread(target=lambda: presenter.present(snapshot("OLD"), None, metadata))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            acquired = presenter._lock.acquire(timeout=0.3)
            if acquired:
                presenter._lock.release()
            self.assertTrue(acquired)
        finally:
            release.set()
            worker.join(2)

    def test_followup_failure_keeps_error_backoff_and_stores_nothing(self):
        store, provider = Mock(), Mock()
        store.get.return_value = None
        provider.search.side_effect = [(), OSError("offline"), (), ()]
        service = TrackMetadataService(store, provider)
        with patch("rekordbox_sync.metadata_service.time.monotonic", return_value=100):
            service._fetch_if_needed("Title (TV size)", "Artist")
        store.put.assert_not_called()
        with patch("rekordbox_sync.metadata_service.time.monotonic", return_value=159):
            service._fetch_if_needed("Title (TV size)", "Artist")
        self.assertEqual(provider.search.call_count, 2)
        with patch("rekordbox_sync.metadata_service.time.monotonic", return_value=160):
            service._fetch_if_needed("Title (TV size)", "Artist")
        self.assertEqual(provider.search.call_count, 4)
        self.assertEqual(store.put.call_args.args[-1].status, "not_found")

    def test_genuine_zero_results_and_no_followup_keep_their_policy(self):
        for title, calls in (("Title (TV size)", 2), ("Title", 1)):
            with self.subTest(title=title):
                store, provider = Mock(), Mock()
                store.get.return_value = None
                provider.search.return_value = ()
                service = TrackMetadataService(store, provider)
                service._fetch_if_needed(title, "Artist")
                self.assertEqual(provider.search.call_count, calls)
                self.assertEqual(store.put.call_args.args[-1].status, "not_found")
