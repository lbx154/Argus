#!/usr/bin/env python3
"""Deterministic Argus Figure Studio v2 contract-to-SVG renderer.

Commands
--------
``render CONTRACT --output FIGURE.svg``
``validate CONTRACT``
``build-all --contracts-dir DIR --output-dir DIR``

Contracts contain semantics and ordering only.  Every coordinate in the SVG is
derived here by one of the five automatic layout algorithms.
"""

from __future__ import annotations
import os

import argparse
import copy
import json
import math
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.dom import minidom
from xml.etree import ElementTree as ET

import jsonschema

import figma_tokens as T

PPT_MASTER_SCRIPTS = Path(os.environ.get("PPT_MASTER_HOME", Path.home() / ".argus-skill/tools/ppt-master/skills/ppt-master")) / "scripts"
if str(PPT_MASTER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PPT_MASTER_SCRIPTS))
from svg_to_pptx.drawingml.elements import (  # noqa: E402
    estimate_single_line_text_frame_width as _pptmaster_text_width,
)


SCHEMA_PATH = Path(__file__).with_name("figure_contract.schema.json")
EDGE_DASH = {
    "data": None,
    "control": "8 5",
    "feedback": "4 4",
    "gradient": "10 4 2 4",
    "broadcast": "2 5",
    "message": None,
    "self": "8 5",
}
LEGEND_LABEL = {
    "data": "Data flow",
    "control": "Control",
    "feedback": "Feedback",
    "gradient": "Gradient",
    "broadcast": "Broadcast",
    "message": "Message",
    "self": "Local operation",
}
ARROW_CLEARANCE = 28.0  # 20px rendered marker plus 8px visual clearance.
GROUP_LABEL_EDGE_CLEARANCE = 10.0
ARROW_MARKER_LENGTH = 20.0
ARROW_ZONE_PADDING = 4.0
LABEL_ROUTE_MARGIN = (ARROW_CLEARANCE - ARROW_MARKER_LENGTH) / 2
LEADER_TICK_LENGTH = 6.0
PARALLEL_MESSAGE_PITCH = ARROW_MARKER_LENGTH + ARROW_ZONE_PADDING
STEP_GEOMETRY_CLEARANCE = ARROW_CLEARANCE - ARROW_MARKER_LENGTH


@dataclass(frozen=True)
class Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)

    def expanded(self, amount: float) -> "Box":
        return Box(self.left - amount, self.top - amount, self.right + amount, self.bottom + amount)

    def overlaps(self, other: "Box", gap: float = 0.0) -> bool:
        return not (
            self.right + gap <= other.left
            or other.right + gap <= self.left
            or self.bottom + gap <= other.top
            or other.bottom + gap <= self.top
        )


@dataclass
class PlacedNode:
    spec: dict[str, Any]
    x: float
    y: float
    width: float
    height: float

    @property
    def box(self) -> Box:
        return Box(
            self.x - self.width / 2,
            self.y - self.height / 2,
            self.x + self.width / 2,
            self.y + self.height / 2,
        )


def _fmt(value: float | int) -> str:
    number = float(value)
    if abs(number - round(number)) < 1e-8:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "item"


def _add_text(
    parent: ET.Element,
    text: str,
    x: float,
    y: float,
    size: float,
    *,
    anchor: str = "middle",
    weight: int = 400,
    fill: str = T.TEXT,
    figure_role: str = "annotation",
    text_role: str | None = None,
    extra: dict[str, str] | None = None,
) -> ET.Element:
    attrs = {
        "x": _fmt(x),
        "y": _fmt(y),
        "font-family": T.FONT_FAMILY,
        "font-size": _fmt(size),
        "font-weight": str(weight),
        "fill": fill,
        "text-anchor": anchor,
        "data-figure-role": figure_role,
        "data-text-role": text_role or figure_role,
    }
    if extra:
        attrs.update(extra)
    element = ET.SubElement(parent, "text", attrs)
    element.text = str(text)
    return element


def _text_box(text: str, x: float, y: float, size: float, weight: int = 400, anchor: str = "middle") -> Box:
    width = T.text_width_px(text, size, weight)
    left = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
    return Box(left, y - size * 0.84, left + width, y + size * 0.24)


def _pill_box(text: str, x: float, y: float, size: float, weight: int = 600) -> Box:
    # Match the gate's deliberately conservative bold-text estimate so the
    # background and group-border avoidance contain the audited text bbox.
    padding_x, padding_y = T.pill_padding(size)
    width = max(T.text_width_px(text, size, weight), len(str(text)) * size * 0.61) + 2 * padding_x
    height = size + 2 * padding_y
    return Box(x - width / 2, y - size - padding_y, x + width / 2, y + padding_y)


def _draw_pill_label(
    parent: ET.Element,
    text: str,
    x: float,
    y: float,
    size: float,
    *,
    edge_id: str,
    fill: str = T.BACKGROUND,
    stroke: str = T.GROUP_BORDER,
    role: str = "edge-label",
    extra: dict[str, str] | None = None,
) -> Box:
    box = _pill_box(text, x, y, size)
    ET.SubElement(
        parent,
        "rect",
        {
            "x": _fmt(box.left),
            "y": _fmt(box.top),
            "width": _fmt(box.width),
            "height": _fmt(box.height),
            "rx": _fmt(box.height / 2),
            "fill": fill,
            "stroke": stroke,
            "stroke-width": "1",
            "data-label-background": "true",
            "data-edge-id": edge_id,
        },
    )
    text_extra = {"data-edge-id": edge_id}
    if extra:
        text_extra.update(extra)
    _add_text(
        parent,
        text,
        x,
        y,
        size,
        weight=600,
        fill=T.SECONDARY_TEXT,
        figure_role=role,
        text_role="edge-label",
        extra=text_extra,
    )
    return box


def _requested_icon(spec: dict[str, Any]) -> str | None:
    if "icon" in spec:
        explicit = spec.get("icon")
        return str(explicit) if explicit and T.has_icon(str(explicit)) else None
    candidate = T.DEFAULT_ICON_BY_ROLE.get(str(spec.get("role", "neutral")))
    return candidate if T.has_icon(candidate) else None


def _label_lines(
    label: str,
    size: float,
    icon_slot: float,
    max_width: float = 270,
    padding_x: float | None = None,
    force_wrap: bool = False,
) -> list[str]:
    """Wrap a card label at one word boundary, never beyond two lines."""

    if padding_x is None:
        padding_x = T.card_padding(size)[0]
    words = label.split()
    if len(words) < 2 or (
        not force_wrap
        and T.text_width_px(label, size, 600) + 2 * padding_x + icon_slot <= max_width
    ):
        return [label]
    choices: list[tuple[float, int]] = []
    for index in range(1, len(words)):
        first, second = " ".join(words[:index]), " ".join(words[index:])
        choices.append((max(T.text_width_px(first, size, 600), T.text_width_px(second, size, 600)), index))
    _, split = min(choices)
    return [" ".join(words[:split]), " ".join(words[split:])]


def _node_dimensions(spec: dict[str, Any], type_scale: dict[str, int]) -> tuple[float, float]:
    label_size = type_scale["card"]
    sub_size = type_scale["minimum"]
    default_padding_x, default_padding_y = T.card_padding(label_size)
    padding_x = float(type_scale.get("card_padding_x", default_padding_x))
    padding_y = float(type_scale.get("card_padding_y", default_padding_y))
    icon_slot = T.ICON_SLOT if _requested_icon(spec) else 0
    lines = _label_lines(
        str(spec["label"]),
        label_size,
        icon_slot,
        padding_x=padding_x,
        force_wrap=bool(type_scale.get("compact_wrap")),
    )
    label_width = max(T.text_width_px(line, label_size, 600) for line in lines)
    sub_width = T.text_width_px(str(spec.get("sublabel", "")), sub_size, 400)
    width = max(142, label_width + 2 * padding_x + icon_slot, sub_width + 2 * padding_x + icon_slot)
    if spec.get("kind") == "chip":
        width = max(112, label_width + 2 * padding_x + icon_slot)
    label_height = len(lines) * label_size * 1.2
    content_height = max(38.0 if _requested_icon(spec) else 0.0, label_height)
    if spec.get("sublabel"):
        content_height = max(content_height, label_height + sub_size * 1.2 + round(sub_size * 0.35))
    height = content_height + 2 * padding_y
    return width, height


def _node_visible_box(node: PlacedNode) -> Box:
    """Return bounds of all painted card surfaces, including their stroke."""

    box = node.box
    if node.spec.get("kind") == "store":
        box = Box(box.left, box.top, box.right + 4, box.bottom + 4)
    stroke_width = T.HIGHLIGHT_STROKE_WIDTH if node.spec.get("emphasis") else T.CARD_STROKE_WIDTH
    return box.expanded(stroke_width / 2)


def _draw_node(
    parent: ET.Element,
    node: PlacedNode,
    type_scale: dict[str, int],
    *,
    shared_label: bool = False,
    expected_count: int | None = None,
) -> None:
    spec = node.spec
    box = node.box
    node_id = str(spec["id"])
    role = str(spec.get("role", "neutral"))
    emphasis = bool(spec.get("emphasis"))
    kind = str(spec.get("kind", "card"))
    pptx_box = _node_visible_box(node)
    group_attrs = {
        "id": _safe_id(node_id),
        "data-pptx-bounds": " ".join(
            _fmt(value) for value in (pptx_box.left, pptx_box.top, pptx_box.width, pptx_box.height)
        ),
        "data-node-id": node_id,
        "data-figure-role": "node",
    }
    if shared_label:
        group_attrs["data-shared-label"] = "true"
        group_attrs["data-expected-count"] = str(expected_count if expected_count is not None else 1)
    group = ET.SubElement(parent, "g", group_attrs)
    common = {
        "fill": T.role_fill(role),
        "stroke": T.HIGHLIGHT if emphasis else T.STROKE,
        "stroke-width": _fmt(T.HIGHLIGHT_STROKE_WIDTH if emphasis else T.CARD_STROKE_WIDTH),
        "data-node-id": node_id,
    }
    padding_x = float(type_scale.get("card_padding_x", T.card_padding(type_scale["card"])[0]))
    if kind == "decision":
        ET.SubElement(
            group,
            "polygon",
            {
                **common,
                "points": " ".join(
                    f"{_fmt(px)},{_fmt(py)}"
                    for px, py in ((node.x, box.top), (box.right, node.y), (node.x, box.bottom), (box.left, node.y))
                ),
            },
        )
    else:
        rx = box.height / 2 if kind == "chip" else T.RADIUS_CARD
        if kind == "store":
            ET.SubElement(
                group,
                "rect",
                {
                    **common,
                    "x": _fmt(box.left + 4),
                    "y": _fmt(box.top + 4),
                    "width": _fmt(box.width),
                    "height": _fmt(box.height),
                    "rx": _fmt(rx),
                    "fill-opacity": "0.6",
                },
            )
        ET.SubElement(
            group,
            "rect",
            {
                **common,
                "x": _fmt(box.left),
                "y": _fmt(box.top),
                "width": _fmt(box.width),
                "height": _fmt(box.height),
                "rx": _fmt(rx),
            },
        )

    icon = _requested_icon(spec)
    label_lines = _label_lines(
        str(spec["label"]),
        type_scale["card"],
        T.ICON_SLOT if icon else 0,
        max_width=box.width,
        padding_x=padding_x,
        force_wrap=bool(type_scale.get("compact_wrap")),
    )
    label_width = max(T.text_width_px(line, type_scale["card"], 600) for line in label_lines)
    text_left = box.left + padding_x
    if icon:
        if kind == "chip":
            content_left = node.x - (T.ICON_SLOT + label_width) / 2
            slot_x = content_left + T.ICON_SLOT / 2
            text_left = content_left
        else:
            slot_x = box.left + padding_x + T.ICON_SIZE / 2
        slot_y = node.y - (10 if spec.get("sublabel") else 0)
        ET.SubElement(
            group,
            "rect",
            {
                "x": _fmt(slot_x - 19),
                "y": _fmt(slot_y - 19),
                "width": "38",
                "height": "38",
                "rx": "9",
                "fill": T.BACKGROUND,
                "fill-opacity": "0.75",
                "stroke": T.MUTED_LINE,
                "stroke-width": "1",
            },
        )
        ET.SubElement(
            group,
            "use",
            {
                "href": f"#ic-{icon}",
                "x": _fmt(slot_x - T.ICON_SIZE / 2),
                "y": _fmt(slot_y - T.ICON_SIZE / 2),
                "width": _fmt(T.ICON_SIZE),
                "height": _fmt(T.ICON_SIZE),
            },
        )
        text_left += T.ICON_SLOT
    text_x = text_left + label_width / 2 if kind == "chip" else (text_left + box.right - padding_x) / 2
    label_y = node.y + type_scale["card"] * 0.34
    if spec.get("sublabel"):
        label_y = node.y - 7
    if len(label_lines) == 1:
        last_label_y = label_y
        _add_text(
            group,
            str(spec["label"]),
            text_x,
            label_y,
            type_scale["card"],
            weight=600,
            figure_role="node-label",
            text_role="node-label",
            extra={"data-label-for": node_id},
        )
    else:
        first_y = label_y - type_scale["card"] * 0.6
        last_label_y = first_y + (len(label_lines) - 1) * type_scale["card"] * 1.2
        text = _add_text(
            group,
            "",
            text_x,
            first_y,
            type_scale["card"],
            weight=600,
            figure_role="node-label",
            text_role="node-label",
            extra={"data-label-for": node_id},
        )
        first = ET.SubElement(text, "tspan", {"x": _fmt(text_x), "y": _fmt(first_y)})
        first.text = label_lines[0] + " "
        second = ET.SubElement(text, "tspan", {"x": _fmt(text_x), "dy": _fmt(type_scale["card"] * 1.2)})
        second.text = label_lines[1]
    if spec.get("sublabel"):
        sublabel_gap = max(2, round(type_scale["minimum"] * 0.15))
        sublabel_y = (
            last_label_y
            + type_scale["card"] * 0.24
            + type_scale["minimum"] * 0.84
            + sublabel_gap
        )
        _add_text(
            group,
            str(spec["sublabel"]),
            text_x,
            sublabel_y,
            type_scale["minimum"],
            weight=400,
            fill=T.SECONDARY_TEXT,
            figure_role="sublabel",
            text_role="sublabel",
            extra={"data-label-for": node_id},
        )
    if spec.get("badge_number") is not None:
        badge_radius = T.badge_diameter(type_scale["minimum"]) / 2
        badge_x, badge_y = box.left + 3, box.top + 3
        ET.SubElement(
            group,
            "circle",
            {"cx": _fmt(badge_x), "cy": _fmt(badge_y), "r": _fmt(badge_radius), "fill": T.HIGHLIGHT},
        )
        _add_text(
            group,
            str(spec["badge_number"]),
            badge_x,
            badge_y + type_scale["minimum"] * 0.34,
            type_scale["minimum"],
            weight=700,
            fill=T.WHITE,
            figure_role="badge",
            text_role="badge",
        )


