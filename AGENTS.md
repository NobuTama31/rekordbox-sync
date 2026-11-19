# Project implementation rules

Read `docs/architecture.md` and `docs/naming.md` before changing code. These documents record the agreed design and naming policy.

- Keep capture, decoding, state storage, lifecycle, and external transport separate.
- Expose the Python library through `rekordbox_sync`; HTTP and CLI consume this public API.
- Use immutable typed observations and snapshots. Never mutate another component's private state.
- Keep Windows/PowerShell and MIDI dependencies inside source adapters. Importing the library must not open devices, start threads, or start a server.
- Keep the existing `/state`, `/health`, and schema version 1 JSON compatible unless a versioned contract change is explicitly agreed.
- Represent unknown values as `None`/`null`. Never equate inferred motion with confirmed PLAY or ON AIR state.
- UI samples expire; hardware MIDI values remain valid while connected even without new messages. Clear them on disconnect.
- Use `snake_case` for modules/functions/variables, `PascalCase` for types, and `UPPER_SNAKE_CASE` for constants. Class acronyms use `Uia`, `Midi`, `Http`.
- Name time units explicitly (`*_seconds`) and UTC timestamps `*_at`. Internal attributes start with `_`.
- Preserve logs and external research sources. Use small recorded fixtures and fake sources for routine tests; request hardware actions only when needed.
- Normal monitoring must not persist UI snapshots. Diagnostic UI capture overwrites latest_uia.json by default; timestamped history requires an explicit keep-history option. Do not silently enable history in helper scripts.
- Package the PowerShell capture resource and test installation outside the repository working directory.
- Keep `song_catalog` independent of Rekordbox/Windows/MIDI. Treat CSV as read-only external data. See `docs/song_catalog.md`; never silently select among duplicate title+artist matches or use fuzzy matching without an explicit policy.
- Keep GUI optional and API-driven. Never perform network I/O on the Tk thread or manipulate Tk widgets from workers. Preserve unknown/stale semantics and stop only API instances owned by the GUI. Do not write snapshots during GUI refresh.
