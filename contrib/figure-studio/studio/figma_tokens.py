#!/usr/bin/env python3
"""Argus Figure Studio v2 design tokens and SVG definition helpers.

This module is deliberately the renderer's only source of visual constants.
The public helpers are small enough to reuse from future contract renderers.
"""

from __future__ import annotations
import os
import sys

import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET


CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
FONT_FAMILY = "Helvetica, Arial, 'Liberation Sans', sans-serif"

# Core paint tokens.  Values follow studio/CONVENTIONS.md verbatim.
BACKGROUND = "#fbfaf7"
WHITE = "#ffffff"
STROKE = "#1f2933"
TEXT = "#111827"
SECONDARY_TEXT = "#4b5563"
GROUP_BORDER = "#9ca3af"
MUTED_LINE = "#cbd5e1"
PALE_LINE = "#d9dee5"
HIGHLIGHT = "#d55e00"

PASTEL_BY_ROLE = {
    "input": "#ffe2d1",
    "process": "#fff2bd",
    "memory": "#dcecff",
    "agent": "#e2f7df",
    "output": "#eadfff",
    "benchmark": "#fff1c9",
    "neutral": "#f3f4f6",
}

# A slightly lighter tint is used for phase/panel backings.
GROUP_FILL_BY_ROLE = {
    "input": "#fff3ec",
    "process": "#fff9df",
    "memory": "#eef6ff",
    "agent": "#f0faee",
    "output": "#f5f0ff",
    "benchmark": "#fff8e5",
    "neutral": "#f8f9fa",
}

DEFAULT_ICON_BY_ROLE = {
    "input": "login",
    "process": "cpu",
    "memory": "database",
    "agent": "robot",
    "output": "logout",
    "benchmark": "chart-bar",
    "neutral": "box",
}

RADIUS_CARD = 12
RADIUS_PANEL = 16
RADIUS_CHIP = 999
CARD_STROKE_WIDTH = 2
EDGE_STROKE_WIDTH = 2
HIGHLIGHT_STROKE_WIDTH = 3
GAP_X = 24
GAP_Y = 20
ICON_SIZE = 26
ICON_SLOT = 42

ICON_DIR = Path(os.environ.get("PPT_MASTER_HOME", Path.home() / ".argus-skill/tools/ppt-master/skills/ppt-master")) / "templates/icons/tabler-outline"


def min_font_px(final_width_mm: float) -> int:
    """Return the minimum integer px size that is at least 8 pt in print.

    SVG px are page-space units on the fixed 1280-wide canvas.  One typographic
    point is 25.4/72 mm.  The explicit 21 px floor keeps the 178 mm publication
    figures above 8 pt after physical scaling.
    """

    if final_width_mm <= 0:
        raise ValueError("final_width_mm must be positive")
    return max(21, math.ceil((8.0 * 25.4 / 72.0) * CANVAS_WIDTH / final_width_mm))


def pill_padding(font_size: float) -> tuple[int, int]:
    """Return horizontal/vertical pill padding derived from its text role."""

    return max(1, math.ceil(font_size * 0.48)), max(1, math.ceil(font_size * 0.32))


def card_padding(font_size: float) -> tuple[int, int]:
    """Return card padding derived from the card-title scale."""

    return max(1, round(font_size * 0.75)), max(1, round(font_size * 0.58))


def badge_diameter(font_size: float) -> int:
    """Return a badge diameter that contains its bold number at this scale."""

    return max(math.ceil(font_size + 8), round(font_size * 1.43))


def badge_gap(font_size: float) -> int:
    """Return the badge-to-label gap for the badge text scale."""

    return max(1, round(font_size * 0.4))


def typography(final_width_mm: float) -> dict[str, int]:
    """Publication-safe typography scale derived from final figure width."""

    minimum = min_font_px(final_width_mm)
    label = minimum
    card = max(minimum + 1, round(minimum * 1.14))
    section = max(card + 1, round(minimum * 1.29))
    title = max(section + 1, round(minimum * 1.43))
    card_padding_x, card_padding_y = card_padding(card)
    pill_padding_x, pill_padding_y = pill_padding(label)
    return {
        "minimum": minimum,
        "footnote": minimum,
        "label": label,
        "body": max(minimum, round(minimum * 1.05)),
        "card": card,
        "section": section,
        "title": title,
        "card_padding_x": card_padding_x,
        "card_padding_y": card_padding_y,
        "pill_padding_x": pill_padding_x,
        "pill_padding_y": pill_padding_y,
        "badge_diameter": badge_diameter(minimum),
    }


# Helvetica-compatible advance widths in em.  ASCII defaults to the average
# lowercase width; the table captures the characters that materially affect
# short scientific labels and card sizing.
_NARROW = " !'(),.:;[]`ijlI|"
_WIDE = "@%&MWQmw"
_DIGITS = "0123456789"
_CHAR_WIDTH = {character: 0.278 for character in _NARROW}
_CHAR_WIDTH.update({character: 0.889 for character in _WIDE})
_CHAR_WIDTH.update({character: 0.556 for character in _DIGITS})
_CHAR_WIDTH.update(
    {
        "f": 0.278,
        "r": 0.333,
        "t": 0.278,
        "A": 0.667,
        "B": 0.667,
        "C": 0.722,
        "D": 0.722,
        "E": 0.611,
        "F": 0.556,
        "G": 0.778,
        "H": 0.722,
        "J": 0.5,
        "K": 0.667,
        "L": 0.556,
        "N": 0.722,
        "O": 0.778,
        "P": 0.667,
        "R": 0.722,
        "S": 0.667,
        "T": 0.611,
        "U": 0.722,
        "V": 0.667,
        "X": 0.667,
        "Y": 0.667,
        "Z": 0.611,
        "-": 0.333,
        "/": 0.278,
        "×": 0.584,
        "→": 0.9,
        "·": 0.278,
    }
)


