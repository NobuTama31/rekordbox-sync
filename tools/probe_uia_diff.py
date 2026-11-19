#!/usr/bin/env python3
"""Diff two diagnostic UIA captures element by element.

Usage:
    python tools/probe_uia_diff.py CAPTURE_A.json CAPTURE_B.json

Built to settle questions like "does anything in the Rekordbox UI tree
change when a deck starts playing?" Capture once per state with the
diagnostic capture (logs/latest_uia.json) and diff the two files.

Elements are matched by (name, type, rect x/y) — runtime IDs and tree
indices are session-specific and are ignored, as are the capture-level
metadata fields. Every other element field is compared, including
patterns, value, toggle_state and range values.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

IGNORED_FIELDS = {"index", "runtime_id", "parent_runtime_id"}


def element_key(element):
    rect = element.get("rect") or {}
    return (element.get("name"), element.get("type"), rect.get("x"), rect.get("y"))


def group_by_key(elements):
    groups = defaultdict(list)
    for element in elements:
        groups[element_key(element)].append(element)
    return groups


def describe(key):
    name, control_type, x, y = key
    return f"{name!r} ({control_type}) at ({x}, {y})"


def diff_captures(capture_a, capture_b):
    """Return a list of human-readable difference lines."""
    groups_a = group_by_key(capture_a.get("elements", []))
    groups_b = group_by_key(capture_b.get("elements", []))
    lines = []
    for key in sorted(set(groups_a) | set(groups_b), key=repr):
        list_a = groups_a.get(key, [])
        list_b = groups_b.get(key, [])
        for element_a, element_b in zip(list_a, list_b):
            fields = (set(element_a) | set(element_b)) - IGNORED_FIELDS
            for field in sorted(fields):
                value_a = element_a.get(field)
                value_b = element_b.get(field)
                if value_a != value_b:
                    lines.append(
                        f"{describe(key)}: {field}: {value_a!r} -> {value_b!r}")
        for _ in list_a[len(list_b):]:
            lines.append(f"{describe(key)}: only in first capture")
        for _ in list_b[len(list_a):]:
            lines.append(f"{describe(key)}: only in second capture")
    return lines


def load_capture(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[3], file=sys.stderr)
        return 2
    lines = diff_captures(load_capture(argv[1]), load_capture(argv[2]))
    if not lines:
        print("no differences")
        return 0
    print(f"{len(lines)} difference(s):")
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
