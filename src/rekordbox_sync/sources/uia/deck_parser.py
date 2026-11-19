"""Experimental, read-only decoder for the verified horizontal two-deck UI.

Uses semantic anchors and geometry, never persistent runtime IDs or tree indices.
Unsupported/ambiguous layouts fail closed. UI reads are not atomic snapshots.
Inside a deck whose layout the anchors have verified, a value that is not
on screen yet (a track still loading or being analyzed, a capture that raced
a redraw) is unknown and reported as None; only genuine ambiguity — more
than one candidate for the same field — fails the snapshot.
"""

import math
import re


def _text(element):
    return str(element.get("value", element.get("name", ""))).strip()


def _one(items, description):
    if len(items) != 1:
        raise ValueError(f"Ambiguous or missing {description}: {len(items)} candidates")
    return items[0]


def _number(element):
    value = float(_text(element))
    if not math.isfinite(value):
        raise ValueError("Non-finite UI value")
    return value


def _optional_number(candidates, description):
    """Number for a best-effort field: one candidate gives its value, no
    candidate means the value is not on screen right now (None), and more
    than one candidate is genuine ambiguity and fails closed."""
    if len(candidates) > 1:
        raise ValueError(f"Ambiguous {description}: {len(candidates)} candidates")
    return _number(candidates[0]) if candidates else None


