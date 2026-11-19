"""Background HTTP reads; never accesses Tk widgets or persists snapshots."""

import json
import queue
import threading
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen


def validate_api_url(value: str) -> str:
    # Base URL only — no credentials, path or query: the client appends
    # the endpoint paths itself.
    url = urlsplit(value.strip())
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("APIのURLを http://127.0.0.1:8765 の形式で入力してください")
    if url.path not in ("", "/") or url.query or url.fragment:
        raise ValueError("APIのURLにはパスやクエリを含めないでください")
    try:
        url.port
    except ValueError as error:
        raise ValueError("ポート番号が不正です") from error
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class ApiUpdate:
    api_url: str
    payload: dict | None
    error: str | None = None
    metadata_error: str | None = None


class ApiClient:
    def __init__(self, api_url: str, timeout_seconds: float = 2.0, fetch_interval_seconds: float = 0.05):
        self._url = validate_api_url(api_url)
        self._timeout_seconds = timeout_seconds
        self._fetch_interval_seconds = fetch_interval_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._updates = queue.Queue(maxsize=1)
        self._thread = None

    def set_url(self, url: str):
        with self._lock:
            self._url = validate_api_url(url)
        self._wake.set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rekordbox-gui-http", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=self._timeout_seconds * 2 + 1)

    def latest(self) -> ApiUpdate | None:
        try:
            return self._updates.get_nowait()
        except queue.Empty:
            return None

    def _read(self, url):
        with urlopen(url, timeout=self._timeout_seconds) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            raise ValueError("APIがJSONオブジェクトを返しませんでした")
        return data

    def fetch(self, base_url: str) -> ApiUpdate:
        try:
            metadata_error = None
            try:
                payload = self._read(base_url + "/state/enriched")
            except HTTPError as error:
                # An API without enrichment (404) or without a catalog
                # (503) still serves plain /state: show the live state
                # and report the metadata side as unavailable instead.
                if error.code not in (404, 503):
                    raise
                payload = {"state": self._read(base_url + "/state"), "song_info": {}}
                metadata_error = "CSV未設定または読み取り不可" if error.code == 503 else "CSV連携に未対応のAPI"
            state = payload.get("state")
            if (
                not isinstance(state, dict)
                or not isinstance(state.get("decks"), dict)
                or not isinstance(state.get("sources"), dict)
            ):
                raise ValueError("ライブ状態のJSON形式が不正です")
            return ApiUpdate(base_url, payload, metadata_error=metadata_error)
        except Exception as error:
            return ApiUpdate(base_url, None, error=f"APIに接続できません: {error}")

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                url = self._url
            update = self.fetch(url)
            # Latest-only delivery: an unconsumed update is replaced,
            # never queued — the GUI must not work through a backlog
            # of stale states after a stall.
            try:
                self._updates.get_nowait()
            except queue.Empty:
                pass
            self._updates.put_nowait(update)
            self._wake.wait(self._fetch_interval_seconds)
            self._wake.clear()
