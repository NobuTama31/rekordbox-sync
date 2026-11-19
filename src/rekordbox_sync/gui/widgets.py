"""Console sections for the monitor: deck, mixer, deck on one surface.

Design tokens live here and nowhere else. ACCENT (FLX4 orange) is the
accent color: deck markers, tempo deltas, and the faders' signal fill.
Connection and source states are written as words; status dots are GREEN
when healthy, AMBER for warnings, RED for errors. Spacing follows a
ladder of multiples of 4, corners come in two radii, type in five sizes.
"""

import math

import customtkinter as ctk

from .presentation import (
    artwork_url,
    format_bpm,
    format_tempo_percent,
    metadata_text,
    valid_number,
)

# Colors by role.
BG = "#0A0A0C"
PANEL = "#17181D"
SURFACE = "#202228"
LINE = "#26282E"
TEXT = "#F2F3F5"
MUTED = "#9A9DA3"
ACCENT = "#FF6B1A"
ACCENT_HOVER = "#FF8C4A"
PRIMARY_HOVER = "#D8DADE"
AMBER = "#F1BD76"
RED = "#F18B94"
GREEN = "#3FB950"
KNOB_FACE = "#43464D"
KNOB_EDGE = "#0B0B0D"

# Spacing ladder: multiples of 4 only.
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16

# Corner radii: console surface and small controls.
RADIUS_LG = 4
RADIUS_SM = 3

# Type scale: display / heading / section / body / note.
SIZE_DISPLAY = 30
SIZE_HEADING = 24
SIZE_SECTION = 15
SIZE_BODY = 13
SIZE_NOTE = 11

ARTWORK_SIZE = 152
_BAR_MIN_HEIGHT = 120
_BAR_MAX_HEIGHT = 420


def font(size=SIZE_BODY, bold=False, mono=False):
    return ctk.CTkFont(family="Consolas" if mono else "Yu Gothic UI", size=size, weight="bold" if bold else "normal")


def label(master, text="", size=SIZE_BODY, color=TEXT, bold=False, **kwargs):
    kwargs.setdefault("anchor", "w")
    return ctk.CTkLabel(master, text=text, font=font(size, bold), text_color=color, **kwargs)


def _rounded_points(x0, y0, x1, y1, radius):
    points = []
    for center_x, center_y, start in (
        (x1 - radius, y0 + radius, -90),
        (x1 - radius, y1 - radius, 0),
        (x0 + radius, y1 - radius, 90),
        (x0 + radius, y0 + radius, 180),
    ):
        for angle in range(start, start + 91, 15):
            rad = math.radians(angle)
            points.extend((center_x + radius * math.cos(rad), center_y + radius * math.sin(rad)))
    return points


class Fader(ctk.CTkCanvas):
    """Hardware-style fader: a slot, an orange signal fill, and a knob
    with a center line, drawn on a canvas. Replaces CTkProgressBar, whose
    own drawing left orange stubs and pinholes at fader sizes."""

    KNOB_ACROSS = 36
    KNOB_ALONG = 16
    SLOT_WIDTH = 8

    # (value, lit) as last actually painted; set() skips the redraw
    # while the request matches it (audit #9). Resizes bypass the
    # guard: <Configure> calls _draw() directly.
    _drawn = None

    def __init__(self, master, orientation="vertical", length=200):
        self._orientation = orientation
        self._value = 0.0
        self._lit = False
        if orientation == "vertical":
            super().__init__(master, width=self.KNOB_ACROSS + 8, height=length, bg=PANEL, highlightthickness=0, bd=0)
        else:
            # A small requested width: the grid stretches the fader, and
            # the canvas default would otherwise inflate the mixer column.
            super().__init__(master, width=120, height=self.KNOB_ACROSS + 8, bg=PANEL, highlightthickness=0, bd=0)
        self.bind("<Configure>", lambda _event: self._draw())

    def set(self, value, lit=True):
        self._value = min(1.0, max(0.0, value))
        self._lit = lit
        if self._drawn != (self._value, self._lit):
            self._draw()

    def _draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        pad = self.KNOB_ALONG / 2 + 2
        if self._orientation == "vertical":
            center = width / 2
            start, end = pad, height - pad
            pos = end - self._value * (end - start)
            self.create_line(center, start, center, end, width=self.SLOT_WIDTH, fill=LINE, capstyle="round")
            if self._lit:
                self.create_line(center, pos, center, end, width=self.SLOT_WIDTH, fill=ACCENT, capstyle="round")
            self._knob(
                center - self.KNOB_ACROSS / 2,
                pos - self.KNOB_ALONG / 2,
                center + self.KNOB_ACROSS / 2,
                pos + self.KNOB_ALONG / 2,
                vertical=True,
            )
        else:
            center = height / 2
            start, end = pad, width - pad
            pos = start + self._value * (end - start)
            self.create_line(start, center, end, center, width=self.SLOT_WIDTH - 2, fill=LINE, capstyle="round")
            if self._lit:
                self.create_line(start, center, pos, center, width=self.SLOT_WIDTH - 2, fill=ACCENT, capstyle="round")
            self._knob(
                pos - self.KNOB_ALONG / 2,
                center - self.KNOB_ACROSS / 2,
                pos + self.KNOB_ALONG / 2,
                center + self.KNOB_ACROSS / 2,
                vertical=False,
            )
        self._drawn = (self._value, self._lit)

    def _knob(self, x0, y0, x1, y1, vertical):
        self.create_polygon(_rounded_points(x0, y0, x1, y1, 3), fill=KNOB_FACE, outline=KNOB_EDGE)
        if vertical:
            self.create_line(x0 + 5, (y0 + y1) / 2, x1 - 5, (y0 + y1) / 2, fill=TEXT, width=2)
        else:
            self.create_line((x0 + x1) / 2, y0 + 5, (x0 + x1) / 2, y1 - 5, fill=TEXT, width=2)


