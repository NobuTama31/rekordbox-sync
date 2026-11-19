"""Optional ownership of a local API. Existing servers are never stopped."""

import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

from song_catalog import CsvSongCatalog
from track_metadata import ItunesSearchProvider, MetadataStore

from .. import MonitorConfig, RekordboxMonitor
from ..api.http import HttpServer
from ..metadata_service import TrackMetadataService, resolve_metadata_db_path
from .client import validate_api_url


def _stop_workers(actions, deadline):
    """Request every independent stop before collecting under one deadline."""
    errors = []

    def run(action):
        try:
            action()
        except Exception as error:
            errors.append(error)

    workers = [threading.Thread(target=run, args=(action,), daemon=True) for action in actions]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=max(0, deadline - time.monotonic()))
    if any(worker.is_alive() for worker in workers):
        raise RuntimeError("Cleanup is still running; retry closing after it finishes")
    if errors:
        raise RuntimeError(f"Cleanup failed: {errors[0]}") from errors[0]


class LocalApiController:
    """Call lifecycle methods on a serialized background worker."""

    def __init__(self):
        self._monitor = None
        self._server = None
        self._metadata_service = None
        self._stop_lock = threading.Lock()

    @property
    def owned(self):
        return any(value is not None for value in (self._server, self._monitor, self._metadata_service))

    def start(
        self,
        api_url: str,
        csv_path: str = "",
        midi_port: str = "DDJ-FLX4 0",
        save_suspect_captures: bool = False,
        uia_resident: bool = True,
        keep_suspect_history: bool = False,
    ) -> bool:
        if self.owned:
            return True
        url = urlsplit(validate_api_url(api_url))
        if url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("GUIから起動できるのはローカルHTTP APIだけです")
        # Binding remains the final guard against another process starting meanwhile.
        try:
            with urlopen(api_url.rstrip("/") + "/health", timeout=1):
                # A server already answers: connect to it, but it is
                # not owned — it will not be stopped from here.
                return False
        except OSError:
            pass
        catalog = CsvSongCatalog(csv_path) if csv_path.strip() else None
        if catalog:
            catalog.reload()
        monitor = RekordboxMonitor(
            MonitorConfig(
                midi_port=midi_port,
                save_suspect_captures=save_suspect_captures,
                keep_suspect_history=keep_suspect_history,
                uia_resident=uia_resident,
            )
        )
        service = TrackMetadataService(MetadataStore(resolve_metadata_db_path()), ItunesSearchProvider())
        server = HttpServer(monitor, url.port or 80, catalog=catalog, track_metadata=service)
        try:
            server.start()
            monitor.start()
            service.start(monitor.snapshot)
        except Exception:
            service.close()
            server.stop()
            monitor.stop()
            raise
        self._monitor, self._server, self._metadata_service = monitor, server, service
        return True

    def stop(self):
        deadline = time.monotonic() + 5.0
        with self._stop_lock:
            service, server, monitor = self._metadata_service, self._server, self._monitor
            if service is not None:
                # Signal first: the metadata watcher stops starting
                # new lookups, so the parallel stops below drain what
                # is in flight instead of racing fresh work.
                service._request_stop()

            def stop_monitor():
                if monitor is not None:
                    monitor.stop()
                    self._monitor = None

            def stop_http_and_metadata():
                try:
                    if server is not None:
                        server.stop()
                        self._server = None
                finally:
                    if service is not None:
                        service.close()
                        self._metadata_service = None

            _stop_workers((stop_monitor, stop_http_and_metadata), deadline)
