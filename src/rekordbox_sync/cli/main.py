"""Command line entry: ``serve`` (monitor + HTTP API), ``extract``
(decode a recorded snapshot offline), ``capture-uia`` (record one on
Windows), plus ``gui`` and ``probe``, which are dispatched before
argparse because they own their argument handling entirely."""

import argparse
import json
import subprocess
import sys
import threading
from importlib.resources import as_file, files
from pathlib import Path

from .. import MonitorConfig, RekordboxMonitor
from ..api.http import HttpServer
from ..sources.uia.deck_parser import parse_snapshot


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "gui":
        try:
            from ..gui.app import main as gui_main
        except ModuleNotFoundError as error:
            if error.name == "customtkinter":
                raise SystemExit('GUI requires: pip install "rekordbox-sync[gui]"') from error
            raise
        return gui_main(argv[1:])
    if argv and argv[0] == "probe":
        from .probe import main as probe_main

        return probe_main(argv[1:])
    parser = argparse.ArgumentParser(description="Rekordbox live state library tools")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Serve local HTTP API")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--midi-port", default="DDJ-FLX4 0")
    serve.add_argument("--catalog", type=Path, help="Optional CSV song metadata catalog")
    serve.add_argument(
        "--metadata-db",
        type=Path,
        help="Track metadata SQLite path (env: REKORDBOX_SYNC_METADATA_DB; default: ~/.rekordbox_sync/track_metadata.db)",
    )
    serve.add_argument("--no-metadata", action="store_true", help="Disable external track metadata fetching")
    serve.add_argument(
        "--save-suspect-captures",
        action="store_true",
        help="Diagnostic: save suspect UIA captures to logs/latest_uia.json",
    )
    serve.add_argument(
        "--uia-resident",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the resident UIA capture (tracked reads); --no-uia-resident selects one-shot captures",
    )
    serve.add_argument(
        "--keep-suspect-history",
        action="store_true",
        help="Diagnostic: also keep every suspect capture under logs/history/ with an index (implies --save-suspect-captures)",
    )
    extract = sub.add_parser("extract", help="Decode a recorded UI snapshot")
    extract.add_argument("snapshot", type=Path)
    extract.add_argument("--output", type=Path)
    capture = sub.add_parser("capture-uia", help="Record a UI snapshot on Windows")
    capture.add_argument("--label", default="manual")
    capture.add_argument("--output-dir", type=Path, default=Path("logs"))
    capture.add_argument(
        "--keep-history", action="store_true", help="Save a new file instead of replacing latest_uia.json"
    )
    sub.add_parser("probe", help="MIDI/OSC capture: probe --help")
    sub.add_parser("gui", help="Dark desktop monitor: gui --help")
    args = parser.parse_args(argv)
    if args.command == "extract":
        try:
            value = parse_snapshot(json.loads(args.snapshot.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError) as error:
            parser.error(str(error))
        rendered = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if value["valid"] else 1
    if args.command == "capture-uia":
        if sys.platform != "win32":
            parser.error("capture-uia requires Windows")
        with as_file(files("rekordbox_sync.sources.uia").joinpath("capture.ps1")) as script:
            command = [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(script),
                "-Label",
                args.label,
                "-OutputDirectory",
                str(args.output_dir.resolve()),
            ]
            if args.keep_history:
                command.append("-KeepHistory")
            return subprocess.run(command, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30).returncode
    catalog = None
    if args.catalog is not None:
        from song_catalog import CsvSongCatalog

        catalog = CsvSongCatalog(args.catalog)
        try:
            catalog.reload()
        except ValueError as error:
            parser.error(str(error))
    service = None
    if not args.no_metadata:
        from track_metadata import ItunesSearchProvider, MetadataStore

        from ..metadata_service import TrackMetadataService, resolve_metadata_db_path

        service = TrackMetadataService(
            MetadataStore(resolve_metadata_db_path(args.metadata_db)), ItunesSearchProvider()
        )
    try:
        with RekordboxMonitor(
            MonitorConfig(
                midi_port=args.midi_port,
                save_suspect_captures=args.save_suspect_captures or args.keep_suspect_history,
                keep_suspect_history=args.keep_suspect_history,
                uia_resident=args.uia_resident,
            )
        ) as monitor:
            if service is not None:
                service.start(monitor.snapshot)
            try:
                with HttpServer(monitor, args.port, catalog=catalog, track_metadata=service) as server:
                    print(f"Live state: http://{server.address[0]}:{server.address[1]}/state", flush=True)
                    # Serve until interrupted. Startup nests monitor
                    # -> metadata service -> server; teardown unwinds
                    # in the reverse order via the with/finally blocks.
                    threading.Event().wait()
            finally:
                if service is not None:
                    service.close()
    except KeyboardInterrupt:
        return 0
    return 0
