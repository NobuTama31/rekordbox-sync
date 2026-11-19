# Track metadata (external catalog)

`src/track_metadata/` is an independent package (like `song_catalog`): provider
search, an explicit matching policy, and a persistent SQLite store. It imports no
Rekordbox, Windows or HTTP code. Fetched results are kept on disk, so a track is
searched once and then served locally.

## Flow

1. `TrackMetadataService` (in `rekordbox_sync/metadata_service.py`) watches monitor
   snapshots on a background thread. When a loaded deck's track changes, it searches
   in the background. HTTP handlers never search; they read the store only.
2. Results are stored keyed by title + artist using the song catalog key rule
   (NFC + trim). `matched` results are kept permanently. `ambiguous` results are
   retried after 30 days. `not_found` results are retried after 10 minutes: a
   search miss is weak evidence (term search can return nothing transiently),
   while an ambiguous candidate set is stable information. Provider errors store
   nothing and are retried after 60 seconds.
3. `GET /state/enriched` gains a `track_info` map next to `song_info` when the
   service is enabled. A deck whose fetch has not completed reports
   `{"status": "pending"}`. `/state` and `/health` are unchanged.

Enable with `serve` (default DB: `~/.rekordbox_sync/track_metadata.db`):
`--metadata-db PATH` overrides the location, `--no-metadata` disables fetching.
The environment variable `REKORDBOX_SYNC_METADATA_DB` also overrides the
location (the flag wins when both are given). A GUI-owned API enables it too
and honours the same environment variable, so tracks played during a DJ
session keep accumulating metadata automatically.

## Provider

`ItunesSearchProvider` uses the iTunes Search API (no key, country JP by default)
and returns canonical title/artist, album, release date and a 600px artwork URL.
The provider is a small injectable seam: another catalog can replace it without
touching matching or storage.

If a lookup comes back `not_found`, the service retries the search once with
decoration-stripped query terms (`query_terms` in the matching module):
bracketed parts and feat. tails are removed from the search text only, because
they poison term search — a long voice-cast parenthetical such as
`Morfonica (進藤あまね, 直田姫奈, …)` returns zero results, while the plain
band name finds the track. Matching always uses the raw Rekordbox strings, so
the retry can only surface candidates, never loosen the policy.

## Matching policy

Comparisons use a notation-insensitive form: NFKC + case-fold, curly quotes
folded to ASCII, and all punctuation, symbols and whitespace removed
(`Q & A` equals `Q&A`; `LEVEL5 -judgelight-` equals `LEVEL5-judgelight-`).
This is still exact equality after normalization, not similarity scoring.

Two tiers, evaluated in order; the first tier with any passing candidate decides.

- **Strict**: title and artist are both equal in the notation-insensitive form.
- **Loose**: titles are equal after removing bracketed decorations and feat.
  tails, and artists are equal after the same stripping. Substring containment
  is not a match: a shorter artist name contained in a longer one (AI vs
  Aimer) never matches. A candidate (or query) whose removed title decoration
  names a different version — instrumental, off vocal, karaoke, live, remix —
  never matches loosely.

Outcomes mirror the song catalog: exactly one distinct passing track is
`matched`, two or more are `ambiguous` (never silently reduced to one; the
candidates are stored for later resolution), none is `not_found`. The same song
released on a single and an album is therefore `ambiguous` by design.

## Stored data and the release year

The store keeps the raw Rekordbox title/artist, the lookup status, the chosen
candidate's canonical fields, all passing candidates as JSON, and `fetched_at`.
`release_date` is the release date of that specific release (single/album).

By the user's decision (2026-11-16), the release **year** may be used as the
work's year when filling the catalog's 放送年 column: for OP/ED tie-in singles
the release year normally coincides with the broadcast year. Only the year is
taken, never the full date. Ambiguous matches are not resolved silently; a
bulk fill must report which rows were filled from an ambiguous candidate set
(for those, the earliest candidate year is the least surprising choice).
