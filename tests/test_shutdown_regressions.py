"""In-flight work must release resources without relying on process exit."""

import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import urlopen

from rekordbox_sync.api.http import HttpServer
from rekordbox_sync.gui.controller import LocalApiController
from rekordbox_sync.metadata_service import TrackMetadataService
from rekordbox_sync.models import DeckObservation, DeckState
from rekordbox_sync.sources.uia.resident import UiaResidentSource, _ResidentProcess
from rekordbox_sync.state_store import StateStore
from track_metadata import MetadataStore


class BlockingProcess:
    def __init__(self):
        self.reading = threading.Event()
        self.terminated = threading.Event()

    def write_line(self, text):
        pass

    def read_line(self, timeout_seconds):
        self.reading.set()
        self.terminated.wait(3)
        raise EOFError("terminated")

    def is_alive(self):
        return not self.terminated.is_set()

    def terminate(self):
        self.terminated.set()


class ShutdownTests(unittest.TestCase):
    def test_resident_close_reaps_real_child_and_pipe_reader(self):
        popen = subprocess.Popen

        def spawn(command, **kwargs):
            return popen([sys.executable, "-c", "import sys,time; sys.stdin.readline(); time.sleep(60)"], **kwargs)

        with patch("rekordbox_sync.sources.uia.resident.subprocess.Popen", side_effect=spawn):
            process = _ResidentProcess()
        self.addCleanup(process.terminate)
        entered = threading.Event()
        original_read = process.read_line

        def read_line(timeout):
            entered.set()
            return original_read(timeout)

        process.read_line = read_line
        source = UiaResidentSource(process_factory=lambda: process)
        failures = []

        def read():
            try:
                source.read()
            except RuntimeError as error:
                failures.append(error)

        worker = threading.Thread(target=read)
        worker.start()
        self.assertTrue(entered.wait(2))
        source.close()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(process.is_alive())
        self.assertFalse(process._reader.is_alive())
        self.assertEqual(len(failures), 1)

    def test_resident_close_cancels_pending_read_promptly(self):
        process = BlockingProcess()
        source = UiaResidentSource(process_factory=lambda: process)
        failures = []

        def read():
            try:
                source.read()
            except RuntimeError as error:
                failures.append(error)

        reader = threading.Thread(target=read)
        closer = threading.Thread(target=source.close)
        reader.start()
        self.assertTrue(process.reading.wait(1))
        closer.start()
        prompt = process.terminated.wait(0.5)
        process.terminate()  # Always release the old implementation on failure.
        reader.join(2)
        closer.join(2)
        self.assertTrue(prompt, "close waited for the response instead of cancelling it")
        self.assertFalse(reader.is_alive())
        self.assertEqual(len(failures), 1)
        source.close()

    def test_controller_stops_monitor_while_metadata_cleanup_is_blocked(self):
        entered, release, monitor_stopped = (threading.Event() for _ in range(3))
        controller = LocalApiController()
        controller._server = Mock()
        controller._metadata_service = Mock()
        controller._metadata_service.close.side_effect = lambda: (entered.set(), release.wait(2))
        controller._monitor = SimpleNamespace(stop=monitor_stopped.set)
        worker = threading.Thread(target=controller.stop)
        worker.start()
        self.assertTrue(entered.wait(1))
        prompt = monitor_stopped.wait(0.3)
        release.set()
        worker.join(2)
        self.assertTrue(prompt, "metadata cleanup delayed monitor.stop")
        self.assertFalse(controller.owned)
        controller.stop()

    def test_gui_does_not_report_timed_out_shutdown_as_complete(self):
        try:
            from rekordbox_sync.gui import app
        except ImportError:
            self.skipTest("Optional GUI dependency")
        release, entered, destroyed = (threading.Event() for _ in range(3))
        callbacks = []
        window = SimpleNamespace(
            _closing=False,
            _busy=False,
            _set_notice=Mock(),
            _tick=Mock(),
            _client=SimpleNamespace(stop=lambda: None),
            _artwork=SimpleNamespace(stop=lambda: None),
            _controller=SimpleNamespace(stop=lambda: (entered.set(), release.wait(7))),
            after=lambda delay, callback: callbacks.append(callback),
            destroy=destroyed.set,
        )
        try:
            with patch.object(app, "_SHUTDOWN_TIMEOUT_SECONDS", 0.05, create=True):
                app.MonitorApp._close(window)
                self.assertTrue(entered.wait(1))
                deadline = time.monotonic() + 5.3
                while time.monotonic() < deadline and not destroyed.is_set():
                    pending, callbacks[:] = callbacks[:], []
                    for callback in pending:
                        callback()
                    time.sleep(0.01)
                self.assertFalse(destroyed.is_set(), "cleanup is still running")
        finally:
            release.set()

    def test_metadata_final_close_is_deferred_and_no_next_deck_is_fetched(self):
        entered, release = threading.Event(), threading.Event()
        store = Mock()
        store.get.return_value = None
        provider = Mock()
        provider.search.side_effect = lambda *args: (entered.set(), release.wait(2), ())[-1]
        service = TrackMetadataService(store, provider, poll_interval_seconds=0.01)
        decks = (DeckState("1", True, "First (TV size)", "Artist"), DeckState("2", True, "Second", "Artist"))
        service.start(lambda: SimpleNamespace(decks=decks))
        self.assertTrue(entered.wait(1))
        worker = service._thread
        with patch.object(worker, "join", return_value=None):
            service.close()
        store.close.assert_not_called()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(provider.search.call_count, 1)
        store.close.assert_called_once()
        service.close()
        store.close.assert_called_once()

    def test_metadata_stop_remains_reusable(self):
        store = Mock()
        service = TrackMetadataService(store, Mock(), poll_interval_seconds=0.01)
        for _ in range(2):
            service.start(lambda: SimpleNamespace(decks=()))
            service.stop()
            store.close.assert_not_called()
        service.close()
        store.close.assert_called_once()

    def test_cancelled_search_does_not_backoff_after_restart(self):
        entered, release = threading.Event(), threading.Event()
        store, provider = Mock(), Mock()
        store.get.return_value = None
        provider.search.side_effect = lambda *args: (entered.set(), release.wait(2), ())[-1]
        service = TrackMetadataService(store, provider, poll_interval_seconds=0.01)

        def snapshot():
            return SimpleNamespace(decks=(DeckState("1", True, "Title", "Artist"),))

        service.start(snapshot)
        self.assertTrue(entered.wait(1))
        worker = service._thread
        with patch.object(worker, "join", return_value=None):
            service.stop()
        release.set()
        worker.join(2)
        service.start(lambda: SimpleNamespace(decks=()))
        service._fetch_if_needed("Title", "Artist")
        service.close()
        self.assertEqual(provider.search.call_count, 2)

    def test_store_waits_for_lookup_and_rejects_new_access_after_close(self):
        entered, release = threading.Event(), threading.Event()
        store = Mock()
        store.get.side_effect = lambda *args: (entered.set(), release.wait(2), None)[-1]
        service = TrackMetadataService(store, Mock())
        reader = threading.Thread(target=lambda: service.lookup("T", "A"))
        reader.start()
        self.assertTrue(entered.wait(1))
        service.close()
        deferred = store.close.call_count == 0
        release.set()
        reader.join(2)
        self.assertTrue(deferred, "store was closed under an active lookup")
        store.close.assert_called_once()
        before = store.get.call_count
        with self.assertRaises(RuntimeError):
            service.lookup("T", "A")
        self.assertEqual(store.get.call_count, before)

    def test_http_drains_inflight_request_before_stop_returns(self):
        entered, release, stopped = (threading.Event() for _ in range(3))
        provider = SimpleNamespace(snapshot=lambda: (entered.set(), release.wait(2), StateStore(60).snapshot())[-1])
        server = HttpServer(provider, 0)
        server.start()
        url = "http://%s:%d/state" % server.address
        errors = []

        def request():
            try:
                with urlopen(url, timeout=3) as response:
                    response.read()
            except Exception as error:
                errors.append(error)

        reader = threading.Thread(target=request)
        closer = threading.Thread(target=lambda: (server.stop(), stopped.set()))
        reader.start()
        self.assertTrue(entered.wait(1))
        closer.start()
        early = stopped.wait(0.25)
        release.set()
        reader.join(3)
        closer.join(3)
        self.assertFalse(early, "HTTP stop returned with a handler still using the provider")
        self.assertEqual(errors, [])
        self.assertTrue(stopped.is_set())
        server.stop()
        server.start()
        server.stop()

    def test_http_stop_closes_half_sent_request_within_bound(self):
        server = HttpServer(StateStore(60), 0)
        server.start()
        with socket.create_connection(server.address, timeout=1) as client:
            client.sendall(b"GET /state HTTP/1.0\r\nX-Half:")
            time.sleep(0.05)
            started = time.monotonic()
            server.stop()
            self.assertLess(time.monotonic() - started, 3)
            client.settimeout(0.5)
            try:
                data = client.recv(1)
            except ConnectionResetError:
                data = b""
            self.assertEqual(data, b"")

    def test_http_drain_timeout_defers_real_store_close_until_lookup_exits(self):
        entered, release, finished = (threading.Event() for _ in range(3))
        with tempfile.TemporaryDirectory() as directory:
            store = MetadataStore(Path(directory) / "metadata.db")
            service = TrackMetadataService(store, Mock())
            state = StateStore(60)
            state.update_decks(
                DeckObservation(
                    decks=(DeckState("1", True, "First", "Artist"), DeckState("2", True, "Second", "Artist")),
                    observed_at="now",
                )
            )
            server = HttpServer(state, 0, track_metadata=service)
            server.start()
            url = "http://%s:%d/state/enriched" % server.address
            original_get = store.get

            def delayed_get(*args):
                entered.set()
                release.wait(3)
                return original_get(*args)

            errors = []

            def request():
                try:
                    with urlopen(url, timeout=3) as response:
                        response.read()
                except OSError:
                    pass  # Shutdown deliberately closes the socket at the deadline.
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()

            with (
                patch.object(store, "get", side_effect=delayed_get),
                patch("rekordbox_sync.api.http._DRAIN_TIMEOUT_SECONDS", 0.05),
            ):
                worker = threading.Thread(target=request)
                worker.start()
                self.assertTrue(entered.wait(1))
                try:
                    server.stop()
                    service.close()
                    self.assertFalse(service._closed)
                finally:
                    release.set()
                worker.join(3)
                deadline = time.monotonic() + 2
                while not service._closed and time.monotonic() < deadline:
                    time.sleep(0.01)
            self.assertTrue(service._closed)
            with self.assertRaises(sqlite3.ProgrammingError):
                store.get("First", "Artist")
            self.assertEqual(errors, [])
            self.assertTrue(finished.is_set())
