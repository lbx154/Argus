"""Research-vertical degradation has to look different from success.

Each path here fails soft and should keep failing soft: a validator fault must
not break prompt building, a broken probe must not wedge the campaign loop, an
absent SVG backend must not stop a render. What none of them may do is come
back wearing success's clothes -- an empty notes block reading as "this
manuscript is structurally fine", a ``False`` reading as "confirmed, this venue
needs nothing", a report with no staleness finding reading as "the questions
are fresh", an install hint for a package that is already installed.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------
# _paper_notes_block: a failure to collect facts is not "no facts"
# ----------------------------------------------------------------------

def test_paper_notes_block_says_so_when_fact_collection_fails(
    tmp_path, monkeypatch, caplog
) -> None:
    """An empty block means "nothing structural to report". A validator that
    exploded must not borrow that meaning -- the campaign would read silence as
    a clean bill of health for a manuscript nobody checked."""
    from argus_skill.verticals.research import paper_structural_minimums, stages

    def boom(_root):
        raise RuntimeError("structural validator exploded")

    monkeypatch.setattr(
        paper_structural_minimums,
        "validate_paper_structural_minimums",
        boom,
    )

    with caplog.at_level(logging.ERROR):
        block = stages._paper_notes_block(tmp_path)

    assert block != "", "a swallowed failure is byte-identical to 'no notes'"
    assert "FAILED" in block
    assert "RuntimeError" in block
    assert "structural validator exploded" in block
    assert "UNVERIFIED" in block
    assert "Traceback (most recent call last)" in caplog.text


def test_paper_notes_block_never_raises_into_prompt_building(
    tmp_path, monkeypatch
) -> None:
    from argus_skill.verticals.research import paper_structural_minimums, stages

    def boom(_root):
        raise MemoryError("not even a normal error")

    monkeypatch.setattr(
        paper_structural_minimums,
        "validate_paper_structural_minimums",
        boom,
    )

    assert isinstance(stages._paper_notes_block(tmp_path), str)


# ----------------------------------------------------------------------
# needs_venue_research: an unanswerable probe asks for the work
# ----------------------------------------------------------------------

def _pipeline_state(root: Path, *, target_venue: str) -> None:
    research = root / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "PIPELINE_STATE.json").write_text(
        json.dumps(
            {
                "vertical": "research",
                "current_stage": "research",
                "target_venue": target_venue,
            }
        ),
        encoding="utf-8",
    )


def test_venue_probe_failure_asks_for_research_instead_of_skipping(
    tmp_path, monkeypatch, caplog
) -> None:
    """``False`` means "confirmed: nothing to research", and nothing asks
    again -- so a probe that merely broke must not answer it. The paper would
    otherwise target a venue whose deadline, scope and format were never
    checked."""
    from argus_skill.verticals.research import venue_research

    _pipeline_state(tmp_path, target_venue="ExampleConf")

    def boom(_root):
        raise OSError("venue profile store unreadable")

    monkeypatch.setattr(venue_research, "load_local_venue_profile", boom)

    with caplog.at_level(logging.WARNING):
        assert venue_research.needs_venue_research(tmp_path) is True

    assert "ExampleConf" in caplog.text
    assert "OSError" in caplog.text
    assert "venue profile store unreadable" in caplog.text


def test_completed_venue_attempt_short_circuits_even_when_the_probe_breaks(
    tmp_path, monkeypatch
) -> None:
    """Failing toward the work must not become a retry loop: an attempt that
    genuinely reached the provider is on record and still ends the question."""
    from argus_skill.verticals.research import venue_research

    _pipeline_state(tmp_path, target_venue="ExampleConf")
    venue_research._record_completed_attempt(tmp_path, "ExampleConf")

    def boom(_root):
        raise OSError("venue profile store unreadable")

    monkeypatch.setattr(venue_research, "load_local_venue_profile", boom)

    assert venue_research.needs_venue_research(tmp_path) is False


def test_completed_venue_attempt_short_circuits_on_the_normal_path(
    tmp_path,
) -> None:
    from argus_skill.verticals.research import venue_research

    _pipeline_state(tmp_path, target_venue="ExampleConf")
    assert venue_research.needs_venue_research(tmp_path) is True

    venue_research._record_completed_attempt(tmp_path, "ExampleConf")

    assert venue_research.needs_venue_research(tmp_path) is False


def test_absent_venue_still_never_triggers_discovery(tmp_path) -> None:
    """Failing toward the work must not turn "no venue chosen" into a search."""
    from argus_skill.verticals.research import venue_research

    _pipeline_state(tmp_path, target_venue="")

    assert venue_research.needs_venue_research(tmp_path) is False


# ----------------------------------------------------------------------
# reviewer simulation: staleness that cannot be checked is not freshness
# ----------------------------------------------------------------------

def _reviewer_project(root: Path) -> None:
    paper = root / "paper"
    paper.mkdir(parents=True, exist_ok=True)
    questions = paper / "REVIEWER_QUESTIONS.json"
    questions.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "questions": [
                    {
                        "id": f"Q{i}",
                        "question": f"why is baseline {i} not SOTA?",
                        "severity": "major",
                        "addressed_in_section": "5.2 Baselines",
                        "addressed_evidence": "Table 3.",
                    }
                    for i in range(1, 13)
                ],
            }
        ),
        encoding="utf-8",
    )
    main_tex = paper / "main.tex"
    main_tex.write_text(
        r"\documentclass{article}\begin{document}x\end{document}",
        encoding="utf-8",
    )
    # Pin the mtimes: the questions were generated against this draft, so the
    # only staleness finding a clean fixture may produce is none at all.
    tex_mtime = main_tex.stat().st_mtime
    os.utime(questions, (tex_mtime + 10, tex_mtime + 10))
    _pipeline_state(root, target_venue="ICLR")


def test_reviewer_project_fixture_is_otherwise_clean(tmp_path) -> None:
    """Without this the staleness assertion below could not fail for the right
    reason -- any other issue would keep ``ok`` False on its own."""
    from argus_skill.verticals.research import reviewer_simulation

    _reviewer_project(tmp_path)

    report = reviewer_simulation.validate_reviewer_simulation(tmp_path)

    assert report.ok, [i.code for i in report.issues]
    assert report.stale_vs_main_tex is False


def test_unreadable_mtimes_are_recorded_as_unknown_not_as_fresh(
    tmp_path, monkeypatch
) -> None:
    from argus_skill.verticals.research import reviewer_simulation

    _reviewer_project(tmp_path)

    real_stat = Path.stat
    real_find_main_tex = reviewer_simulation._find_main_tex
    armed: list[bool] = []

    def find_main_tex(project_root):
        # _find_main_tex runs once, immediately before the staleness
        # comparison; arming here leaves the earlier exists() probes alone.
        found = real_find_main_tex(project_root)
        armed.append(True)
        return found

    def stat(self, *args, **kwargs):
        if armed and self.name == reviewer_simulation.QUESTIONS_FILENAME:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(reviewer_simulation, "_find_main_tex", find_main_tex)
    monkeypatch.setattr(Path, "stat", stat)

    report = reviewer_simulation.validate_reviewer_simulation(tmp_path)

    unknown = [
        i
        for i in report.issues
        if i.code == "reviewer_questions_staleness_unknown"
    ]
    assert unknown, [i.code for i in report.issues]
    assert "PermissionError" in unknown[0].detail
    assert "Permission denied" in unknown[0].detail
    assert report.ok is False
    # Unknown is its own answer: it is not quietly promoted to "stale" either.
    assert report.stale_vs_main_tex is False


# ----------------------------------------------------------------------
# svg_to_png: the reason it failed, not a fix that will not work
# ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def renderer():
    path = (
        REPO_ROOT
        / "argus_skill/verticals/research/skills/engineer"
        / "figure_spec_scripts/figure_renderer.py"
    )
    spec = importlib.util.spec_from_file_location(
        "argus_figure_renderer_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _svg(tmp_path: Path) -> Path:
    path = tmp_path / "figure.svg"
    path.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    return path


def test_svg_to_png_names_the_real_failure_not_an_install_hint(
    renderer, tmp_path, monkeypatch, capsys
) -> None:
    """Both backends installed, both failing. Telling the operator to install
    what they already have sends them after a fix that cannot work."""
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/nonexistent")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, b"", b"Error domain 1 code 3: Unable to parse XML"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    cairosvg = types.ModuleType("cairosvg")
    cairosvg.svg2png = lambda **kwargs: (_ for _ in ()).throw(
        ValueError("invalid SVG: unclosed element")
    )
    monkeypatch.setitem(sys.modules, "cairosvg", cairosvg)

    svg = _svg(tmp_path)
    assert renderer.svg_to_png(str(svg), str(tmp_path / "figure.png")) is False

    out = capsys.readouterr().out
    assert "rsvg-convert exited 1" in out
    assert "Unable to parse XML" in out
    assert "ValueError: invalid SVG: unclosed element" in out
    assert "install" not in out.lower()


def test_svg_to_png_keeps_the_install_hint_for_a_genuinely_absent_backend(
    renderer, tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/nonexistent")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'rsvg-convert'")

    monkeypatch.setattr(subprocess, "run", fake_run)
    # None in sys.modules makes `import cairosvg` raise ImportError.
    monkeypatch.setitem(sys.modules, "cairosvg", None)

    svg = _svg(tmp_path)
    assert renderer.svg_to_png(str(svg), str(tmp_path / "figure.png")) is False

    out = capsys.readouterr().out
    assert "rsvg-convert: not installed" in out
    assert "cairosvg: not installed" in out
    assert "install rsvg-convert or cairosvg" in out


def test_svg_to_png_hint_names_only_the_backend_that_is_missing(
    renderer, tmp_path, monkeypatch, capsys
) -> None:
    """rsvg-convert absent, cairosvg present and raising: the hint may name the
    one that is genuinely not there, and must not name the one that is."""
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/nonexistent")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'rsvg-convert'")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cairosvg = types.ModuleType("cairosvg")
    cairosvg.svg2png = lambda **kwargs: (_ for _ in ()).throw(
        OSError("no library called 'cairo-2' was found")
    )
    monkeypatch.setitem(sys.modules, "cairosvg", cairosvg)

    svg = _svg(tmp_path)
    assert renderer.svg_to_png(str(svg), str(tmp_path / "figure.png")) is False

    out = capsys.readouterr().out
    assert "no library called 'cairo-2' was found" in out
    assert "install rsvg-convert to enable" in out
    assert "install rsvg-convert or cairosvg" not in out


def test_svg_to_png_still_succeeds_quietly_when_a_backend_works(
    renderer, tmp_path, monkeypatch, capsys
) -> None:
    png = tmp_path / "figure.png"

    def fake_run(cmd, **kwargs):
        png.write_bytes(b"\x89PNG\r\n")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert renderer.svg_to_png(str(_svg(tmp_path)), str(png)) is True
    assert capsys.readouterr().out == ""
