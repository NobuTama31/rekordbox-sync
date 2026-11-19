import copy
import json
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import FrozenInstanceError
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from rekordbox_sync import (
    DeckObservation,
    FaderState,
    MixerObservation,
    MixerState,
    MonitorConfig,
    RekordboxMonitor,
)
from rekordbox_sync.api.http import HttpServer
from rekordbox_sync.api.serialization import to_api_dict
from rekordbox_sync.sources.midi.flx4_decoder import Flx4Decoder
from rekordbox_sync.sources.uia.deck_parser import parse_snapshot
from rekordbox_sync.sources.uia.source import decode_observation
from rekordbox_sync.state_store import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name="minimized"):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class ParserTests(unittest.TestCase):
    def test_recorded_outputs_match_original(self):
        for name in ("unloaded", "tempo_changed", "minimized"):
            with self.subTest(name=name):
                sample = fixture(name)
                self.assertEqual(parse_snapshot(sample["input"]), sample["expected"])

    def test_sync_deck_bpm_box_is_a_text(self):
        # A tracked capture with BEAT SYNC on for deck 2: that deck's
        # BPM box is a Text, not an Edit (see deck_parser). Kept out of
        # the top-level fixtures because the tracked-selection test
        # treats every top-level fixture as a full walk.
        sample = json.loads((FIXTURES / "captures" / "sync_deck.json").read_text(encoding="utf-8"))
        self.assertEqual(parse_snapshot(sample["input"]), sample["expected"])

    def test_window_translation_and_element_order(self):
        sample = fixture()
        changed = copy.deepcopy(sample["input"])
        for element in changed["elements"]:
            element["rect"]["x"] += 32100
            element["rect"]["y"] += 32200
        changed["elements"].reverse()
        self.assertEqual(parse_snapshot(changed), sample["expected"])

    def test_missing_and_duplicate_anchor_fail_closed(self):
        for duplicate in (False, True):
            sample = fixture()["input"]
            anchors = [e for e in sample["elements"] if e["name"] == "BEAT\nSYNC"]
            if duplicate:
                sample["elements"].append(copy.deepcopy(anchors[0]))
            else:
                sample["elements"].remove(anchors[0])
            parsed = parse_snapshot(sample)
            self.assertFalse(parsed["valid"])
            self.assertEqual(parsed["decks"], {})

    def test_blank_or_missing_current_bpm_is_unknown_not_fatal(self):
        # A track still being analyzed has a BPM field that is blank or not
        # rendered yet; the deck itself must survive with bpm_current None.
        for mutate in ("blank", "remove"):
            with self.subTest(mutate=mutate):
                sample = fixture("tempo_changed")
                changed = copy.deepcopy(sample["input"])
                edits = [
                    e
                    for e in changed["elements"]
                    if e["type"] == "ControlType.Edit" and (e.get("value") or "").strip().replace(".", "").isdigit()
                ]
                self.assertEqual(len(edits), 2)
                if mutate == "blank":
                    for element in edits:
                        element["value"] = ""
                        element["name"] = ""
                else:
                    for element in edits:
                        changed["elements"].remove(element)
                parsed = parse_snapshot(changed)
                self.assertTrue(parsed["valid"], parsed.get("error"))
                for deck_id, expected in sample["expected"]["decks"].items():
                    self.assertEqual(parsed["decks"][deck_id]["title"], expected["title"])
                    self.assertIsNone(parsed["decks"][deck_id]["bpm_current"])
                    self.assertEqual(parsed["decks"][deck_id]["bpm_original"], expected["bpm_original"])

    def test_duplicate_current_bpm_edit_fails_closed(self):
        sample = fixture("tempo_changed")
        changed = copy.deepcopy(sample["input"])
        edit = next(
            e
            for e in changed["elements"]
            if e["type"] == "ControlType.Edit" and (e.get("value") or "").strip().replace(".", "").isdigit()
        )
        duplicate = copy.deepcopy(edit)
        duplicate["value"] = " 88.00"
        duplicate["name"] = " 88.00"
        duplicate["rect"]["x"] += 10
        changed["elements"].append(duplicate)
        parsed = parse_snapshot(changed)
        self.assertFalse(parsed["valid"])
        self.assertEqual(parsed["decks"], {})

    def test_missing_artist_and_original_bpm_are_unknown_not_fatal(self):
        sample = fixture("tempo_changed")
        changed = copy.deepcopy(sample["input"])
        artists = {deck["artist"] for deck in sample["expected"]["decks"].values()}
        originals = {f"{deck['bpm_original']:.2f}" for deck in sample["expected"]["decks"].values()}
        changed["elements"] = [
            e
            for e in changed["elements"]
            if not (
                e["type"] == "ControlType.Text" and (e.get("value", e.get("name", ""))).strip() in artists | originals
            )
        ]
        parsed = parse_snapshot(changed)
        self.assertTrue(parsed["valid"], parsed.get("error"))
        for deck in parsed["decks"].values():
            self.assertIsNone(deck["artist"])
            self.assertIsNone(deck["bpm_original"])
            self.assertIsNotNone(deck["bpm_current"])

    def test_resources_packaged(self):
        self.assertIn(
            "IsIconic", files("rekordbox_sync.sources.uia").joinpath("capture.ps1").read_text(encoding="utf-8-sig")
        )

    def test_invalid_snapshot_fails_closed(self):
        for sample in (None, {}, fixture()):
            result = parse_snapshot(sample)
            self.assertFalse(result["valid"])
            self.assertEqual(result["decks"], {})

    def test_unnamed_uia_control_is_ignored(self):
        sample = fixture()
        sample["input"]["elements"].append({"name": None, "type": "ControlType.Custom", "offscreen": False})
        self.assertEqual(parse_snapshot(sample["input"]), sample["expected"])


