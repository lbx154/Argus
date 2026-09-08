from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.verticals.research.prompt_policy import render_role_prompt_fragment
from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

REPO_ROOT = Path(__file__).resolve().parents[2]
RETIRED_MODULE = "argus_skill.verticals.research.pipeline_figure"


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if value
    )
    return subprocess.run(
        [sys.executable, "-m", RETIRED_MODULE, *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )


def test_retired_render_creates_no_files_and_provides_runnable_migration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Retained</text></svg>')
    output = tmp_path / "new-output" / "figure.svg"
    result = _run_cli(
        tmp_path, "render", "--input", str(source), "--output", str(output), "--pdf", "--png",
    )
    assert result.returncode == 2
    assert not result.stdout
    assert "has been removed" in result.stderr
    assert "Method D" in result.stderr and "Method B" in result.stderr
    assert "native editable PPT" in result.stderr
    assert "engineer/paper-framework-figure-studio.md" in result.stderr
    assert "engineer/presentation-master.md" in result.stderr
    assert not output.parent.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["source.svg"]

    # Check the actual replacement command reaches a supported CLI parser.
    # --help avoids installing or changing the operator's existing toolkit.
    command_line = next(line.strip() for line in result.stderr.splitlines() if line.startswith("  "))
    command = shlex.split(command_line)
    assert command == [sys.executable, "-m", "argus_skill.tools.ppt_master", "status"]
    replacement = subprocess.run(
        [*command, "--help"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert "status" in replacement.stdout


def test_retired_render_preserves_source_and_existing_exports(tmp_path: Path) -> None:
    source = tmp_path / "source.svg"
    source.write_text("existing editable source")
    output = tmp_path / "figure.svg"
    for suffix in (".svg", ".pdf", ".png"):
        output.with_suffix(suffix).write_bytes(f"existing {suffix}".encode())
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = _run_cli(
        tmp_path, "render", "--input", str(source), "--output", str(output), "--pdf", "--png",
    )
    assert result.returncode == 2
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("args", [(), ("brief", "--code", "missing.py", "--paper", "missing.tex")])
def test_retired_command_cannot_supply_an_old_design_brief(
    tmp_path: Path, args: tuple[str, ...],
) -> None:
    result = _run_cli(tmp_path, *args)
    assert result.returncode == 2
    assert not result.stdout
    assert "has been removed" in result.stderr
    assert "method_pipeline.svg" not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_retired_command_help_explains_the_replacement(tmp_path: Path) -> None:
    result = _run_cli(tmp_path, "--help")
    assert result.returncode == 0
    assert "has been removed" in result.stdout
    assert "PPT Master's internal SVG conversion remain available" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_svg_workflow_is_removed_and_paper_uses_method_d_with_b_fallback() -> None:
    texts = dict(iter_vertical_skill_texts("research"))
    skill = "engineer/research-svg-pipeline.md"
    assert skill not in texts
    assert skill not in texts["research-paper-playbook.md"]
    assert skill not in texts["research-review-playbook.md"]
    prompt = render_role_prompt_fragment(
        role="engineer", operation="author_draft", stage="paper", scope="",
        project_root=None,
    )
    assert "engineer/paper-framework-figure-studio.md" in prompt
    assert "PPT Master" in prompt
    assert "Method D is the default" in prompt
    assert "Method B is the fallback" in prompt
    assert "native editable PPT" in prompt
    assert "ECharts" in prompt
    assert "TikZ" not in prompt
    assert "argus_skill.tools.ppt_master status" in prompt
    assert "pipeline_figure" not in prompt
    assert "Reuse an existing suitable figure" in prompt
    assert "after the Introduction" in prompt
    assert "page 2 or 3" in prompt
    for stage in ("paper", "review"):
        checklist = " ".join(item.statement for item in STAGE_CHECKLISTS[stage])
        assert "PPT Master" in checklist
        assert "visual hierarchy" in checklist
    for stage in ("idea", "experiment", "review"):
        prompt = render_role_prompt_fragment(
            role="engineer", operation="", stage=stage, scope="", project_root=None,
        )
        assert "pipeline_figure" not in prompt
        if stage == "review":
            assert "Method D is the default" in prompt
            assert "Method B is the fallback" in prompt
            assert "native editable PPT" in prompt
            assert "TikZ" not in prompt
            assert "engineer/paper-framework-figure-studio.md" in prompt
            assert "operator-rejected figure needs a fresh composition" in prompt
    for stage in ("paper", "review"):
        prompt = render_role_prompt_fragment(
            role="engineer", operation="narrative_edit", stage=stage, scope="",
            project_root=None,
        )
        assert "pipeline_figure" not in prompt
        assert "Fresh-context Narrative Editor" in prompt
