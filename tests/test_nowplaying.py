import json
import unittest
import urllib.error
import urllib.request

from rekordbox_sync.api.http import HttpServer
from rekordbox_sync.api.nowplaying import NowPlayingPresenter
from rekordbox_sync.models import (
    DeckState,
    FaderState,
    LiveStateSnapshot,
    MixerState,
    SourceStatus,
)
from song_catalog import SongLookupResult


def _snapshot(decks, uia_status="ok", fader_1=None, fader_2=None):
    mixer = MixerState(hardware_channel_1=FaderState(value=fader_1), hardware_channel_2=FaderState(value=fader_2))
    return LiveStateSnapshot(
        served_at="2026-11-17T00:00:00+00:00",
        decks=tuple(decks),
        mixer=mixer,
        uia_status=SourceStatus(status=uia_status),
        midi_status=SourceStatus(),
    )


def _deck(deck_id, title=None, artist=None):
    return DeckState(deck_id, title is not None, title, artist)


class FakeCatalog:
    def __init__(self, results):
        self._results = results

    def lookup(self, title, artist, *, fields=None):
        return self._results.get((title, artist), SongLookupResult("not_found", 0))


class FakeTrackMetadata:
    def __init__(self, results):
        self._results = results

    def lookup(self, title, artist):
        return self._results.get((title, artist), {"status": "pending", "match_count": 0, "fetched_at": None})


def _matched_catalog(*keys):
    return FakeCatalog(
        {key: SongLookupResult("matched", 1, (("TVA", "TVA「作品名」OP"), ("放送年", "2020"))) for key in keys}
    )


def _matched_metadata(*keys):
    return FakeTrackMetadata(
        {
            key: {
                "status": "matched",
                "match_count": 1,
                "fetched_at": "2026-11-17T00:00:00+00:00",
                "artwork_url": f"https://example.com/{key[0]}.jpg",
            }
            for key in keys
        }
    )


