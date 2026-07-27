from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIGURES_DIR = _REPO_ROOT / "technical_report" / "figures"
_FIGURE_BUILDER = _FIGURES_DIR / "build_report_figures.py"
_REPORT_FIGURES_JSON = _FIGURES_DIR / "REPORT_FIGURES.json"
_MAIN_TEX = _REPO_ROOT / "technical_report" / "main.tex"

# The final hybrid contract: build_report_figures.py owns ONLY the two
# deterministic data figures. The six structural figures are image-2 outputs
# handled by build_ai_figure_provenance.py / validate_ai_figures.py.
_DATA_FIGURES = ("public_results", "paper_portfolio")
_LEGACY_STRUCTURAL_STEMS = (
    "master_spine",
    "dense_intelligence",
    "system_planes",
    "argus_architecture",
    "mission_lifecycle",
    "long_horizon_reliability",
)
_CURRENT_REPORT_PDF_STEMS = (
    "argus_teaser",
    "swebench_evolution",
    "reviewer_mechanism",
    "horizon_mountain",
    "erdos_vertical_trace",
    "paper_case_study",
    "paper_case_trajectory",
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


def _figure_text(monkeypatch, builder_name: str) -> str:
    builder = _load_figure_builder()
    rendered: list[str] = []

    def capture_text(fig, _stem):
        rendered.extend(text.get_text() for axis in fig.axes for text in axis.texts)
        plt.close(fig)
        return {}

    monkeypatch.setattr(builder, "_save", capture_text)
    getattr(builder, builder_name)()
    return "\n".join(rendered)


# --------------------------------------------------------------------------- #
# Scope: only the two deterministic data figures remain in the builder.
# --------------------------------------------------------------------------- #
def test_builder_exposes_only_the_two_data_figure_functions() -> None:
    builder = _load_figure_builder()

    assert hasattr(builder, "build_public_results")
    assert hasattr(builder, "build_paper_portfolio")

    # Structural drawing functions and their helpers were removed.
    for removed in (
        "build_master_spine",
        "build_dense_intelligence",
        "build_system_planes",
        "build_mission_lifecycle",
        "_arrow",
        "_box",
        "_new_axes",
    ):
        assert not hasattr(builder, removed), f"unexpected structural symbol: {removed}"


def test_report_figures_manifest_has_exactly_the_two_data_figures() -> None:
    manifest = json.loads(_REPORT_FIGURES_JSON.read_text(encoding="utf-8"))

    assert set(manifest["figures"]) == set(_DATA_FIGURES)
    for stem in _DATA_FIGURES:
        entry = manifest["figures"][stem]
        assert entry["pdf"] == f"{stem}.pdf"
        assert entry["png"] == f"{stem}.png"
        assert len(entry["pdf_sha256"]) == 64
        assert len(entry["png_sha256"]) == 64


def test_report_figures_manifest_excludes_structural_stems() -> None:
    manifest = json.loads(_REPORT_FIGURES_JSON.read_text(encoding="utf-8"))
    for stem in _LEGACY_STRUCTURAL_STEMS:
        assert stem not in manifest["figures"]


def test_no_structural_pdf_files_remain() -> None:
    for stem in _LEGACY_STRUCTURAL_STEMS:
        assert not (_FIGURES_DIR / f"{stem}.pdf").exists(), (
            f"structural PDF {stem}.pdf should have been removed"
        )


def test_latex_references_the_current_academic_report_figures() -> None:
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [_MAIN_TEX, *(_REPO_ROOT / "technical_report" / "sections").glob("*.tex")]
    )
    for stem in _CURRENT_REPORT_PDF_STEMS:
        assert f"figures/{stem}.pdf" in sources, f"missing .pdf ref for {stem}"
        assert (_FIGURES_DIR / f"{stem}.pdf").is_file()
    for stem in _LEGACY_STRUCTURAL_STEMS:
        assert f"figures/{stem}.png" not in sources


# --------------------------------------------------------------------------- #
# Deterministic-data content contracts.
# --------------------------------------------------------------------------- #
def test_public_results_contains_the_current_task_native_values(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_public_results")

    for expected in (
        "NVIDIA SOL-ExecBench",
        "nanochat · B200",
        "nanochat · H100",
        "nanoGPT speedrun",
        "AARRI-Bench",
        "Math-Reasoning Data",
        "0.9636",
        "0.9855",
        "79.77s",
        "76.8%",
    ):
        assert expected in text


def test_public_results_reproducible_digests() -> None:
    builder = _load_figure_builder()
    first = builder.build_public_results()
    second = builder.build_public_results()

    assert first["png_sha256"] == second["png_sha256"]
    assert first["pdf_sha256"] == second["pdf_sha256"]


def test_paper_portfolio_reproducible_digests() -> None:
    builder = _load_figure_builder()
    first = builder.build_paper_portfolio()
    second = builder.build_paper_portfolio()

    assert first["png_sha256"] == second["png_sha256"]
    assert first["pdf_sha256"] == second["pdf_sha256"]


def test_paperbox_titles_use_the_current_contrasting_text() -> None:
    source = _MAIN_TEX.read_text(encoding="utf-8")
    paperbox_style = source.split(r"\newtcolorbox{paperbox}", 1)[1].split(r"\newcommand{\code}", 1)[
        0
    ]

    assert r"fonttitle=\sffamily\bfseries\small\color{argusdeep}" in paperbox_style
    assert "colback=softgray" in paperbox_style


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


def test_website_palette_is_used_by_report_figures() -> None:
    builder = _load_figure_builder()

    assert builder.BONE == "#FBFAF6"
    assert builder.BLUE == "#315BCE"
    assert builder.BLUE_DEEP == "#214884"
    assert builder.GOLD == "#C38A20"
