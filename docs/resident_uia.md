# Resident UIA capture (design + implementation brief)

Status: implemented. `tests/test_uia_resident.py` pins the Python-side
contract; `tests/test_uia_transport.py` exercises real subprocess pipes,
UTF-8, process cleanup, and the PowerShell protocol with fake capture data.
Full-session validation with Rekordbox remains pending.

## Why

`UiaSource` spawns a fresh `powershell.exe` for every poll. Process startup
plus a full UI-tree enumeration makes each read slow, and reads fail or time
out exactly when Rekordbox is busy redrawing (fader moves, waveform scroll).
The StateStore now holds the last good decks through failed reads, but that
only absorbs the symptom. A resident PowerShell process removes the startup
cost and keeps the UIA assemblies loaded.

## Shape

`UiaResidentSource` is a drop-in `DeckSource` (same `read()` / `close()` as
`UiaSource`, see `sources/protocols.py`). `RekordboxMonitor` already accepts
an injected `deck_source`, so wiring is a construction choice, not a
redesign. Do not change `/state`, `/health`, or schema v1.

Select the resident source explicitly when constructing a monitor (the
default remains the one-shot source):

```python
from rekordbox_sync import MonitorConfig, RekordboxMonitor
from rekordbox_sync.sources.uia.resident import UiaResidentSource

config = MonitorConfig()
with RekordboxMonitor(
    config,
    deck_source=UiaResidentSource(timeout_seconds=config.uia_timeout_seconds),
) as monitor:
    # Read monitor.snapshot() while the monitor runs asynchronously.
    ...
```

### Wire protocol (Python <-> PowerShell, stdio, one line each way)

- Request: one line of JSON, `{"label": "live-api"}`.
- Response: exactly one line per request — the same JSON object that
  `capture.ps1 -StdoutJson` emits today (`received_at`, `elements`, ...),
  so `decode_observation` and `deck_parser` stay untouched.
- Requests are strictly sequential; the source serializes `read()` calls
  with a lock. No request ids.

### Failure semantics (pinned by tests; do not soften)

A failed read is *no information*, never an empty state. The StateStore's
hold-last-good behaviour depends on this distinction.

- Response timeout, process exit (EOF), or a response line that is not
  valid JSON: terminate the process, forget it, raise `RuntimeError`.
  The next `read()` spawns a fresh process and may succeed.
- A response that *is* valid JSON flows through `decode_observation`
  unchanged — including payload-level errors (an observation whose
  `error` is set and whose decks are empty), exactly like the one-shot
  path today. The process stays alive in this case.
- `close()` terminates the process and is idempotent. `read()` after
  `close()` spawns a fresh process (parity with `UiaSource`, whose
  `close()` is a no-op).
- Without an injected process factory, `read()` on a non-Windows platform
  raises `RuntimeError`, like `UiaSource`.

### Process handle seam

The source talks to the child process only through a small handle:

- `write_line(text: str) -> None`
- `read_line(timeout_seconds: float) -> str | None` — `None` on timeout,
  raises `EOFError` when the stream is closed.
- `is_alive() -> bool`
- `terminate() -> None`

`UiaResidentSource(timeout_seconds=10.0, process_factory=None, platform=None, on_suspect=None)`.
The optional diagnostic hook receives raw snapshots that decode to an error
or a loaded deck with missing BPM. Hook exceptions never fail a read; with
the default `None`, monitoring does not persist snapshots.
The default factory wraps `subprocess.Popen` (reader thread + queue for
timed line reads, `CREATE_NO_WINDOW`, UTF-8) and launches:

```
powershell.exe -NoProfile -File capture.ps1 -Serve -Label live-api
```

Tests inject a fake factory, so they run on any platform.

### PowerShell side

Add a `-Serve` mode to `capture.ps1`. The enumeration body must stay
single-sourced: `-Serve` loops over stdin lines and answers each with the
same compressed JSON the `-StdoutJson` path emits; the one-shot path runs
the body once. Do not duplicate the enumeration code in a second script.

Per request, catch capture exceptions (e.g. Rekordbox not running) and
answer with a JSON object carrying an `error` field and no elements, then
keep serving. Exit the loop on stdin EOF. Write errors only to stderr —
stdout is the protocol channel and must carry nothing but response lines.

Root selection (both modes): `Get-UiaRoot` picks the process's largest
top-level window. Picking the first window by process id can root the
walk at an auxiliary window — observed live on 2026-11-18 as a capture
with `window_name` "tooltip" and 0 elements — so the no-anchor re-walk
re-selects the root as well as re-walking.

## Tracked-element reads (v2, designed 2026-11-17)

Status: implemented in `capture.ps1 -Serve`; the selection rule below is
pinned by `tests/test_tracked_selection.py` and exercised against the real
PowerShell implementation by `tests/test_uia_tracked_transport.py` with a
fake UIA provider. The resident source is the default since 2026-11-18,
after that morning's live validation (a clean session once the root
selection was fixed); one-shot remains available via
`MonitorConfig(uia_resident=False)`, the CLI's `--no-uia-resident`, or
unchecking the GUI settings checkbox.

### Why

Even after the 2026-11-17 slimming, a full walk costs roughly 4,000 UIA
round trips per poll, every one serviced by Rekordbox's own UI thread —
and the `FindAll` enumeration is precisely the step that degrades under
load (the multi-second "found 0 of 0 anchors" outages while the decks
are visibly fine). The fields the monitor needs live on a small, stable
set of elements; re-locating them from scratch on every poll is the
waste. MIDI is push-driven; this makes the UIA side read only what it
already knows.

### Shape (all inside `capture.ps1 -Serve`; wire, parser, Python unchanged)