class PresenterTests(unittest.TestCase):
    def test_most_recent_change_wins(self):
        presenter = NowPlayingPresenter()
        catalog = _matched_catalog(("T1", "A1"), ("T2", "A2"), ("T3", "A1"))
        metadata = _matched_metadata(("T1", "A1"), ("T2", "A2"), ("T3", "A1"))
        first = presenter.present(_snapshot([_deck("1", "T1", "A1"), _deck("2", "T2", "A2")]), catalog, metadata)
        self.assertEqual(first["title"], "T2")
        second = presenter.present(_snapshot([_deck("1", "T3", "A1"), _deck("2", "T2", "A2")]), catalog, metadata)
        self.assertEqual(second["title"], "T3")
        unchanged = presenter.present(_snapshot([_deck("1", "T3", "A1"), _deck("2", "T2", "A2")]), catalog, metadata)
        self.assertEqual(unchanged["title"], "T3")

    def test_fader_priority_overrides_recency(self):
        presenter = NowPlayingPresenter()
        catalog = _matched_catalog(("T1", "A1"), ("T2", "A2"))
        metadata = _matched_metadata(("T1", "A1"), ("T2", "A2"))
        decks = [_deck("1", "T1", "A1"), _deck("2", "T2", "A2")]
        loud_1 = presenter.present(_snapshot(decks, fader_1=1.0, fader_2=0.0), catalog, metadata)
        self.assertEqual(loud_1["title"], "T1")
        loud_2 = presenter.present(_snapshot(decks, fader_1=0.2, fader_2=0.9), catalog, metadata)
        self.assertEqual(loud_2["title"], "T2")

    def test_fader_tie_uses_recency(self):
        presenter = NowPlayingPresenter()
        catalog = _matched_catalog(("T1", "A1"), ("T2", "A2"))
        metadata = _matched_metadata(("T1", "A1"), ("T2", "A2"))
        result = presenter.present(
            _snapshot([_deck("1", "T1", "A1"), _deck("2", "T2", "A2")], fader_1=0.5, fader_2=0.5), catalog, metadata
        )
        self.assertEqual(result["title"], "T2")

    def test_fader_top_lock_holds_displayed_deck(self):
        presenter = NowPlayingPresenter()
        catalog = _matched_catalog(("T1", "A1"), ("T2", "A2"))
        metadata = _matched_metadata(("T1", "A1"), ("T2", "A2"))
        decks = [_deck("1", "T1", "A1"), _deck("2", "T2", "A2")]
        first = presenter.present(_snapshot(decks, fader_1=1.0, fader_2=0.0), catalog, metadata)
        self.assertEqual(first["title"], "T1")
        both_top = presenter.present(_snapshot(decks, fader_1=1.0, fader_2=1.0), catalog, metadata)
        self.assertEqual(both_top["title"], "T1")
        released = presenter.present(_snapshot(decks, fader_1=0.9, fader_2=1.0), catalog, metadata)
        self.assertEqual(released["title"], "T2")

    def test_no_lock_below_top(self):
        presenter = NowPlayingPresenter()
        catalog = _matched_catalog(("T1", "A1"), ("T2", "A2"))
        metadata = _matched_metadata(("T1", "A1"), ("T2", "A2"))
        decks = [_deck("1", "T1", "A1"), _deck("2", "T2", "A2")]
        first = presenter.present(_snapshot(decks, fader_1=0.8, fader_2=0.0), catalog, metadata)
        self.assertEqual(first["title"], "T1")
        tied = presenter.present(_snapshot(decks, fader_1=0.8, fader_2=0.8), catalog, metadata)
        self.assertEqual(tied["title"], "T2")

    def test_partial_fader_data_falls_back_to_recency(self):
        presenter = NowPlayingPresenter()
        catalog = _matched_catalog(("T1", "A1"), ("T2", "A2"))
        metadata = _matched_metadata(("T1", "A1"), ("T2", "A2"))
        result = presenter.present(
            _snapshot([_deck("1", "T1", "A1"), _deck("2", "T2", "A2")], fader_1=1.0, fader_2=None), catalog, metadata
        )
        self.assertEqual(result["title"], "T2")

    def test_playing_fields(self):
        presenter = NowPlayingPresenter()
        result = presenter.present(
            _snapshot([_deck("1", "T1", "A1")]), _matched_catalog(("T1", "A1")), _matched_metadata(("T1", "A1"))
        )
        self.assertEqual(
            result,
            {
                "status": "playing",
                "title": "T1",
                "artist": "A1",
                "artwork": "https://example.com/T1.jpg",
                "work": "TVA「作品名」OP",
                "manual_revision": "",
            },
        )

    def test_pending_is_loading(self):
        presenter = NowPlayingPresenter()
        result = presenter.present(
            _snapshot([_deck("1", "T1", "A1")]), _matched_catalog(("T1", "A1")), FakeTrackMetadata({})
        )
        self.assertEqual(result["status"], "loading")
        self.assertEqual(result["title"], "")

    def test_hidden_is_held_during_grace(self):
        now = [100.0]
        presenter = NowPlayingPresenter(clock=lambda: now[0])
        catalog = _matched_catalog(("T1", "A1"))
        metadata = _matched_metadata(("T1", "A1"))
        playing = presenter.present(_snapshot([_deck("1", "T1", "A1")]), catalog, metadata)
        self.assertEqual(playing["status"], "playing")
        now[0] += 2.0
        held = presenter.present(_snapshot([], uia_status="unavailable"), catalog, metadata)
        self.assertEqual(held["title"], "T1")
        now[0] += 2.0
        hidden = presenter.present(_snapshot([], uia_status="unavailable"), catalog, metadata)
        self.assertEqual(hidden["status"], "hidden")

    def test_stale_is_hidden(self):
        presenter = NowPlayingPresenter()
        stale = _snapshot([_deck("1", "T1", "A1")], uia_status="unavailable")
        result = presenter.present(stale, _matched_catalog(("T1", "A1")), _matched_metadata(("T1", "A1")))
        self.assertEqual(result["status"], "hidden")
        empty = presenter.present(_snapshot([]), None, None)
        self.assertEqual(empty["status"], "hidden")

    def test_selected_deck_unloaded_is_hidden(self):
        now = [100.0]
        presenter = NowPlayingPresenter(clock=lambda: now[0])
        catalog = _matched_catalog(("T1", "A1"))
        metadata = _matched_metadata(("T1", "A1"))
        playing = presenter.present(_snapshot([_deck("1", "T1", "A1"), _deck("2")]), catalog, metadata)
        self.assertEqual(playing["status"], "playing")
        held = presenter.present(_snapshot([_deck("1"), _deck("2")]), catalog, metadata)
        self.assertEqual(held["status"], "playing")
        now[0] += 4.0
        unloaded = presenter.present(_snapshot([_deck("1"), _deck("2")]), catalog, metadata)
        self.assertEqual(unloaded["status"], "hidden")

    def test_ambiguous_artwork_and_unmatched_work(self):
        presenter = NowPlayingPresenter()
        metadata = FakeTrackMetadata(
            {
                ("T1", "A1"): {
                    "status": "ambiguous",
                    "match_count": 2,
                    "fetched_at": "2026-11-17T00:00:00+00:00",
                    "candidates": [
                        {"artwork_url": "https://example.com/first.jpg"},
                        {"artwork_url": "https://example.com/second.jpg"},
                    ],
                }
            }
        )
        result = presenter.present(_snapshot([_deck("1", "T1", "A1")]), FakeCatalog({}), metadata)
        self.assertEqual(result["status"], "playing")
        self.assertEqual(result["artwork"], "https://example.com/first.jpg")
        self.assertEqual(result["work"], "")

    def test_not_found_artwork_is_empty(self):
        presenter = NowPlayingPresenter()
        metadata = FakeTrackMetadata(
            {("T1", "A1"): {"status": "not_found", "match_count": 0, "fetched_at": "2026-11-17T00:00:00+00:00"}}
        )
        result = presenter.present(_snapshot([_deck("1", "T1", "A1")]), _matched_catalog(("T1", "A1")), metadata)
        self.assertEqual(result["status"], "playing")
        self.assertEqual(result["artwork"], "")
        self.assertEqual(result["work"], "TVA「作品名」OP")

    def test_without_metadata_services_plays_bare(self):
        presenter = NowPlayingPresenter()
        result = presenter.present(_snapshot([_deck("1", "T1", "A1")]), None, None)
        self.assertEqual(result["status"], "playing")
        self.assertEqual(result["artwork"], "")
        self.assertEqual(result["work"], "")


