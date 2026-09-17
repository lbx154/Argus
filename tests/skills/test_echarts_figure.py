"""The vertical's data-figure route: an ECharts option, one paper theme, a browser render.

Offline: the renderer is replaced by a stub that writes the expected files, so
the tests cover the theme, the editable source layout, error bars from data,
locating the local ECharts bundle and provenance bookkeeping.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "argus" / "verticals" / "research" / "skills" / "engineer" / "figure_spec_scripts" / "echarts_figure.py"
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("echarts_figure", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


OPTION = {
    "xAxis": {"type": "category", "name": "Budget M", "data": ["128", "256"]},
    "yAxis": {"type": "log", "name": "Error"},
    "series": [
        {"name": "Baseline", "type": "line", "data": [0.01, 0.008], "error": [0.001, 0.0005]},
        {"name": "Ours", "type": "line", "data": [0.001, 0.0007]},
        {"name": "Bars", "type": "bar", "data": [[0, 1.0], [1, 2.0]], "error": [[0.9, 1.2], [1.8, 2.1]]},
    ],
}


def test_theme_emphasises_ours_and_draws_error_bars_from_the_data(mod) -> None:
    themed = mod.themed_option(OPTION, font_pt=8, ours="ours")

    assert themed["animation"] is False
    names = [s.get("name") for s in themed["series"]]
    assert names == ["Baseline", "Baseline (uncertainty)", "Ours", "Bars", "Bars (uncertainty)"]
    baseline, baseline_err, ours, _bars, bars_err = themed["series"]
    assert ours["lineStyle"]["color"] == mod.OURS_COLOR and ours["lineStyle"]["width"] > baseline["lineStyle"]["width"]
    assert baseline["itemStyle"]["color"] == mod.PALETTE[0]
    assert "error" not in baseline and baseline_err["type"] == "custom"
    assert baseline_err["renderItem"] == mod.ERROR_BAR_MARKER
    assert baseline_err["data"] == [pytest.approx([0, 0.009, 0.011]), pytest.approx([1, 0.0075, 0.0085])]
    assert bars_err["data"] == [pytest.approx([0, 0.9, 1.2]), pytest.approx([1, 1.8, 2.1])]
    assert themed["legend"]["data"] == ["Baseline", "Ours", "Bars"]
    assert themed["yAxis"]["nameGap"] > 0 and themed["xAxis"]["axisLabel"]["fontSize"] == pytest.approx(8 * 96 / 72, rel=0.01)
    # The caller's own settings survive the theme.
    assert themed["yAxis"]["type"] == "log" and themed["xAxis"]["name"] == "Budget M"


def test_source_layout_and_render_through_a_stub_renderer(mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = tmp_path / "echarts.min.js"
    bundle.write_text("/* echarts stub */", encoding="utf-8")
    monkeypatch.setenv("ARGUS_ECHARTS_JS", str(bundle))
    calls: list[list[str]] = []

    class _Renderer:
        @staticmethod
        def main(argv: list[str]) -> int:
            calls.append(argv)
            output = Path(argv[argv.index("--output") + 1])
            output.write_bytes(b"%PDF-1.4 stub" if output.suffix == ".pdf" else b"<svg/>")
            output.with_name(output.name + ".render.json").write_text("{}", encoding="utf-8")
            return 0

    monkeypatch.setattr(mod, "_load_browser_render", lambda: _Renderer)

    written = mod.render_chart(OPTION, figure_id="fig2_error", project_root=tmp_path, width_mm=84, height_mm=52, ours="Ours")

    source = tmp_path / "paper" / "figures" / "src" / "fig2_error"
    assert Path(written["option"]) == source / "option.json"
    assert Path(written["html"]) == source / "index.html"
    html = (source / "index.html").read_text(encoding="utf-8")
    assert "data-figure-ready" in html and "renderer: 'svg'" in html
    assert '<script src="../vendor/echarts.min.js"></script>' in html
    assert (tmp_path / "paper" / "figures" / "src" / "vendor" / "echarts.min.js").is_file()
    assert json.loads((source / "option.json").read_text(encoding="utf-8"))["series"][2]["name"] == "Ours"
    assert [Path(c[c.index("--output") + 1]).name for c in calls] == ["fig2_error.pdf", "fig2_error.svg"]
    width = int(calls[0][calls[0].index("--width") + 1])
    assert width == round(84 * 96 / 25.4)
    manifest = json.loads((tmp_path / "paper" / "figures" / "FIGURE_PROVENANCE.json").read_text(encoding="utf-8"))
    entry = manifest["figures"][0]
    assert entry["figure_id"] == "fig2_error" and entry["renderer"] == "echarts-browser"
    assert entry["output_path"] == "paper/figures/fig2_error.pdf"


def test_cli_reads_settings_from_the_spec(mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_render(option, **kwargs):
        seen.update(kwargs)
        seen["option"] = option
        return {"pdf": "x.pdf"}

    monkeypatch.setattr(mod, "render_chart", fake_render)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"option": OPTION, "ours": "Ours", "width_mm": 140, "height_mm": 60, "font_pt": 9}), encoding="utf-8")

    assert mod.main(["--spec", str(spec), "--id", "fig3", "--project-root", str(tmp_path), "--no-svg"]) == 0

    assert seen["figure_id"] == "fig3" and seen["ours"] == "Ours"
    assert seen["width_mm"] == 140 and seen["height_mm"] == 60 and seen["font_pt"] == 9
    assert seen["formats"] == ("pdf",)