def _group_bounds(group: dict[str, Any], nodes: dict[str, PlacedNode]) -> Box | None:
    members = [nodes[node_id].box for node_id in group.get("node_ids", []) if node_id in nodes]
    if not members:
        return None
    padding_x = 28
    padding_top = 42
    padding_bottom = 22
    return Box(
        min(box.left for box in members) - padding_x,
        min(box.top for box in members) - padding_top,
        max(box.right for box in members) + padding_x,
        max(box.bottom for box in members) + padding_bottom,
    )


def _group_label_box(group: dict[str, Any], nodes: dict[str, PlacedNode], type_scale: dict[str, int]) -> Box | None:
    border = _group_bounds(group, nodes)
    if border is None:
        return None
    padding_x, padding_y = T.pill_padding(type_scale["section"])
    label_width = T.text_width_px(str(group["label"]), type_scale["section"], 600) + 2 * padding_x
    chip_height = type_scale["section"] + 2 * padding_y
    return Box(
        border.left + 16,
        border.top - chip_height - 6,
        border.left + 16 + label_width,
        border.top - 6,
    )


def _draw_groups(parent: ET.Element, groups: Sequence[dict[str, Any]], nodes: dict[str, PlacedNode], type_scale: dict[str, int]) -> list[Box]:
    label_boxes: list[Box] = []
    for group_spec in groups:
        box = _group_bounds(group_spec, nodes)
        if not box:
            continue
        style = str(group_spec.get("style", "phase"))
        attrs = {
            "id": _safe_id(str(group_spec["id"])),
            "data-pptx-bounds": " ".join(_fmt(value) for value in (box.left, box.top, box.width, box.height)),
            "data-group-id": str(group_spec["id"]),
            "data-figure-role": "group",
        }
        group = ET.SubElement(parent, "g", attrs)
        rect_attrs = {
            "x": _fmt(box.left),
            "y": _fmt(box.top),
            "width": _fmt(box.width),
            "height": _fmt(box.height),
            "rx": _fmt(T.RADIUS_PANEL),
            "fill": T.group_fill(str(group_spec.get("role", "neutral"))),
            "fill-opacity": "0.35",
            "stroke": T.GROUP_BORDER,
            "stroke-width": "1.5",
            "data-figure-role": "group",
        }
        if style == "phase":
            rect_attrs["stroke-dasharray"] = "7 5"
        elif style == "lane":
            rect_attrs["stroke-dasharray"] = "11 5"
        ET.SubElement(group, "rect", rect_attrs)
        label = str(group_spec["label"])
        chip = _group_label_box(group_spec, nodes, type_scale)
        assert chip is not None
        ET.SubElement(
            group,
            "rect",
            {
                "x": _fmt(chip.left),
                "y": _fmt(chip.top),
                "width": _fmt(chip.width),
                "height": _fmt(chip.height),
                "rx": _fmt(chip.height / 2),
                "fill": T.BACKGROUND,
                "stroke": T.GROUP_BORDER,
                "stroke-width": "1",
                "data-label-background": "true",
            },
        )
        _add_text(
            group,
            label,
            chip.left + T.pill_padding(type_scale["section"])[0],
            (chip.top + chip.bottom) / 2 + type_scale["section"] * 0.3,
            type_scale["section"],
            anchor="start",
            weight=600,
            figure_role="group-label",
            text_role="group-label",
        )
        label_boxes.append(chip)
    return label_boxes


