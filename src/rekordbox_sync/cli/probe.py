"""Read-only MIDI/OSC capture for the Rekordbox live-state feasibility study."""

import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ports", help="List MIDI inputs without opening them")
    midi = sub.add_parser("midi", help="Capture a single MIDI input; never sends MIDI")
    midi.add_argument("--port", required=True, help="Exact input name from ports")
    osc = sub.add_parser("osc", help="Receive OSC locally")
    osc.add_argument("--port", type=int, default=4460)
    for capture in (midi, osc):
        capture.add_argument("--seconds", type=float, default=60)
        capture.add_argument("--label", default="manual-test")
    args = parser.parse_args(argv)
    if args.command == "ports":
        import mido

        names = mido.get_input_names()
        print(json.dumps(names, ensure_ascii=False, indent=2))
        return
    if args.seconds <= 0:
        parser.error("--seconds must be greater than zero")
    directory = Path.cwd() / "logs"
    directory.mkdir(exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{args.command}.jsonl"
    start = time.monotonic()
    count = 0
    # "x": a capture never appends to another session's log.
    with path.open("x", encoding="utf-8") as output:

        def emit(kind, **data):
            row = {
                "kind": kind,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": time.monotonic() - start,
                **data,
            }
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            # Flush per event: the log must stay usable even when
            # the capture is killed mid-session.
            output.flush()

        emit("session", os=platform.platform(), python=sys.version, arguments=vars(args))
        print(f"Log: {path}", flush=True)
        try:
            if args.command == "midi":
                import mido

                names = mido.get_input_names()
                if args.port not in names:
                    raise ValueError(f"Input not found: {args.port!r}; available: {names!r}")
                with mido.open_input(args.port) as port:
                    print("Recording MIDI. Move each fader separately. Ctrl+C to stop.", flush=True)
                    deadline = time.monotonic() + args.seconds
                    while time.monotonic() < deadline:
                        for message in port.iter_pending():
                            emit("midi", hex=message.hex(), message=message.dict())
                            count += 1
                            print(message, flush=True)
                        time.sleep(0.005)
            else:
                from pythonosc.dispatcher import Dispatcher
                from pythonosc.osc_server import BlockingOSCUDPServer

                def received(address, *values):
                    nonlocal count

                    # Preserve OSC blobs without invalid JSON or lossy string conversion.
                    def encode(value):
                        if isinstance(value, bytes):
                            return {"blob_hex": value.hex()}
                        if isinstance(value, (list, tuple)):
                            return [encode(item) for item in value]
                        return value

                    emit("osc", address=address, values=encode(values))
                    count += 1
                    print(address, values, flush=True)

                dispatcher = Dispatcher()
                dispatcher.set_default_handler(received)
                with BlockingOSCUDPServer(("127.0.0.1", args.port), dispatcher) as server:
                    server.timeout = 0.2
                    print(f"Recording OSC on 127.0.0.1:{args.port}. Ctrl+C to stop.", flush=True)
                    deadline = time.monotonic() + args.seconds
                    while time.monotonic() < deadline:
                        server.handle_request()
        except KeyboardInterrupt:
            emit("interrupted")
        except Exception as error:
            emit("error", error_type=type(error).__name__, detail=str(error))
            print(f"Capture failed: {error}", file=sys.stderr)
            return 1
        finally:
            emit("end", event_count=count)
            print(f"Saved {count} events: {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