class DeckCard(ctk.CTkFrame):
    """One deck section of the console; the console frame is the surface."""

    def __init__(self, master, deck_id, artwork_loader=None):
        super().__init__(master, fg_color="transparent")
        self._loader = artwork_loader
        self._artwork_url = None
        self._artwork_pil = None
        self._artwork_image = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=SPACE_L)
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(header, width=4, height=16, fg_color=ACCENT, corner_radius=2).grid(row=0, column=0)
        label(header, f"DECK {deck_id}", SIZE_SECTION, TEXT, True).grid(row=0, column=1, sticky="w", padx=(SPACE_S, 0))
        self._state = label(header, "WAITING", SIZE_NOTE, MUTED, True)
        self._state.grid(row=0, column=2)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=SPACE_L, pady=(SPACE_M, 0))
        body.grid_columnconfigure(1, weight=1)
        self._art_placeholder = ctk.CTkLabel(
            body,
            text="♪",
            width=ARTWORK_SIZE,
            height=ARTWORK_SIZE,
            corner_radius=RADIUS_LG,
            fg_color=SURFACE,
            font=font(SIZE_DISPLAY),
            text_color=MUTED,
        )
        self._art_placeholder.grid(row=0, column=0, sticky="n", padx=(0, SPACE_M))
        self._art_image = ctk.CTkLabel(body, text="", width=ARTWORK_SIZE, height=ARTWORK_SIZE)
        self._art_image.grid(row=0, column=0, sticky="n", padx=(0, SPACE_M))
        self._art_image.grid_remove()
        texts = ctk.CTkFrame(body, fg_color="transparent")
        texts.grid(row=0, column=1, sticky="nsew")
        texts.grid_columnconfigure(0, weight=1)
        self._title = label(
            texts, "楽曲情報の取得待ち", SIZE_HEADING, TEXT, True, width=10, justify="left", wraplength=300
        )
        self._title.grid(row=0, column=0, sticky="ew")
        self._artist = label(texts, "—", SIZE_BODY, MUTED, width=10, justify="left", wraplength=300)
        self._artist.grid(row=1, column=0, sticky="ew", pady=(SPACE_XS, 0))
        bpm_row = ctk.CTkFrame(texts, fg_color="transparent")
        bpm_row.grid(row=2, column=0, sticky="w", pady=(SPACE_M, 0))
        self._bpm = ctk.CTkLabel(bpm_row, text="—", font=font(SIZE_DISPLAY, True, True), text_color=TEXT, anchor="w")
        self._bpm.grid(row=0, column=0, sticky="w")
        label(bpm_row, "BPM", SIZE_NOTE, MUTED, True).grid(
            row=0, column=1, sticky="sw", padx=(SPACE_XS, SPACE_S), pady=(0, SPACE_XS)
        )
        self._tempo = ctk.CTkLabel(bpm_row, text="", font=font(SIZE_BODY, True, True), text_color=ACCENT, anchor="w")
        self._tempo.grid(row=0, column=2, sticky="sw", pady=(0, SPACE_XS))
        ctk.CTkFrame(bpm_row, width=1, height=24, fg_color=LINE).grid(row=0, column=3, padx=(SPACE_S, SPACE_S))
        label(bpm_row, "元BPM", SIZE_NOTE, MUTED, True).grid(row=0, column=4, sticky="sw", pady=(0, SPACE_XS))
        self._original = ctk.CTkLabel(bpm_row, text="—", font=font(SIZE_BODY, True, True), text_color=TEXT, anchor="w")
        self._original.grid(row=0, column=5, sticky="sw", padx=(SPACE_XS, 0), pady=(0, SPACE_XS))
        ctk.CTkFrame(self, height=1, fg_color=LINE).grid(row=3, column=0, sticky="ew", padx=SPACE_L, pady=(SPACE_M, 0))
        self._context = label(self, "", SIZE_NOTE, MUTED, width=10, justify="left", wraplength=380)
        self._context.grid(row=4, column=0, sticky="ew", padx=SPACE_L, pady=(SPACE_S, 0))
        self._last_context = None
        self.bind("<Configure>", self._resize)
        self.render(None, None)

    def _resize(self, event):
        width = max(160, event.width - 200)
        self._title.configure(wraplength=width)
        self._artist.configure(wraplength=width)
        self._context.configure(wraplength=max(200, event.width - 32))

    def _render_artwork(self, track_info, loaded):
        url = artwork_url(track_info) if loaded else None
        if url != self._artwork_url:
            self._artwork_url = url
            self._artwork_pil = None
            self._artwork_image = None
        image = self._loader.image_for(url) if (url and self._loader) else None
        if image is not None and image is not self._artwork_pil:
            self._artwork_pil = image
            self._artwork_image = ctk.CTkImage(light_image=image, size=(ARTWORK_SIZE, ARTWORK_SIZE))
            self._art_image.configure(image=self._artwork_image)
        if self._artwork_pil is not None:
            self._art_placeholder.grid_remove()
            self._art_image.grid()
        else:
            self._art_image.grid_remove()
            self._art_placeholder.grid()

    def render(self, deck, metadata, metadata_error=None, track_info=None):
        loaded = bool(deck and deck.get("loaded"))
        self._state.configure(text="LOADED" if loaded else "WAITING")
        self._title.configure(text=deck.get("title") or "曲名未取得" if loaded else "楽曲情報の取得待ち")
        self._artist.configure(text=(deck.get("artist") or "アーティスト未取得") if loaded else "—")
        deck = deck or {}
        self._bpm.configure(text=format_bpm(deck.get("bpm_current")))
        self._tempo.configure(text=format_tempo_percent(deck.get("bpm_current"), deck.get("bpm_original")))
        self._original.configure(text=format_bpm(deck.get("bpm_original")))
        self._render_artwork(track_info, loaded)
        context = metadata_text(metadata, metadata_error)
        # Reconfiguring identical text still triggers a relayout;
        # at a 100 ms refresh that churn is worth skipping.
        if context != self._last_context:
            self._context.configure(text=context)
            self._last_context = context