def _topological_layers(nodes: Sequence[dict[str, Any]], edges: Sequence[dict[str, Any]]) -> tuple[dict[str, int], dict[int, list[str]]]:
    node_ids = [str(node["id"]) for node in nodes]
    order_index = {node_id: index for index, node_id in enumerate(node_ids)}
    successors: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    predecessors: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        source, target = str(edge["from"]), str(edge["to"])
        if source in indegree and target in indegree and target not in successors[source]:
            successors[source].append(target)
            predecessors[target].append(source)
            indegree[target] += 1
    queue = deque(sorted((node_id for node_id in node_ids if indegree[node_id] == 0), key=order_index.get))
    layers = {node_id: 0 for node_id in node_ids}
    visited: list[str] = []
    while queue:
        current = queue.popleft()
        visited.append(current)
        for target in sorted(successors[current], key=order_index.get):
            layers[target] = max(layers[target], layers[current] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    # A genuine feedback cycle is placed one layer beyond its earliest known
    # predecessor; edge routing will take the exterior corridor.
    for node_id in node_ids:
        if node_id not in visited:
            known = [layers[source] for source in predecessors[node_id] if source in layers]
            layers[node_id] = (max(known) + 1) if known else 0

    by_layer: dict[int, list[str]] = defaultdict(list)
    for node_id in node_ids:
        by_layer[layers[node_id]].append(node_id)

    # Two deterministic barycentric sweeps reduce crossings without exposing
    # coordinates or ranks in the contract.
    for layer in range(1, max(by_layer, default=0) + 1):
        previous = {node_id: index for index, node_id in enumerate(by_layer[layer - 1])}
        by_layer[layer].sort(
            key=lambda node_id: (
                sum(previous[p] for p in predecessors[node_id] if p in previous)
                / max(1, sum(p in previous for p in predecessors[node_id])),
                order_index[node_id],
            )
        )
    for layer in range(max(by_layer, default=0) - 1, -1, -1):
        following = {node_id: index for index, node_id in enumerate(by_layer[layer + 1])}
        by_layer[layer].sort(
            key=lambda node_id: (
                sum(following[s] for s in successors[node_id] if s in following)
                / max(1, sum(s in following for s in successors[node_id])),
                order_index[node_id],
            )
        )
    return layers, dict(by_layer)


def _layer_boundary_gaps(
    contract: dict[str, Any],
    layers: dict[str, int],
    by_layer: dict[int, list[str]],
    layer_keys: Sequence[int],
    direction: str,
    natural_gap: float,
    type_scale: dict[str, int],
) -> list[float]:
    """Size each rank corridor for its arrows, labels, and group chrome."""

    boundary_count = max(0, len(layer_keys) - 1)
    gaps = [max(natural_gap, 16.0 + ARROW_CLEARANCE)] * boundary_count
    key_position = {layer: index for index, layer in enumerate(layer_keys)}
    edge_labels = [False] * boundary_count
    lr_inline_label_gaps = [0.0] * boundary_count
    cross_rank_position: dict[str, float] = {}
    for layer, members in by_layer.items():
        for index, node_id in enumerate(members):
            cross_rank_position[node_id] = 0.5 if len(members) == 1 else index / (len(members) - 1)
    for edge in contract.get("edges", []):
        source_id, target_id = str(edge["from"]), str(edge["to"])
        if source_id not in layers or target_id not in layers:
            continue
        source_position = key_position[layers[source_id]]
        target_position = key_position[layers[target_id]]
        if target_position <= source_position:
            continue
        for boundary in range(source_position, target_position):
            edge_labels[boundary] = edge_labels[boundary] or bool(edge.get("label"))
            if edge.get("label"):
                pill_width = _pill_box(str(edge["label"]), 0, 0, type_scale["label"]).width
                # Cross-rank aligned LR endpoints have no useful vertical bus
                # on which to host a pill.  Phrase labels reserve an arrow-clear
                # corridor at each end; compact algebraic labels reserve their
                # measured width plus the complete marker/padding budget.
                if (
                    direction == "LR"
                    and abs(cross_rank_position[source_id] - cross_rank_position[target_id]) < 1e-6
                ):
                    if len(str(edge["label"])) <= 3:
                        required_gap = (
                            pill_width
                            + ARROW_MARKER_LENGTH
                            + 2 * ARROW_ZONE_PADDING
                            + LABEL_ROUTE_MARGIN
                        )
                    else:
                        required_gap = pill_width + 2 * ARROW_CLEARANCE
                    lr_inline_label_gaps[boundary] = max(lr_inline_label_gaps[boundary], required_gap)

    groups_starting: list[list[dict[str, Any]]] = [[] for _ in range(boundary_count)]
    groups_ending: list[list[dict[str, Any]]] = [[] for _ in range(boundary_count)]
    for group in contract.get("groups", []):
        positions = sorted(
            key_position[layers[str(node_id)]]
            for node_id in group.get("node_ids", [])
            if str(node_id) in layers
        )
        if not positions:
            continue
        first, last = positions[0], positions[-1]
        if first > 0:
            groups_starting[first - 1].append(group)
        if last < len(layer_keys) - 1:
            groups_ending[last].append(group)

    sample_pill = _pill_box("M", 0, 0, type_scale["label"])
    pill_half_height = sample_pill.height / 2
    labelled_tb_gap = (
        sample_pill.height
        + ARROW_MARKER_LENGTH
        + 2 * ARROW_ZONE_PADDING
        + LABEL_ROUTE_MARGIN
    )
    chip_height = type_scale["section"] + 2 * T.pill_padding(type_scale["section"])[1]
    for boundary in range(boundary_count):
        carries_label = edge_labels[boundary]
        if direction == "TB":
            source_clearance = max(16.0, pill_half_height + 2 if carries_label else 0.0)
            target_clearance = ARROW_CLEARANCE
            if groups_ending[boundary]:
                source_clearance = 22.0 + max(
                    12.0,
                    pill_half_height + 4 if carries_label else 0.0,
                )
            if groups_starting[boundary]:
                # The label obstacle is the chip expanded by 10px.  A pill
                # centred on the bus additionally needs its half-height and
                # the standard 2px node/label obstacle clearance.
                chip_clearance = GROUP_LABEL_EDGE_CLEARANCE
                if carries_label:
                    chip_clearance += pill_half_height + 2
                target_clearance = 42.0 + chip_height + 6.0 + chip_clearance
            gaps[boundary] = max(gaps[boundary], labelled_tb_gap if carries_label else 0.0)
        else:
            source_clearance = 28.0 + 12.0 if groups_ending[boundary] else 16.0
            target_clearance = 28.0 + GROUP_LABEL_EDGE_CLEARANCE if groups_starting[boundary] else ARROW_CLEARANCE
        gaps[boundary] = max(
            gaps[boundary],
            source_clearance + target_clearance,
            lr_inline_label_gaps[boundary],
        )
    return gaps


def _separate_nonmembers_crosswise(
    contract: dict[str, Any],
    nodes: dict[str, PlacedNode],
    layers: dict[str, int],
    direction: str,
) -> None:
    """Move parallel non-members outside a group's perpendicular span."""

    for _ in range(max(1, len(contract.get("groups", [])))):
        changed = False
        for group in contract.get("groups", []):
            member_ids = {str(node_id) for node_id in group.get("node_ids", [])}
            member_layers = [layers[node_id] for node_id in member_ids if node_id in layers]
            border = _group_bounds(group, nodes)
            if not member_layers or border is None:
                continue
            first_layer, last_layer = min(member_layers), max(member_layers)
            for node_id, node in nodes.items():
                if node_id in member_ids or not first_layer <= layers[node_id] <= last_layer:
                    continue
                box = node.box
                if direction == "LR":
                    if box.bottom <= border.top - 24 or box.top >= border.bottom + 24:
                        continue
                    above = border.top - 24 - node.height / 2
                    below = border.bottom + 24 + node.height / 2
                    node.y = above if abs(node.y - above) <= abs(node.y - below) else below
                else:
                    if box.right <= border.left - 24 or box.left >= border.right + 24:
                        continue
                    left = border.left - 24 - node.width / 2
                    right = border.right + 24 + node.width / 2
                    node.x = left if abs(node.x - left) <= abs(node.x - right) else right
                changed = True
        if not changed:
            break


def _place_layered(
    contract: dict[str, Any],
    type_scale: dict[str, int],
    natural_gap: float,
) -> dict[str, PlacedNode]:
    specs = {str(node["id"]): node for node in contract["nodes"]}
    layers, by_layer = _topological_layers(contract["nodes"], contract["edges"])
    direction = str(contract["layout"]["direction"])
    dimensions = {node_id: _node_dimensions(spec, type_scale) for node_id, spec in specs.items()}
    layer_keys = sorted(by_layer)
    gaps = _layer_boundary_gaps(
        contract,
        layers,
        by_layer,
        layer_keys,
        direction,
        natural_gap,
        type_scale,
    )
    result: dict[str, PlacedNode] = {}
    if direction == "LR":
        rank_extents = [max(dimensions[node_id][0] for node_id in by_layer[layer]) for layer in layer_keys]
        content_start, content_end = 24.0, T.CANVAS_WIDTH - 24.0
        cross_start = 145.0
        legend_space = 62 if _graph_legend_kinds(contract) else 18
        cross_end = T.CANVAS_HEIGHT - 110 - legend_space / 2
    else:
        rank_extents = [max(dimensions[node_id][1] for node_id in by_layer[layer]) for layer in layer_keys]
        content_start = 24.0
        content_end = T.CANVAS_HEIGHT - (51.0 if _graph_legend_kinds(contract) else 24.0)
        grouped_ids = {
            str(node_id)
            for group in contract.get("groups", [])
            for node_id in group.get("node_ids", [])
        }
        grouped_half_width = max(
            (dimensions[node_id][0] / 2 for node_id in grouped_ids if node_id in dimensions),
            default=73.0,
        )
        cross_start = max(125.0, 24.0 + 28.0 + grouped_half_width)
        cross_end = T.CANVAS_WIDTH - cross_start
    total = sum(rank_extents) + sum(gaps)
    cursor = (content_start + content_end - total) / 2
    primary: list[float] = []
    for index, extent in enumerate(rank_extents):
        primary.append(cursor + extent / 2)
        cursor += extent + (gaps[index] if index < len(gaps) else 0)
    for layer_position, layer in zip(primary, layer_keys):
        members = by_layer[layer]
        cross = _distributed(cross_start, cross_end, len(members))
        for cross_position, node_id in zip(cross, members):
            width, height = dimensions[node_id]
            x, y = (layer_position, cross_position) if direction == "LR" else (cross_position, layer_position)
            result[node_id] = PlacedNode(specs[node_id], x, y, width, height)
    _separate_nonmembers_crosswise(contract, result, layers, direction)
    return result


def _layered_bounds(
    contract: dict[str, Any],
    nodes: dict[str, PlacedNode],
    type_scale: dict[str, int],
) -> Box:
    # Canvas fitting uses painted bounds, including the full card stroke and
    # the 4px backing offset used by stores. This keeps every visible card
    # inside the 8px safe inset after the v1 2px stroke is applied.
    boxes = [_node_visible_box(node) for node in nodes.values()]
    for group in contract.get("groups", []):
        border = _group_bounds(group, nodes)
        if border is None:
            continue
        boxes.append(border.expanded(0.75))
        chip = _group_label_box(group, nodes, type_scale)
        if chip is not None:
            boxes.append(chip.expanded(0.5))
    return Box(
        min(box.left for box in boxes),
        min(box.top for box in boxes),
        max(box.right for box in boxes),
        max(box.bottom for box in boxes),
    )


def _layout_layered(contract: dict[str, Any], type_scale: dict[str, int]) -> dict[str, PlacedNode]:
    """Choose the first deterministic, canvas-safe layered layout."""

    base_card = int(type_scale["card"])
    font_floor = int(type_scale["minimum"])
    base_padding_x = max(12, int(type_scale["card_padding_x"]))
    # The v1 Figma-style token spec requires at least 12px horizontal padding.
    padding_floor = max(12, round(font_floor * 0.4))
    gap_floor = max(24, round(font_floor * 1.14))
    # Deterministic pressure order protects publication type: compact horizontal
    # padding, then wrap long LR card titles onto two lines, then the
    # discretionary natural gap, and only then step card type down toward the
    # mandatory floor. Core arrow/group-chip corridors never shrink.
    lr_direction = str(contract["layout"]["direction"]) == "LR"
    configurations: list[tuple[int, int, int, int]] = []
    for card in range(base_card, font_floor - 1, -1):
        wrap_modes = (0, 1) if lr_direction else (0,)
        for wrap in wrap_modes:
            configurations.extend(
                (card, padding, 28, wrap)
                for padding in range(base_padding_x, padding_floor - 1, -1)
            )
        configurations.extend(
            (card, padding_floor, gap, wrap_modes[-1])
            for gap in range(27, gap_floor - 1, -1)
        )
    chosen_nodes: dict[str, PlacedNode] = {}
    chosen = configurations[-1]
    fitted = False
    for card_size, padding_x, layer_gap, compact_wrap in configurations:
        candidate_scale = dict(type_scale)
        candidate_scale.update(
            card=card_size,
            card_padding_x=padding_x,
            card_padding_y=T.card_padding(card_size)[1],
            compact_wrap=compact_wrap,
        )
        candidate_nodes = _place_layered(contract, candidate_scale, layer_gap)
        bounds = _layered_bounds(contract, candidate_nodes, candidate_scale)
        chosen_nodes, chosen = candidate_nodes, (card_size, padding_x, layer_gap, compact_wrap)
        if (
            bounds.left >= 8
            and bounds.top >= 8
            and bounds.right <= T.CANVAS_WIDTH - 8
            and bounds.bottom <= T.CANVAS_HEIGHT - 8
        ):
            fitted = True
            break
    if not fitted:
        raise ValueError(
            "layered layout cannot fit the 1280x720 canvas at the mandatory "
            f"{font_floor}px font floor"
        )
    type_scale.update(
        card=chosen[0],
        card_padding_x=chosen[1],
        card_padding_y=T.card_padding(chosen[0])[1],
        layered_gap=chosen[2],
        compact_wrap=chosen[3],
    )
    return chosen_nodes


def _layout_nested(contract: dict[str, Any], type_scale: dict[str, int]) -> dict[str, PlacedNode]:
    nodes = contract["nodes"]
    groups = contract.get("groups", [])
    claimed = {node_id for group in groups for node_id in group.get("node_ids", [])}
    panels: list[tuple[str, list[str]]] = [(str(group["id"]), list(group["node_ids"])) for group in groups]
    panels.extend((f"implicit-{node['id']}", [str(node["id"])]) for node in nodes if str(node["id"]) not in claimed)
    count = max(1, len(panels))
    columns = min(3, math.ceil(math.sqrt(count)))
    rows = math.ceil(count / columns)
    left, top, right, bottom = 56, 76, T.CANVAS_WIDTH - 56, T.CANVAS_HEIGHT - (78 if _graph_legend_kinds(contract) else 36)
    panel_width = (right - left - (columns - 1) * 28) / columns
    panel_height = (bottom - top - (rows - 1) * 28) / rows
    specs = {str(node["id"]): node for node in nodes}
    result: dict[str, PlacedNode] = {}
    for index, (_, member_ids) in enumerate(panels):
        row, column = divmod(index, columns)
        panel_left = left + column * (panel_width + 28)
        panel_top = top + row * (panel_height + 28)
        inner_columns = min(2, max(1, math.ceil(math.sqrt(len(member_ids)))))
        inner_rows = math.ceil(len(member_ids) / inner_columns)
        x_positions = _distributed(panel_left + 72, panel_left + panel_width - 72, inner_columns)
        y_positions = _distributed(panel_top + 68, panel_top + panel_height - 42, inner_rows)
        for member_index, node_id in enumerate(member_ids):
            inner_row, inner_column = divmod(member_index, inner_columns)
            width, height = _node_dimensions(specs[node_id], type_scale)
            width = min(width, panel_width - 42)
            result[node_id] = PlacedNode(specs[node_id], x_positions[inner_column], y_positions[inner_row], width, height)
    return result


def _layout_pipeline(contract: dict[str, Any], type_scale: dict[str, int]) -> dict[str, PlacedNode]:
    nodes = contract["nodes"]
    _, by_layer = _topological_layers(nodes, contract["edges"])
    ordered = [node_id for layer in sorted(by_layer) for node_id in by_layer[layer]]
    specs = {str(node["id"]): copy.deepcopy(node) for node in nodes}
    x_positions = _distributed(105, T.CANVAS_WIDTH - 105, len(ordered))
    result: dict[str, PlacedNode] = {}
    for index, (node_id, x) in enumerate(zip(ordered, x_positions), start=1):
        specs[node_id].setdefault("badge_number", index)
        width, height = _node_dimensions(specs[node_id], type_scale)
        # Alternating insets keep branch-heavy pipelines legible while the
        # dominant reading order remains left-to-right.
        y = 350 + (72 if index % 4 == 0 else -72 if index % 4 == 3 else 0)
        result[node_id] = PlacedNode(specs[node_id], x, y, min(width, 205), height)
    return result


def _distributed(start: float, end: float, count: int) -> list[float]:
    if count <= 0:
        return []
    if count == 1:
        return [(start + end) / 2]
    return [start + index * (end - start) / (count - 1) for index in range(count)]


def _port(node: PlacedNode, side: str, offset: float = 0.0) -> tuple[float, float]:
    box = node.box
    if side == "left":
        return box.left, max(box.top + 8, min(box.bottom - 8, node.y + offset))
    if side == "right":
        return box.right, max(box.top + 8, min(box.bottom - 8, node.y + offset))
    if side == "top":
        return max(box.left + 8, min(box.right - 8, node.x + offset)), box.top
    return max(box.left + 8, min(box.right - 8, node.x + offset)), box.bottom


def _port_offsets(
    edges: Sequence[dict[str, Any]],
    nodes: dict[str, PlacedNode],
    direction: str,
    type_scale: dict[str, int],
) -> dict[tuple[str, str], float]:
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        outgoing[str(edge["from"])].append(edge)
        incoming[str(edge["to"])].append(edge)
    result: dict[tuple[str, str], float] = {}
    transverse = (lambda node_id: nodes[node_id].y) if direction == "LR" else (lambda node_id: nodes[node_id].x)

    def clusters(members: Sequence[dict[str, Any]], other_end: str) -> list[list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for edge in members:
            label = edge.get("label")
            key = ("label", str(label)) if label else ("edge", str(edge["id"]))
            grouped[key].append(edge)
        return sorted(
            grouped.values(),
            key=lambda group: (
                sum(transverse(str(edge[other_end])) for edge in group) / len(group),
                str(group[0].get("label", "")),
                str(group[0]["id"]),
            ),
        )

    def port_pitch(node_id: str, count: int) -> float:
        if count <= 1:
            return 0.0
        cross_extent = nodes[node_id].height if direction == "LR" else nodes[node_id].width
        available_pitch = max(0.0, cross_extent - 16.0) / (count - 1)
        measured_pitch = _pill_box("M", 0, 0, type_scale["label"]).height / 2 + ARROW_ZONE_PADDING
        return min(measured_pitch, available_pitch)

    for node_id, members in outgoing.items():
        grouped = clusters(members, "to")
        step = port_pitch(node_id, len(grouped))
        for index, cluster in enumerate(grouped):
            offset = (index - (len(grouped) - 1) / 2) * step
            for edge in cluster:
                result[(str(edge["id"]), "source")] = offset
    for node_id, members in incoming.items():
        grouped = clusters(members, "from")
        step = port_pitch(node_id, len(grouped))
        for index, cluster in enumerate(grouped):
            offset = (index - (len(grouped) - 1) / 2) * step
            for edge in cluster:
                result[(str(edge["id"]), "target")] = offset
    return result


def _simplify_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        if result and math.dist(result[-1], point) < 0.01:
            continue
        if len(result) >= 2:
            x1, y1 = result[-2]
            x2, y2 = result[-1]
            x3, y3 = point
            if (abs(x1 - x2) < 0.01 and abs(x2 - x3) < 0.01) or (abs(y1 - y2) < 0.01 and abs(y2 - y3) < 0.01):
                result[-1] = point
                continue
        result.append(point)
    return result


def _route_edges(
    contract: dict[str, Any],
    nodes: dict[str, PlacedNode],
    type_scale: dict[str, int],
) -> dict[str, list[tuple[float, float]]]:
    direction = str(contract["layout"]["direction"])
    offsets = _port_offsets(contract["edges"], nodes, direction, type_scale)
    group_info = [
        (
            {str(node_id) for node_id in group.get("node_ids", [])},
            _group_bounds(group, nodes),
            _group_label_box(group, nodes, type_scale),
        )
        for group in contract.get("groups", [])
    ]
    chip_obstacles = [chip.expanded(GROUP_LABEL_EDGE_CLEARANCE) for _, _, chip in group_info if chip is not None]

    def avoid_group_labels(
        coordinate: float,
        transverse_start: float,
        transverse_end: float,
        lower: float,
        upper: float,
    ) -> float:
        """Keep a horizontal/vertical bus out of every expanded group chip."""

        transverse_low, transverse_high = sorted((transverse_start, transverse_end))
        for obstacle in chip_obstacles:
            rank_low, rank_high = (
                (obstacle.left, obstacle.right) if direction == "LR" else (obstacle.top, obstacle.bottom)
            )
            obstacle_transverse_low, obstacle_transverse_high = (
                (obstacle.top, obstacle.bottom) if direction == "LR" else (obstacle.left, obstacle.right)
            )
            transverse_overlap = min(transverse_high, obstacle_transverse_high) >= max(
                transverse_low, obstacle_transverse_low
            )
            if transverse_overlap and rank_low < coordinate < rank_high:
                choices = [value for value in (rank_low, rank_high) if lower <= value <= upper]
                if choices:
                    coordinate = min(choices, key=lambda value: (abs(value - coordinate), value))
        return coordinate

    def clearance_bus(edge: dict[str, Any], source: PlacedNode, target: PlacedNode) -> float | None:
        source_id, target_id = str(edge["from"]), str(edge["to"])
        labelled = bool(edge.get("label"))
        pill_half_height = _pill_box("M", 0, 0, type_scale["label"]).height / 2
        if direction == "LR":
            lower = source.box.right + 16.0
            upper = target.box.left - ARROW_CLEARANCE
        else:
            lower = source.box.bottom + max(16.0, pill_half_height + 2 if labelled else 0.0)
            upper = target.box.top - ARROW_CLEARANCE
        prefer_lower = False
        for members, border, chip in group_info:
            if border is None:
                continue
            if source_id not in members and target_id in members:
                if direction == "LR" and source.box.right <= border.left:
                    upper = min(upper, border.left - GROUP_LABEL_EDGE_CLEARANCE)
                if direction == "TB" and source.box.bottom <= border.top and chip is not None:
                    chip_clearance = GROUP_LABEL_EDGE_CLEARANCE
                    if labelled:
                        chip_clearance += pill_half_height + 2
                    upper = min(upper, chip.top - chip_clearance)
            if source_id in members and target_id not in members:
                if direction == "LR" and border.right <= target.box.left:
                    lower = max(lower, border.right + 12.0)
                    prefer_lower = True
                if direction == "TB" and border.bottom <= target.box.top:
                    border_clearance = max(12.0, pill_half_height + 4 if labelled else 0.0)
                    lower = max(lower, border.bottom + border_clearance)
                    prefer_lower = True
        if direction == "LR" and labelled:
            pill_half_width = _pill_box(str(edge["label"]), 0, 0, type_scale["label"]).width / 2
            label_lower = max(lower, source.box.right + pill_half_width + 2)
            label_upper = min(upper, target.box.left - max(ARROW_CLEARANCE, pill_half_width + 2))
            if label_lower <= label_upper + 0.01:
                lower, upper = label_lower, label_upper
        if lower > upper + 0.01:
            return None
        coordinate = lower if prefer_lower else upper
        transverse_start, transverse_end = (
            (source.y, target.y) if direction == "LR" else (source.x, target.x)
        )
        return avoid_group_labels(coordinate, transverse_start, transverse_end, lower, upper)

    result: dict[str, list[tuple[float, float]]] = {}
    for edge in contract["edges"]:
        edge_id = str(edge["id"])
        source, target = nodes[str(edge["from"])], nodes[str(edge["to"])]
        source_offset = offsets.get((edge_id, "source"), 0.0)
        target_offset = offsets.get((edge_id, "target"), 0.0)
        if direction == "LR":
            forward = target.x >= source.x
            source_side, target_side = ("right", "left") if forward else ("bottom", "bottom")
            start, end = _port(source, source_side, source_offset), _port(target, target_side, target_offset)
            if forward:
                midpoint = clearance_bus(edge, source, target)
                if midpoint is None:
                    midpoint = (start[0] + end[0]) / 2
                points = [start, (midpoint, start[1]), (midpoint, end[1]), end]
            else:
                corridor = max(source.box.bottom, target.box.bottom) + 42
                points = [start, (start[0], corridor), (end[0], corridor), end]
        else:
            forward = target.y >= source.y
            source_side, target_side = ("bottom", "top") if forward else ("right", "right")
            start, end = _port(source, source_side, source_offset), _port(target, target_side, target_offset)
            if forward:
                midpoint = clearance_bus(edge, source, target)
                if midpoint is None:
                    midpoint = (start[1] + end[1]) / 2
                points = [start, (start[0], midpoint), (end[0], midpoint), end]
                # A target port can sit directly below a group's label chip.
                # Bend around the expanded chip, then retain a long vertical
                # arrow run into the target instead of piercing the label.
                for obstacle in chip_obstacles:
                    if (
                        obstacle.left < end[0] < obstacle.right
                        and midpoint <= obstacle.top
                        and end[1] >= obstacle.bottom
                    ):
                        bypass = min(
                            (obstacle.left, obstacle.right),
                            key=lambda value: (abs(value - end[0]), value),
                        )
                        points = [
                            start,
                            (start[0], midpoint),
                            (bypass, midpoint),
                            (bypass, obstacle.bottom),
                            (end[0], obstacle.bottom),
                            end,
                        ]
                        break
            else:
                corridor = max(source.box.right, target.box.right) + 42
                points = [start, (corridor, start[1]), (corridor, end[1]), end]
        result[edge_id] = _simplify_points(points)
    return result


def _path_data(points: Sequence[tuple[float, float]]) -> str:
    return " ".join(("M" if index == 0 else "L") + f" {_fmt(x)} {_fmt(y)}" for index, (x, y) in enumerate(points))


def _preferred_midpoint(
    points: Sequence[tuple[float, float]],
    direction: str,
    label: str = "",
    label_size: float = 0.0,
) -> tuple[float, float]:
    segments = list(zip(points, points[1:]))
    preferred = [
        (start, end)
        for start, end in segments
        if (abs(start[1] - end[1]) < 0.01 if direction == "LR" else abs(start[0] - end[0]) < 0.01)
    ]
    if direction == "LR" and label_size:
        pill_width = _pill_box(label, 0, 0, label_size).width
        if abs(points[-1][0] - points[0][0]) >= pill_width + 4:
            bus_segments = [(start, end) for start, end in segments if abs(start[0] - end[0]) < 0.01]
            if bus_segments:
                start, end = max(bus_segments, key=lambda pair: math.dist(pair[0], pair[1]))
                return ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    # Compact algebraic labels (Q/K/V, short port names) read best on the
    # longest trunk, while phrase labels stay on the reading-direction run.
    candidates = segments if len(label) <= 3 else (preferred or segments)
    start, end = max(candidates, key=lambda pair: math.dist(pair[0], pair[1]))
    return ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)


def _shared_bus_midpoint(
    routes: Sequence[Sequence[tuple[float, float]]],
    direction: str,
) -> tuple[float, float]:
    """Return the visual center of same-label fan-out/fan-in bus runs."""

    bus_runs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for points in routes:
        segments = list(zip(points, points[1:]))
        bus_segments = [
            (start, end)
            for start, end in segments
            if (abs(start[0] - end[0]) < 0.01 if direction == "LR" else abs(start[1] - end[1]) < 0.01)
        ]
        start, end = max(bus_segments or segments, key=lambda pair: math.dist(pair[0], pair[1]))
        bus_runs.append((start, end))
    if direction == "LR":
        shared_low = max(min(start[1], end[1]) for start, end in bus_runs)
        shared_high = min(max(start[1], end[1]) for start, end in bus_runs)
        if shared_low <= shared_high:
            return (sum(start[0] for start, _ in bus_runs) / len(bus_runs), (shared_low + shared_high) / 2)
    else:
        shared_low = max(min(start[0], end[0]) for start, end in bus_runs)
        shared_high = min(max(start[0], end[0]) for start, end in bus_runs)
        if shared_low <= shared_high:
            return ((shared_low + shared_high) / 2, sum(start[1] for start, _ in bus_runs) / len(bus_runs))
    midpoints = [((start[0] + end[0]) / 2, (start[1] + end[1]) / 2) for start, end in bus_runs]
    return (
        sum(point[0] for point in midpoints) / len(midpoints),
        sum(point[1] for point in midpoints) / len(midpoints),
    )


def _segment_intersects_box(start: tuple[float, float], end: tuple[float, float], box: Box) -> bool:
    """Return whether a straight segment meets a non-empty box."""

    if box.width <= 0 or box.height <= 0:
        return False
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    lower, upper = 0.0, 1.0
    for coefficient, distance in zip(
        (-dx, dx, -dy, dy),
        (x1 - box.left, box.right - x1, y1 - box.top, box.bottom - y1),
    ):
        if abs(coefficient) < 1e-12:
            if distance < 0:
                return False
            continue
        ratio = distance / coefficient
        if coefficient < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _arrowhead_zone(points: Sequence[tuple[float, float]], padding: float = 0.0) -> Box:
    """Axis-aligned box of the rendered 20px marker footprint."""

    start, end = points[-2], points[-1]
    length = math.dist(start, end)
    if length < 1e-9:
        return Box(end[0], end[1], end[0], end[1]).expanded(padding)
    ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    base = (end[0] - ux * min(ARROW_MARKER_LENGTH, length), end[1] - uy * min(ARROW_MARKER_LENGTH, length))
    half_width = ARROW_MARKER_LENGTH / 2
    normal = (-uy * half_width, ux * half_width)
    return Box(
        min(base[0] - normal[0], base[0] + normal[0], end[0] - normal[0], end[0] + normal[0]),
        min(base[1] - normal[1], base[1] + normal[1], end[1] - normal[1], end[1] + normal[1]),
        max(base[0] - normal[0], base[0] + normal[0], end[0] - normal[0], end[0] + normal[0]),
        max(base[1] - normal[1], base[1] + normal[1], end[1] - normal[1], end[1] + normal[1]),
    ).expanded(padding)


def _route_label_position(
    base: tuple[float, float],
    text: str,
    size: float,
    label_route_ids: Sequence[str],
    routes: dict[str, list[tuple[float, float]]],
    obstacles: Sequence[Box],
    used: Sequence[Box],
    border_obstacles: Sequence[Box],
    arrowhead_zones: Sequence[Box],
) -> tuple[float, float, tuple[tuple[float, float], tuple[float, float]] | None]:
    """Place a measured pill on a safe straight run, or add a 6px leader tick."""

    sample = _pill_box(text, 0, size / 2, size)
    allowed_ids = set(label_route_ids)

    def valid(box: Box) -> bool:
        if box.left < 8 or box.right > T.CANVAS_WIDTH - 8 or box.top < 8 or box.bottom > T.CANVAS_HEIGHT - 8:
            return False
        if any(box.overlaps(obstacle.expanded(2)) for obstacle in obstacles):
            return False
        if any(box.overlaps(previous, gap=7) for previous in used):
            return False
        if any(box.overlaps(zone) for zone in arrowhead_zones):
            return False
        for edge_id, edge_points in routes.items():
            if edge_id in allowed_ids:
                continue
            if any(
                _segment_intersects_box(start, end, box.expanded(1))
                for start, end in zip(edge_points, edge_points[1:])
            ):
                return False
        return True

    candidates: list[tuple[tuple[float, float, int, int, float], tuple[float, float]]] = []
    for route_index, edge_id in enumerate(label_route_ids):
        points = routes[edge_id]
        for segment_index, (start, end) in enumerate(zip(points, points[1:])):
            horizontal = abs(start[1] - end[1]) < 0.01
            vertical = abs(start[0] - end[0]) < 0.01
            if not horizontal and not vertical:
                continue
            axis_start, axis_end = (start[0], end[0]) if horizontal else (start[1], end[1])
            axis_low, axis_high = sorted((axis_start, axis_end))
            half_extent = (sample.width if horizontal else sample.height) / 2
            endpoint_padding = ARROW_ZONE_PADDING / 2
            low = axis_low + half_extent + endpoint_padding
            high = axis_high - half_extent - endpoint_padding
            if low > high + 1e-6:
                continue
            preferred_axis = base[0] if horizontal else base[1]
            count = max(0, math.ceil(high - low))
            axis_values = [low, high, (low + high) / 2, max(low, min(high, preferred_axis))]
            axis_values.extend(min(high, low + index) for index in range(count + 1))
            for axis in sorted(set(round(value, 6) for value in axis_values), key=lambda value: (abs(value - preferred_axis), value)):
                center = (axis, start[1]) if horizontal else (start[0], axis)
                box = _pill_box(text, center[0], center[1] + size / 2, size)
                if not valid(box):
                    continue
                score = (
                    route_index if len(label_route_ids) > 1 else 0,
                    math.dist(center, base),
                    -math.dist(start, end),
                    segment_index,
                    axis,
                )
                candidates.append((score, center))
    if candidates:
        _, center = min(candidates, key=lambda item: item[0])
        return center[0], center[1] + size / 2, None

    # If a measured inline pill truly cannot fit, leave the edge untouched and
    # move the pill perpendicular to a straight run.  The short leader is not
    # marker-ended and therefore cannot create an endpoint loop.
    fallback: list[
        tuple[
            tuple[float, float, int, int, int],
            tuple[float, float],
            tuple[tuple[float, float], tuple[float, float]],
        ]
    ] = []
    for route_index, edge_id in enumerate(label_route_ids):
        points = routes[edge_id]
        for segment_index, (start, end) in enumerate(zip(points, points[1:])):
            horizontal = abs(start[1] - end[1]) < 0.01
            vertical = abs(start[0] - end[0]) < 0.01
            if not horizontal and not vertical:
                continue
            anchor = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            for side_order, side in enumerate((-1, 1)):
                if horizontal:
                    center = (anchor[0], anchor[1] + side * (sample.height / 2 + LEADER_TICK_LENGTH))
                    box = _pill_box(text, center[0], center[1] + size / 2, size)
                    pill_end = (anchor[0], box.bottom if side < 0 else box.top)
                else:
                    center = (anchor[0] + side * (sample.width / 2 + LEADER_TICK_LENGTH), anchor[1])
                    box = _pill_box(text, center[0], center[1] + size / 2, size)
                    pill_end = (box.right if side < 0 else box.left, anchor[1])
                if valid(box):
                    fallback.append(
                            (
                                (
                                    route_index if len(label_route_ids) > 1 else 0,
                                    math.dist(center, base),
                                    -math.dist(start, end),
                                    segment_index,
                                    side_order,
                                ),
                            center,
                            (pill_end, anchor),
                        )
                    )
    if not fallback:
        raise ValueError(f"no collision-free straight segment or leader placement for edge label {text!r}")
    _, center, leader = min(fallback, key=lambda item: item[0])
    return center[0], center[1] + size / 2, leader


def _box_intersects_outline(box: Box, border: Box) -> bool:
    horizontal_overlap = min(box.right, border.right) >= max(box.left, border.left)
    vertical_overlap = min(box.bottom, border.bottom) >= max(box.top, border.top)
    return (
        horizontal_overlap and (box.top <= border.top <= box.bottom or box.top <= border.bottom <= box.bottom)
    ) or (
        vertical_overlap and (box.left <= border.left <= box.right or box.left <= border.right <= box.right)
    )


def _edge_attrs(edge: dict[str, Any], points: Sequence[tuple[float, float]]) -> dict[str, str]:
    emphasis = bool(edge.get("emphasis"))
    color = T.HIGHLIGHT if emphasis else T.STROKE
    attrs = {
        "id": f"e-{_safe_id(str(edge['id']))}",
        "d": _path_data(points),
        "fill": "none",
        "stroke": color,
        "stroke-width": _fmt(T.HIGHLIGHT_STROKE_WIDTH if emphasis else T.EDGE_STROKE_WIDTH),
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
        "marker-end": "url(#arrow-highlight)" if emphasis else "url(#arrow)",
        "data-edge-id": str(edge["id"]),
        "data-edge-from": str(edge["from"]),
        "data-edge-to": str(edge["to"]),
        "data-edge-source": str(edge["from"]),
        "data-edge-target": str(edge["to"]),
        "data-figure-role": "edge",
    }
    dash = EDGE_DASH.get(str(edge.get("kind", "data")))
    if dash:
        attrs["stroke-dasharray"] = dash
    return attrs


def _graph_legend_kinds(contract: dict[str, Any]) -> list[str]:
    if contract.get("legend") != "auto":
        return []
    kinds = list(dict.fromkeys(str(edge.get("kind", "data")) for edge in contract.get("edges", [])))
    return kinds if len(kinds) > 1 else []


def _draw_legend(
    parent: ET.Element,
    kinds: Sequence[str],
    type_scale: dict[str, int],
    *,
    y: float = 684,
    x_start: float | None = None,
) -> None:
    if len(kinds) < 2:
        return
    legend = ET.SubElement(parent, "g", {"id": "legend"})
    sizes = [60 + T.text_width_px(LEGEND_LABEL[kind], type_scale["minimum"], 400) for kind in kinds]
    total = sum(sizes) + 28 * (len(kinds) - 1)
    cursor = (T.CANVAS_WIDTH - total) / 2 if x_start is None else x_start
    for kind, width in zip(kinds, sizes):
        line_y = y - 6
        path_attrs = {
            "d": f"M {_fmt(cursor)} {_fmt(line_y)} L {_fmt(cursor + 34)} {_fmt(line_y)}",
            "fill": "none",
            "stroke": T.STROKE,
            "stroke-width": "2",
            "stroke-linecap": "round",
        }
        dash = EDGE_DASH.get(kind)
        if dash:
            path_attrs["stroke-dasharray"] = dash
        ET.SubElement(legend, "path", path_attrs)
        _add_text(
            legend,
            LEGEND_LABEL[kind],
            cursor + 46,
            y,
            type_scale["minimum"],
            anchor="start",
            weight=400,
            fill=T.SECONDARY_TEXT,
            figure_role="legend",
            text_role="legend",
        )
        cursor += width + 28


def _svg_root(contract: dict[str, Any], icon_specs: Iterable[dict[str, Any]]) -> tuple[ET.Element, ET.Element]:
    svg = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": "0 0 1280 720",
            "width": "1280",
            "height": "720",
            "data-pptx-page-role": "content",
        },
    )
    ET.SubElement(
        svg,
        "rect",
        {
            "id": "background",
            "data-pptx-role": "background",
            "x": "0",
            "y": "0",
            "width": "1280",
            "height": "720",
            "fill": T.BACKGROUND,
        },
    )
    defs = ET.SubElement(svg, "defs")
    for marker in T.marker_defs():
        defs.append(marker)
    icon_names = sorted({icon for spec in icon_specs if (icon := _requested_icon(spec))})
    for icon_name in icon_names:
        symbol = T.icon_symbol(icon_name)
        if symbol is not None:
            defs.append(symbol)
    return svg, defs