class SlimCaptureFormatTests(unittest.TestCase):
    def test_slim_capture_format_parses_identically(self):
        # capture.ps1 no longer emits fields the parser never reads, and
        # only Edit controls carry a value; the parse must not change.
        sample = fixture("tempo_changed")
        slim = copy.deepcopy(sample["input"])
        for element in slim["elements"]:
            for field in (
                "patterns",
                "range_value",
                "range_minimum",
                "range_maximum",
                "runtime_id",
                "parent_runtime_id",
            ):
                element.pop(field, None)
            if element["type"] != "ControlType.Edit":
                element.pop("value", None)
        self.assertEqual(parse_snapshot(slim), parse_snapshot(sample["input"]))


class UiaSourceRetryTests(unittest.TestCase):
    def _source_with_payloads(self, payloads, on_suspect=None):
        import subprocess as sp

        from rekordbox_sync.sources.uia.source import UiaSource

        calls = []

        def fake_run(*args, **kwargs):
            calls.append(1)
            payload = payloads[min(len(calls) - 1, len(payloads) - 1)]
            return sp.CompletedProcess(args, 0, stdout=json.dumps(payload).encode())

        return UiaSource(on_suspect=on_suspect), calls, fake_run

    def test_parse_failure_retries_once_and_recovers(self):
        good = fixture("tempo_changed")["input"]
        bad = {"received_at": "2026-01-01T00:00:00+00:00", "elements": []}
        source, calls, fake_run = self._source_with_payloads([bad, good])
        with (
            patch("rekordbox_sync.sources.uia.source.sys.platform", "win32"),
            patch("rekordbox_sync.sources.uia.source.subprocess.run", fake_run),
        ):
            observation = source.read()
        self.assertIsNone(observation.error)
        self.assertEqual(len(observation.decks), 2)
        self.assertEqual(len(calls), 2)

    def test_persistent_failure_retries_only_on_first_of_streak(self):
        bad = {"received_at": "2026-01-01T00:00:00+00:00", "elements": []}
        source, calls, fake_run = self._source_with_payloads([bad])
        with (
            patch("rekordbox_sync.sources.uia.source.sys.platform", "win32"),
            patch("rekordbox_sync.sources.uia.source.subprocess.run", fake_run),
        ):
            self.assertTrue(source.read().error)
            self.assertEqual(len(calls), 2)
            self.assertTrue(source.read().error)
            self.assertEqual(len(calls), 3)

    def _missing_bpm_payload(self):
        import copy
        import re

        payload = copy.deepcopy(fixture("tempo_changed")["input"])
        for element in payload["elements"]:
            value = element.get("value") or ""
            if element["type"] == "ControlType.Edit" and re.fullmatch(r"\s*\d+\.\d{2}", value):
                element["value"] = ""
        return payload

    def test_missing_bpm_retries_once_and_recovers(self):
        good = fixture("tempo_changed")["input"]
        source, calls, fake_run = self._source_with_payloads([self._missing_bpm_payload(), good])
        with (
            patch("rekordbox_sync.sources.uia.source.sys.platform", "win32"),
            patch("rekordbox_sync.sources.uia.source.subprocess.run", fake_run),
        ):
            observation = source.read()
        self.assertIsNone(observation.error)
        self.assertTrue(all(deck.bpm_current is not None for deck in observation.decks))
        self.assertEqual(len(calls), 2)

    def test_close_cancels_retry_and_later_read_rearms(self):
        import subprocess as sp

        from rekordbox_sync.sources.uia.source import UiaSource

        good = fixture("tempo_changed")["input"]
        payloads = [self._missing_bpm_payload(), self._missing_bpm_payload(), good]
        calls = []
        source = UiaSource()

        def fake_run(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                source.close()
            payload = payloads[min(len(calls) - 1, len(payloads) - 1)]
            return sp.CompletedProcess(args, 0, stdout=json.dumps(payload).encode())

        with (
            patch("rekordbox_sync.sources.uia.source.sys.platform", "win32"),
            patch("rekordbox_sync.sources.uia.source.subprocess.run", fake_run),
        ):
            first = source.read()
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(deck.bpm_current is None for deck in first.decks if deck.loaded))
            second = source.read()
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(deck.bpm_current is not None for deck in second.decks))

    def test_suspect_sink_receives_raw_only_for_suspect_reads(self):
        good = fixture("tempo_changed")["input"]
        saved = []
        source, _calls, fake_run = self._source_with_payloads(
            [self._missing_bpm_payload(), good, good], on_suspect=saved.append
        )
        with (
            patch("rekordbox_sync.sources.uia.source.sys.platform", "win32"),
            patch("rekordbox_sync.sources.uia.source.subprocess.run", fake_run),
        ):
            source.read()
            source.read()
        self.assertEqual(len(saved), 1)
        self.assertTrue(any(e.get("value") == "" for e in saved[0]["elements"] if e["type"] == "ControlType.Edit"))

    def test_missing_bpm_error_retry_does_not_discard_decks(self):
        bad = {"received_at": "2026-01-01T00:00:00+00:00", "elements": []}
        source, calls, fake_run = self._source_with_payloads([self._missing_bpm_payload(), bad])
        with (
            patch("rekordbox_sync.sources.uia.source.sys.platform", "win32"),
            patch("rekordbox_sync.sources.uia.source.subprocess.run", fake_run),
        ):
            observation = source.read()
        self.assertIsNone(observation.error)
        self.assertEqual(len(observation.decks), 2)
        self.assertEqual(len(calls), 2)


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.decoder = Flx4Decoder(clock=lambda: self.now)

    def test_initial_orphan_and_expired_pair(self):
        self.decoder.feed(0, 51, 127)
        self.assertIsNone(self.decoder.snapshot().hardware_channel_1.value)
        self.decoder.feed(0, 19, 127)
        self.now += 1
        self.decoder.feed(0, 51, 127)
        self.assertIsNone(self.decoder.snapshot().hardware_channel_1.value)

    def test_channels_and_endpoint_pairs(self):
        for channel, msb, lsb, field in (
            (0, 19, 51, "hardware_channel_1"),
            (1, 19, 51, "hardware_channel_2"),
            (6, 31, 63, "hardware_crossfader"),
        ):
            self.decoder.feed(channel, msb, 127)
            self.decoder.feed(channel, lsb, 127)
            self.assertEqual(getattr(self.decoder.snapshot(), field).value, 1)
        previous = self.decoder.snapshot()
        self.decoder.reset()
        self.assertEqual(previous.hardware_channel_1.value, 1)
        self.assertIsNone(self.decoder.snapshot().hardware_channel_1.value)

    def test_channel_pairs_do_not_mix(self):
        self.decoder.feed(0, 19, 127)
        self.decoder.feed(1, 51, 127)
        self.assertIsNone(self.decoder.snapshot().hardware_channel_2.value)

    def test_concurrent_feed_and_snapshot(self):
        import threading

        errors = []

        def feed(channel, msb, lsb):
            try:
                for _ in range(200):
                    self.decoder.feed(channel, msb, 64)
                    self.decoder.feed(channel, lsb, 32)
                    self.decoder.snapshot()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=feed, args=args) for args in ((0, 19, 51), (1, 19, 51), (6, 31, 63))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertAlmostEqual(self.decoder.snapshot().hardware_channel_1.value, (64 << 7 | 32) / 16383)


