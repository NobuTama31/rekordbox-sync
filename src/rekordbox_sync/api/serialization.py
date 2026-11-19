"""Version 1 wire format; internal model names are deliberately not the API schema."""

from dataclasses import asdict

from ..models import LiveStateSnapshot

SCHEMA_VERSION = 1


def to_api_dict(snapshot: LiveStateSnapshot) -> dict:
    decks = {}
    for deck in snapshot.decks:
        value = asdict(deck)
        # The deck id moves from a field to the mapping key.
        del value["deck_id"]
        decks[deck.deck_id] = value
    uia, midi = snapshot.uia_status, snapshot.midi_status
    return {
        "schema_version": SCHEMA_VERSION,
        "served_at": snapshot.served_at,
        "decks": decks,
        "mixer": {
            "channel_1": asdict(snapshot.mixer.hardware_channel_1),
            "channel_2": asdict(snapshot.mixer.hardware_channel_2),
            "crossfader": asdict(snapshot.mixer.hardware_crossfader),
        },
        "on_air_estimate": list(snapshot.on_air_estimate) if snapshot.on_air_estimate is not None else None,
        "sources": {
            "uia": {
                "status": uia.status,
                "observed_at": uia.observed_at,
                "window_minimized": uia.window_minimized,
                "age_seconds": uia.age_seconds,
                "error": uia.error,
            },
            "midi": {
                "status": midi.status,
                "control_event_count": midi.control_event_count,
                "error": midi.error,
                "values_describe": "hardware_controls; software effective values may differ",
            },
        },
    }
