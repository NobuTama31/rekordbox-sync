# Architecture

The reusable Python library is the center of the project. HTTP and CLI are consumers.

Dependency direction:

```text
CLI / HTTP -> RekordboxMonitor -> source protocols + StateStore
UIA source -> capture.ps1 + deck_parser
MIDI source -> MIDI port + Flx4Decoder
StateStore -> immutable models
HTTP serialization -> immutable snapshot
```

Public entry points are `RekordboxMonitor`, `MonitorConfig`, and snapshot model types exported from the package root. The monitor owns startup, shutdown, retry scheduling and source lifetimes. It never starts on import. Users may inject source implementations for replay or embedding.

Source adapters return typed observations. They do not know the state store or HTTP. UIA capture and parsing are separate; MIDI connection management and byte decoding are separate. Protocols express the required source behavior without an inheritance framework.

StateStore owns synchronization and freshness decisions. It exposes update methods and immutable snapshots, never shared mutable dictionaries or locks. UI observations expire, while last known hardware positions remain until disconnect. On failure, data is invalidated rather than replaced with zero.

The API serializer alone owns JSON schema version 1. Internal names can evolve independently of existing JSON fields. A snapshot is internally consistent but its UI values were sampled sequentially; it is not an atomic view of Rekordbox.

Current limits: horizontal two-deck UI only; UI reads take seconds; PLAY, MASTER and ON AIR remain unknown. This refactor does not add layout support, WebSocket, OBS output or inferred playback.

Validation: replay recorded fixtures, exercise decoder and stale/disconnect cases, lifecycle with fake sources, HTTP contract tests, installed-wheel resource checks, and a live smoke check. Raw research artifacts stay in logs/ and external/ (ignored).

## Independent song catalog

`src/song_catalog/` owns read-only CSV loading, indexing and title+artist lookup. It imports no Rekordbox, MIDI, Windows or HTTP code. Additional CSV columns are data, not new source-adapter behavior.

`api/metadata.py` joins monitor snapshots to a catalog through a small lookup protocol. The monitor and StateStore do not depend on the catalog. `/state/enriched` and `/song-info` are optional consumers; the existing state contract stays unchanged. See song_catalog.md for matching, reload and error semantics.

## Desktop GUI

The optional CustomTkinter GUI consumes the HTTP API. `gui/client.py` performs background reads and keeps only the latest response in memory; widgets are updated exclusively on the Tk thread. `gui/controller.py` can own a local monitor and HTTP server through public interfaces, but never stops a pre-existing server. GUI dependencies are optional and do not enter the capture/model layers. See gui.md.