# Helvetica-Bold advance widths in em (Adobe AFM metrics, identical to the
# TeX Gyre Heros Bold clone used by fontconfig on the build host).  Bold is up
# to 16% wider than regular, so a scalar factor on the regular table is not
# sufficient to keep card titles inside their borders.
_BOLD_CHAR_WIDTH = {
    "a": 0.556, "b": 0.611, "c": 0.556, "d": 0.611, "e": 0.556, "f": 0.333, "g": 0.611,
    "h": 0.611, "i": 0.278, "j": 0.278, "k": 0.556, "l": 0.278, "m": 0.889, "n": 0.611,
    "o": 0.611, "p": 0.611, "q": 0.611, "r": 0.389, "s": 0.556, "t": 0.333, "u": 0.611,
    "v": 0.556, "w": 0.778, "x": 0.556, "y": 0.556, "z": 0.5,
    "A": 0.722, "B": 0.722, "C": 0.722, "D": 0.722, "E": 0.667, "F": 0.611, "G": 0.778,
    "H": 0.722, "I": 0.278, "J": 0.556, "K": 0.722, "L": 0.611, "M": 0.833, "N": 0.722,
    "O": 0.778, "P": 0.667, "Q": 0.778, "R": 0.722, "S": 0.667, "T": 0.611, "U": 0.722,
    "V": 0.667, "W": 0.944, "X": 0.667, "Y": 0.667, "Z": 0.611,
    " ": 0.278, "-": 0.333, ".": 0.278, "/": 0.278, ":": 0.333, ",": 0.278, "(": 0.333,
    ")": 0.333, "[": 0.333, "]": 0.333, "+": 0.584, "%": 0.889, "×": 0.584, "→": 1.0,
    "·": 0.278,
}
_BOLD_CHAR_WIDTH.update({character: 0.556 for character in _DIGITS})


def text_width_px(text: str, size: float, weight: int | str = 400) -> float:
    """Estimate Helvetica text width deterministically without font I/O."""

    numeric_weight = int(weight) if str(weight).isdigit() else (700 if str(weight).lower() == "bold" else 400)
    bold = numeric_weight >= 600
    table = _BOLD_CHAR_WIDTH if bold else _CHAR_WIDTH
    total = 0.0
    for character in str(text):
        if ord(character) > 0x2E80:
            advance = 1.0
        elif ord(character) > 0x7F:
            advance = table.get(character, 0.66 if bold else 0.62)
        elif character.islower():
            advance = table.get(character, 0.556 if bold else 0.5)
        else:
            advance = table.get(character, 0.667 if bold else 0.556)
        total += advance
    # Small safety margin absorbs hinting and renderer rounding differences.
    return max(size * 0.28, total * size * 1.02)


def has_icon(name: str | None) -> bool:
    """Return whether a requested Tabler outline icon is locally available."""

    return bool(name and re.fullmatch(r"[a-z0-9-]+", name) and (ICON_DIR / f"{name}.svg").is_file())


def icon_symbol(name: str, stroke: str = STROKE) -> ET.Element | None:
    """Build a PPT-Master-safe static ``<symbol>`` for a Tabler icon.

    Only primitive children are copied.  ``currentColor`` is replaced by an
    explicit symbol stroke, and the invisible Tabler bounding-box path is
    omitted.
    """

    if not has_icon(name):
        return None
    source = ET.parse(ICON_DIR / f"{name}.svg").getroot()
    symbol = ET.Element(
        "symbol",
        {
            "id": f"ic-{name}",
            "viewBox": "0 0 24 24",
            "fill": "none",
            "stroke": stroke,
            "stroke-width": "2",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
        },
    )
    supported = {"path", "line", "polyline", "polygon", "rect", "circle", "ellipse"}
    for original in source:
        tag = original.tag.rsplit("}", 1)[-1]
        if tag not in supported:
            continue
        attrs: dict[str, str] = {}
        for key, value in original.attrib.items():
            clean_key = key.rsplit("}", 1)[-1]
            if clean_key in {"class", "style", "id"}:
                continue
            if value == "currentColor":
                continue
            attrs[clean_key] = value
        if attrs.get("stroke") == "none" and attrs.get("fill") == "none":
            continue
        ET.SubElement(symbol, tag, attrs)
    return symbol


def marker_defs() -> list[ET.Element]:
    """Return compliant triangle arrow markers for normal and selected paths."""

    definitions: list[ET.Element] = []
    for marker_id, color in (("arrow", STROKE), ("arrow-highlight", HIGHLIGHT)):
        marker = ET.Element(
            "marker",
            {
                "id": marker_id,
                "markerWidth": "10",
                "markerHeight": "10",
                "refX": "9",
                "refY": "5",
                "orient": "auto",
            },
        )
        ET.SubElement(marker, "polygon", {"points": "0,0 10,5 0,10", "fill": color})
        definitions.append(marker)
    return definitions


def role_fill(role: str) -> str:
    return PASTEL_BY_ROLE.get(role, PASTEL_BY_ROLE["neutral"])


def group_fill(role: str) -> str:
    return GROUP_FILL_BY_ROLE.get(role, GROUP_FILL_BY_ROLE["neutral"])
