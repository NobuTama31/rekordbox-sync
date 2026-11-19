"""Pure display formatting; no fake values or playback inference."""

import math


def valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def format_bpm(value):
    return f"{value:.2f}" if valid_number(value) else "—"


def format_tempo_percent(bpm_current, bpm_original):
    """Tempo offset of the current BPM against the track's original BPM.

    Pure arithmetic on values the API already reports; an unknown or
    zero original BPM has no meaningful offset and stays an em dash.
    """
    if not valid_number(bpm_current) or not valid_number(bpm_original) or bpm_original == 0:
        return "—"
    percent = (bpm_current - bpm_original) / bpm_original * 100
    if abs(percent) < 0.005:
        return "±0.00%"
    return f"{percent:+.2f}%"


def artwork_url(info):
    """Jacket image URL for a deck's track_info, or None.

    Same display rule as the overlay: a matched result's own artwork, else
    the first ambiguous candidate's. Anything else has no jacket to show.
    """
    status = (info or {}).get("status")
    if status == "matched":
        return (info or {}).get("artwork_url") or None
    if status == "ambiguous":
        candidates = (info or {}).get("candidates") or []
        if candidates:
            return candidates[0].get("artwork_url") or None
    return None


def metadata_text(info, error=None):
    if error:
        return error
    status = (info or {}).get("status")
    if status == "matched":
        values = (info or {}).get("fields", {})
        # The work name (TVA) leads; the remaining fields follow it.
        parts = [str(values["TVA"])] if values.get("TVA") else []
        parts += [f"{key}：{value}" for key, value in values.items() if value and key != "TVA"]
        return " | ".join(parts) or "登録情報は空です"
    return {
        "not_found": "CSVに一致する楽曲がありません",
        "ambiguous": "同じ曲名・歌手が複数登録されています",
        "track_unavailable": "楽曲情報の取得待ち",
    }.get(status, "CSV情報の取得待ち")
