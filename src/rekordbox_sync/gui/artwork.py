"""Background jacket-image fetching for the GUI.

The Tk thread never does network or image work: it asks for a URL and gets
either a ready PIL image or None, while a single worker thread fetches,
decodes and squares the image. Results are cached per URL for the session;
failures are not retried. Without Pillow the loader simply never returns
an image and the decks show their placeholder.
"""

import io
import logging
import queue
import threading
from urllib.parse import urlsplit
from urllib.request import urlopen

_LOGGER = logging.getLogger(__name__)
_IMAGE_PIXELS = 184
_CACHE_LIMIT = 96

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow ships with customtkinter
    Image = None


def _prepare(raw: bytes):
    image = Image.open(io.BytesIO(raw))
    image = image.convert("RGB")
    width, height = image.size
    side = min(width, height)
    image = image.crop(((width - side) // 2, (height - side) // 2, (width + side) // 2, (height + side) // 2))
    return image.resize((_IMAGE_PIXELS, _IMAGE_PIXELS), Image.LANCZOS)


class ArtworkLoader:
    def __init__(self, opener=None, timeout_seconds: float = 5.0):
        self._opener = opener or urlopen
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._cache: dict[str, object] = {}
        self._failed: set[str] = set()
        self._inflight: set[str] = set()
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = None

    def image_for(self, url):
        """Ready image for url, or None; schedules a fetch on first sight."""
        if Image is None or not url or urlsplit(url).scheme not in ("http", "https"):
            return None
        with self._lock:
            if url in self._cache:
                return self._cache[url]
            if url in self._failed or url in self._inflight:
                return None
            self._inflight.add(url)
        self._ensure_thread()
        self._queue.put(url)
        return None

    def stop(self):
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=self._timeout_seconds + 1)

    def _ensure_thread(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="rekordbox-gui-artwork", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            url = self._queue.get()
            if url is None or self._stop.is_set():
                return
            try:
                with self._opener(url, timeout=self._timeout_seconds) as response:
                    raw = response.read()
                image = _prepare(raw)
            except Exception:
                _LOGGER.debug("Artwork fetch failed for %s", url, exc_info=True)
                with self._lock:
                    self._inflight.discard(url)
                    self._failed.add(url)
                continue
            with self._lock:
                self._inflight.discard(url)
                self._cache[url] = image
                while len(self._cache) > _CACHE_LIMIT:
                    self._cache.pop(next(iter(self._cache)))
