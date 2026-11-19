# Brief for the implementer: resident UIA capture v2 (tracked reads)

Task: implement the tracked-element read path designed in
`docs/resident_uia.md`, section "Tracked-element reads (v2)". This brief
carries the design philosophy; the section carries the spec. Where they
seem to conflict, stop and flag it rather than improvising.

## The philosophy (why the spec is shaped the way it is)

1. **The observer must be nearly free.** Every UIA call is a round trip
   serviced by Rekordbox's own UI thread — the app under observation
   pays for our monitoring. v1 polls a full enumeration (~4,000 round
   trips after slimming); the multi-second capture outages we chased
   (trees returned with whole subtrees missing while the decks were
   visibly fine) are what a busy provider does under that load. The
   fast path exists to make the common poll cost ~300 round trips and
   to remove `FindAll` — the step that degrades — from the common path
   entirely.
2. **The parser is the only interpreter.** Capture transports facts
   (names, rects, values, flags); it never decides what they mean. The
   tracked set is therefore a *positional superset* of what the parser
   reads — never a semantic pick, never a reimplementation of the
   parser's geometry in PowerShell. Two implementations of the same
   rule would drift; one interpreter, one dumb transport.
3. **When in doubt, walk.** The tracked path is an optimization; the
   full walk is the truth. Every uncertainty — a stale element, a
   changed title, a timer expiring — falls back to a full walk
   immediately and silently. Never protect the fast path at the
   expense of correctness.
4. **A failed read is no information, never an empty state.** Unknown
   stays `null`; nothing is fabricated; inferred state is never
   presented as confirmed state. The StateStore's hold-last-good
   behaviour, the GUI, and the overlay all depend on this distinction.
   The failure semantics pinned in `docs/resident_uia.md` (timeout /
   EOF / bad JSON → terminate and raise; valid-JSON payload errors
   flow through) must not be softened.
5. **Contracts are pinned by tests, not by intentions.**
   `tests/test_tracked_selection.py` pins the selection rule by
   requiring byte-identical parser output on every recorded capture;
   `tests/test_uia_resident.py` pins the transport contract. If a pin
   seems wrong, stop and flag it — do not edit the test to make the
   code pass.
6. **Don't move the wire.** Request/response lines, `decode_observation`,
   `/state`, `/health`, and schema v1 are unchanged. `capture_mode`
   (`"full"` / `"tracked"`) is additive and ignored by the decoder; it
   exists so diagnostic captures show which path produced them.
7. **One enumeration implementation.** One-shot and `-Serve` share
   `Get-UiaCapture` / `Get-UiaRows`. The Windows protocol tests replace
   the script text from `function Get-UiaCapture {` up to
   `if ($Serve) {` with a fake — that seam must survive the refactor.
8. **Normal monitoring never persists snapshots.** Diagnostics are
   opt-in only (see the `on_suspect` item below).

## Work items

- `capture.ps1`: serve-mode state (tracked element references, last
  stable text and offscreen flags, last-full-walk time); the tracked read
  (re-read Current + Edit value + toggle state per tracked element,
  same row shape); the fallback triggers from the design section
  (unlocated / stale read throws / stable text changed / 60s re-sync);
  `capture_mode` on every response. The no-anchor re-walk rule inside
  full walks stays. One-shot behaviour (files, `-StdoutJson`) is
  byte-for-byte unchanged in shape.
- `sources/uia/resident.py`: add the `on_suspect` hook parity with
  `UiaSource` (called with the raw snapshot when a read decodes to
  quality < 2: an error, or a loaded deck's BPM missing) so the GUI's
  opt-in diagnostic capture keeps working after the default switch.
  The hook must never break a read.
- Tests: extend the Windows protocol tests to cover
  full → tracked → (stable-text change) → full, and tracked-read
  failure → full. Keep the whole suite green.

## Decisions

- **Title-change detection (2026-11-17).** Asked whether PowerShell
  should receive the title elements' indices in the request, or
  replicate the parser's title-position judgement. Neither: the
  re-walk trigger is "a tracked element's *stable* text changed",
  with volatility decided by text shape (timer and BPM patterns), as
  specified in the design section. PowerShell identifies no element's
  role; the parser remains the only interpreter, and the wire is
  unchanged.

## Acceptance

- `tests/test_tracked_selection.py` and the full suite pass unmodified
  in spirit (see point 5).
- A live Rekordbox session on the tracked path is validated by the
  user afterwards; the implementation is not expected to prove that
  part, and must not claim it.