def _render_graph(contract: dict[str, Any]) -> ET.Element:
    type_scale = T.typography(float(contract["final_width_mm"]))
    variant = str(contract["layout"]["variant"])
    if variant == "layered-dag":
        nodes = _layout_layered(contract, type_scale)
    elif variant == "nested-containers":
        nodes = _layout_nested(contract, type_scale)
    elif variant == "pipeline-numbered":
        nodes = _layout_pipeline(contract, type_scale)
    else:
        raise ValueError(f"graph renderer does not support layout variant {variant!r}")
    svg, _ = _svg_root(contract, (node.spec for node in nodes.values()))
    if variant == "layered-dag":
        svg.set("data-layout-type-scale", str(type_scale["card"]))
        svg.set("data-layout-card-padding-x", str(type_scale["card_padding_x"]))
        svg.set("data-layout-layer-gap", str(type_scale["layered_gap"]))
    if contract["layout"].get("show_title"):
        header = ET.SubElement(svg, "g", {"id": "header"})
        _add_text(header, contract["takeaway"], T.CANVAS_WIDTH / 2, 36, type_scale["title"], weight=700, figure_role="title")

    groups_layer = ET.SubElement(svg, "g", {"id": "groups"})
    group_label_boxes = _draw_groups(groups_layer, contract.get("groups", []), nodes, type_scale)
    group_borders = [box for group in contract.get("groups", []) if (box := _group_bounds(group, nodes)) is not None]
    routes = _route_edges(contract, nodes, type_scale)
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in contract["edges"]:
        if edge.get("label"):
            by_label[str(edge["label"])].append(edge)
    used_label_boxes: list[Box] = []
    # Group chips remain label obstacles and their 10px expansion is also the
    # hard routing obstacle used by _route_edges.
    obstacles = [node.box for node in nodes.values()] + [box.expanded(GROUP_LABEL_EDGE_CLEARANCE) for box in group_label_boxes]
    arrowhead_zones = [_arrowhead_zone(points, ARROW_ZONE_PADDING) for points in routes.values()]
    direction = str(contract["layout"]["direction"])
    label_placements: list[
        tuple[
            str,
            list[dict[str, Any]],
            float,
            float,
            Box,
            tuple[tuple[float, float], tuple[float, float]] | None,
        ]
    ] = []
    for label, label_edges in by_label.items():
        label_routes = [routes[str(edge["id"])] for edge in label_edges]
        if len(label_edges) > 1:
            base = _shared_bus_midpoint(label_routes, direction)
        else:
            base = _preferred_midpoint(label_routes[0], direction, label, type_scale["label"])
        x, y, leader = _route_label_position(
            base,
            label,
            type_scale["label"],
            [str(edge["id"]) for edge in label_edges],
            routes,
            obstacles,
            used_label_boxes,
            group_borders,
            arrowhead_zones,
        )
        pill_box = _pill_box(label, x, y, type_scale["label"])
        used_label_boxes.append(pill_box)
        label_placements.append((label, label_edges, x, y, pill_box, leader))

    edge_layer = ET.SubElement(svg, "g", {"id": "edges"})
    for edge in contract["edges"]:
        ET.SubElement(edge_layer, "path", _edge_attrs(edge, routes[str(edge["id"])]))

    node_layer = ET.SubElement(svg, "g", {"id": "nodes"})
    for spec in contract["nodes"]:
        _draw_node(node_layer, nodes[str(spec["id"])], type_scale)

    label_layer = ET.SubElement(svg, "g", {"id": "edge-labels"})
    for label, label_edges, x, y, _, leader in label_placements:
        if leader is not None:
            ET.SubElement(
                label_layer,
                "path",
                {
                    "d": _path_data(leader),
                    "fill": "none",
                    "stroke": T.GROUP_BORDER,
                    "stroke-width": "1.5",
                    "stroke-linecap": "round",
                    "data-label-leader-for": str(label_edges[0]["id"]),
                },
            )
        is_shared = len(label_edges) > 1
        extra_attrs = {"data-shared-label": "true", "data-expected-count": "1"} if is_shared else {}
        _draw_pill_label(
            label_layer,
            label,
            x,
            y,
            type_scale["label"],
            edge_id=str(label_edges[0]["id"]),
            extra=extra_attrs,
        )
    _draw_legend(
        svg,
        _graph_legend_kinds(contract),
        type_scale,
        x_start=48.0 if direction == "TB" else None,
    )
    if contract.get("footnote"):
        footnote = ET.SubElement(svg, "g", {"id": "footnote"})
        _add_text(
            footnote,
            str(contract["footnote"]),
            T.CANVAS_WIDTH / 2,
            708,
            type_scale["footnote"],
            weight=400,
            fill=T.SECONDARY_TEXT,
            figure_role="footnote",
        )
    return svg


