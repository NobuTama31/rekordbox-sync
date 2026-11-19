"""CustomTkinter desktop client; capture stays behind the public monitor/API.

Refresh model: a 100 ms tick consumes the ApiClient's latest update
and re-renders; nothing on the Tk thread does network or shutdown
work. Start/stop run on a worker thread and report back through the
events queue, which the tick applies.
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog
from urllib.parse import urlparse

import customtkinter as ctk

from .artwork import ArtworkLoader
from .client import ApiClient, validate_api_url
from .controller import LocalApiController, _stop_workers
from .widgets import (
    ACCENT,
    AMBER,
    BG,
    GREEN,
    LINE,
    MUTED,
    PANEL,
    PRIMARY_HOVER,
    RADIUS_LG,
    RADIUS_SM,
    RED,
    SIZE_BODY,
    SIZE_HEADING,
    SIZE_NOTE,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XS,
    SURFACE,
    TEXT,
    DeckCard,
    MixerCard,
    font,
    label,
)

_SHUTDOWN_TIMEOUT_SECONDS = 5.0


def _package_version():
    try:
        from importlib.metadata import version

        return version("rekordbox-sync")
    except Exception:
        return ""


class MonitorApp(ctk.CTk):
    def __init__(self, api_url="http://127.0.0.1:8765", csv_path="", start_api=False):
        # Some Windows virtual environments omit the base install's Tcl search path.
        # Use only this interpreter's bundled resources, without changing system settings.
        if sys.platform == "win32":
            import _tkinter

            for variable, version, filename in (
                ("TCL_LIBRARY", "tcl" + _tkinter.TCL_VERSION, "init.tcl"),
                ("TK_LIBRARY", "tk" + _tkinter.TK_VERSION, "tk.tcl"),
            ):
                directory = Path(sys.base_prefix) / "tcl" / version
                if (directory / filename).is_file():
                    os.environ.setdefault(variable, str(directory))
        super().__init__()
        self.title("Rekordbox Sync")
        self.geometry("1340x600")
        self.minsize(1240, 560)
        self.configure(fg_color=BG)
        self._api_url = validate_api_url(api_url)
        self._csv_path = csv_path
        self._midi_port = "DDJ-FLX4 0"
        self._save_suspect = False
        self._uia_resident = True
        self._client = ApiClient(self._api_url)
        self._controller = LocalApiController()
        self._artwork = ArtworkLoader()
        self._events = queue.Queue()
        self._busy = False
        self._closing = False
        self._payload = None
        self._received_monotonic = None
        self._stale_cleared = False
        self._settings_window = None
        self._page = "monitor"
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._client.start()
        self.after(100, self._tick)
        if start_api:
            self.after(350, self._start_api)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=SPACE_L, pady=(SPACE_M, 0))
        label(header, "Rekordbox Sync", SIZE_BODY, TEXT, True).pack(side="left")
        version_text = _package_version()
        if version_text:
            label(header, f"v{version_text}", SIZE_NOTE, MUTED).pack(side="left", padx=(SPACE_S, 0))
        self._start_button = ctk.CTkButton(
            header,
            text="APIを起動",
            width=96,
            height=30,
            corner_radius=RADIUS_SM,
            font=font(SIZE_NOTE, True),
            fg_color=TEXT,
            text_color=BG,
            hover_color=PRIMARY_HOVER,
            command=self._start_api,
        )
        self._start_button.pack(side="right")
        self._stop_button = ctk.CTkButton(
            header,
            text="APIを停止",
            width=88,
            height=30,
            corner_radius=RADIUS_SM,
            fg_color="transparent",
            border_color=LINE,
            border_width=1,
            text_color=MUTED,
            font=font(SIZE_NOTE),
            state="disabled",
            command=self._stop_api,
        )
        self._stop_button.pack(side="right", padx=(0, SPACE_S))
        ctk.CTkButton(
            header,
            text="設定",
            width=56,
            height=30,
            corner_radius=RADIUS_SM,
            fg_color="transparent",
            hover_color=LINE,
            text_color=TEXT,
            font=font(SIZE_NOTE),
            command=self._open_settings,
        ).pack(side="right", padx=(0, SPACE_S))
        self._page_button = ctk.CTkButton(
            header,
            text="JSON",
            width=64,
            height=30,
            corner_radius=RADIUS_SM,
            fg_color="transparent",
            hover_color=LINE,
            text_color=TEXT,
            font=font(SIZE_NOTE),
            command=self._toggle_page,
        )
        self._page_button.pack(side="right", padx=(0, SPACE_S))
        ctk.CTkButton(
            header,
            text="APIを開く",
            width=76,
            height=30,
            corner_radius=RADIUS_SM,
            fg_color="transparent",
            hover_color=LINE,
            text_color=MUTED,
            font=font(SIZE_NOTE),
            command=lambda: webbrowser.open(self._api_url + "/state"),
        ).pack(side="right", padx=(0, SPACE_S))

        # One console surface: deck | divider | mixer | divider | deck.
        self._console = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=RADIUS_LG)
        self._console.grid(row=1, column=0, sticky="nsew", padx=SPACE_L, pady=(SPACE_M, 0))
        self._console.grid_columnconfigure((0, 4), weight=1, uniform="decks")
        self._console.grid_columnconfigure(2, weight=0)
        self._console.grid_rowconfigure(0, weight=1)
        self._deck_cards = [DeckCard(self._console, 1, self._artwork), DeckCard(self._console, 2, self._artwork)]
        self._deck_cards[0].grid(row=0, column=0, sticky="nsew", pady=SPACE_M)
        ctk.CTkFrame(self._console, width=1, fg_color=LINE).grid(row=0, column=1, sticky="ns", pady=SPACE_M)
        self._mixer = MixerCard(self._console)
        self._mixer.grid(row=0, column=2, sticky="nsew", pady=SPACE_M)
        ctk.CTkFrame(self._console, width=1, fg_color=LINE).grid(row=0, column=3, sticky="ns", pady=SPACE_M)
        self._deck_cards[1].grid(row=0, column=4, sticky="nsew", pady=SPACE_M)

        # Operations live in the status bar, apart from the console:
        # labeled segments divided by hairlines, notice at the far right.
        status = ctk.CTkFrame(self, fg_color="transparent")
        status.grid(row=2, column=0, sticky="ew", padx=SPACE_L, pady=(SPACE_S, SPACE_M))
        self._seg_uia = self._status_segment(status, "Rekordbox：待機中")
        self._seg_midi = self._status_segment(status, "FLX4：待機中")
        self._seg_csv = self._status_segment(status, "CSV：待機中")
        self._status_separator(status)
        self._seg_api = label(status, "API：停止中", SIZE_NOTE, MUTED)
        self._seg_api.pack(side="left", padx=(0, SPACE_L))
        self._status_separator(status)
        self._seg_source = label(status, "取得元：UI Automation + MIDI", SIZE_NOTE, MUTED)
        self._seg_source.pack(side="left", padx=(0, SPACE_L))
        self._status_separator(status)
        self._seg_age = label(status, "UI更新：—", SIZE_NOTE, MUTED)
        self._seg_age.pack(side="left")
        self._notice = label(status, "通知：なし", SIZE_NOTE, MUTED)
        self._notice.pack(side="right")

        self._json_page = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=RADIUS_LG)
        self._json_page.grid(row=1, column=0, sticky="nsew", padx=SPACE_L, pady=(SPACE_M, 0))
        self._json_page.grid_columnconfigure(0, weight=1)
        self._json_page.grid_rowconfigure(1, weight=1)
        self._json_page.grid_remove()
        self._copy_button = ctk.CTkButton(
            self._json_page,
            text="JSONをコピー",
            width=110,
            height=28,
            corner_radius=RADIUS_SM,
            fg_color=SURFACE,
            hover_color=LINE,
            font=font(SIZE_NOTE),
            command=self._copy_json,
        )
        self._copy_button.grid(row=0, column=0, sticky="e", padx=SPACE_M, pady=SPACE_S)
        self._json_text = ctk.CTkTextbox(
            self._json_page, font=font(SIZE_BODY, mono=True), fg_color="transparent", text_color=TEXT, wrap="none"
        )
        self._json_text.grid(row=1, column=0, sticky="nsew", padx=SPACE_M, pady=(0, SPACE_S))
        self._json_text.configure(state="disabled")

    def _status_segment(self, parent, initial):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(side="left", padx=(0, SPACE_L))
        dot = label(frame, "●", SIZE_NOTE, MUTED)
        dot.pack(side="left")
        text = label(frame, initial, SIZE_NOTE, MUTED)
        text.pack(side="left", padx=(SPACE_XS, 0))
        return dot, text

    def _status_separator(self, parent):
        ctk.CTkFrame(parent, width=1, height=14, fg_color=LINE).pack(side="left", padx=(0, SPACE_L))

    def _set_notice(self, message, color=MUTED):
        self._notice.configure(text=f"通知：{message}" if message else "通知：なし", text_color=color)

    def _toggle_page(self):
        self._show_page("json" if self._page == "monitor" else "monitor")

    def _show_page(self, page):
        self._page = page
        self._page_button.configure(text="モニター" if page == "json" else "JSON")
        if page == "monitor":
            self._json_page.grid_remove()
            self._console.grid(row=1, column=0, sticky="nsew", padx=SPACE_L, pady=(SPACE_M, 0))
        else:
            self._console.grid_remove()
            self._json_page.grid(row=1, column=0, sticky="nsew", padx=SPACE_L, pady=(SPACE_M, 0))
            self._render_json()

    def _render_json(self):
        # The text is replaced wholesale on every refresh; save and
        # restore the scroll position so reading the JSON is not
        # interrupted by the 100 ms tick.
        y_position = self._json_text.yview()[0]
        x_position = self._json_text.xview()[0]
        self._json_text.configure(state="normal")
        self._json_text.delete("1.0", "end")
        self._json_text.insert(
            "1.0",
            json.dumps(self._payload, ensure_ascii=False, indent=2)
            if self._payload
            else "APIからのデータを待っています。",
        )
        self._json_text.configure(state="disabled")
        self._json_text.yview("moveto", y_position)
        self._json_text.xview("moveto", x_position)

    def _copy_json(self):
        if self._payload is None:
            return
        self.clipboard_clear()
        self.clipboard_append(json.dumps(self._payload, ensure_ascii=False, indent=2))
        self._copy_button.configure(text="コピーしました")
        self.after(1800, lambda: self._copy_button.configure(text="JSONをコピー") if not self._closing else None)

    def _render(self, update):
        self._payload = update.payload
        online = update.payload is not None
        payload = update.payload or {}
        state = payload.get("state", {})
        sources = state.get("sources", {})
        uia, midi = sources.get("uia", {}), sources.get("midi", {})
        available = uia.get("status") == "ok"
        # Decks render only while the UIA state is fresh, the mixer
        # only while MIDI is connected; otherwise the cards receive
        # None / {} and show their waiting states.
        for index, card in enumerate(self._deck_cards, 1):
            card.render(
                state.get("decks", {}).get(str(index)) if available else None,
                payload.get("song_info", {}).get(str(index)),
                update.metadata_error,
                payload.get("track_info", {}).get(str(index)),
            )
        self._mixer.render(state.get("mixer", {}) if midi.get("status") == "connected" else {})
        matched = any(v.get("status") == "matched" for v in payload.get("song_info", {}).values())
        dot, text = self._seg_uia
        dot.configure(text_color=GREEN if available else AMBER if uia.get("error") else MUTED)
        text.configure(
            text="Rekordbox："
            + ("取得中" + ("（最小化）" if uia.get("window_minimized") else "") if available else "未取得")
        )
        dot, text = self._seg_midi
        connected = midi.get("status") == "connected"
        dot.configure(text_color=GREEN if connected else AMBER if midi.get("error") else MUTED)
        text.configure(text="FLX4：" + ("接続中" if connected else "未接続"))
        dot, text = self._seg_csv
        dot.configure(text_color=GREEN if matched else AMBER if update.metadata_error else MUTED)
        text.configure(text="CSV：" + ("照合済み" if matched else "一致なし / 未設定"))
        self._seg_api.configure(text=f"API：稼働中 ({urlparse(self._api_url).netloc})" if online else "API：停止中")
        # The GUI knows the capture mode only for an API it started itself
        # (settings cannot change while it owns one, so the flag cannot
        # diverge from the running instance).
        self._seg_source.configure(
            text="取得元：UI Automation（常駐）+ MIDI"
            if self._controller.owned and self._uia_resident
            else "取得元：UI Automation + MIDI"
        )
        age = uia.get("age_seconds")
        self._seg_age.configure(text=f"UI更新：{age:.1f}秒前" if isinstance(age, (int, float)) else "UI更新：—")
        if not self._busy:
            message = update.error or uia.get("error") or midi.get("error") or update.metadata_error
            self._set_notice(
                str(message)[:220] if message else "", RED if update.error else AMBER if message else MUTED
            )
        if self._page == "json":
            self._render_json()
        self._start_button.configure(
            state="disabled" if self._busy or online else "normal",
            text="API稼働中" if online else "APIを起動",
            fg_color=LINE if online else TEXT,
            text_color=MUTED if online else BG,
        )
        self._stop_button.configure(state="normal" if self._controller.owned and not self._busy else "disabled")

    def _tick(self):
        if self._closing:
            return
        update = self._client.latest()
        if update and update.api_url == self._api_url:
            self._received_monotonic = time.monotonic()
            self._stale_cleared = False
            self._render(update)
        elif (
            # Watchdog: the client thread reports failures as updates,
            # so silence means it stalled. Surface that once (the latch
            # resets on the next real update) instead of leaving the
            # last good frame up forever.
            self._received_monotonic is not None
            and time.monotonic() - self._received_monotonic > 6
            and not self._stale_cleared
        ):
            from .client import ApiUpdate

            self._render(ApiUpdate(self._api_url, None, error="更新が停止しました。接続を確認してください。"))
            self._stale_cleared = True
        try:
            message, error = self._events.get_nowait()
            self._busy = False
            self._set_notice(message, RED if error else TEXT)
            self._start_button.configure(state="normal" if not self._controller.owned else "disabled")
            self._stop_button.configure(state="normal" if self._controller.owned else "disabled")
        except queue.Empty:
            pass
        self.after(100, self._tick)

    def _run_action(self, action, message):
        # Start/stop can block for seconds, so they run on a worker;
        # the busy latch serializes them, and results come back via
        # the events queue for the tick to apply on the Tk thread.
        if self._busy or self._closing:
            return
        self._busy = True
        self._set_notice(message, AMBER)
        self._start_button.configure(state="disabled")
        self._stop_button.configure(state="disabled")

        def worker():
            try:
                result = action()
                self._events.put((result or "完了しました", False))
            except Exception as error:
                self._events.put((str(error), True))

        threading.Thread(target=worker, name="rekordbox-gui-control", daemon=True).start()

    def _start_api(self):
        url, path, midi = self._api_url, self._csv_path, self._midi_port
        save_suspect = self._save_suspect
        uia_resident = self._uia_resident

        def action():
            owned = self._controller.start(
                url, path, midi, save_suspect, uia_resident=uia_resident, keep_suspect_history=save_suspect
            )
            return "APIを起動しました" if owned else "既存のAPIに接続しました"

        self._run_action(action, "APIを起動しています…")

    def _stop_api(self):
        def action():
            self._controller.stop()
            return "このGUIで起動したAPIを停止しました"

        self._run_action(action, "APIを停止しています…")

    def _open_settings(self):
        if self._settings_window and self._settings_window.winfo_exists():
            self._settings_window.focus()
            return
        window = ctk.CTkToplevel(self)
        self._settings_window = window
        window.title("接続設定")
        window.geometry("620x540")
        window.resizable(False, False)
        window.configure(fg_color=BG)
        window.transient(self)
        window.grid_columnconfigure(0, weight=1)
        label(window, "接続設定", SIZE_HEADING, TEXT, True).grid(
            row=0, column=0, sticky="w", padx=SPACE_L, pady=(SPACE_L, SPACE_L)
        )
        entries = []
        for row, caption, value in (
            (1, "API URL", self._api_url),
            (3, "CSVカタログ（任意）", self._csv_path),
            (5, "MIDI入力名", self._midi_port),
        ):
            label(window, caption, SIZE_NOTE, MUTED, True).grid(row=row, column=0, sticky="w", padx=SPACE_L)
            entry = ctk.CTkEntry(window, height=36, fg_color=PANEL, border_color=LINE, font=font(SIZE_BODY))
            entry.insert(0, value)
            entry.grid(
                row=row + 1,
                column=0,
                sticky="ew",
                padx=(SPACE_L, 110) if row == 3 else SPACE_L,
                pady=(SPACE_XS, SPACE_M),
            )
            entries.append(entry)

        def browse():
            path = filedialog.askopenfilename(parent=window, title="楽曲CSVを選択", filetypes=[("CSV", "*.csv")])
            if path:
                entries[1].delete(0, "end")
                entries[1].insert(0, path)

        ctk.CTkButton(
            window,
            text="参照",
            width=68,
            height=36,
            corner_radius=RADIUS_SM,
            fg_color=LINE,
            font=font(SIZE_NOTE),
            command=browse,
        ).grid(row=4, column=0, sticky="e", padx=SPACE_L, pady=(SPACE_XS, SPACE_M))
        suspect_var = ctk.BooleanVar(value=self._save_suspect)
        ctk.CTkCheckBox(
            window,
            text="診断：おかしい取得を logs に保存する（最新＋履歴）",
            variable=suspect_var,
            font=font(SIZE_NOTE),
            text_color=TEXT,
            fg_color=ACCENT,
            hover_color=ACCENT,
            border_color=LINE,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=7, column=0, sticky="w", padx=SPACE_L, pady=(SPACE_XS, SPACE_S))
        resident_var = ctk.BooleanVar(value=self._uia_resident)
        ctk.CTkCheckBox(
            window,
            text="UIA常駐版（追跡読み）を使う",
            variable=resident_var,
            font=font(SIZE_NOTE),
            text_color=TEXT,
            fg_color=ACCENT,
            hover_color=ACCENT,
            border_color=LINE,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=8, column=0, sticky="w", padx=SPACE_L, pady=(SPACE_XS, SPACE_S))
        hint = label(
            window,
            "CSV・MIDI・診断・常駐設定は次のAPI起動時に反映します。\nGUIから起動したAPIは、GUI終了時に停止します。",
            SIZE_NOTE,
            MUTED,
            justify="left",
            wraplength=550,
        )
        hint.grid(row=9, column=0, sticky="w", padx=SPACE_L, pady=SPACE_XS)

        def apply():
            try:
                if self._busy or self._controller.owned:
                    raise ValueError("設定を変える前に、このGUIで起動したAPIを停止してください")
                self._api_url = validate_api_url(entries[0].get())
                self._csv_path = entries[1].get().strip()
                self._midi_port = entries[2].get().strip() or "DDJ-FLX4 0"
                self._save_suspect = bool(suspect_var.get())
                self._uia_resident = bool(resident_var.get())
                self._client.set_url(self._api_url)
                from .client import ApiUpdate

                self._render(ApiUpdate(self._api_url, None, error="新しい接続先を確認しています…"))
                window.destroy()
            except ValueError as error:
                hint.configure(text=str(error), text_color=RED)

        ctk.CTkButton(
            window,
            text="適用",
            height=36,
            corner_radius=RADIUS_SM,
            fg_color=TEXT,
            text_color=BG,
            hover_color=PRIMARY_HOVER,
            font=font(SIZE_NOTE, True),
            command=apply,
        ).grid(row=10, column=0, sticky="e", padx=SPACE_L, pady=(SPACE_M, SPACE_L))

    def _close(self):
        if self._closing:
            return
        if self._busy:
            self._set_notice("処理の完了を待って終了します…", AMBER)
            self.after(200, self._close)
            return
        self._closing = True
        self._set_notice("接続を終了しています…", AMBER)
        # Shutdown runs on a thread under one shared deadline while
        # the window polls for completion: stopping inline would freeze
        # the UI for seconds. If cleanup fails the close is aborted —
        # the tick resumes and the user can retry — rather than
        # destroying the window over half-stopped workers.
        finished = threading.Event()
        failures = []

        def shutdown():
            try:
                _stop_workers(
                    (self._client.stop, self._controller.stop, self._artwork.stop),
                    time.monotonic() + _SHUTDOWN_TIMEOUT_SECONDS,
                )
            except Exception as error:
                failures.append(str(error))
            finally:
                finished.set()

        threading.Thread(target=shutdown, daemon=True).start()

        def wait():
            if finished.is_set():
                if failures:
                    self._closing = False
                    self._set_notice(failures[0], RED)
                    self.after(100, self._tick)
                else:
                    self.destroy()
            else:
                self.after(100, wait)

        wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rekordbox Sync desktop monitor")
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--catalog", default="")
    parser.add_argument("--start-api", action="store_true", help="Start a local API if none is running")
    args = parser.parse_args(argv)
    ctk.set_appearance_mode("dark")
    from importlib.resources import as_file, files

    with as_file(files(__package__).joinpath("theme.json")) as theme_path:
        ctk.set_default_color_theme(str(theme_path))
    ctk.set_widget_scaling(1.0)
    ctk.set_window_scaling(1.0)
    app = MonitorApp(args.api_url, args.catalog, args.start_api)
    app.mainloop()
