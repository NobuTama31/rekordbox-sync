# Brief for the implementer: fix the 2026-11-18 audit findings

Task: fix the 11 findings of the 2026-11-18 audit (base commit
`5ee6ba5`), in the audit's stage order, one stage per commit series.
Findings #1, #5 and #7 were independently re-verified against the code
by the reviewer; treat the audit's reproductions as ground truth, but
re-derive each one yourself before fixing (see principle 2).

## Working principles

1. **Stage order is fixed; never batch stages.** Stage 1: findings #1
   and #8. Stage 2: #2–#4 (shutdown). Stage 3: #5–#7 (display and
   retry semantics). Stage 4: #9–#11 (small optimizations and
   cleanup). Each stage lands as its own small commits and must leave
   the full suite green before the next stage starts.
2. **Reproduction first, as a regression test.** The audit reproduced
   every finding with one-off mocks, a live local HTTP server, an
   isolated SQLite store, or Node. Convert each reproduction into a
   durable test in the suite *before* fixing: the test must fail on
   the base code and pass after the fix. A fix without a failing-first
   test is not done. For the overlay.js finding (#6), where the suite
   has no JS harness, add a Node test that skips cleanly when Node is
   absent.
3. **No contract changes.** `/state`, `/health`, schema version 1,
   `/nowplaying.json`, and the public Python API stay byte-compatible.
   No new dependencies. If a fix seems to require either, stop and
   report instead of improvising.
4. **Never edit a test to make code pass.** If an existing test
   contradicts a fix, the fix is wrong or the contract is being
   renegotiated — stop and report. Changing a test's *harness* to
   follow a deliberate production change (the way the root-selection
   change retargeted an injection point) is allowed only when the
   test's assertions are untouched and the reason is stated in the
   commit message.
5. **Working-tree hygiene.** The Windows clone carries uncommitted
   changes (database-related and launcher scripts) that are *not*
   part of this task. Commit or stash them before starting; never mix
   them into a stage commit.
6. **Adjudicated warnings are not findings.** The audit already
   cleared these; do not "fix" them: `validate_api_url()`'s `url.port`
   access (property evaluation *is* the validation), the diff tool's
   `zip()` (the remainder is handled separately), and unused callback
   parameters that are part of a calling contract.
7. **Polling is not a target.** Do not lengthen the GUI refresh, the
   HTTP cadence, the 5 ms MIDI poll, or the UIA rescan behaviour to
   save CPU; the audit measured the polling and explicitly recommends
   against it. The observer's cost discipline lives in the capture
   layer, not in slower sampling.

## Stage 1 — widget lifecycle and test encoding

- **#1 (High) — `gui/app.py`: `_set_notice()` rebuilds the JSON page
  on every call.** The JSON frame, copy button and textbox are
  constructed inside `_set_notice()`, which runs on every GUI refresh;
  widgets accumulate undisposed, and the visible JSON page stops being
  the widget `_render_json()` writes to. Move page construction into
  `_build()` (constructed once); `_set_notice()` updates only the
  notice text and color. Verify: initial display, page switching,
  copy, and 100+ notice updates creating zero new widgets.
- **#8 (Medium) — tests depend on the platform default encoding.**
  Test code reads the PowerShell resource without an explicit
  encoding (fails under CP932) and parent/child processes disagree on
  UTF-8. Make every test-side file read encoding-explicit and align
  subprocess output decoding with what the child writes. Acceptance
  for this stage: **the plain Windows run — no `PYTHONUTF8`, no `-X
  utf8` — passes all tests**, and becomes the standard verification
  command for the remaining stages. Cover BOM and non-BOM Japanese
  output.

## Stage 2 — shutdown ordering (#2–#4)

The common defect: stop paths assume a sequence completes within a
deadline, but nothing enforces what happens to work still in flight
when it does not.

- **#2 (Medium) — the GUI can finish closing before cleanup runs.**
  `gui/app.py` treats shutdown as complete after 5 s even if stop
  work remains; `gui/controller.py` stops metadata before the
  monitor; `sources/uia/resident.py` `close()` waits on the read
  lock, so it cannot cancel an in-flight read promptly. Restructure:
  signal every stop first, then collect within one shared deadline;
  give the resident source a way to abandon an in-flight read so the
  child PowerShell process is terminated deterministically.
- **#3 (Medium) — in-flight HTTP requests can touch the store after
  it is closed.** `ThreadingHTTPServer` handler threads can outlive
  server shutdown; the controller's comment claims no metadata access
  happens after HTTP stop, which is not guaranteed. Separate "stop
  accepting" from "drain in-flight requests" with a bounded wait,
  and close the store only after requests can no longer reach it.
  The wait must stay bounded for slow or half-dead clients.
- **#4 (Medium) — the metadata service starts new work after a stop
  request and can leak the store.** The stop flag is checked only in
  the outer loop (a stop during deck 1's search still starts deck
  2's), and if `close()` times out, nothing closes the store when the
  worker finally exits. Check the stop request between decks and
  before each additional search; arrange a single final close,
  executed by the worker on exit when a close was requested but timed
  out. Keep `stop()`-then-reuse distinct from final `close()`.

Regression coverage for this stage must include: in-flight HTTP /
search / UIA read during shutdown, double stop, stop-then-restart,
and confirmation that the store and the child process are reclaimed
in every path.

## Stage 3 — display and retry semantics (#5–#7)

- **#5 (Medium) — `api/nowplaying.py`: concurrent requests can roll
  the held display back to an older track.** Deck selection and the
  `_last_visible` update happen in separate lock sections, so a slow
  older request can overwrite a newer request's held value. Make
  selection + hold update one consistent unit, without holding the
  lock across metadata I/O. Preserve the existing selection and
  hidden-grace rules exactly; only their atomicity changes.
- **#6 (Medium) — `api/overlay/overlay.js`: a failed artwork fetch is
  never retried while responses stay 304.** The retry entry point is
  only reached on a 200. On 304, re-evaluate pending artwork using
  the last good payload, or give artwork its own bounded retry
  schedule. Do not hammer a permanently failing URL, and cancel stale
  retries when the track changes.
- **#7 (Medium) — a provider failure in the follow-up search is
  stored as `not_found`.** In `metadata_service.py`, an exception in
  `_retry_with_query_terms` returns `None`, so the original
  `not_found` result is persisted (10-minute backoff) instead of the
  documented provider-error policy (store nothing, retry after
  60 s). Distinguish "zero results" from "search failed" inside the
  follow-up path and apply the error policy to failures. Preserve
  genuine zero-result behaviour and the no-follow-up-needed path.

## Stage 4 — small optimizations and cleanup (#9–#11)

- **#9 (Low) — `gui/widgets.py`: `Fader.set()` redraws even when
  nothing changed.** Skip `_draw()` when value and lit state are
  unchanged. Keep the `<Configure>` redraw, the first draw, and the
  unreceived→received transition.
- **#10 (Low) — `track_metadata/matching.py`: the query side is
  re-normalized for every candidate.** Normalize/squash the query
  once per match call and reuse it. Match results (strict priority,
  version-mismatch exclusion, ambiguity handling) must be identical
  before and after — pin with the existing matching tests plus a
  result-equality check over recorded candidates if available.
- **#11 (Low) — three unused imports.** Remove exactly: `ACCENT_HOVER`
  in `gui/app.py`, `json` in `tests/test_gui.py`, the module-level
  `threading` in `tests/test_regression.py`. Do not cascade into
  public constants or API cleanup.

## Out of scope

- Live-hardware measurement (real Rekordbox / DDJ-FLX4 load, OBS
  rendering) — verified separately by the user afterwards.
- Any behaviour change not listed above, however tempting. Report
  additional findings; do not fix them inside these stages.

## Acceptance

Per stage: the new regression tests fail on the pre-stage code and
pass after; the full suite passes in a plain Windows environment;
the stage's commits touch only that stage's files. At the end:
a report mapping each of the 11 findings to fixed / not fixed (with
the reason), the regression test that pins it, and the suite result.
