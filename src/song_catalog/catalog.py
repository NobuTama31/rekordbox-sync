"""Read-only lookup over the user's song CSV (title + artist -> fields).

Independent of Rekordbox, Windows and MIDI by design; the CSV is
external data and is never written. See docs/song_catalog.md.
"""

import csv
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class CatalogError(ValueError):
    """CSV is missing, unreadable or invalid; old cached data is not served."""


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


@dataclass(frozen=True)
class SongLookupResult:
    status: Literal["matched", "not_found", "ambiguous"]
    match_count: int
    fields: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict:
        return {"status": self.status, "match_count": self.match_count, "fields": dict(self.fields)}


class CsvSongCatalog:
    """Thread-safe, read-only title+artist lookup with atomic CSV reload.

    Keys use NFC and surrounding-whitespace trimming only. All cell values remain
    strings. A change to file size/mtime triggers a reload at the next lookup.
    Call reload() explicitly when an editor preserves both size and mtime.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        title_column: str = "タイトル",
        artist_column: str = "歌手",
        encoding: str = "utf-8-sig",
    ):
        if title_column == artist_column:
            raise ValueError("Title and artist columns must be different")
        self._path = Path(path).resolve()
        self._title_column = title_column
        self._artist_column = artist_column
        self._encoding = encoding
        self._lock = threading.RLock()
        self._signature = None
        self._columns = ()
        self._index = {}
        self._row_count = 0

    def _file_signature(self):
        stat = self._path.stat()
        return stat.st_mtime_ns, stat.st_size

    def reload(self) -> None:
        with self._lock:
            self._signature = None
            self._refresh()

    def _refresh(self):
        try:
            signature = self._file_signature()
            if signature == self._signature:
                return
            index = {}
            count = 0
            with self._path.open(encoding=self._encoding, newline="") as stream:
                reader = csv.DictReader(stream, strict=True)
                columns = tuple(reader.fieldnames or ())
                if not columns or len(set(columns)) != len(columns) or any(not name for name in columns):
                    raise CatalogError("CSV requires nonempty, unique column names")
                if self._title_column not in columns or self._artist_column not in columns:
                    raise CatalogError("CSV is missing the configured title or artist column")
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise CatalogError(f"CSV column count mismatch near line {reader.line_num}")
                    key = (_normalize(row[self._title_column]), _normalize(row[self._artist_column]))
                    if not all(key):
                        raise CatalogError(f"Empty title or artist near line {reader.line_num}")
                    index.setdefault(key, []).append(tuple(row.items()))
                    count += 1
            if self._file_signature() != signature:
                raise CatalogError("CSV changed while being read; retry lookup")
            # Publish all new data together only after validation finishes.
            self._index = {key: tuple(rows) for key, rows in index.items()}
            self._columns = columns
            self._row_count = count
            self._signature = signature
        except (OSError, UnicodeError, csv.Error, CatalogError) as error:
            self._signature = None
            self._index = {}
            self._columns = ()
            self._row_count = 0
            raise CatalogError(str(error)) from error

    @property
    def row_count(self) -> int:
        with self._lock:
            self._refresh()
            return self._row_count

    @property
    def columns(self) -> tuple[str, ...]:
        with self._lock:
            self._refresh()
            return self._columns

    def lookup(self, title: str, artist: str, *, fields: Sequence[str] | None = None) -> SongLookupResult:
        with self._lock:
            self._refresh()
            selected = (
                tuple(fields)
                if fields is not None
                else tuple(name for name in self._columns if name not in (self._title_column, self._artist_column))
            )
            if isinstance(fields, str) or len(set(selected)) != len(selected):
                raise ValueError("fields must be a sequence of unique column names")
            if any(name not in self._columns for name in selected):
                raise ValueError("Unknown output column")
            rows = self._index.get((_normalize(title), _normalize(artist)), ())
            if not rows:
                return SongLookupResult("not_found", 0)
            if len(rows) != 1:
                return SongLookupResult("ambiguous", len(rows))
            row = dict(rows[0])
            return SongLookupResult("matched", 1, tuple((name, row[name]) for name in selected))
