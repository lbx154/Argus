from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt

from argus_skill.core.event_catalog import EventType


_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIGURE_BUILDER = (
    _REPO_ROOT / "technical_report" / "figures" / "build_report_figures.py"
)
_MAIN_TEX = _REPO_ROOT / "technical_report" / "main.tex"


def _load_figure_builder():
    spec = importlib.util.spec_from_file_location(
        "technical_report_figures",
        _FIGURE_BUILDER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _figure_text(monkeypatch, builder_name: str) -> str:
    builder = _load_figure_builder()
    rendered: list[str] = []

    def capture_text(fig, _stem):
        rendered.extend(
            text.get_text()
            for axis in fig.axes
            for text in axis.texts
        )
        plt.close(fig)
        return {}

    monkeypatch.setattr(builder, "_save", capture_text)
    getattr(builder, builder_name)()
    return "\n".join(rendered)


def test_system_planes_uses_live_event_type_count(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_system_planes")

    assert f"{len(EventType)} typed events" in text


def test_system_planes_describes_bounded_engineer_session(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_system_planes")

    assert "bounded session" in text
    assert "fresh session" not in text


def test_mission_lifecycle_describes_bounded_session_reuse(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_mission_lifecycle")

    assert "bounded session reuse" in text
    assert "fresh session / round" not in text


def test_public_results_distinguishes_corroborated_digests(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_public_results")

    assert text.count("artifact digest") == 2
    assert text.count("website snapshot") == 4


def test_callout_titles_use_contrasting_text() -> None:
    source = _MAIN_TEX.read_text(encoding="utf-8")
    callout_style = source.split(r"\newtcolorbox{callout}", 1)[1].split(
        r"\newtcolorbox{designbox}",
        1,
    )[0]

    assert r"fonttitle=\bfseries\small\color{white}" in callout_style
    assert "coltitle=white" in callout_style


def test_figure_builder_imports_from_outside_repository(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy; "
                f"runpy.run_path({_FIGURE_BUILDER.as_posix()!r}, "
                "run_name='technical_report_figures_cli_test')"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_master_spine_contains_causal_chain_and_four_roles(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_master_spine")

    required = {
        "Unknown objective",
        "Dense Intelligence Runtime",
        "Evidence Gate",
        "Runtime Evolution",
        "Expanded OOD Frontier",
        "Manager",
        "Planner",
        "Engineer",
        "Reviewer",
        "Memory",
        "Skills",
        "Tools",
        "Verifiers",
        "Routing",
        "Evaluations",
    }
    assert required <= set(text.splitlines())
    assert "Every run expands the frontier." in text


def test_master_spine_states_fixed_model_parameters(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_master_spine")

    assert "H(t+1) = U(H(t), trajectory, evidence)" in text
    assert "model parameters remain fixed" in text
    assert "capability is not guaranteed to grow every run" in text


def test_master_spine_stage_connectors_have_visible_span(monkeypatch) -> None:
    """The five stage boxes must be joined by four visibly non-zero-length
    left-to-right connector arrows. A prior regression computed both arrow
    endpoints from a fixed +1/-1 inset around a gap that happened to be
    exactly 2 units wide, so the two insets cancelled out and every
    connector collapsed to a zero-length (invisible) arrow.
    """
    builder = _load_figure_builder()
    calls: list[tuple] = []
    original_arrow = builder._arrow

    def capture_arrow(ax, x1, y1, x2, y2, **kwargs):
        calls.append((x1, y1, x2, y2, kwargs))
        return original_arrow(ax, x1, y1, x2, y2, **kwargs)

    def noop_save(fig, _stem):
        plt.close(fig)
        return {}

    monkeypatch.setattr(builder, "_arrow", capture_arrow)
    monkeypatch.setattr(builder, "_save", noop_save)
    builder.build_master_spine()

    # The causal-chain stage connectors are the horizontal (y1 == y2) BLUE
    # arrows without a curved connection style; the gold feedback arrow at
    # the bottom of the figure uses an arc connection and GOLD color, so it
    # is excluded by these filters.
    connectors = [
        (x1, y1, x2, y2)
        for x1, y1, x2, y2, kwargs in calls
        if y1 == y2
        and kwargs.get("color") == builder.BLUE
        and kwargs.get("connection", "arc3,rad=0.0") == "arc3,rad=0.0"
    ]

    assert len(connectors) == 4, (
        f"expected exactly 4 stage connectors, found {len(connectors)}: {connectors}"
    )
    for x1, _y1, x2, _y2 in connectors:
        assert x2 - x1 > 0, f"connector ({x1}, {x2}) has zero or negative horizontal span"


def test_dense_intelligence_is_explanatory_not_a_score(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_dense_intelligence")

    assert "decision" in text
    assert "execution" in text
    assert "verification" in text
    assert "conceptual model \u00b7 not a reported benchmark" in text
    assert "Argus > human" not in text


def test_website_palette_is_used_by_report_figures() -> None:
    builder = _load_figure_builder()

    assert builder.BONE == "#FBFAF6"
    assert builder.BLUE == "#315BCE"
    assert builder.BLUE_DEEP == "#214884"
    assert builder.GOLD == "#C38A20"