class MidiSourceTests(unittest.TestCase):
    def test_callback_feeds_decoder_without_polling(self):
        from types import SimpleNamespace

        from rekordbox_sync.sources.midi.source import MidiSource

        source = MidiSource("unused-port")
        source._on_message(SimpleNamespace(type="note_on", channel=0, control=19, value=127))
        source._on_message(SimpleNamespace(type="control_change", channel=0, control=19, value=127))
        source._on_message(SimpleNamespace(type="control_change", channel=0, control=51, value=127))
        self.assertEqual(source._decoder.snapshot().hardware_channel_1.value, 1)
        self.assertEqual(source._decoder.control_event_count, 2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.store = StateStore(6, clock=lambda: self.now)
        self.store.update_decks(decode_observation(fixture()["input"]))

    def test_stale_uia_does_not_expire_connected_midi(self):
        self.store.update_mixer(MixerObservation(MixerState(hardware_channel_1=FaderState(1, 16383, "time")), True))
        self.now += 7
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot.decks, ())
        self.assertEqual(snapshot.uia_status.error, "stale")
        self.assertEqual(snapshot.mixer.hardware_channel_1.value, 1)
        self.store.update_mixer(MixerObservation(error="unplugged"))
        self.assertIsNone(self.store.snapshot().mixer.hardware_channel_1.value)

    def test_error_holds_last_good_decks_until_stale(self):
        self.store.update_decks(DeckObservation(error="capture failed"))
        snapshot = self.store.snapshot()
        self.assertNotEqual(snapshot.decks, ())
        self.assertEqual(snapshot.uia_status.status, "ok")
        self.assertEqual(snapshot.uia_status.error, "capture failed")
        self.now += 7
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot.decks, ())
        self.assertEqual(snapshot.uia_status.status, "unavailable")

    def test_good_read_clears_error(self):
        self.store.update_decks(DeckObservation(error="capture failed"))
        self.store.update_decks(decode_observation(fixture()["input"]))
        self.assertIsNone(self.store.snapshot().uia_status.error)

    def test_immutable_snapshot_and_independent_wire_dict(self):
        snapshot = self.store.snapshot()
        with self.assertRaises(FrozenInstanceError):
            snapshot.decks[0].title = "changed"
        wire = to_api_dict(snapshot)
        wire["decks"]["1"]["title"] = "changed"
        self.assertNotEqual(self.store.snapshot().decks[0].title, "changed")

    def test_http_contract(self):
        with HttpServer(self.store, port=0) as server:
            url = f"http://{server.address[0]}:{server.address[1]}"
            with urllib.request.urlopen(url + "/state") as response:
                result = json.load(response)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["decks"], fixture()["expected"]["decks"])
            self.assertEqual(set(result["mixer"]), {"channel_1", "channel_2", "crossfader"})
            self.assertIsNone(result["decks"]["1"]["playing"])
            with urllib.request.urlopen(url + "/health") as response:
                self.assertEqual(set(json.load(response)), {"sources"})
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(url + "/missing")
            self.assertEqual(raised.exception.code, 404)


