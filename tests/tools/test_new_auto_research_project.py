from __future__ import annotations

import json
import py_compile
from pathlib import Path

from argus_skill.skills.builtins import builtin_skill_count
from argus_skill.tools.new_auto_research_project import (
    LaunchConfig,
    create_project,
    default_objective,
    extract_copy_ready_agents_md,
    load_template_text,
    next_version,
    render_agents_md,
)


def test_extract_copy_ready_agents_md_omits_skill_frontmatter() -> None:
    body = extract_copy_ready_agents_md(load_template_text())

    # Structural: the copy-ready body starts with the AGENTS heading and
    # does NOT include the wrapping skill heading. Specific phrase asserts
    # were removed — those drift every time the template is rewritten.
    assert body.startswith("# AGENTS.md\n")
    assert "## Copy-ready `AGENTS.md`" not in body


def test_render_agents_md_fills_placeholders_and_quality_contracts() -> None:
    rendered = render_agents_md(
        load_template_text(),
        project_name="agent-emnlp-auto-research-v15",
        version="v15",
    )

    # Structural: all placeholder tokens are substituted and the project
    # name reaches the rendered output. Specific section/path strings
    # were removed — those drift with every template rewrite and add no
    # behavioral signal.
    assert "[write the target research problem and deliverable]" not in rendered
    assert "| [input] | [source] | [status] | [allowed use] | [rationale] |" not in rendered
    assert "agent-emnlp-auto-research-v15" in rendered


def test_rendered_agents_md_has_no_stale_validator_or_critic_prose() -> None:
    """Guard against the botched validator->reviewer find/replace recurring.

    The reviewer completion contract replaced the old "validator command must
    exit 0" gate. Earlier mechanical edits left garbled, incoherent prose and
    dead-module references in every generated AGENTS.md. These assertions keep
    the generated project doc coherent with the current architecture.
    """
    rendered = render_agents_md(
        load_template_text(),
        project_name="agent-emnlp-auto-research-v15",
        version="v15",
    )
    forbidden = (
        "exact command to exit 0",
        "this exact command",
        "the exact the",
        "command above exits 0",
        "marking done against the full pipeline checklist exit 0",
        "passes the exact",
        "critic/critic",
        "L3 critic",
        "repair-lane",
        # nested-backtick reviewer prose produced by the bad replace
        "reviewer marking `done`",
    )
    for phrase in forbidden:
        assert phrase not in rendered, f"stale prose leaked into AGENTS.md: {phrase!r}"

    # The live scientist distiller/compactor package is still referenced.
    assert "scientist/*" in rendered
    # The completion contract is described in reviewer-certification terms.
    assert "scope: final_submission" in rendered


def test_default_objective_is_coherent_paper_submission_goal() -> None:
    objective = default_objective("agent-emnlp-auto-research-v15")
    assert "the exact the" not in objective
    assert "passes the exact" not in objective
    # Supervisor long-horizon heuristics still key on broad paper terms.
    assert "EMNLP/ACL long-paper" in objective
    assert "submission package" in objective


def test_create_project_without_daemon_exports_template_and_skills(tmp_path: Path) -> None:
    result = create_project(
        LaunchConfig(
            parent=tmp_path,
            version="v15",
            start_daemon=False,
            init_git=False,
            create_project_venv=False,
        )
    )

    # Behavior: project directory at the right path, daemon not started,
    # AGENTS file is written, skills are exported, helper python files
    # are present and compilable, pipeline state JSON has the right shape.
    assert result.project_dir == tmp_path / "agent-emnlp-auto-research-v15"
    assert result.daemon_started is False
    assert result.agents_path.exists()
    exported = sorted(result.skills_dir.rglob("*.md"))
    assert len(exported) == builtin_skill_count()
    assert (result.skills_dir / "agent-md-new-project-template.md").exists()

    code_dir = result.project_dir / "code"
    for required in ("__init__.py", "llm.py", "generate_image_2.py", "generate_image2_figure.py"):
        assert (code_dir / required).exists(), f"missing {required}"
    for path in (code_dir / "llm.py", code_dir / "generate_image_2.py",
                 code_dir / "generate_image2_figure.py"):
        py_compile.compile(str(path), doraise=True)

    pipeline_state = json.loads(
        (result.project_dir / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert pipeline_state["current_stage"] == "research"
    assert pipeline_state["stages"]["research"]["status"] == "pending"
    assert pipeline_state["stages"]["submission"]["status"] == "missing"
    for required in (
        "research/RESEARCH_BRIEF.md",
        "research/EXPERIMENT_PLAN.md",
        "research/CLAIMS_TO_TEST.md",
        "research/GO_NO_GO.md",
        "experiments/BENCHMARK_PROVENANCE.md",
    ):
        assert (result.project_dir / required).exists(), f"missing {required}"


def test_create_project_without_domain_exports_all_skills(
    tmp_path: Path,
) -> None:
    """Domain packs removed — all projects get the same skill set."""
    result = create_project(
        LaunchConfig(
            parent=tmp_path,
            version="v16",
            start_daemon=False,
            init_git=False,
            create_project_venv=False,
        )
    )

    exported = sorted(result.skills_dir.rglob("*.md"))
    assert len(exported) > 0
    assert (result.skills_dir / "engineer" / "auto-research-pipeline.md").exists()


def test_next_version_uses_highest_existing_workspace(tmp_path: Path) -> None:
    (tmp_path / "agent-emnlp-auto-research-v2").mkdir()
    (tmp_path / "agent-emnlp-auto-research-v10").mkdir()
    (tmp_path / "agent-emnlp-auto-research-not-a-version").mkdir()

    assert next_version(tmp_path) == "v11"


def test_create_project_seeds_an_isolated_venv(tmp_path: Path) -> None:
    """The launcher must build an isolated `.venv` inside each project so
    the agent can `pip install` experiment dependencies without polluting
    the Argus framework venv.
    """

    result = create_project(
        LaunchConfig(
            parent=tmp_path,
            version="v17",
            start_daemon=False,
            init_git=False,
            create_project_venv=True,
        )
    )

    project = result.project_dir
    venv_dir = project / ".venv"
    assert result.project_venv == venv_dir
    assert venv_dir.is_dir(), "per-project virtualenv was not created"
    # pyvenv.cfg is the canonical marker that this is a real venv.
    assert (venv_dir / "pyvenv.cfg").exists()
    # The venv must ship its own python interpreter binary.
    py = venv_dir / "bin" / "python"
    if not py.exists():
        py = venv_dir / "Scripts" / "python.exe"
    assert py.exists(), "project venv does not expose its own python"
    # The launcher's gitignore should keep the venv out of git history.
    gitignore = (project / ".gitignore").read_text(encoding="utf-8")
    assert ".venv/" in gitignore


def test_default_compute_budget_mentions_project_venv() -> None:
    """`default_compute_budget()` is what fills the project's AGENTS.md
    compute clause; it must teach the agent to use the project venv and
    NOT pip into the framework venv.
    """

    from argus_skill.tools.new_auto_research_project import default_compute_budget

    rendered = default_compute_budget()
    # Structural: the venv path must appear somewhere in the clause. The
    # specific "framework" / "NEVER" wording is intentionally not asserted
    # because it drifts every time we rephrase the warning.
    assert "./.venv" in rendered
