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


def _load_figure_builder():
    spec = importlib.util.spec_from_file_location(
        "technical_report_figures",
        _FIGURE_BUILDER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _system_planes_text(monkeypatch) -> str:
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
    builder.build_system_planes()
    return "\n".join(rendered)


def test_system_planes_uses_live_event_type_count(monkeypatch) -> None:
    text = _system_planes_text(monkeypatch)

    assert f"{len(EventType)} typed events" in text


def test_system_planes_describes_bounded_engineer_session(monkeypatch) -> None:
    text = _system_planes_text(monkeypatch)

    assert "bounded session" in text
    assert "fresh session" not in text


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