class FakeSource:
    def __init__(self, observation, fail_first=False):
        self.observation = observation
        self.reads = 0
        self.closes = 0
        self.fail_first = fail_first

    def read(self):
        self.reads += 1
        if self.fail_first and self.reads == 1:
            raise RuntimeError("temporary disconnect")
        return self.observation

    def close(self):
        self.closes += 1


class LifecycleTests(unittest.TestCase):
    def test_monitor_wires_suspect_sink_only_when_enabled(self):
        from rekordbox_sync import service

        captured = {}

        class FakeUia:
            def __init__(self, timeout, on_suspect=None):
                captured["on_suspect"] = on_suspect

        with patch.object(service, "UiaSource", FakeUia), patch.object(service, "UiaResidentSource", FakeUia):
            RekordboxMonitor(MonitorConfig(save_suspect_captures=True), mixer_source=FakeSource(DeckObservation()))
            self.assertIsNotNone(captured["on_suspect"])
            RekordboxMonitor(MonitorConfig(), mixer_source=FakeSource(DeckObservation()))
            self.assertIsNone(captured["on_suspect"])

    def test_context_retries_closes_and_restarts(self):
        deck = FakeSource(decode_observation(fixture()["input"]), fail_first=True)
        mixer = FakeSource(MixerObservation(connected=True))
        config = MonitorConfig(uia_poll_interval_seconds=0.01, retry_interval_seconds=0.01)
        monitor = RekordboxMonitor(config, deck_source=deck, mixer_source=mixer)
        self.assertEqual(deck.reads, 0)
        for _ in range(2):
            with monitor:
                monitor.start()  # idempotent
                deadline = time.monotonic() + 2
                while not monitor.snapshot().decks and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(monitor.snapshot().decks)
            self.assertEqual(monitor.snapshot().decks, ())
            self.assertEqual(monitor.snapshot().midi_status.error, "stopped")
        monitor.stop()
        # The deck source is closed twice per cycle: stop() closes it up
        # front to cancel a pending retry, the worker's finally closes it
        # again on the way out (close is idempotent in real sources).
        self.assertEqual((deck.closes, mixer.closes), (4, 2))

    def test_constructor_does_not_open_devices(self):
        with patch("subprocess.run") as run, patch("mido.open_input") as open_input:
            monitor = RekordboxMonitor()
            monitor.snapshot()
            monitor.stop()
            run.assert_not_called()
            open_input.assert_not_called()

    def test_invalid_settings(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                MonitorConfig(uia_timeout_seconds=value)


if __name__ == "__main__":
    unittest.main()
