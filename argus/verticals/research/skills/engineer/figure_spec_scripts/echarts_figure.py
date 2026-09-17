"""Render a publication data chart from an ECharts option through the browser renderer.

This is the vertical's data-figure route. The editable source is a JSON
option under ``paper/figures/src/<id>/`` (with the page that hosts it); the
export is the vector PDF (and SVG) under ``paper/figures/<id>.*`` that the
manuscript includes. The paper theme -- colour-blind-safe palette, thin axes,
sans typography at a stated point size, the method under test emphasised --
is applied here so every chart in a paper shares it without a hand-maintained
style file. Uncertainty is drawn from the data (``error`` on a series), never
from a sentinel.

Usage (from the project root)::

    python echarts_figure.py --spec paper/analysis/fig2.option.json --id fig2_results \
        --width-mm 140 --height-mm 60 --font-pt 8 --ours "Our method"

``--spec`` holds either a bare ECharts option or ``{"option": {...}, "ours":
"...", "width_mm": ..., "height_mm": ..., "font_pt": ...}``. The ECharts
library is a local file: ``--echarts-js``, ``ARGUS_ECHARTS_JS``,
``paper/figures/src/vendor/echarts.min.js``, a ``node_modules`` copy above the
project, or a one-time pinned download into the vendor directory.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any

ECHARTS_VERSION = "5.6.0"
ECHARTS_URL = f"https://cdn.jsdelivr.net/npm/echarts@{ECHARTS_VERSION}/dist/echarts.min.js"
VENDOR_RELATIVE = Path("paper/figures/src/vendor/echarts.min.js")
SOURCE_ROOT = Path("paper/figures/src")
OUTPUT_ROOT = Path("paper/figures")
PX_PER_MM = 96.0 / 25.4
PX_PER_PT = 96.0 / 72.0

# Okabe-Ito: distinguishable under the common colour-vision deficiencies and in
# greyscale print; the method under test takes the vermilion so it reads first.
PALETTE = ["#0072B2", "#009E73", "#E69F00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
OURS_COLOR = "#D55E00"
INK = "#333333"
GRID = "#DDDDDD"
FONT_FAMILY = "Liberation Sans, Arial, Helvetica, sans-serif"
ERROR_BAR_MARKER = "__ERRBAR__"

_ERROR_BAR_JS = """
function (params, api) {
  var x = api.value(0), lo = api.value(1), hi = api.value(2);
  var p1 = api.coord([x, lo]), p2 = api.coord([x, hi]);
  var half = Math.max(2.5, api.size([1, 0])[0] * 0.10);
  var style = api.style({stroke: api.visual('color'), fill: 'none', lineWidth: 1});
  var line = function (x1, y1, x2, y2) {
    return {type: 'line', shape: {x1: x1, y1: y1, x2: x2, y2: y2}, style: style};
  };
  return {type: 'group', children: [
    line(p1[0], p1[1], p2[0], p2[1]),
    line(p1[0] - half, p1[1], p1[0] + half, p1[1]),
    line(p2[0] - half, p2[1], p2[0] + half, p2[1])
  ]};
}
"""

_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<script src="{echarts_src}"></script>
<style>html, body {{ margin: 0; background: #ffffff; }}</style>
</head><body>
<div data-figure-root style="width:{width}px;height:{height}px"></div>
<script>
var option = {option_json};
(function () {{
  var errorBar = {error_bar_js};
  (option.series || []).forEach(function (s) {{
    if (s.renderItem === "{marker}") {{ s.renderItem = errorBar; }}
  }});
  var root = document.querySelector('[data-figure-root]');
  var chart = echarts.init(root, null, {{renderer: 'svg', width: {width}, height: {height}}});
  chart.setOption(option);
  requestAnimationFrame(function () {{ root.setAttribute('data-figure-ready', 'true'); }});
}})();
</script>
</body></html>
"""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """``override`` wins; nested dicts merge so a partial ``xAxis`` keeps the theme."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _axis_theme(font_px: float) -> dict[str, Any]:
    return {
        "axisLine": {"lineStyle": {"color": INK, "width": 0.8}},
        "axisTick": {"lineStyle": {"color": INK, "width": 0.8}},
        "axisLabel": {"color": INK, "fontSize": font_px},
        "nameTextStyle": {"color": INK, "fontSize": font_px},
        "nameLocation": "middle",
        "splitLine": {"lineStyle": {"color": GRID, "width": 0.6}},
    }


def _themed_axes(axes: Any, font_px: float) -> Any:
    theme = _axis_theme(font_px)
    if isinstance(axes, list):
        return [_deep_merge(theme, axis) for axis in axes]
    if isinstance(axes, dict):
        return _deep_merge(theme, axes)
    return axes


def _is_ours(series: dict[str, Any], ours: str | None) -> bool:
    if not ours:
        return False
    name = str(series.get("name") or "")
    return name.strip().lower() == ours.strip().lower() or bool(series.get("ours"))


def _error_series(series: dict[str, Any], color: str) -> dict[str, Any] | None:
    """A custom series drawing whiskers for ``series['error']``.

    ``error`` is one entry per point: a half-width (symmetric) or ``[lo, hi]``
    absolute bounds. Points are ``y`` values (category axis, x = index) or
    ``[x, y]`` pairs.
    """
    errors = series.get("error")
    data = series.get("data")
    if not isinstance(errors, list) or not isinstance(data, list) or len(errors) != len(data):
        return None
    rows: list[list[float]] = []
    for index, (point, err) in enumerate(zip(data, errors)):
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = point[0], point[1]
        else:
            x, y = index, point
        if y is None or err is None:
            continue
        if isinstance(err, (list, tuple)) and len(err) == 2:
            lo, hi = float(err[0]), float(err[1])
        else:
            lo, hi = float(y) - float(err), float(y) + float(err)
        rows.append([x, lo, hi])
    if not rows:
        return None
    return {
        "type": "custom",
        "name": f"{series.get('name', 'series')} (uncertainty)",
        "renderItem": ERROR_BAR_MARKER,
        "data": rows,
        "itemStyle": {"color": color},
        "z": 3,
        "silent": True,
        "legendHoverLink": False,
        "encode": {"x": 0, "y": [1, 2]},
    }


def themed_option(option: dict[str, Any], *, font_pt: float = 8.0, ours: str | None = None) -> dict[str, Any]:
    """The option with the paper theme applied and error bars materialised."""
    font_px = round(font_pt * PX_PER_PT, 2)
    base: dict[str, Any] = {
        "animation": False,
        "backgroundColor": "#ffffff",
        "textStyle": {"fontFamily": FONT_FAMILY, "fontSize": font_px, "color": INK},
        "grid": {"containLabel": True, "left": 6, "right": 8, "top": 30, "bottom": 6},
        "legend": {
            "top": 0,
            "icon": "roundRect",
            "itemWidth": 14,
            "itemHeight": 8,
            "itemGap": 12,
            "textStyle": {"fontSize": font_px, "color": INK},
        },
        "tooltip": {"show": False},
    }
    merged = _deep_merge(base, option)
    for axis_key in ("xAxis", "yAxis"):
        if axis_key in merged:
            merged[axis_key] = _themed_axes(merged[axis_key], font_px)
    # containLabel keeps tick labels inside the canvas but not axis names;
    # give a named axis the room its rotated or centred name needs.
    def _has_name(axes: Any) -> bool:
        items = axes if isinstance(axes, list) else [axes]
        return any(isinstance(a, dict) and str(a.get("name") or "").strip() for a in items)
    def _set_gap(axes: Any, gap: float) -> Any:
        items = axes if isinstance(axes, list) else [axes]
        for a in items:
            if isinstance(a, dict):
                a.setdefault("nameGap", gap)
        return axes
    if "yAxis" in merged and _has_name(merged["yAxis"]):
        merged["yAxis"] = _set_gap(merged["yAxis"], round(font_px * 3.2))
        merged["grid"]["left"] = max(merged["grid"].get("left", 6), round(font_px * 1.6))
    if "xAxis" in merged and _has_name(merged["xAxis"]):
        merged["xAxis"] = _set_gap(merged["xAxis"], round(font_px * 2.0))
        merged["grid"]["bottom"] = max(merged["grid"].get("bottom", 6), round(font_px * 1.4))
    series_in = merged.get("series") or []
    series_out: list[dict[str, Any]] = []
    palette = iter(PALETTE)
    legend_names: list[str] = []
    for series in series_in:
        if not isinstance(series, dict):
            continue
        series = copy.deepcopy(series)
        ours_here = _is_ours(series, ours)
        color = OURS_COLOR if ours_here else next(palette, INK)
        series.pop("ours", None)
        kind = series.get("type", "line")
        series.setdefault("itemStyle", {}).setdefault("color", color)
        if kind == "line":
            series.setdefault("lineStyle", {})
            series["lineStyle"].setdefault("color", color)
            series["lineStyle"].setdefault("width", 2.6 if ours_here else 1.5)
            series.setdefault("symbol", "circle")
            series.setdefault("symbolSize", 7 if ours_here else 5)
            series.setdefault("showSymbol", True)
        if kind == "bar":
            series.setdefault("barMaxWidth", 28)
            if ours_here:
                series["itemStyle"].setdefault("borderColor", INK)
                series["itemStyle"].setdefault("borderWidth", 0.8)
        if ours_here:
            series.setdefault("z", 10)
        error_series = _error_series(series, color)
        series.pop("error", None)
        if series.get("name"):
            legend_names.append(str(series["name"]))
        series_out.append(series)
        if error_series is not None:
            series_out.append(error_series)
    merged["series"] = series_out
    if legend_names and "data" not in merged.get("legend", {}):
        merged.setdefault("legend", {})["data"] = legend_names
    return merged


def _load_browser_render():
    path = Path(__file__).resolve().parents[1] / "research_visual_scripts" / "browser_render.py"
    spec = importlib.util.spec_from_file_location("argus_browser_render", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"browser renderer not found beside this skill: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locate_echarts(project_root: Path, explicit: str | None = None) -> Path:
    """The local ECharts bundle, fetched once into the vendor directory when absent."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("ARGUS_ECHARTS_JS"):
        candidates.append(Path(os.environ["ARGUS_ECHARTS_JS"]))
    vendor = project_root / VENDOR_RELATIVE
    candidates.append(vendor)
    for parent in [project_root, *project_root.parents]:
        candidates.append(parent / "node_modules" / "echarts" / "dist" / "echarts.min.js")
    for candidate in candidates:
        if candidate.is_file():
            if candidate != vendor:
                vendor.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(candidate, vendor)
            return vendor
    vendor.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(ECHARTS_URL, timeout=60) as response:  # noqa: S310 - pinned https URL
        payload = response.read()
    if len(payload) < 200_000 or b"echarts" not in payload[:4096].lower() and b"echarts" not in payload:
        raise RuntimeError(f"downloaded ECharts bundle looks wrong ({len(payload)} bytes)")
    vendor.write_bytes(payload)
    return vendor