class MixerCard(ctk.CTkFrame):
    """Mixer section of the console. Fader throw grows with the window."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._fader_length = 150
        label(self, "MIXER", SIZE_NOTE, MUTED, True, anchor="center").grid(row=0, column=0, sticky="ew")
        channels = ctk.CTkFrame(self, fg_color="transparent")
        channels.grid(row=1, column=0, sticky="nsew", padx=SPACE_L, pady=(SPACE_M, 0))
        channels.grid_columnconfigure((0, 1), weight=1, uniform="ch")
        self._channels = channels
        self._faders = []
        self._values = []
        for index, name in enumerate(("CH 1", "CH 2")):
            channel_label = label(channels, name, SIZE_NOTE, MUTED, True, anchor="center")
            channel_label.grid(row=0, column=index, sticky="ew")
            if index == 0:
                self._channel_label = channel_label
            fader = Fader(channels, "vertical", self._fader_length)
            fader.grid(row=1, column=index, pady=SPACE_S)
            self._faders.append(fader)
            value = label(channels, "未受信", SIZE_NOTE, MUTED, anchor="center")
            value.grid(row=2, column=index, sticky="ew")
            self._values.append(value)
        ctk.CTkFrame(channels, width=1, fg_color=LINE).place(relx=0.5, rely=0.06, relheight=0.88)
        channels.bind("<Configure>", lambda _event: self.after_idle(self._fit_faders))
        ctk.CTkFrame(self, height=1, fg_color=LINE).grid(row=2, column=0, sticky="ew", padx=SPACE_L, pady=(SPACE_M, 0))
        label(self, "CROSSFADER", SIZE_NOTE, MUTED, True, anchor="center").grid(
            row=3, column=0, sticky="ew", pady=(SPACE_S, 0)
        )
        cross = Fader(self, "horizontal")
        cross.grid(row=4, column=0, sticky="ew", padx=SPACE_L, pady=(SPACE_XS, 0))
        self._faders.append(cross)
        cross_value = label(self, "未受信", SIZE_NOTE, MUTED, anchor="center")
        cross_value.grid(row=5, column=0, sticky="ew", pady=(SPACE_XS, 0))
        self._values.append(cross_value)

    def _fit_faders(self):
        """Size the channel faders from the space the channels area
        actually has, measured from the laid-out widgets: a fixed reserve
        constant under Windows font metrics left the value labels clipped."""
        available = self._channels.winfo_height()
        if available <= 1:
            return
        overhead = self._channel_label.winfo_reqheight() + self._values[0].winfo_reqheight() + 2 * SPACE_S
        length = max(_BAR_MIN_HEIGHT, min(_BAR_MAX_HEIGHT, available - overhead))
        if abs(length - self._fader_length) >= 2:
            self._fader_length = length
            for fader in self._faders[:2]:
                fader.configure(height=length)

    def render(self, mixer):
        for fader, value_label, key in zip(self._faders, self._values, ("channel_1", "channel_2", "crossfader")):
            value = (mixer.get(key) or {}).get("value")
            valid = valid_number(value) and 0 <= value <= 1
            # No data is drawn as an unlit fader at zero: "lit" is
            # what separates a real 0% from "nothing received yet".
            fader.set(value if valid else 0.0, lit=valid)
            value_label.configure(
                text=f"{value * 100:.0f}%" if valid else "未受信", text_color=TEXT if valid else MUTED
            )