def parse_snapshot(snapshot):
    state = {
        "source": "windows_uia",
        "received_at": snapshot.get("received_at") if isinstance(snapshot, dict) else None,
        "layout": "experimental_horizontal_2deck",
        "valid": False,
        "decks": {},
        "on_air_estimate": None,
    }
    try:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("received_at"), str):
            raise ValueError("Expected a UI snapshot with received_at and elements")
        elements = [e for e in snapshot["elements"] if not e["offscreen"]]
        texts = [e for e in elements if e["type"] == "ControlType.Text"]
        anchors = sorted(
            [e for e in elements if (e.get("name") or "").replace("\n", " ") == "BEAT SYNC"],
            key=lambda e: e["rect"]["x"],
        )
        if len(anchors) != 2 or abs(anchors[0]["rect"]["y"] - anchors[1]["rect"]["y"]) > 3:
            # Keep the counts in the message: from the GUI notice alone it
            # must be possible to tell a flickered enumeration (a wrong
            # count that recovers) from a layout change (no anchors at all).
            raw_anchors = [e for e in snapshot["elements"] if (e.get("name") or "").replace("\n", " ") == "BEAT SYNC"]
            detail = f"found {len(anchors)} of {len(raw_anchors)} anchors, {len(snapshot['elements'])} elements"
            if len(anchors) == 2:
                detail += f", y {anchors[0]['rect']['y']} vs {anchors[1]['rect']['y']}"
            raise ValueError(f"Expected two horizontal BEAT SYNC anchors: {detail}")
        plays = sorted(
            [e for e in elements if e["name"] in ("プレイ/ポーズ", "Play/Pause")], key=lambda e: e["rect"]["x"]
        )
        if len(plays) != 2:
            raise ValueError("Expected two play/pause controls")
        for index, (anchor, play) in enumerate(zip(anchors, plays), 1):
            # Every field is located relative to this deck's anchor,
            # title and play button — never by tree position. Deck 2's
            # left boundary is deck 1's anchor, so the two decks' fields
            # cannot bleed into each other.
            ax, ay = anchor["rect"]["x"], anchor["rect"]["y"]
            left = anchors[index - 2]["rect"]["x"] + anchors[index - 2]["rect"]["width"] if index > 1 else -math.inf
            title = _one(
                [
                    e
                    for e in texts
                    if left < e["rect"]["x"] < ax and abs(e["rect"]["y"] - ay) <= 5 and e["rect"]["width"] > 150
                ],
                "title",
            )
            tx, ty = title["rect"]["x"], title["rect"]["y"]
            loaded = _text(title) != "Not Loaded."
            deck = {
                "loaded": loaded,
                "title": _text(title) if loaded else None,
                "artist": None,
                "bpm_original": None,
                "bpm_current": None,
                "position_seconds": None,
                "remaining_seconds": None,
                "playing": None,
                "master": None,
            }
            if loaded:
                # Artist, original BPM and the time pair all live in
                # the band just under the title (10–40 px below it,
                # between the title's left edge and the anchor).
                artists = [e for e in texts if abs(e["rect"]["x"] - tx) <= 2 and 10 < e["rect"]["y"] - ty < 40]
                if len(artists) > 1:
                    raise ValueError(f"Ambiguous artist: {len(artists)} candidates")
                deck["artist"] = (_text(artists[0]) or None) if artists else None
                deck["bpm_original"] = _optional_number(
                    [
                        e
                        for e in texts
                        if tx < e["rect"]["x"] < ax
                        and 10 < e["rect"]["y"] - ty < 40
                        and re.fullmatch(r"\d+\.\d{2}", _text(e))
                    ],
                    "original BPM",
                )
                px, py = play["rect"]["x"], play["rect"]["y"]
                # The current-BPM readout is an Edit on the jog display,
                # right of and above the play button. A missing Edit is
                # not an error — the value is simply not on screen in
                # this capture (None) — but two candidates are ambiguous.
                edits = [
                    e
                    for e in elements
                    if e["type"] == "ControlType.Edit"
                    and px < e["rect"]["x"] < px + 200
                    and 0 < py - e["rect"]["y"] < 80
                ]
                if len(edits) > 1:
                    raise ValueError(f"Ambiguous current BPM: {len(edits)} candidates")
                if edits and re.fullmatch(r"\d+\.\d{2}", _text(edits[0])):
                    deck["bpm_current"] = _number(edits[0])
                elif not edits:
                    # With BEAT SYNC on, Rekordbox renders the same box
                    # as a read-only Text instead of an Edit (the tempo
                    # is driven by the sync master, so there is nothing
                    # to edit). The box keeps its exact spot in every
                    # recorded capture (dx 76, dy 38 from the play
                    # button); the jog readout below it (dy ~15) is a
                    # different element, so the band stays tight.
                    boxes = [
                        e
                        for e in texts
                        if px + 60 < e["rect"]["x"] < px + 95
                        and 28 < py - e["rect"]["y"] < 48
                        and re.fullmatch(r"\d+\.\d{2}", _text(e))
                    ]
                    if len(boxes) > 1:
                        raise ValueError(f"Ambiguous current BPM: {len(boxes)} candidates")
                    if boxes:
                        deck["bpm_current"] = _number(boxes[0])
                # Elapsed / remaining are separate "M:SS" texts, each
                # followed by its own ".d" fraction element; a leading
                # "-" marks the remaining time. Anything that does not
                # pair up cleanly leaves both fields unknown.
                times = [
                    e
                    for e in texts
                    if tx < e["rect"]["x"] < ax
                    and 10 < e["rect"]["y"] - ty < 40
                    and re.fullmatch(r"-?\d+:\d{2}", _text(e))
                ]
                if len(times) == 2:
                    parsed_times = {}
                    for timer in times:
                        rect = timer["rect"]
                        fractions = [
                            e
                            for e in texts
                            if abs(e["rect"]["x"] - rect["x"] - rect["width"]) <= 2
                            and abs(e["rect"]["y"] - rect["y"]) <= 2
                            and re.fullmatch(r"\.\d", _text(e))
                        ]
                        minutes, seconds = map(int, _text(timer).lstrip("-").split(":"))
                        field = "remaining_seconds" if _text(timer).startswith("-") else "position_seconds"
                        if len(fractions) != 1 or seconds >= 60 or field in parsed_times:
                            parsed_times = None
                            break
                        parsed_times[field] = minutes * 60 + seconds + _number(fractions[0])
                    if parsed_times:
                        deck.update(parsed_times)
            state["decks"][str(index)] = deck
        state["valid"] = True
    except (ValueError, KeyError, TypeError) as error:
        state["decks"] = {}
        state["error"] = str(error)
    return state
