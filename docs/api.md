# Public interfaces

## Python

Import `RekordboxMonitor`, `MonitorConfig`, `LiveStateSnapshot`, `DeckState`, `MixerState`, `FaderState`, `SourceStatus`, `DeckObservation`, and `MixerObservation` from `rekordbox_sync`.

- `RekordboxMonitor(config=None, *, deck_source=None, mixer_source=None)` constructs without I/O.
- `start()` starts asynchronous capture and is idempotent while running.
- `snapshot()` returns an immutable `LiveStateSnapshot`; startup may return empty decks and unknown faders.
- `stop()` waits for workers to finish and release sources, and is idempotent. A source that fails to stop within its bound causes an error, rather than silent reuse of a still-running worker.
- A context manager calls start/stop. A stopped monitor can be started again; previous observations are invalidated.
- A monitor exclusively owns supplied source instances. Do not share the same source between monitors. `read()` must return within a bounded time, and `close()` must release resources and allow a later read to reconnect.
- Sources implement `DeckSource` / `MixerSource` in `rekordbox_sync.sources.protocols`. Use them to replay observations or introduce another capture mechanism.
- `HttpServer` in `rekordbox_sync.api.http` accepts a provider implementing `snapshot()`. Its lifecycle is separate from the monitor's. It binds localhost only.
- `to_api_dict()` in `rekordbox_sync.api.serialization` converts a snapshot to the existing wire format. Returned dictionaries are independent of internal state.

Snapshots use tuples for decks and frozen dataclasses throughout. Mixer fields explicitly use `hardware_channel_1`, `hardware_channel_2`, and `hardware_crossfader`.

## HTTP schema version 1

`GET /state` returns JSON, UTF-8, `Cache-Control: no-store`. It preserves the existing paths and field names. `GET /health` returns the same `sources` object only. Unknown paths return 404. Responses are 200 even if a source is unavailable; consumers must inspect source statuses.

| Field | Meaning |
| --- | --- |
| schema_version | Integer 1; breaking wire changes require a new version |
| served_at | UTC response snapshot time |
| decks | Map of string deck IDs; empty when UI data is unavailable |
| decks[id].loaded | Whether a track is loaded |
| title / artist | Track metadata, null when unknown |
| bpm_original / bpm_current | Original/current BPM, distinct observations |
| position_seconds / remaining_seconds | Displayed elapsed and remaining time in seconds, 0.1-second display precision |
| playing / master | Currently null; no inference is presented as direct state |
| mixer.channel_1 / channel_2 / crossfader | Hardware fader observations |
| mixer[field].value | Normalized 0..1, null until observed |
| mixer[field].raw_14bit | Raw 0..16383, null until observed |
| mixer[field].observed_at | UTC last hardware observation time |
| on_air_estimate | Currently null |
| sources.uia | status, observed_at, window_minimized, age_seconds, error |
| sources.midi | status, control_event_count, error, values_describe |

UI status is `ok` or `unavailable`; MIDI status is `connected` or `unavailable`. A connected MIDI source may have all-null values before any controls move. An old MIDI timestamp does not by itself mean disconnection. UI freshness uses a monotonic clock; observed_at timestamps use UTC. Source failures clear values independently.

Existing snapshots are not transactionally atomic with Rekordbox: UI elements are read sequentially and MIDI arrives independently. Consumers must not treat fresh UI status as proof of simultaneous track/transport observations.

No CORS policy, remote binding, authentication, WebSocket or write/control endpoints are provided in this local prototype. Non-browser clients can use HTTP; browser cross-origin integration requires a separately designed CORS policy.

Optional CSV metadata endpoints `/song-info` and `/state/enriched` are documented in [song_catalog.md](song_catalog.md). Enable them with `serve --catalog path/to/song_info.csv`. `HttpServer(..., catalog=...)` accepts an independent catalog implementation.

With external track metadata enabled, `/state/enriched` also carries a `track_info` map (canonical title/artist, album, release date, artwork URL) served from a persistent SQLite store; fetching happens in the background, never inside a request. `/state/enriched` returns 503 only when neither a catalog nor track metadata is configured. See [track_metadata.md](track_metadata.md). `HttpServer(..., track_metadata=...)` accepts a lookup object with `lookup(title, artist)`.

## Configuration

`MonitorConfig` includes `midi_port`, `uia_poll_interval_seconds`, `midi_poll_interval_seconds`, `retry_interval_seconds`, `uia_timeout_seconds`, `uia_stale_after_seconds`, `midi_port_check_interval_seconds`, and `midi_pair_timeout_seconds`. Durations must be finite and positive. Poll intervals are waits after capture completes, not guarantees of sample frequency.