The serve loop keeps state between requests: the tracked element
references, each tracked element's last stable text and offscreen
flag, and the time of the last full walk. One-shot mode is untouched
(always a full walk).

- **Full walk** = today's `Get-UiaCapture`. Afterwards, extract the
  tracked set with a purely positional superset rule (the parser stays
  the only interpreter of what the elements mean): the 2 BEAT SYNC
  anchors + the 2 play/pause controls + every visible Text/Edit whose
  rect lies in the deck band, from 16px above the anchors' y to 40px
  below the play controls' bottom edge.
- **Tracked read** = for each tracked element, re-read Current
  (name, type, automation_id, offscreen, rect) plus the Edit value and
  toggle state, and emit rows in exactly the full walk's shape. No
  `FindAll`, so there is no enumeration to go partial.
- **Fall back to a full walk** (and re-extract the tracked set) when:
  not yet located; any tracked element read throws (stale reference);
  a tracked element's stable text or offscreen flag changed since the
  last read; or 60 seconds have passed since the last full walk
  (safety re-sync). "Stable" is decided by shape, not by role: texts
  matching the volatile shapes — timers (`-?\d+:\d{2}`,
  `-?\d+:\d{2}\.\d`, `\.\d`) and the BPM value (`\s*\d+\.\d{2}\s*`) —
  may change freely; anything else changing means the deck's content
  changed (a track load/unload rewrites or recreates it), so re-walk.
  PowerShell never learns which element is the title: the sentinel
  only decides *when* to walk, never what the snapshot means — the
  same category as the existing no-anchor re-walk. (Considered and
  rejected: passing the parser's title-element indices in the request
  — it couples the parser to enumeration internals and makes the
  protocol stateful; and replicating the parser's title geometry in
  PowerShell — a second implementation of a rule that would drift.)
- Responses gain `capture_mode`: `"full"` or `"tracked"`.
  `decode_observation` ignores it; it exists so diagnostic captures
  show which path produced them.

### Validation of the core claim (2026-11-17, simulation)

Applying the selection rule to every recorded capture and parsing the
subset gives byte-identical parser output to the full capture, at
553–664 → 95–97 elements (about 300 UIA round trips per poll instead
of about 4,000). `tests/test_tracked_selection.py` pins this; the
PowerShell implementation must keep it true.

### Risks and guards

- Qt may recreate a widget silently, leaving a tracked reference
  returning its last values. The stable-text sentinel, the 60s re-sync, and
  the throw-on-stale fallback bound this; a same-title track swap is
  the worst case and is corrected by the re-sync at the latest (the
  BPM Edit is tracked directly, so tempo stays live throughout).
- Tracked snapshots hold ~95 elements, so the parser's anchor-error
  diagnostics ("N elements") change scale; `capture_mode` keeps the
  two paths distinguishable in suspect captures.
- When the resident source becomes the default, give
  `UiaResidentSource` the same `on_suspect` diagnostic hook
  `UiaSource` has, so the GUI's diagnostic option keeps working.

### Acceptance

- A live session on the tracked path: deck display matches the full
  walk's across track loads, tempo moves, and idle periods; the
  Windows protocol tests cover full → tracked → (stable-text
  change) → full.
- Full suite green, including `tests/test_tracked_selection.py`.

### v2 implementation validation (2026-11-17)

- Windows / Python 3.13: all 120 tests passed with
  `.venv/Scripts/python.exe -m unittest discover -s tests`.
- The PowerShell tests verify the selected rows and parser output against
  every recorded fixture, volatile text updates without enumeration,
  stable-text/offscreen changes, stale references, the 60-second boundary,
  no-anchor re-walks, and recovery after a failed full walk.
- Built and installed a wheel in a temporary directory outside the
  repository. All 15 transport tests passed using the installed package
  and its PowerShell resource, with a fake UIA provider.
- No live Rekordbox session was used for these v2 checks; track loads,
  tempo changes, idle periods, and provider behavior still need live validation.

## PLAY state: evidence before implementation

`capture.ps1` already records `toggle_state` for any element exposing a
TogglePattern, and `deck_parser` already locates the two
`プレイ/ポーズ` / `Play/Pause` controls (as geometry anchors only).

Checked against the recorded fixtures (2026-11-17): the play controls are
`ControlType.Button` with **no supported patterns at all**, and no element
in the ~590-element tree carries a `toggle_state`. Rekordbox's custom UI
exposes no button-state channel to UIA. Do not build PLAY detection on
button state unless new evidence appears.

To close the question definitively on the real machine, use
`tools/probe_uia_diff.py`: capture once while a deck is paused and once
while it plays (diagnostic capture writes `logs/latest_uia.json`), then
diff the two files. The script reports every element whose fields differ,
so any state channel — expected or not — shows up. If only the timer texts
differ, the remaining route is position advance between captures, which is
*inferred motion*: per `docs/naming.md` it must be exposed under an
explicit estimate name (e.g. `playing_estimate`), never as confirmed
`playing`.

## Acceptance

- `tests/test_uia_resident.py` passes in full; the rest of the suite stays
  green.
- On the real machine: monitor runs on the resident source for a full DJ
  session without the track display dropping during fader moves.
- One-shot `capture.ps1` behaviour (diagnostic JSON files, `-StdoutJson`)
  is unchanged.

### Validation (2026-11-17)

- Windows / Python 3.13: all 94 tests passed with
  `.venv/Scripts/python.exe -m unittest discover -s tests`.
- Built a wheel and installed it in a temporary directory outside the
  repository. Its packaged PowerShell script answered repeated requests;
  resident source reads and child/reader shutdown succeeded.
- Rekordbox was not running during the smoke check, so this verifies the
  capture-error response path, not successful live deck capture. A full DJ
  session and the paused/playing UI comparison remain unverified.