def write_source(
    option: dict[str, Any],
    *,
    project_root: Path,
    figure_id: str,
    width_px: int,
    height_px: int,
    echarts_js: Path,
) -> tuple[Path, Path]:
    """Write ``option.json`` and ``index.html`` under ``paper/figures/src/<id>/``."""
    source_dir = project_root / SOURCE_ROOT / figure_id
    source_dir.mkdir(parents=True, exist_ok=True)
    option_path = source_dir / "option.json"
    option_path.write_text(json.dumps(option, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_path = source_dir / "index.html"
    try:
        echarts_src = os.path.relpath(echarts_js, source_dir)
    except ValueError:
        echarts_src = echarts_js.as_uri()
    html_path.write_text(
        _HTML.format(
            echarts_src=echarts_src,
            width=width_px,
            height=height_px,
            option_json=json.dumps(option, ensure_ascii=False),
            error_bar_js=_ERROR_BAR_JS.strip(),
            marker=ERROR_BAR_MARKER,
        ),
        encoding="utf-8",
    )
    return option_path, html_path


def render_chart(
    option: dict[str, Any],
    *,
    figure_id: str,
    project_root: Path | str = ".",
    width_mm: float = 84.0,
    height_mm: float = 52.0,
    font_pt: float = 8.0,
    ours: str | None = None,
    formats: tuple[str, ...] = ("pdf", "svg"),
    echarts_js: str | None = None,
    role: str = "result",
    timeout_ms: int = 120_000,
) -> dict[str, str]:
    """Theme, write the editable source, render every format, record provenance.

    Returns the written paths keyed by kind (``option``, ``html``, ``pdf``, ``svg``).
    """
    root = Path(project_root).resolve()
    width_px = max(1, round(width_mm * PX_PER_MM))
    height_px = max(1, round(height_mm * PX_PER_MM))
    themed = themed_option(option, font_pt=font_pt, ours=ours)
    bundle = locate_echarts(root, echarts_js)
    option_path, html_path = write_source(
        themed, project_root=root, figure_id=figure_id, width_px=width_px, height_px=height_px, echarts_js=bundle
    )
    renderer = _load_browser_render()
    written = {"option": str(option_path), "html": str(html_path)}
    for fmt in formats:
        output = root / OUTPUT_ROOT / f"{figure_id}.{fmt}"
        renderer.main(
            [
                "--input", str(html_path),
                "--selector", "[data-figure-root]",
                "--output", str(output),
                "--width", str(width_px),
                "--height", str(height_px),
                "--timeout-ms", str(timeout_ms),
            ]
        )
        written[fmt] = str(output)
    try:
        from argus.verticals.research.figure_provenance import register_figure

        primary = Path(written.get("pdf") or written.get("svg") or written["html"])
        metadata = primary.with_name(primary.name + ".render.json")
        register_figure(
            project_root=root,
            figure_id=figure_id,
            role=role,
            renderer="echarts-browser",
            source_path=html_path,
            output_path=primary,
            inputs=[option_path],
            render_metadata_path=metadata if metadata.is_file() else None,
            command=f"echarts_figure.py --id {figure_id}",
        )
    except Exception as exc:  # noqa: BLE001 - provenance is bookkeeping, the figure exists
        print(f"figure provenance not recorded: {exc}", file=sys.stderr)
    return written


def _load_spec(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "option" in payload and isinstance(payload["option"], dict):
        settings = {k: v for k, v in payload.items() if k != "option"}
        return payload["option"], settings
    if not isinstance(payload, dict):
        raise ValueError("spec must be a JSON object: an ECharts option or {option, ours, width_mm, ...}")
    return payload, {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", type=Path, required=True, help="ECharts option JSON (or {option, ours, width_mm, height_mm, font_pt})")
    parser.add_argument("--id", required=True, help="figure id; export is paper/figures/<id>.pdf")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--width-mm", type=float, default=None)
    parser.add_argument("--height-mm", type=float, default=None)
    parser.add_argument("--font-pt", type=float, default=None)
    parser.add_argument("--ours", default=None, help="series name to emphasise as the method under test")
    parser.add_argument("--role", default="result", help="provenance role: mechanism, result, ablation, ...")
    parser.add_argument("--no-svg", action="store_true", help="export the PDF only")
    parser.add_argument("--echarts-js", default=None, help="local echarts.min.js to use")
    parser.add_argument("--timeout-ms", type=int, default=120_000)
    args = parser.parse_args(argv)
    option, settings = _load_spec(args.spec)
    written = render_chart(
        option,
        figure_id=args.id,
        project_root=args.project_root,
        width_mm=args.width_mm if args.width_mm is not None else float(settings.get("width_mm", 84.0)),
        height_mm=args.height_mm if args.height_mm is not None else float(settings.get("height_mm", 52.0)),
        font_pt=args.font_pt if args.font_pt is not None else float(settings.get("font_pt", 8.0)),
        ours=args.ours if args.ours is not None else settings.get("ours"),
        formats=("pdf",) if args.no_svg else ("pdf", "svg"),
        echarts_js=args.echarts_js,
        role=args.role,
        timeout_ms=args.timeout_ms,
    )
    print(json.dumps(written, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