def _sequence_legend_kinds(contract: dict[str, Any]) -> list[str]:
    if contract.get("legend") != "auto":
        return []
    kinds = list(dict.fromkeys(str(message["kind"]) for message in contract["sequence"]["messages"]))
    return kinds if len(kinds) > 1 else []


def _draw_step_badge(parent: ET.Element, number: int, center_x: float, center_y: float, type_scale: dict[str, int]) -> None:
    radius = T.badge_diameter(type_scale["minimum"]) / 2
    ET.SubElement(
        parent,
        "circle",
        {"cx": _fmt(center_x), "cy": _fmt(center_y), "r": _fmt(radius), "fill": T.HIGHLIGHT},
    )
    _add_text(
        parent,
        str(number),
        center_x,
        center_y + type_scale["minimum"] * 0.34,
        type_scale["minimum"],
        weight=700,
        fill=T.WHITE,
        figure_role="badge",
        text_role="badge",
    )


def _render_sequence(contract: dict[str, Any]) -> ET.Element:
    type_scale = T.typography(float(contract["final_width_mm"]))
    sequence = contract["sequence"]
    participants = sequence["participants"]
    svg, _ = _svg_root(contract, participants)
    if contract["layout"].get("show_title"):
        header = ET.SubElement(svg, "g", {"id": "header"})
        _add_text(header, contract["takeaway"], T.CANVAS_WIDTH / 2, 30, type_scale["title"], weight=700, figure_role="title")

    x_positions = _distributed(150, T.CANVAS_WIDTH - 150, len(participants))
    participant_x = {str(participant["id"]): x for participant, x in zip(participants, x_positions)}
    header_y, header_width, header_height = 76.0, 220.0, 72.0
    lifeline_top, lifeline_bottom = header_y + header_height / 2, 656.0

    lifelines = ET.SubElement(svg, "g", {"id": "lifelines"})
    ET.SubElement(
        lifelines,
        "path",
        {
            "d": f"M 46 {_fmt(lifeline_top + 16)} L 46 {_fmt(lifeline_bottom - 5)}",
            "fill": "none",
            "stroke": T.STROKE,
            "stroke-width": "1.5",
            "marker-end": "url(#arrow)",
        },
    )
    _add_text(
        lifelines,
        "Time",
        60,
        lifeline_top + 42,
        type_scale["minimum"],
        anchor="start",
        weight=600,
        fill=T.SECONDARY_TEXT,
        figure_role="axis",
        text_role="axis",
    )
    for participant in participants:
        x = participant_x[str(participant["id"])]
        ET.SubElement(
            lifelines,
            "path",
            {
                "d": f"M {_fmt(x)} {_fmt(lifeline_top)} L {_fmt(x)} {_fmt(lifeline_bottom)}",
                "fill": "none",
                "stroke": T.GROUP_BORDER,
                "stroke-width": "1.5",
                "stroke-dasharray": "7 6",
                "data-lifeline-for": str(participant["id"]),
            },
        )

    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for message in sequence["messages"]:
        grouped[(int(message["step"]), str(message["label"]))].append(message)
    unique_steps = sorted({step for step, _ in grouped})

    def is_local_group(step: int, label: str) -> bool:
        return any(
            int(activation["from_step"]) <= step <= int(activation["to_step"])
            and str(activation.get("label", "")) == label
            for activation in sequence.get("activations", [])
        )

    marker_half_width = ARROW_MARKER_LENGTH / 2
    step_bounds: dict[int, tuple[float, float]] = {}
    label_center_offset: dict[tuple[int, str], float] = {}
    message_offsets: dict[tuple[int, str], list[float]] = {}
    for step in unique_steps:
        geometry_top = math.inf
        geometry_bottom = -math.inf
        for (group_step, label), messages in sorted(grouped.items()):
            if group_step != step:
                continue
            local = is_local_group(step, label)
            if local:
                group_top, group_bottom = -20.0, 36.0
                offsets: list[float] = []
            elif messages[0]["kind"] == "self":
                loop_drop = 42.0
                group_top, group_bottom = 0.0, loop_drop + marker_half_width
                offsets = [0.0] * len(messages)
            else:
                span = PARALLEL_MESSAGE_PITCH * max(0, len(messages) - 1)
                offsets = _distributed(-span / 2, span / 2, len(messages)) if len(messages) > 1 else [0.0]
                group_top = min(offsets) - marker_half_width
                group_bottom = max(offsets) + marker_half_width
            message_offsets[(step, label)] = offsets
            pill_height = _pill_box(label, 0, 0, type_scale["label"]).height
            center = group_top - ARROW_ZONE_PADDING - pill_height / 2
            label_center_offset[(step, label)] = center
            geometry_top = min(geometry_top, center - pill_height / 2)
            geometry_bottom = max(geometry_bottom, group_bottom)
        step_bounds[step] = (geometry_top, geometry_bottom)

    # Preserve the intended 194..590 vertical span while packing the actual
    # measured block heights.  Any spare room is distributed between blocks;
    # the mandatory clearance never drops below 8px.
    layout_top = 194.0 + step_bounds[unique_steps[0]][0]
    layout_bottom = 590.0 + step_bounds[unique_steps[-1]][1]
    total_step_height = sum(bottom - top for top, bottom in step_bounds.values())
    if len(unique_steps) > 1:
        step_gap = max(
            STEP_GEOMETRY_CLEARANCE,
            (layout_bottom - layout_top - total_step_height) / (len(unique_steps) - 1),
        )
    else:
        step_gap = 0.0
    step_y: dict[int, float] = {}
    cursor = layout_top
    for step in unique_steps:
        top, bottom = step_bounds[step]
        step_y[step] = cursor - top
        cursor += bottom - top + step_gap

    activations_layer = ET.SubElement(svg, "g", {"id": "activations"})
    for activation in sequence.get("activations", []):
        participant_id = str(activation["participant"])
        x = participant_x[participant_id]
        top = step_y[int(activation["from_step"])] - 20
        bottom = step_y[int(activation["to_step"])] + 36
        ET.SubElement(
            activations_layer,
            "rect",
            {
                "x": _fmt(x - 8),
                "y": _fmt(top),
                "width": "16",
                "height": _fmt(bottom - top),
                "rx": "5",
                "fill": T.role_fill("process"),
                "stroke": T.HIGHLIGHT,
                "stroke-width": "1.5",
                "data-activation-for": participant_id,
            },
        )

    messages_layer = ET.SubElement(svg, "g", {"id": "messages"})
    for (step, label), messages in sorted(grouped.items()):
        base_y = step_y[step]
        is_local_activation = is_local_group(step, label)
        if not is_local_activation:
            offsets = message_offsets[(step, label)]
            for message, offset in zip(messages, offsets):
                source_id, target_id = str(message["from"]), str(message["to"])
                source_x, target_x = participant_x[source_id], participant_x[target_id]
                y = base_y + offset
                if message["kind"] == "self":
                    direction = 1 if source_x < T.CANVAS_WIDTH / 2 else -1
                    reach, drop = 128 * direction, 42
                    points = [(source_x, y), (source_x + reach, y), (source_x + reach, y + drop), (source_x + 12 * direction, y + drop)]
                else:
                    points = [(source_x, y), (target_x, y)]
                edge = {
                    "id": message["id"],
                    "from": source_id,
                    "to": target_id,
                    "kind": message["kind"],
                    "emphasis": False,
                }
                ET.SubElement(messages_layer, "path", _edge_attrs(edge, points))

        widest_source = min(participant_x[str(message["from"])] for message in messages)
        widest_target = max(participant_x[str(message["to"])] for message in messages)
        if messages[0]["kind"] == "self" and not is_local_activation:
            label_x = participant_x[str(messages[0]["from"])] + 82
        elif is_local_activation:
            active_x = [participant_x[str(activation["participant"])] for activation in sequence.get("activations", []) if int(activation["from_step"]) <= step <= int(activation["to_step"])]
            label_x = sum(active_x) / len(active_x)
        else:
            label_x = (widest_source + widest_target) / 2
        label_center_y = base_y + label_center_offset[(step, label)]
        label_y = label_center_y + type_scale["label"] / 2
        label_box = _pill_box(label, label_x, label_y, type_scale["label"])
        badge_offset = T.badge_diameter(type_scale["minimum"]) / 2 + T.badge_gap(type_scale["minimum"])
        _draw_step_badge(messages_layer, step, label_box.left - badge_offset, label_center_y, type_scale)
        # One pill stands in for every message *and* activation that carries
        # this label, so the gate must treat it as a shared label whenever the
        # contract mentions the text more than once.
        activation_mentions = sum(
            1 for activation in sequence.get("activations", []) if str(activation.get("label", "")) == label
        )
        shared = (len(messages) + activation_mentions) > 1
        _draw_pill_label(
            messages_layer,
            label,
            label_x,
            label_y,
            type_scale["label"],
            edge_id=str(messages[0]["id"]),
            extra={"data-shared-label": "true", "data-expected-count": "1"} if shared else None,
        )

    headers = ET.SubElement(svg, "g", {"id": "participants"})
    for participant in participants:
        spec = {
            "id": participant["id"],
            "label": participant["label"],
            "role": participant["role"],
            "kind": "card",
            "icon": participant.get("icon"),
        }
        _draw_node(headers, PlacedNode(spec, participant_x[str(participant["id"])], header_y, header_width, header_height), type_scale)
    _draw_legend(svg, _sequence_legend_kinds(contract), type_scale, y=698)
    return svg


