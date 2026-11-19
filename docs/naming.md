# Naming conventions

| Item | Convention | Example |
| --- | --- | --- |
| Distribution/package | lowercase, package uses underscores | rekordbox-sync / rekordbox_sync |
| Module/function/variable | snake_case | deck_parser, observed_at |
| Type | PascalCase | RekordboxMonitor, MonitorConfig, DeckState |
| Acronym in a type | normal word casing | UiaSource, MidiSource, HttpServer |
| Constant | UPPER_SNAKE_CASE | SCHEMA_VERSION |
| Internal member | leading underscore | _lock, _port |
| Duration | explicit unit | poll_interval_seconds |
| UTC timestamp | _at | observed_at |
| Inferred state | explicit estimate | on_air_estimate |

Use source-specific names for hardware observations: `hardware_channel_1`, `hardware_channel_2`, `hardware_crossfader`. They do not imply effective software mixer gain. Existing JSON names remain unchanged for compatibility.

Public verbs: `start()`, `stop()`, `snapshot()`. Context-manager entry starts the monitor and exit stops it. Avoid generic helper/utils modules and unnecessary base classes. Functions are preferred for stateless parsing and serialization.
