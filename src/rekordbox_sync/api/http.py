"""Local HTTP presentation. Server lifetime does not control monitor lifetime."""

import hashlib
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from song_catalog import CatalogError

from ..models import LiveStateSnapshot
from .metadata import SongCatalog, enrich_snapshot
from .nowplaying import NowPlayingPresenter
from .serialization import to_api_dict

_OVERLAY_FILES = {
    "/overlay": ("index.html", "text/html; charset=utf-8"),
    "/overlay.css": ("overlay.css", "text/css; charset=utf-8"),
    "/overlay.js": ("overlay.js", "text/javascript; charset=utf-8"),
}
_DRAIN_TIMEOUT_SECONDS = 2.0


class _RequestStopped(Exception):
    pass


class _StoppingLookup:
    def __init__(self, lookup, stopping):
        self._lookup = lookup
        self._stopping = stopping

    def lookup(self, *args, **kwargs):
        if self._stopping.is_set():
            raise _RequestStopped()
        try:
            return self._lookup.lookup(*args, **kwargs)
        except RuntimeError:
            if self._stopping.is_set():
                raise _RequestStopped() from None
            raise


class _DrainingHttpServer(ThreadingHTTPServer):
    def __init__(self, *args):
        self._requests = set()
        self._requests_changed = threading.Condition()
        self.stopping = threading.Event()
        super().__init__(*args)

    def process_request(self, request, client_address):
        with self._requests_changed:
            self._requests.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._requests_changed:
                self._requests.discard(request)
                self._requests_changed.notify_all()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._requests_changed:
                self._requests.discard(request)
                self._requests_changed.notify_all()

    def drain(self, deadline):
        with self._requests_changed:
            self._requests_changed.wait_for(lambda: not self._requests, timeout=max(0, deadline - time.monotonic()))
            remaining = tuple(self._requests)
        # Unblock incomplete headers and slow socket writers. A handler stuck
        # inside a lookup is not forcibly killed; the metadata service retains
        # its store until that active lookup's finally block releases it.
        for request in remaining:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # The handler may have closed it since the snapshot.
            request.close()

    def handle_error(self, request, client_address):
        if self.stopping.is_set() and isinstance(sys.exc_info()[1], OSError):
            return
        super().handle_error(request, client_address)


def _overlay_bytes(name: str) -> bytes:
    return resources.files(__package__).joinpath("overlay", name).read_bytes()


class SnapshotProvider(Protocol):
    def snapshot(self) -> LiveStateSnapshot: ...


def _make_handler(
    provider: SnapshotProvider,
    catalog: SongCatalog | None = None,
    track_metadata=None,
    now_playing: NowPlayingPresenter | None = None,
):
    if now_playing is None:
        now_playing = NowPlayingPresenter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.server.stopping.is_set():
                return
            try:
                self._get()
            except _RequestStopped:
                self.close_connection = True
            except OSError:
                if not self.server.stopping.is_set():
                    raise

        def _get(self):
            url = urlsplit(self.path)
            if url.path in _OVERLAY_FILES:
                name, content_type = _OVERLAY_FILES[url.path]
                try:
                    payload = _overlay_bytes(name)
                except (FileNotFoundError, ModuleNotFoundError):
                    self.send_error(404)
                    return
                self._send_bytes(payload, content_type)
                return
            if url.path == "/nowplaying.json":
                try:
                    value = now_playing.present(provider.snapshot(), catalog, track_metadata)
                except CatalogError as error:
                    self._send_json({"error": str(error)}, 503)
                    return
                self._send_json_with_etag(value)
                return
            if url.path not in ("/state", "/health", "/song-info", "/state/enriched"):
                self.send_error(404)
                return
            if url.path in ("/song-info", "/state/enriched"):
                # /song-info needs the catalog itself; the enriched
                # state is useful with either source attached.
                if catalog is None and (url.path == "/song-info" or track_metadata is None):
                    self._send_json({"error": "Song catalog is not configured"}, 503)
                    return
                query = parse_qs(url.query, keep_blank_values=True)
                try:
                    if url.path == "/song-info":
                        if any(
                            len(query.get(key, [])) != 1 or not query[key][0].strip() for key in ("title", "artist")
                        ):
                            raise ValueError("One nonempty title and artist are required")
                        value = catalog.lookup(
                            query["title"][0], query["artist"][0], fields=query.get("field")
                        ).to_dict()
                    else:
                        value = enrich_snapshot(
                            provider.snapshot(), catalog, fields=query.get("field"), track_metadata=track_metadata
                        )
                except CatalogError as error:
                    self._send_json({"error": str(error)}, 503)
                    return
                except ValueError as error:
                    self._send_json({"error": str(error)}, 400)
                    return
            else:
                current = to_api_dict(provider.snapshot())
                value = current if url.path == "/state" else {"sources": current["sources"]}
            self._send_json(value)

        def _send_json(self, value, status=200):
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self._write(payload)

        def _send_json_with_etag(self, value):
            # Only /nowplaying.json gets an ETag: the overlay polls it
            # constantly and its payload changes only on track changes,
            # so 304s save real work. /state changes on every poll
            # (served_at, age_seconds) and would never match.
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            etag = f'"{hashlib.sha256(payload).hexdigest()}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self._write(payload)

        def _send_bytes(self, payload, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self._write(payload)

        def _write(self, payload):
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    return Handler


class HttpServer:
    def __init__(
        self, provider: SnapshotProvider, port: int = 8765, *, catalog: SongCatalog | None = None, track_metadata=None
    ):
        self._provider = provider
        self._port = port
        self._catalog = catalog
        self._track_metadata = track_metadata
        self._now_playing = NowPlayingPresenter()
        self._server = None
        self._thread = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("HTTP server is not running")
        return self._server.server_address

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = _DrainingHttpServer(("127.0.0.1", self._port), BaseHTTPRequestHandler)
        metadata = (
            _StoppingLookup(self._track_metadata, self._server.stopping) if self._track_metadata is not None else None
        )
        self._server.RequestHandlerClass = _make_handler(self._provider, self._catalog, metadata, self._now_playing)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, name="rekordbox-http", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        deadline = time.monotonic() + _DRAIN_TIMEOUT_SECONDS
        self._server.stopping.set()
        self._server.shutdown()
        self._server.drain(deadline)
        self._server.server_close()
        self._thread.join()
        self._server = None
        self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_args):
        self.stop()