def _candidate_slug(value: str) -> str:
    return _safe_id(value.lower().replace("x", "x"))


def _boundary_segment(source: PlacedNode, target: PlacedNode) -> list[tuple[float, float]]:
    dx, dy = target.x - source.x, target.y - source.y
    source_scale = min(source.width / 2 / max(abs(dx), 1e-9), source.height / 2 / max(abs(dy), 1e-9))
    target_scale = min(target.width / 2 / max(abs(dx), 1e-9), target.height / 2 / max(abs(dy), 1e-9))
    start = (source.x + dx * source_scale, source.y + dy * source_scale)
    end = (target.x - dx * target_scale, target.y - dy * target_scale)
    return [start, end]


def _render_search_grid(contract: dict[str, Any]) -> ET.Element:
    type_scale = T.typography(float(contract["final_width_mm"]))
    grid = contract["grid"]
    layers, candidates = grid["layers"], grid["candidates"]
    terminal_specs = {str(node["id"]): node for node in contract["nodes"]}
    icon_specs = list(terminal_specs.values())
    svg, _ = _svg_root(contract, icon_specs)
    if contract["layout"].get("show_title"):
        header = ET.SubElement(svg, "g", {"id": "header"})
        _add_text(header, contract["takeaway"], T.CANVAS_WIDTH / 2, 30, type_scale["title"], weight=700, figure_role="title")

    candidate_y = _distributed(142, 526, len(candidates))
    terminal_y = (candidate_y[0] + candidate_y[-1]) / 2
    input_spec = terminal_specs.get("input", {"id": "input", "label": "Input", "role": "input", "kind": "chip"})
    output_spec = terminal_specs.get("output", {"id": "output", "label": "Output", "role": "output", "kind": "chip"})
    input_width, input_height = _node_dimensions(input_spec, type_scale)
    output_width, output_height = _node_dimensions(output_spec, type_scale)
    canvas_margin = 16.0
    terminal_gap = 28.0
    input_node = PlacedNode(input_spec, canvas_margin + input_width / 2, terminal_y, input_width, input_height)
    output_node = PlacedNode(
        output_spec,
        T.CANVAS_WIDTH - canvas_margin - output_width / 2,
        terminal_y,
        output_width,
        output_height,
    )
    grid_left = input_node.box.right + terminal_gap
    grid_right = output_node.box.left - terminal_gap
    layer_gap = 24.0
    available_layer_width = (grid_right - grid_left - layer_gap * max(0, len(layers) - 1)) / max(1, len(layers))
    candidate_width = min(178.0, available_layer_width - 36.0)
    candidate_padding_x, candidate_padding_y = T.card_padding(type_scale["card"])
    candidate_lines = max(
        len(
            _label_lines(
                str(candidate),
                type_scale["card"],
                0,
                max_width=candidate_width,
                padding_x=candidate_padding_x,
            )
        )
        for candidate in candidates
    )
    candidate_height = max(58.0, candidate_lines * type_scale["card"] * 1.2 + 2 * candidate_padding_y)
    container_width = candidate_width + 36.0
    layer_x = _distributed(grid_left + container_width / 2, grid_right - container_width / 2, len(layers))
    nodes: dict[str, PlacedNode] = {}
    for layer, x in zip(layers, layer_x):
        for candidate, y in zip(candidates, candidate_y):
            node_id = f"{layer['id']}--{_candidate_slug(candidate)}"
            spec = {"id": node_id, "label": candidate, "role": "neutral", "kind": "card", "icon": None}
            nodes[node_id] = PlacedNode(spec, x, y, candidate_width, candidate_height)
    nodes["input"] = input_node
    nodes["output"] = output_node

    groups_layer = ET.SubElement(svg, "g", {"id": "layer-containers"})
    layer_boxes: dict[str, Box] = {}
    for layer, x in zip(layers, layer_x):
        box = Box(x - candidate_width / 2 - 18, 77, x + candidate_width / 2 + 18, 577)
        layer_boxes[str(layer["id"])] = box
        group = ET.SubElement(
            groups_layer,
            "g",
            {
                "id": f"group-{_safe_id(str(layer['id']))}",
                "data-pptx-bounds": " ".join(_fmt(value) for value in (box.left, box.top, box.width, box.height)),
                "data-group-id": str(layer["id"]),
                "data-figure-role": "group",
            },
        )
        ET.SubElement(
            group,
            "rect",
            {
                "x": _fmt(box.left),
                "y": _fmt(box.top),
                "width": _fmt(box.width),
                "height": _fmt(box.height),
                "rx": _fmt(T.RADIUS_PANEL),
                "fill": T.group_fill("neutral"),
                "fill-opacity": "0.35",
                "stroke": T.GROUP_BORDER,
                "stroke-width": "1.5",
                "stroke-dasharray": "7 5",
                "data-figure-role": "group",
            },
        )
        label = str(layer["label"])
        label_padding_x, label_padding_y = T.pill_padding(type_scale["section"])
        label_width = T.text_width_px(label, type_scale["section"], 600) + 2 * label_padding_x
        label_height = type_scale["section"] + 2 * label_padding_y
        label_bottom = box.top - 7
        ET.SubElement(
            group,
            "rect",
            {
                "x": _fmt(x - label_width / 2),
                "y": _fmt(label_bottom - label_height),
                "width": _fmt(label_width),
                "height": _fmt(label_height),
                "rx": _fmt(label_height / 2),
                "fill": T.BACKGROUND,
                "stroke": T.GROUP_BORDER,
                "stroke-width": "1",
                "data-label-background": "true",
            },
        )
        _add_text(
            group,
            label,
            x,
            label_bottom - label_height / 2 + type_scale["section"] * 0.3,
            type_scale["section"],
            weight=600,
            figure_role="group-label",
        )

    feasible = ET.SubElement(svg, "g", {"id": "feasible-transitions"})
    first_ids = [f"{layers[0]['id']}--{_candidate_slug(candidate)}" for candidate in candidates]
    last_ids = [f"{layers[-1]['id']}--{_candidate_slug(candidate)}" for candidate in candidates]
    feasible_pairs: list[tuple[str, str]] = [("input", target) for target in first_ids]
    for first_layer, second_layer in zip(layers, layers[1:]):
        for source_candidate in candidates:
            for target_candidate in candidates:
                feasible_pairs.append(
                    (
                        f"{first_layer['id']}--{_candidate_slug(source_candidate)}",
                        f"{second_layer['id']}--{_candidate_slug(target_candidate)}",
                    )
                )
    feasible_pairs.extend((source, "output") for source in last_ids)
    for source_id, target_id in feasible_pairs:
        ET.SubElement(
            feasible,
            "path",
            {
                "d": _path_data(_boundary_segment(nodes[source_id], nodes[target_id])),
                "fill": "none",
                "stroke": T.GROUP_BORDER,
                "stroke-width": "1.25",
                "stroke-opacity": "0.9",
                "stroke-linecap": "round",
            },
        )

    selected_by_layer = {str(item["layer"]): str(item["candidate"]) for item in grid["selected"]}
    selected_ids = ["input"] + [
        f"{layer['id']}--{_candidate_slug(selected_by_layer[str(layer['id'])])}"
        for layer in layers
        if str(layer["id"]) in selected_by_layer
    ] + ["output"]
    selected_layer = ET.SubElement(svg, "g", {"id": "selected-path"})
    for index, (source_id, target_id) in enumerate(zip(selected_ids, selected_ids[1:]), start=1):
        edge = {"id": f"selected-{index}", "from": source_id, "to": target_id, "kind": "data", "emphasis": True}
        if source_id == "input":
            source, target = nodes[source_id], nodes[target_id]
            corridor = (source.box.right + layer_boxes[str(layers[0]["id"])].left) / 2
            points = [(source.box.right, source.y), (corridor, source.y), (corridor, target.y), (target.box.left, target.y)]
        elif target_id == "output":
            source, target = nodes[source_id], nodes[target_id]
            corridor = target.box.left - ARROW_CLEARANCE
            points = [(source.box.right, source.y), (corridor, source.y), (corridor, target.y), (target.box.left, target.y)]
        else:
            source, target = nodes[source_id], nodes[target_id]
            source_layer = source_id.split("--", 1)[0]
            target_layer = target_id.split("--", 1)[0]
            corridor = (layer_boxes[source_layer].right + layer_boxes[target_layer].left) / 2
            target_label = str(target.spec["label"])
            target_text_width = max(
                T.text_width_px(target_label, type_scale["card"], 600),
                len(target_label) * type_scale["card"] * 0.61,
            )
            if target_text_width > target.width:
                # A long label can conservatively protrude through the side
                # boundary.  Enter through the nearer horizontal boundary so
                # the measured 28px final run stays clear of that text.
                entry_y = target.box.top if target.y >= source.y else target.box.bottom
                outside_y = entry_y - ARROW_CLEARANCE if entry_y == target.box.top else entry_y + ARROW_CLEARANCE
                port_x = target.box.left + ARROW_CLEARANCE
                points = [
                    (source.box.right, source.y),
                    (corridor, source.y),
                    (corridor, outside_y),
                    (port_x, outside_y),
                    (port_x, entry_y),
                ]
                foreign_nodes = [
                    node.box
                    for node_id, node in nodes.items()
                    if node_id not in {source_id, target_id}
                ]
                if any(
                    _segment_intersects_box(first, second, box)
                    for first, second in zip(points, points[1:])
                    for box in foreign_nodes
                ):
                    entry_y = target.box.bottom
                    outside_y = entry_y + ARROW_CLEARANCE
                    points = [
                        (source.box.right, source.y),
                        (corridor, source.y),
                        (corridor, outside_y),
                        (port_x, outside_y),
                        (port_x, entry_y),
                    ]
            else:
                points = [(source.box.right, source.y), (corridor, source.y), (corridor, target.y), (target.box.left, target.y)]
        ET.SubElement(selected_layer, "path", _edge_attrs(edge, points))

    node_layer = ET.SubElement(svg, "g", {"id": "nodes"})
    _draw_node(node_layer, nodes["input"], type_scale)
    for layer in layers:
        for candidate in candidates:
            _draw_node(
                node_layer,
                nodes[f"{layer['id']}--{_candidate_slug(candidate)}"],
                type_scale,
                shared_label=True,
                expected_count=len(layers),
            )
    _draw_node(node_layer, nodes["output"], type_scale)

    legend_y = 624
    legend = ET.SubElement(svg, "g", {"id": "legend"})
    ET.SubElement(
        legend,
        "path",
        {
            "d": f"M 432 {legend_y} L 474 {legend_y}",
            "fill": "none",
            "stroke": T.HIGHLIGHT,
            "stroke-width": "3",
            "marker-end": "url(#arrow-highlight)",
        },
    )
    _add_text(legend, "Selected optimal path", 490, legend_y + 6, type_scale["minimum"], anchor="start", weight=600, fill=T.HIGHLIGHT, figure_role="legend")
    ET.SubElement(
        legend,
        "path",
        {
            "d": f"M 746 {legend_y} L 788 {legend_y}",
            "fill": "none",
            "stroke": T.GROUP_BORDER,
            "stroke-width": "1.25",
            "stroke-opacity": "0.9",
        },
    )
    _add_text(legend, "Feasible transition", 802, legend_y + 6, type_scale["minimum"], anchor="start", fill=T.SECONDARY_TEXT, figure_role="legend")
    if contract.get("footnote"):
        footnote = ET.SubElement(svg, "g", {"id": "footnote"})
        _add_text(
            footnote,
            str(contract["footnote"]),
            T.CANVAS_WIDTH / 2,
            697,
            type_scale["footnote"],
            fill=T.SECONDARY_TEXT,
            figure_role="footnote",
            text_role="footnote",
        )
    return svg


