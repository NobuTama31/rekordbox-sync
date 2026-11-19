"""Resident PowerShell UIA capture over sequential JSON lines.

One long-lived ``capture.ps1 -Serve`` process answers one request line
with one response line; requests are strictly serialized (the monitor
polls from a single thread, and the protocol has no request ids). Any
transport failure — timeout, EOF, malformed JSON — discards the process
rather than trying to resynchronize the stream: the next read spawns a
fresh one. The full protocol contract lives in docs/resident_uia.md.
"""

import json
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import ExitStack
from importlib.resources import as_file, files
from typing import Protocol

from ...models import DeckObservation
from .source import _quality, decode_observation


class _ProcessHandle(Protocol):
    def write_line(self, text: str) -> None: ...
    def read_line(self, timeout_seconds: float) -> str | None: ...
    def is_alive(self) -> bool: ...
    def terminate(self) -> None: ...


class _ResidentProcess:
    def __init__(self):
        self._resources = ExitStack()
        self._lines = queue.Queue()
        try:
            script = self._resources.enter_context(as_file(files(__package__).joinpath("capture.ps1")))
            self._process = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-File", str(script), "-Serve", "-Label", "live-api"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except BaseException:
            self._resources.close()
            raise
        self._reader = threading.Thread(target=self._read_stdout, name="rekordbox-uia-stdout", daemon=True)
        try:
            self._reader.start()
        except BaseException:
            self._process.kill()
            self._process.wait()
            self._process.stdin.close()
            self._process.stdout.close()
            self._resources.close()
            raise

    def _read_stdout(self) -> None:
        # A dedicated reader thread feeds a queue because a blocking
        # pipe read cannot time out; read_line() waits on the queue
        # instead. Lines, read errors and the final EOF marker all
        # travel through the same queue, in order.
        try:
            for line in self._process.stdout:
                self._lines.put(line.rstrip("\r\n"))
        except (OSError, UnicodeError) as error:
            self._lines.put(error)
        finally:
            self._lines.put(EOFError("UIA capture stream closed"))

    def write_line(self, text: str) -> None:
        self._process.stdin.write(text + "\n")
        self._process.stdin.flush()

    def read_line(self, timeout_seconds: float) -> str | None:
        try:
            line = self._lines.get(timeout=timeout_seconds)
        except queue.Empty:
            return None
        if isinstance(line, Exception):
            raise line
        return line

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def terminate(self) -> None:
        try:
            if self.is_alive():
                self._process.terminate()
            try:
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._reader.join()
        finally:
            try:
                try:
                    self._process.stdin.close()
                except BrokenPipeError:
                    pass  # The child may have exited before a buffered write.
            finally:
                self._process.stdout.close()
                self._resources.close()


class UiaResidentSource:
    """DeckSource backed by one long-lived capture.ps1 -Serve process."""

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        process_factory: Callable[[], _ProcessHandle] | None = None,
        platform: str | None = None,
        on_suspect: Callable[[dict], None] | None = None,
    ):
        self._timeout_seconds = timeout_seconds
        self._process_factory = process_factory
        self._platform = platform if platform is not None else sys.platform
        self._on_suspect = on_suspect
        self._process: _ProcessHandle | None = None
        self._lock = threading.Lock()
        self._process_lock = threading.Lock()

    def read(self) -> DeckObservation:
        with self._lock:
            if self._process_factory is None and self._platform != "win32":
                raise RuntimeError("Live UI Automation capture requires Windows")
            process = None
            try:
                with self._process_lock:
                    if self._process is None:
                        self._process = (self._process_factory or _ResidentProcess)()
                    process = self._process
                    if not process.is_alive():
                        raise EOFError("UIA capture process exited")
                    process.write_line(json.dumps({"label": "live-api"}))
                # Serialize requests, but leave close() free to terminate the
                # child and wake this wait with EOF during monitor shutdown.
                line = process.read_line(self._timeout_seconds)
                with self._process_lock:
                    if self._process is not process:
                        raise EOFError("UIA capture cancelled")
                if line is None:
                    raise TimeoutError("UIA capture response timed out")
                raw = json.loads(line)
            except (OSError, EOFError, ValueError) as error:
                with self._process_lock:
                    if self._process is process:
                        self._close_process()
                raise RuntimeError(f"Rekordbox UI Automation capture failed: {error}") from error
            observation = decode_observation(raw)
            if self._on_suspect is not None and _quality(observation) < 2:
                try:
                    self._on_suspect(raw)
                except Exception:
                    # Diagnostics are optional and must never fail a read or
                    # replace its observation with a transport failure.
                    pass
            return observation

    def _close_process(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.terminate()

    def close(self) -> None:
        with self._process_lock:
            self._close_process()