class _StubProvider:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


class HttpTests(unittest.TestCase):
    def setUp(self):
        provider = _StubProvider(_snapshot([_deck("1", "T1", "A1")]))
        self.server = HttpServer(
            provider, port=0, catalog=_matched_catalog(("T1", "A1")), track_metadata=_matched_metadata(("T1", "A1"))
        )
        self.server.start()
        self.base = f"http://{self.server.address[0]}:{self.server.address[1]}"

    def tearDown(self):
        self.server.stop()

    def test_overlay_files_served(self):
        with urllib.request.urlopen(self.base + "/overlay") as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.headers.get_content_type(), "text/html")
        self.assertIn('class="work"', body)
        self.assertIn("/overlay.js", body)
        with urllib.request.urlopen(self.base + "/overlay.css") as response:
            self.assertEqual(response.headers.get_content_type(), "text/css")
            self.assertIn(".work", response.read().decode("utf-8"))
        with urllib.request.urlopen(self.base + "/overlay.js") as response:
            self.assertEqual(response.headers.get_content_type(), "text/javascript")
            self.assertIn("/nowplaying.json", response.read().decode("utf-8"))

    def test_nowplaying_etag_round_trip(self):
        with urllib.request.urlopen(self.base + "/nowplaying.json") as response:
            payload = json.load(response)
            etag = response.headers["ETag"]
        self.assertEqual(payload["status"], "playing")
        self.assertEqual(payload["work"], "TVA「作品名」OP")
        self.assertTrue(etag)
        request = urllib.request.Request(self.base + "/nowplaying.json", headers={"If-None-Match": etag})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code, 304)


if __name__ == "__main__":
    unittest.main()