def _union_boxes(boxes: Iterable[Box | None]) -> Box | None:
    visible = [box for box in boxes if box is not None]
    if not visible:
        return None
    return Box(
        min(box.left for box in visible),
        min(box.top for box in visible),
        max(box.right for box in visible),
        max(box.bottom for box in visible),
    )


def _stroke_expansion(element: ET.Element) -> float:
    stroke = (element.get("stroke") or "none").strip().lower()
    if stroke == "none" or float(element.get("stroke-opacity", "1")) <= 0:
        return 0.0
    return float(element.get("stroke-width", "1")) / 2


def _path_points(element: ET.Element) -> list[tuple[float, float]]:
    """Return points from renderer-owned M/L paths used in visible layers."""

    values = [float(value) for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", element.get("d", ""))]
    return list(zip(values[0::2], values[1::2])) if len(values) % 2 == 0 else []


def _marker_end_bounds(points: Sequence[tuple[float, float]], stroke_width: float) -> Box | None:
    if len(points) < 2 or stroke_width <= 0:
        return None
    end = points[-1]
    previous = next((point for point in reversed(points[:-1]) if point != end), None)
    if previous is None:
        return None
    dx, dy = end[0] - previous[0], end[1] - previous[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        return None
    ux, uy = dx / length, dy / length
    vx, vy = -uy, ux
    # markerWidth/Height=10, ref=(9,5), default markerUnits=strokeWidth.
    local = ((-9 * stroke_width, -5 * stroke_width), (stroke_width, 0), (-9 * stroke_width, 5 * stroke_width))
    marker_points = [
        (end[0] + ux * along + vx * across, end[1] + uy * along + vy * across)
        for along, across in local
    ]
    return Box(
        min(point[0] for point in marker_points),
        min(point[1] for point in marker_points),
        max(point[0] for point in marker_points),
        max(point[1] for point in marker_points),
    )


def _text_element_bounds(element: ET.Element) -> Box | None:
    size = float(element.get("font-size", "0"))
    weight = int(element.get("font-weight", "400"))
    anchor = element.get("text-anchor", "start")
    x = float(element.get("x", "0"))
    y = float(element.get("y", "0"))
    tspans = [child for child in element if child.tag.rsplit("}", 1)[-1] == "tspan"]
    def checker_box(value: str, line_x: float, line_y: float) -> Box:
        width = _pptmaster_text_width(
            [{
                "text": value,
                "font_size": size,
                "font_weight": str(weight),
                "font_family": element.get("font-family", T.FONT_FAMILY),
                "letter_spacing": float(element.get("letter-spacing", "0")),
            }]
        )
        left = line_x - width / 2 if anchor == "middle" else line_x - width if anchor == "end" else line_x
        return Box(left, line_y - size * 0.85, left + width, line_y + size * 0.35)

    if not tspans:
        return checker_box("".join(element.itertext()), x, y)

    lines: list[Box] = []
    current_x, current_y = x, y
    for tspan in tspans:
        if tspan.get("x") is not None:
            current_x = float(tspan.get("x", "0"))
        if tspan.get("y") is not None:
            current_y = float(tspan.get("y", "0"))
        current_x += float(tspan.get("dx", "0"))
        current_y += float(tspan.get("dy", "0"))
        lines.append(checker_box((tspan.text or "").strip(), current_x, current_y))
    return _union_boxes(lines)


def _element_bounds(element: ET.Element) -> Box | None:
    tag = element.tag.rsplit("}", 1)[-1]
    if tag in {"defs", "marker", "symbol", "title", "desc", "metadata"}:
        return None
    if tag == "g":
        return _union_boxes(_element_bounds(child) for child in element)
    if tag == "text":
        return _text_element_bounds(element)
    if tag in {"rect", "use", "image"}:
        x, y = float(element.get("x", "0")), float(element.get("y", "0"))
        width, height = float(element.get("width", "0")), float(element.get("height", "0"))
        box = Box(x, y, x + width, y + height)
    elif tag == "circle":
        cx, cy, radius = float(element.get("cx", "0")), float(element.get("cy", "0")), float(element.get("r", "0"))
        box = Box(cx - radius, cy - radius, cx + radius, cy + radius)
    elif tag == "ellipse":
        cx, cy = float(element.get("cx", "0")), float(element.get("cy", "0"))
        radius_x, radius_y = float(element.get("rx", "0")), float(element.get("ry", "0"))
        box = Box(cx - radius_x, cy - radius_y, cx + radius_x, cy + radius_y)
    elif tag == "line":
        x1, y1 = float(element.get("x1", "0")), float(element.get("y1", "0"))
        x2, y2 = float(element.get("x2", "0")), float(element.get("y2", "0"))
        box = Box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    elif tag in {"polygon", "polyline"}:
        values = [float(value) for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", element.get("points", ""))]
        points = list(zip(values[0::2], values[1::2])) if len(values) % 2 == 0 else []
        if not points:
            return None
        box = Box(
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        )
    elif tag == "path":
        points = _path_points(element)
        if not points:
            return None
        box = Box(
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        )
        if element.get("marker-end"):
            stroke_width = float(element.get("stroke-width", "1"))
            box = _union_boxes((box, _marker_end_bounds(points, stroke_width))) or box
    else:
        return None

    stroke = _stroke_expansion(element)
    return box.expanded(stroke) if stroke else box


def _bounds_value(box: Box) -> str:
    left = math.floor(box.left * 100) / 100
    top = math.floor(box.top * 100) / 100
    right = math.ceil(box.right * 100) / 100
    bottom = math.ceil(box.bottom * 100) / 100
    return " ".join(_fmt(value) for value in (left, top, right - left, bottom - top))


def _assign_top_level_bounds(root: ET.Element) -> None:
    groups = [child for child in root if child.tag.rsplit("}", 1)[-1] == "g"]
    if not 3 <= len(groups) <= 8:
        raise ValueError(f"flat page requires 3–8 top-level logical groups; found {len(groups)}")
    for group in groups:
        box = _element_bounds(group)
        if box is None or box.width <= 0 or box.height <= 0:
            raise ValueError(f"top-level group {group.get('id')!r} has no positive visible geometry")
        if box.left < 0 or box.top < 0 or box.right > T.CANVAS_WIDTH or box.bottom > T.CANVAS_HEIGHT:
            raise ValueError(f"top-level group {group.get('id')!r} exceeds the root viewBox: {box}")
        group.set("data-pptx-bounds", _bounds_value(box))


def _pretty_svg(root: ET.Element) -> str:
    rough = ET.tostring(root, encoding="utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


def _uppercase_hex_colors(root: ET.Element) -> None:
    pattern = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
    for element in root.iter():
        for key, value in element.attrib.items():
            element.set(key, pattern.sub(lambda match: match.group(0).upper(), value))


def _visible_texts(root: ET.Element) -> list[tuple[str, ET.Element]]:
    return [("".join(element.itertext()), element) for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "text"]


def _assert_rendered_contract(contract: dict[str, Any], root: ET.Element) -> None:
    visible = _visible_texts(root)
    values = [text for text, _ in visible]
    required_label_counts: dict[str, int] = defaultdict(int)
    for node in contract.get("nodes", []):
        required_label_counts[str(node["label"])] += 1
    if contract.get("sequence"):
        for participant in contract["sequence"]["participants"]:
            required_label_counts[str(participant["label"])] += 1
    for label in {str(edge["label"]) for edge in contract.get("edges", []) if edge.get("label")}:
        required_label_counts[label] += 1
    if contract.get("sequence"):
        for _, label in {
            (int(item["step"]), str(item["label"]))
            for item in contract["sequence"]["messages"]
        }:
            required_label_counts[label] += 1
    for label, expected_count in sorted(required_label_counts.items()):
        count = values.count(label)
        if count != expected_count:
            raise ValueError(
                f"render assertion failed: label {label!r} appears {count} times "
                f"(expected {expected_count})"
            )

    minimum = T.min_font_px(float(contract["final_width_mm"]))
    for text, element in visible:
        size = float(element.get("font-size", "0"))
        if size < minimum - 1e-6:
            raise ValueError(f"render assertion failed: {text!r} uses {size}px below {minimum}px")
        x, y = float(element.get("x", "0")), float(element.get("y", "0"))
        weight = int(element.get("font-weight", "400"))
        anchor = element.get("text-anchor", "start")
        tspans = [child for child in element if child.tag.rsplit("}", 1)[-1] == "tspan"]
        if tspans:
            line_texts = [(child.text or "").strip() for child in tspans]
            width = max(T.text_width_px(line, size, weight) for line in line_texts)
            left = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
            line_height = size * 1.2
            box = Box(left, y - size * 0.84, left + width, y + (len(tspans) - 1) * line_height + size * 0.24)
        else:
            box = _text_box(text, x, y, size, weight, anchor)
        if box.left < -0.5 or box.top < -0.5 or box.right > T.CANVAS_WIDTH + 0.5 or box.bottom > T.CANVAS_HEIGHT + 0.5:
            raise ValueError(f"render assertion failed: text outside viewBox: {text!r} at {box}")


def render_contract(contract: dict[str, Any]) -> str:
    variant = str(contract["layout"]["variant"])
    if variant == "sequence":
        root = _render_sequence(contract)
    elif variant == "search-space-grid":
        root = _render_search_grid(contract)
    else:
        root = _render_graph(contract)
    _uppercase_hex_colors(root)
    _assign_top_level_bounds(root)
    _assert_rendered_contract(contract, root)
    return _pretty_svg(root)


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _semantic_errors(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = contract.get("nodes", [])
    node_ids = [str(node.get("id")) for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        errors.append("node ids must be unique")
    group_ids = [str(group.get("id")) for group in contract.get("groups", [])]
    if len(group_ids) != len(set(group_ids)):
        errors.append("group ids must be unique")
    edge_ids = [str(edge.get("id")) for edge in contract.get("edges", [])]
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("edge ids must be unique")
    valid_nodes = set(node_ids)
    for group in contract.get("groups", []):
        for node_id in group.get("node_ids", []):
            if node_id not in valid_nodes:
                errors.append(f"group {group.get('id')!r} references unknown node {node_id!r}")
    for edge in contract.get("edges", []):
        if edge.get("from") not in valid_nodes:
            errors.append(f"edge {edge.get('id')!r} has unknown source {edge.get('from')!r}")
        if edge.get("to") not in valid_nodes:
            errors.append(f"edge {edge.get('id')!r} has unknown target {edge.get('to')!r}")
    if contract.get("layout", {}).get("variant") == "sequence" and contract.get("sequence"):
        participant_ids = [str(item["id"]) for item in contract["sequence"]["participants"]]
        if len(participant_ids) != len(set(participant_ids)):
            errors.append("sequence participant ids must be unique")
        valid_participants = set(participant_ids)
        message_ids = [str(item["id"]) for item in contract["sequence"]["messages"]]
        if len(message_ids) != len(set(message_ids)):
            errors.append("sequence message ids must be unique")
        steps = {int(message["step"]) for message in contract["sequence"]["messages"]}
        for message in contract["sequence"]["messages"]:
            if message["from"] not in valid_participants or message["to"] not in valid_participants:
                errors.append(f"message {message['id']!r} references an unknown participant")
        for activation in contract["sequence"].get("activations", []):
            if activation["participant"] not in valid_participants:
                errors.append(f"activation references unknown participant {activation['participant']!r}")
            if activation["from_step"] not in steps or activation["to_step"] not in steps:
                errors.append("activation step must refer to a message step")
            if activation["from_step"] > activation["to_step"]:
                errors.append("activation from_step must be <= to_step")
    if contract.get("layout", {}).get("variant") == "search-space-grid" and contract.get("grid"):
        layer_ids = [str(layer["id"]) for layer in contract["grid"]["layers"]]
        candidates = set(str(candidate) for candidate in contract["grid"]["candidates"])
        selected_layers: set[str] = set()
        for selection in contract["grid"]["selected"]:
            if selection["layer"] not in layer_ids:
                errors.append(f"selected path references unknown layer {selection['layer']!r}")
            if selection["candidate"] not in candidates:
                errors.append(f"selected path references unknown candidate {selection['candidate']!r}")
            if selection["layer"] in selected_layers:
                errors.append(f"selected path chooses layer {selection['layer']!r} more than once")
            selected_layers.add(selection["layer"])
    return errors


def validate_contract(contract: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(_load_schema())
    errors = [f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}" for error in validator.iter_errors(contract)]
    errors.extend(_semantic_errors(contract))
    return sorted(errors)


def _read_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    errors = validate_contract(contract)
    if errors:
        raise ValueError("contract validation failed:\n  - " + "\n  - ".join(errors))
    return contract


def _write_svg(path: Path, svg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="validate and render one figure contract")
    render.add_argument("contract", type=Path)
    render.add_argument("--output", "-o", type=Path, required=True)
    validate = commands.add_parser("validate", help="validate one figure contract")
    validate.add_argument("contract", type=Path)
    build = commands.add_parser("build-all", help="validate and render every contract in a directory")
    build.add_argument("--contracts-dir", type=Path, default=Path(__file__).with_name("contracts"))
    build.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("render_check"))
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            contract = _read_contract(args.contract)
            print(f"valid {args.contract} ({contract['layout']['variant']})")
            return 0
        if args.command == "render":
            contract = _read_contract(args.contract)
            _write_svg(args.output, render_contract(contract))
            print(f"rendered {args.output}")
            return 0
        paths = sorted(args.contracts_dir.glob("*.json"))
        if not paths:
            raise ValueError(f"no contracts found in {args.contracts_dir}")
        for path in paths:
            contract = _read_contract(path)
            output = args.output_dir / f"{contract['figure_id']}.svg"
            _write_svg(output, render_contract(contract))
            print(f"rendered {output}")
        return 0
    except (ValueError, OSError, KeyError, jsonschema.SchemaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
