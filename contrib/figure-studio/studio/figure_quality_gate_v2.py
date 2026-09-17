#!/usr/bin/env python3
"""Deterministic source gate for publication SVG and editable PPTX files.

Checks node overlap, text overlap, edge-label overlap with nodes, canvas
overflow, minimum font size, and edges crossing non-endpoint nodes.  Native
renderer metadata is used when available; conservative shape inference
supports the legacy baseline SVGs.  Contract mode additionally rejects labels
covering arrowheads, endpoint re-entry, overlapping arrowheads, edges crossing
group-label chips, and marker-ended paths whose last segment is under 20px,
then checks physical size, exact labels, metadata, PPT Master, and PPTX
editability.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

sys.dont_write_bytecode = True

from pptmaster_bridge import check_svg as pptmaster_check_svg
from pptmaster_bridge import pptx_inventory


GATE_VERSION = "2.0"
MIN_FONT_SIZE = 12.0
MIN_PHYSICAL_PT = 7.0
PREFERRED_PHYSICAL_PT = 8.0
OVERLAP_TOLERANCE = 1.0
ARROW_MARKER_LENGTH = 20.0
ARROW_LABEL_PADDING = 4.0
ALLOWED_NON_CONTRACT_ROLES = {"title", "badge", "legend", "axis", "footnote", "step-number"}
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


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

    def inset(self, amount: float) -> "Box":
        return Box(self.left + amount, self.top + amount, self.right - amount, self.bottom - amount)

    def expanded(self, amount: float) -> "Box":
        return Box(self.left - amount, self.top - amount, self.right + amount, self.bottom + amount)

    def contains(self, point: tuple[float, float]) -> bool:
        x, y = point
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def union(self, other: "Box") -> "Box":
        return Box(
            min(self.left, other.left),
            min(self.top, other.top),
            max(self.right, other.right),
            max(self.bottom, other.bottom),
        )


@dataclass
class NodeGeometry:
    node_id: str
    box: Box


@dataclass
class TextGeometry:
    text: str
    box: Box
    font_size: float
    role: str
    shared_label: bool = False
    expected_counts: frozenset[int] = frozenset()


@dataclass
class EdgeGeometry:
    edge_id: str
    source: str | None
    target: str | None
    points: list[tuple[float, float]]
    marker_end: bool


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", value)
    return float(match.group()) if match else default


def _style(element: ET.Element, inherited: dict[str, str]) -> dict[str, str]:
    result = dict(inherited)
    inline = element.get("style", "")
    for item in inline.split(";"):
        if ":" in item:
            key, value = item.split(":", 1)
            result[key.strip()] = value.strip()
    for key in (
        "font-size", "font-family", "font-weight", "text-anchor", "fill", "fill-opacity",
        "stroke", "stroke-width", "stroke-opacity", "opacity", "display", "visibility",
    ):
        if element.get(key) is not None:
            result[key] = str(element.get(key))
    return result


def _multiply(first: Matrix, second: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _transform_matrix(value: str | None) -> Matrix:
    if not value:
        return IDENTITY
    result = IDENTITY
    pattern = re.compile(r"(matrix|translate|scale|rotate)\s*\(([^)]*)\)")
    for name, raw_args in pattern.findall(value):
        args = [float(item) for item in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", raw_args)]
        operation = IDENTITY
        if name == "matrix" and len(args) >= 6:
            operation = tuple(args[:6])  # type: ignore[assignment]
        elif name == "translate" and args:
            operation = (1, 0, 0, 1, args[0], args[1] if len(args) > 1 else 0)
        elif name == "scale" and args:
            operation = (args[0], 0, 0, args[1] if len(args) > 1 else args[0], 0, 0)
        elif name == "rotate" and args:
            radians = math.radians(args[0])
            cosine, sine = math.cos(radians), math.sin(radians)
            rotation: Matrix = (cosine, sine, -sine, cosine, 0, 0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                operation = _multiply(_multiply((1, 0, 0, 1, cx, cy), rotation), (1, 0, 0, 1, -cx, -cy))
            else:
                operation = rotation
        result = _multiply(result, operation)
    return result


def _point(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _box_from_points(points: Iterable[tuple[float, float]]) -> Box:
    points = list(points)
    return Box(
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _transformed_box(box: Box, matrix: Matrix) -> Box:
    return _box_from_points(
        _point(matrix, x, y)
        for x, y in (
            (box.left, box.top), (box.right, box.top), (box.right, box.bottom), (box.left, box.bottom)
        )
    )


def _shape_box(element: ET.Element, matrix: Matrix) -> Box | None:
    tag = _local_name(element.tag)
    if tag == "rect":
        if "%" in element.get("width", "") or "%" in element.get("height", ""):
            return None
        x, y = _number(element.get("x")), _number(element.get("y"))
        width, height = _number(element.get("width")), _number(element.get("height"))
        if width <= 0 or height <= 0:
            return None
        return _transformed_box(Box(x, y, x + width, y + height), matrix)
    if tag in {"ellipse", "circle"}:
        cx, cy = _number(element.get("cx")), _number(element.get("cy"))
        rx = _number(element.get("rx"), _number(element.get("r")))
        ry = _number(element.get("ry"), _number(element.get("r")))
        return _transformed_box(Box(cx - rx, cy - ry, cx + rx, cy + ry), matrix)
    if tag == "polygon":
        values = [float(item) for item in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)", element.get("points", ""))]
        if len(values) >= 6:
            return _box_from_points(_point(matrix, values[index], values[index + 1]) for index in range(0, len(values) - 1, 2))
    return None


def _font_size(element: ET.Element, style: dict[str, str]) -> float:
    raw = style.get("font-size")
    if raw is None:
        for descendant in element.iter():
            if descendant is element:
                continue
            descendant_style = _style(descendant, style)
            if descendant_style.get("font-size"):
                raw = descendant_style["font-size"]
                break
    size = _number(raw, 16.0)
    if raw and raw.strip().endswith("pt"):
        size *= 4 / 3
    return size


def _text_geometry(
    element: ET.Element,
    matrix: Matrix,
    style: dict[str, str],
    role: str,
    shared_label: bool,
    expected_counts: frozenset[int],
) -> TextGeometry | None:
    text = re.sub(r"\s+", " ", "".join(element.itertext())).strip()
    if not text or style.get("display") == "none" or style.get("visibility") == "hidden":
        return None
    coordinate_source = element
    if element.get("x") is None or element.get("y") is None:
        coordinate_source = next(
            (descendant for descendant in element.iter() if descendant.get("x") is not None and descendant.get("y") is not None),
            element,
        )
    x, y = _number(coordinate_source.get("x")), _number(coordinate_source.get("y"))
    font_size = _font_size(element, style)
    bold = style.get("font-weight", "").lower() in {"bold", "600", "700", "800", "900"}
    anchor = style.get("text-anchor", "start")

    def line_box(value: str, line_x: float, line_y: float) -> Box:
        width = max(font_size * 0.55, len(value) * font_size * (0.61 if bold else 0.57))
        left = line_x - width / 2 if anchor == "middle" else line_x - width if anchor == "end" else line_x
        return Box(left, line_y - 0.84 * font_size, left + width, line_y + 0.24 * font_size)

    tspans = [child for child in element if _local_name(child.tag) == "tspan" and (child.text or "").strip()]
    if tspans:
        line_boxes: list[Box] = []
        current_x, current_y = x, y
        for tspan in tspans:
            if tspan.get("x") is not None:
                current_x = _number(tspan.get("x"))
            if tspan.get("y") is not None:
                current_y = _number(tspan.get("y"))
            elif tspan.get("dy") is not None:
                current_y += _number(tspan.get("dy"))
            line_value = re.sub(r"\s+", " ", tspan.text or "").strip()
            if line_value:
                line_boxes.append(line_box(line_value, current_x, current_y))
        local_box = line_boxes[0]
        for item in line_boxes[1:]:
            local_box = local_box.union(item)
    else:
        # This is deliberately conservative but stable across installed fonts.
        local_box = line_box(text, x, y)
    return TextGeometry(
        text=text,
        box=_transformed_box(local_box, matrix),
        font_size=font_size,
        role=role,
        shared_label=shared_label,
        expected_counts=expected_counts,
    )


def _bounds_box(element: ET.Element, matrix: Matrix) -> Box | None:
    raw = element.get("data-pptx-bounds")
    if not raw:
        return None
    values = [float(item) for item in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", raw)]
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        return None
    x, y, width, height = values
    return _transformed_box(Box(x, y, x + width, y + height), matrix)


def _normalise_text(value: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value))).strip()


def _contract_labels(contract: dict[str, Any]) -> Counter[str]:
    """Collect label-bearing contract fields without treating titles as labels."""
    labels: Counter[str] = Counter()
    collections = {
        "nodes", "edges", "groups", "participants", "messages", "labels",
        "layers", "candidates", "activations",
    }

    def add(value: object) -> None:
        if isinstance(value, str):
            normalized = _normalise_text(value)
            if normalized:
                labels[normalized] += 1
        elif isinstance(value, list):
            for item in value:
                add(item)

    def entity(collection: str, value: object) -> None:
        if isinstance(value, str):
            add(value)
            return
        if not isinstance(value, dict):
            return
        for key in ("label", "sublabel", "sublabels"):
            if key in value:
                add(value[key])
        if collection == "participants" and not any(key in value for key in ("label", "sublabel", "sublabels")):
            add(value.get("name", ""))
        if collection in {"messages", "activations"} and not any(key in value for key in ("label", "sublabel", "sublabels")):
            add(value.get("text", value.get("name", "")))

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = key.replace("-", "_").casefold()
                if normalized_key in collections:
                    if isinstance(child, list):
                        for item in child:
                            entity(normalized_key, item)
                    else:
                        entity(normalized_key, child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(contract)
    return labels


def _load_contract(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load contract {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"contract must be a JSON object: {path}")
    return value


def _polyline_points(element: ET.Element, matrix: Matrix) -> list[tuple[float, float]]:
    tag = _local_name(element.tag)
    if tag == "line":
        return [
            _point(matrix, _number(element.get("x1")), _number(element.get("y1"))),
            _point(matrix, _number(element.get("x2")), _number(element.get("y2"))),
        ]
    if tag in {"polyline", "polygon"}:
        values = [float(item) for item in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", element.get("points", ""))]
        return [_point(matrix, values[index], values[index + 1]) for index in range(0, len(values) - 1, 2)]
    if tag != "path":
        return []
    tokens = re.findall(r"[MLHVZmlhvz]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", element.get("d", ""))
    result: list[tuple[float, float]] = []
    index = 0
    command = ""
    x = y = 0.0
    start = (0.0, 0.0)
    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in "Zz" and result:
                result.append(_point(matrix, *start))
            continue
        relative = command.islower()
        upper = command.upper()
        if upper in {"M", "L"} and index + 1 < len(tokens):
            nx, ny = float(tokens[index]), float(tokens[index + 1])
            if relative:
                nx, ny = x + nx, y + ny
            x, y = nx, ny
            if upper == "M":
                start = (x, y)
                command = "l" if relative else "L"
            result.append(_point(matrix, x, y))
            index += 2
        elif upper == "H":
            nx = float(tokens[index]) + (x if relative else 0)
            x = nx
            result.append(_point(matrix, x, y))
            index += 1
        elif upper == "V":
            ny = float(tokens[index]) + (y if relative else 0)
            y = ny
            result.append(_point(matrix, x, y))
            index += 1
        else:
            # Curves/arcs are intentionally not approximated as straight
            # segments.  Renderer output and legacy baselines use M/L paths.
            index += 1
    return result


def _overlap(first: Box, second: Box, tolerance: float = OVERLAP_TOLERANCE) -> bool:
    return (
        min(first.right, second.right) - max(first.left, second.left) > tolerance
        and min(first.bottom, second.bottom) - max(first.top, second.top) > tolerance
    )


def _box_intersects_outline(box: Box, border: Box) -> bool:
    horizontal_overlap = min(box.right, border.right) >= max(box.left, border.left)
    vertical_overlap = min(box.bottom, border.bottom) >= max(box.top, border.top)
    return (
        horizontal_overlap and (box.top <= border.top <= box.bottom or box.top <= border.bottom <= box.bottom)
    ) or (
        vertical_overlap and (box.left <= border.left <= box.right or box.left <= border.right <= box.right)
    )


def _segment_intersects_box(start: tuple[float, float], end: tuple[float, float], box: Box) -> bool:
    if box.width <= 0 or box.height <= 0:
        return False
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    p = (-dx, dx, -dy, dy)
    q = (x1 - box.left, box.right - x1, y1 - box.top, box.bottom - y1)
    lower, upper = 0.0, 1.0
    for direction, distance in zip(p, q):
        if abs(direction) < 1e-12:
            if distance < 0:
                return False
            continue
        ratio = distance / direction
        if direction < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _arrowhead_zone(edge: EdgeGeometry, padding: float = 0.0) -> Box:
    """Return the axis-aligned footprint of the final rendered 20px marker."""

    start, end = edge.points[-2], edge.points[-1]
    length = math.dist(start, end)
    if length < 1e-9:
        return Box(end[0], end[1], end[0], end[1]).expanded(padding)
    ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    base = (
        end[0] - ux * min(ARROW_MARKER_LENGTH, length),
        end[1] - uy * min(ARROW_MARKER_LENGTH, length),
    )
    half_width = ARROW_MARKER_LENGTH / 2
    normal = (-uy * half_width, ux * half_width)
    return Box(
        min(base[0] - normal[0], base[0] + normal[0], end[0] - normal[0], end[0] + normal[0]),
        min(base[1] - normal[1], base[1] + normal[1], end[1] - normal[1], end[1] + normal[1]),
        max(base[0] - normal[0], base[0] + normal[0], end[0] - normal[0], end[0] + normal[0]),
        max(base[1] - normal[1], base[1] + normal[1], end[1] - normal[1], end[1] + normal[1]),
    ).expanded(padding)


def inspect_svg(
    path: Path,
    *,
    contract: dict[str, Any] | None = None,
    pptx: Path | None = None,
) -> dict[str, Any]:
    """Inspect one SVG; omitted ``contract`` preserves legacy gate behavior."""
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return {
            "file": str(path), "status": "fail", "counts": {"nodes": 0, "texts": 0, "edges": 0, "errors": 1, "warnings": 0},
            "minimum_font_size": None,
            "issues": [{"code": "invalid_svg", "message": str(exc)}],
            "warnings": [],
        }

    view_box = [_number(item) for item in root.get("viewBox", "").replace(",", " ").split()]
    if len(view_box) == 4:
        canvas = Box(view_box[0], view_box[1], view_box[0] + view_box[2], view_box[1] + view_box[3])
    else:
        canvas = Box(0, 0, _number(root.get("width")), _number(root.get("height")))
    is_matplotlib = any("Matplotlib" in (element.text or "") for element in root.iter() if _local_name(element.tag) == "title")
    # Contract mode is metadata-first.  It deliberately does not infer every
    # colored template rectangle as a scientific node.
    has_native_nodes = contract is not None or any(element.get("data-node-id") is not None for element in root.iter())
    has_native_edges = contract is not None or any(
        element.get("data-edge-id") is not None
        or element.get("data-edge-from") is not None
        or element.get("data-edge-source") is not None
        for element in root.iter()
    )

    nodes: list[NodeGeometry] = []
    texts: list[TextGeometry] = []
    edges: list[EdgeGeometry] = []
    marker_paths: list[EdgeGeometry] = []
    label_backgrounds: list[Box] = []
    group_label_backgrounds: list[Box] = []
    group_borders: list[Box] = []
    used_node_ids: set[str] = set()

    def walk(
        element: ET.Element,
        parent_matrix: Matrix,
        inherited_style: dict[str, str],
        in_defs: bool = False,
        inherited_role: str = "text",
        inherited_shared: bool = False,
        inherited_expected_counts: frozenset[int] = frozenset(),
    ) -> None:
        tag = _local_name(element.tag)
        matrix = _multiply(parent_matrix, _transform_matrix(element.get("transform")))
        style = _style(element, inherited_style)
        now_in_defs = in_defs or tag in {"defs", "marker", "clipPath"}
        role = element.get("data-figure-role") or element.get("data-text-role") or inherited_role
        shared_label = element.get("data-shared-label") == "true" or inherited_shared
        raw_expected_count = element.get("data-expected-count")
        expected_counts = inherited_expected_counts
        if raw_expected_count is not None and raw_expected_count.isdigit():
            expected_counts = expected_counts | {int(raw_expected_count)}
        node_id = element.get("data-node-id")
        shape_box = _shape_box(element, matrix)
        node_box = _bounds_box(element, matrix) or shape_box
        if element.get("data-label-background") == "true" and shape_box and not now_in_defs:
            label_backgrounds.append(shape_box)
            if tag == "rect" and role == "group":
                group_label_backgrounds.append(shape_box)
        if tag == "rect" and role == "group" and shape_box and element.get("data-label-background") != "true" and not now_in_defs:
            group_borders.append(shape_box)
        if node_id and node_box and node_id not in used_node_ids:
            nodes.append(NodeGeometry(node_id=node_id, box=node_box))
            used_node_ids.add(node_id)
        elif not has_native_nodes and not is_matplotlib and not now_in_defs and shape_box and tag in {"rect", "ellipse", "circle", "polygon"}:
            fill = style.get("fill", element.get("fill", "")).replace(" ", "").lower()
            opacity = _number(style.get("opacity", style.get("fill-opacity", "1")), 1)
            is_candidate = (
                shape_box.width >= 40
                and shape_box.height >= 28
                and fill not in {"", "none", "#fff", "#ffffff", "white"}
                and opacity > 0.2
                and element.get("data-label-background") != "true"
            )
            if is_candidate:
                inferred_id = f"inferred-node-{len(nodes) + 1}"
                nodes.append(NodeGeometry(node_id=inferred_id, box=shape_box))

        if tag == "text" and not now_in_defs:
            geometry = _text_geometry(element, matrix, style, role, shared_label, expected_counts)
            if geometry:
                texts.append(geometry)

        is_native_edge = (
            element.get("data-edge-id") is not None
            or element.get("data-edge-from") is not None
            or element.get("data-edge-source") is not None
        ) and tag in {"path", "polyline", "line"}
        is_legacy_edge = not has_native_edges and not is_matplotlib and not now_in_defs and tag in {"path", "polyline", "line"} and element.get("marker-end") is not None
        is_auxiliary_marker_path = (
            not now_in_defs
            and tag in {"path", "polyline", "line"}
            and element.get("marker-end") is not None
        )
        if is_native_edge or is_legacy_edge or is_auxiliary_marker_path:
            points = _polyline_points(element, matrix)
            if len(points) >= 2:
                geometry = EdgeGeometry(
                    edge_id=element.get("data-edge-id") or element.get("id") or f"marker-path-{len(marker_paths) + 1}",
                    source=element.get("data-edge-from") or element.get("data-edge-source"),
                    target=element.get("data-edge-to") or element.get("data-edge-target"),
                    points=points,
                    marker_end=element.get("marker-end") is not None,
                )
                if is_native_edge or is_legacy_edge:
                    edges.append(geometry)
                if geometry.marker_end:
                    marker_paths.append(geometry)
        for child in element:
            walk(child, matrix, style, now_in_defs, role, shared_label, expected_counts)

    walk(root, IDENTITY, {})

    for index, first in enumerate(nodes):
        for second in nodes[index + 1 :]:
            if _overlap(first.box, second.box):
                issues.append(
                    {"code": "node_bbox_overlap", "message": f"node bboxes overlap: {first.node_id} / {second.node_id}", "objects": [first.node_id, second.node_id]}
                )

    for index, first in enumerate(texts):
        for second in texts[index + 1 :]:
            if _overlap(first.box, second.box):
                issues.append(
                    {"code": "text_bbox_overlap", "message": f"text bboxes overlap: {first.text!r} / {second.text!r}", "objects": [first.text, second.text]}
                )

    for text in texts:
        if text.role != "edge-label":
            continue
        effective_box = text.box
        center = ((text.box.left + text.box.right) / 2, (text.box.top + text.box.bottom) / 2)
        for background in label_backgrounds:
            if background.expanded(1.0).contains(center):
                effective_box = effective_box.union(background)
        for node in nodes:
            if _overlap(effective_box, node.box):
                issues.append(
                    {
                        "code": "text_overlaps_node",
                        "message": f"edge-label {text.text!r} overlaps node {node.node_id}",
                        "objects": [text.text, node.node_id],
                    }
                )

    for text in texts:
        if (
            text.box.left < canvas.left - 0.5
            or text.box.top < canvas.top - 0.5
            or text.box.right > canvas.right + 0.5
            or text.box.bottom > canvas.bottom + 0.5
        ):
            issues.append(
                {"code": "text_out_of_canvas", "message": f"text outside canvas: {text.text!r}", "objects": [text.text]}
            )
        if text.font_size < MIN_FONT_SIZE - 1e-6:
            issues.append(
                {"code": "font_below_minimum", "message": f"font {text.font_size:.2f}px < {MIN_FONT_SIZE:.0f}px: {text.text!r}", "objects": [text.text], "font_size": round(text.font_size, 3)}
            )

    for text in texts:
        for border in group_borders:
            if _box_intersects_outline(text.box, border):
                warnings.append({
                    "code": "label_on_group_border",
                    "message": f"label intersects a group border: {text.text!r}",
                    "objects": [text.text],
                })
                break

    for edge in edges:
        exempt = {value for value in (edge.source, edge.target) if value}
        if not exempt:
            for node in nodes:
                if node.box.expanded(2.0).contains(edge.points[0]) or node.box.expanded(2.0).contains(edge.points[-1]):
                    exempt.add(node.node_id)
        for node in nodes:
            if node.node_id in exempt:
                continue
            interior = node.box.inset(2.0)
            if any(_segment_intersects_box(start, end, interior) for start, end in zip(edge.points, edge.points[1:])):
                issues.append(
                    {"code": "edge_crosses_non_endpoint_node", "message": f"edge {edge.edge_id} crosses node {node.node_id}", "objects": [edge.edge_id, node.node_id]}
                )

    minimum_physical_pt: float | None = None
    expected_labels: Counter[str] = Counter()
    if contract is not None:
        expected_labels = _contract_labels(contract)
        node_by_id = {node.node_id: node for node in nodes}
        for edge in edges:
            segment_count = len(edge.points) - 1
            endpoint_checks: list[tuple[str, NodeGeometry, range]] = []
            source_node = node_by_id.get(edge.source or "")
            target_node = node_by_id.get(edge.target or "")
            if source_node is not None and target_node is not None and source_node.node_id == target_node.node_id:
                endpoint_checks.append(("endpoint", source_node, range(1, max(1, segment_count - 1))))
            else:
                if source_node is not None:
                    endpoint_checks.append(("source", source_node, range(1, segment_count)))
                if target_node is not None:
                    endpoint_checks.append(("target", target_node, range(0, max(0, segment_count - 1))))
            for endpoint_role, node, prohibited_indices in endpoint_checks:
                interior = node.box.inset(2.0)
                if any(
                    _segment_intersects_box(edge.points[index], edge.points[index + 1], interior)
                    for index in prohibited_indices
                ):
                    issues.append({
                        "code": "edge_reenters_endpoint_node",
                        "message": f"edge {edge.edge_id} re-enters its {endpoint_role} node {node.node_id}",
                        "objects": [edge.edge_id, node.node_id],
                        "endpoint_role": endpoint_role,
                    })

        marker_edges = marker_paths
        label_objects = [
            (f"text:{text.text}", text.box)
            for text in texts
        ] + [
            (f"pill-background-{index}", box)
            for index, box in enumerate(label_backgrounds, start=1)
        ]
        for edge in marker_edges:
            zone = _arrowhead_zone(edge, ARROW_LABEL_PADDING)
            for object_id, box in label_objects:
                if _overlap(zone, box, tolerance=0.0):
                    issues.append({
                        "code": "label_covers_arrowhead",
                        "message": f"{object_id} intersects the arrowhead zone of edge {edge.edge_id}",
                        "objects": [object_id, edge.edge_id],
                    })

        for index, first in enumerate(marker_edges):
            first_endpoint = first.points[-1]
            first_zone = _arrowhead_zone(first)
            for second in marker_edges[index + 1:]:
                if math.dist(first_endpoint, second.points[-1]) <= 1e-6:
                    continue
                if _overlap(first_zone, _arrowhead_zone(second), tolerance=0.0):
                    issues.append({
                        "code": "arrowheads_overlap",
                        "message": f"arrowhead zones overlap: {first.edge_id} / {second.edge_id}",
                        "objects": [first.edge_id, second.edge_id],
                    })

        for edge in edges:
            for chip_index, chip in enumerate(group_label_backgrounds, start=1):
                interior = chip.inset(OVERLAP_TOLERANCE)
                if any(
                    _segment_intersects_box(start, end, interior)
                    for start, end in zip(edge.points, edge.points[1:])
                ):
                    issues.append({
                        "code": "edge_crosses_group_label",
                        "message": f"edge {edge.edge_id} crosses group-label chip {chip_index}",
                        "objects": [edge.edge_id, f"group-label-{chip_index}"],
                    })
        for edge in edges:
            if edge.marker_end and math.dist(edge.points[-2], edge.points[-1]) < 20.0 - 1e-6:
                issues.append({
                    "code": "arrowhead_clipped",
                    "message": f"edge {edge.edge_id} last segment is {math.dist(edge.points[-2], edge.points[-1]):.2f}px < 20px",
                    "objects": [edge.edge_id],
                    "last_segment_length": round(math.dist(edge.points[-2], edge.points[-1]), 3),
                })
        try:
            final_width_mm = float(contract["final_width_mm"])
        except (KeyError, TypeError, ValueError) as exc:
            issues.append({"code": "contract_final_width_missing", "message": f"contract final_width_mm is required: {exc}"})
            final_width_mm = 0.0
        if canvas.width <= 0:
            issues.append({"code": "invalid_viewbox_width", "message": "SVG viewBox width must be positive"})
        elif final_width_mm > 0:
            physical_sizes = [text.font_size * final_width_mm / canvas.width / 0.3528 for text in texts]
            minimum_physical_pt = min(physical_sizes, default=None)
            for text, physical_pt in zip(texts, physical_sizes):
                detail = {
                    "objects": [text.text],
                    "font_size_px": round(text.font_size, 3),
                    "physical_pt": round(physical_pt, 3),
                }
                if physical_pt < MIN_PHYSICAL_PT - 1e-6:
                    issues.append({
                        "code": "physical_font_below_minimum",
                        "message": f"font {physical_pt:.2f}pt < {MIN_PHYSICAL_PT:g}pt at {final_width_mm:g} mm: {text.text!r}",
                        **detail,
                    })
                elif physical_pt < PREFERRED_PHYSICAL_PT - 1e-6:
                    warnings.append({
                        "code": "physical_font_below_preferred",
                        "message": f"font {physical_pt:.2f}pt < preferred {PREFERRED_PHYSICAL_PT:g}pt: {text.text!r}",
                        **detail,
                    })

        actual_labels = Counter(_normalise_text(text.text) for text in texts)
        shared_labels = {
            _normalise_text(text.text)
            for text in texts
            if text.shared_label
        }
        expected_count_by_label: dict[str, set[int]] = {}
        for text in texts:
            if text.expected_counts:
                expected_count_by_label.setdefault(_normalise_text(text.text), set()).update(text.expected_counts)
        for label, counts in expected_count_by_label.items():
            if len(counts) > 1:
                issues.append({
                    "code": "inconsistent_expected_count",
                    "message": f"inconsistent expected counts {sorted(counts)}: {label!r}",
                    "objects": [label],
                    "expected_counts": sorted(counts),
                })
        for label, required_count in expected_labels.items():
            actual_count = actual_labels[label]
            renderer_counts = expected_count_by_label.get(label, set())
            if len(renderer_counts) == 1:
                renderer_count = next(iter(renderer_counts))
                if actual_count != renderer_count:
                    issues.append({
                        "code": "label_count_mismatch",
                        "message": f"label count is {actual_count}, expected {renderer_count}: {label!r}",
                        "objects": [label],
                        "expected_count": renderer_count,
                        "actual_count": actual_count,
                    })
            elif len(renderer_counts) > 1:
                continue
            elif actual_count == 0:
                issues.append({
                    "code": "missing_label",
                    "message": f"contract label is missing from SVG text: {label!r}",
                    "objects": [label],
                    "expected_count": required_count,
                    "actual_count": 0,
                })
            elif label not in shared_labels and actual_count < required_count:
                issues.append({
                    "code": "missing_label",
                    "message": f"contract label count is {actual_count}, expected {required_count}: {label!r}",
                    "objects": [label],
                    "expected_count": required_count,
                    "actual_count": actual_count,
                })
            elif label not in shared_labels and actual_count > required_count:
                issues.append({
                    "code": "duplicate_label",
                    "message": f"contract label count is {actual_count}, expected {required_count}: {label!r}",
                    "objects": [label],
                    "expected_count": required_count,
                    "actual_count": actual_count,
                })
        for text in texts:
            value = _normalise_text(text.text)
            if value not in expected_labels and text.role not in ALLOWED_NON_CONTRACT_ROLES:
                issues.append({
                    "code": "unexpected_text",
                    "message": f"visible text is not a contract label or allowed figure role: {value!r}",
                    "objects": [value],
                    "role": text.role,
                })

        for element in root.iter():
            role = element.get("data-figure-role")
            if role == "node" and not element.get("data-node-id"):
                issues.append({"code": "node_metadata_missing", "message": "node element is missing data-node-id"})
            if role == "edge":
                missing = [
                    attribute
                    for attribute in ("data-edge-id", "data-edge-from", "data-edge-to")
                    if not element.get(attribute)
                ]
                if missing:
                    issues.append({
                        "code": "edge_metadata_missing",
                        "message": "edge element is missing " + ", ".join(missing),
                        "objects": [element.get("id", "<edge>")],
                    })

        pptmaster_report = pptmaster_check_svg(path)
        for error in pptmaster_report.get("errors", []):
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            issues.append({"code": "pptmaster_error", "message": message})
        for warning in pptmaster_report.get("warnings", []):
            message = warning.get("message", str(warning)) if isinstance(warning, dict) else str(warning)
            warnings.append({"code": "pptmaster_warning", "message": message})

        if pptx is not None:
            try:
                inventory = pptx_inventory(pptx)
                pptx_texts: set[str] = set()
                pptx_frames: list[str] = []
                for raw in inventory["texts"]:
                    normalized = _normalise_text(raw)
                    if normalized:
                        pptx_texts.add(normalized)
                        pptx_frames.append(normalized)
                    for line in str(raw).splitlines():
                        line_normalized = _normalise_text(line)
                        if line_normalized:
                            pptx_texts.add(line_normalized)
                # PPT Master exports SVG tspans as consecutive editable text
                # frames.  Recombine adjacent line fragments solely for label
                # presence; the underlying frames remain native and editable.
                max_label_words = max((len(label.split()) for label in expected_labels), default=1)
                for start in range(len(pptx_frames)):
                    for count in range(2, max_label_words + 1):
                        if start + count <= len(pptx_frames):
                            pptx_texts.add(_normalise_text(" ".join(pptx_frames[start:start + count])))
                for label in expected_labels:
                    if label not in pptx_texts:
                        issues.append({
                            "code": "pptx_missing_label",
                            "message": f"contract label is absent from editable PPTX text frames: {label!r}",
                            "objects": [label],
                        })
            except (OSError, ValueError, KeyError) as exc:
                issues.append({"code": "invalid_pptx", "message": str(exc)})

    minimum_font = min((text.font_size for text in texts), default=None)
    unique_codes: dict[str, int] = {}
    for issue in issues:
        unique_codes[issue["code"]] = unique_codes.get(issue["code"], 0) + 1
    return {
        "file": str(path),
        "status": "pass" if not issues else "fail",
        "canvas": {"x": canvas.left, "y": canvas.top, "width": canvas.width, "height": canvas.height},
        "counts": {"nodes": len(nodes), "texts": len(texts), "edges": len(edges), "errors": len(issues), "warnings": len(warnings)},
        "minimum_font_size": round(minimum_font, 3) if minimum_font is not None else None,
        "minimum_physical_font_pt": round(minimum_physical_pt, 3) if minimum_physical_pt is not None else None,
        "expected_labels": dict(expected_labels),
        "issue_counts": unique_codes,
        "issues": issues,
        "warnings": warnings,
    }


def _summary(results: list[dict[str, Any]]) -> dict[str, int]:
    passed = sum(result["status"] == "pass" for result in results)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "errors": sum(result["counts"]["errors"] for result in results),
        "warnings": sum(result["counts"].get("warnings", 0) for result in results),
    }


def _relative_results(
    paths: Iterable[Path],
    root: Path,
    *,
    contract: dict[str, Any] | None = None,
    pptx: Path | None = None,
) -> list[dict[str, Any]]:
    results = []
    for path in sorted(paths):
        result = inspect_svg(path, contract=contract, pptx=pptx)
        try:
            result["file"] = str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            result["file"] = str(path)
        results.append(result)
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="check one SVG file")
    check.add_argument("svg", type=Path)
    check.add_argument("--contract", type=Path)
    check.add_argument("--pptx", type=Path)
    check.add_argument("--output", "-o", type=Path)
    compare = subparsers.add_parser("compare", help="compare baseline, optimized, and studio directories")
    compare.add_argument("--baseline-dir", type=Path, required=True)
    compare.add_argument("--optimized-dir", type=Path, required=True)
    compare.add_argument("--studio-dir", type=Path, required=True)
    compare.add_argument("--output", "-o", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "check":
        root = Path.cwd()
        try:
            contract = _load_contract(args.contract)
        except ValueError as exc:
            print(f"figure_quality_gate_v2 failed: {exc}", file=sys.stderr)
            return 2
        if args.pptx and contract is None:
            print("figure_quality_gate_v2 failed: --pptx requires --contract", file=sys.stderr)
            return 2
        results = _relative_results([args.svg], root, contract=contract, pptx=args.pptx)
        report: dict[str, Any] = {
            "gate_version": GATE_VERSION,
            "mode": "contract" if contract is not None else "legacy",
            "contract": str(args.contract) if args.contract else None,
            "pptx": str(args.pptx) if args.pptx else None,
            "thresholds": {
                "minimum_font_size_px": MIN_FONT_SIZE,
                "minimum_physical_font_pt": MIN_PHYSICAL_PT,
                "preferred_physical_font_pt": PREFERRED_PHYSICAL_PT,
                "bbox_overlap_tolerance_px": OVERLAP_TOLERANCE,
            },
            "summary": _summary(results),
            "files": results,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        root = args.baseline_dir.resolve().parent
        baseline = _relative_results(args.baseline_dir.glob("*.svg"), root)
        optimized = _relative_results(args.optimized_dir.glob("*.svg"), root)
        studio = _relative_results(args.studio_dir.glob("*.svg"), root)
        baseline_summary, optimized_summary, studio_summary = _summary(baseline), _summary(optimized), _summary(studio)
        report = {
            "gate_version": GATE_VERSION,
            "thresholds": {
                "minimum_font_size_px": MIN_FONT_SIZE,
                "minimum_physical_font_pt": MIN_PHYSICAL_PT,
                "preferred_physical_font_pt": PREFERRED_PHYSICAL_PT,
                "bbox_overlap_tolerance_px": OVERLAP_TOLERANCE,
            },
            "baseline": {"summary": baseline_summary, "files": baseline},
            "optimized": {"summary": optimized_summary, "files": optimized},
            "studio": {"summary": studio_summary, "files": studio},
            "comparison": {
                "optimized_vs_baseline_pass_rate_delta": round(
                    optimized_summary["passed"] / max(1, optimized_summary["total"])
                    - baseline_summary["passed"] / max(1, baseline_summary["total"]),
                    3,
                ),
                "studio_vs_baseline_pass_rate_delta": round(
                    studio_summary["passed"] / max(1, studio_summary["total"])
                    - baseline_summary["passed"] / max(1, baseline_summary["total"]),
                    3,
                ),
                "optimized_vs_baseline_error_delta": optimized_summary["errors"] - baseline_summary["errors"],
                "studio_vs_baseline_error_delta": studio_summary["errors"] - baseline_summary["errors"],
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.command == "check":
        failed = report["summary"]["failed"]
    else:
        failed = sum(report[name]["summary"]["failed"] for name in ("baseline", "optimized", "studio"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
