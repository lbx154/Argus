from __future__ import annotations

from pathlib import Path

import argus_skill.tools.new_auto_research_project as narp
from argus_skill.tools.new_auto_research_project import (
    default_objective,
    extract_copy_ready_agents_md,
    load_template_text,
    render_agents_md,
)


def test_extract_copy_ready_agents_md_omits_skill_frontmatter() -> None:
    body = extract_copy_ready_agents_md(load_template_text())

    # Structural: the copy-ready body starts with the AGENTS heading and
    # does NOT include the wrapping skill heading. Specific phrase asserts
    # were removed — those drift every time the template is rewritten.
    assert body.startswith("# AGENTS.md\n")
    assert "## Copy-ready `AGENTS.md`" not in body


def test_extract_copy_ready_agents_md_keeps_body_after_nested_code_fence() -> None:
    """Nested ```python fences inside the body must not truncate AGENTS.md.

    A naive 'first ```' search closed the copy-ready block at the embedded
    rollback example and silently dropped the entire back half of the agent
    prompt (Operational safety, Forbidden shortcuts, Completion contract).
    Extraction must walk fence depth and keep content after the nested fence.
    """
    body = extract_copy_ready_agents_md(load_template_text())

    # The nested code example itself and every section that follows it must
    # survive extraction.
    assert "rollback_stage(" in body
    for section in (
        "## Operational safety",
        "## Forbidden shortcuts",
        "## Completion contract",
    ):
        assert section in body, f"section dropped by extraction: {section!r}"

    # Template-only meta sections live outside the copy-ready fence and must
    # NOT leak into the generated agent prompt.
    assert "## Generality check" not in body
    assert "## Coverage check" not in body


def test_extract_copy_ready_agents_md_survives_bare_nested_fence() -> None:
    """A bare (untagged) nested fence must not close the copy-ready block.

    The four-backtick wrapper plus length-aware closing means inner 3-backtick
    fences — tagged or bare — stay body content. This is the failure class that
    silently truncated the agent prompt before.
    """
    template = (
        "---\nname: demo\n---\n\n"
        "## Copy-ready `AGENTS.md`\n\n"
        "````markdown\n"
        "# AGENTS.md\n\n"
        "Intro before the example.\n\n"
        "```\n"
        "a bare untagged code block\n"
        "```\n\n"
        "## Trailing section\n"
        "This must survive extraction.\n"
        "````\n\n"
        "## Generality check\nMeta only.\n"
    )
    body = extract_copy_ready_agents_md(template)
    assert body.startswith("# AGENTS.md\n")
    assert "a bare untagged code block" in body
    assert "## Trailing section" in body
    assert "This must survive extraction." in body
    assert "## Generality check" not in body


def test_render_agents_md_fills_placeholders_and_quality_contracts() -> None:
    rendered = render_agents_md(
        load_template_text(),
        project_name="agent-emnlp-auto-research-v15",
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

    # The live skill-memory modules are still referenced.
    assert "skills/skill_router.py" in rendered
    assert "skills/compaction.py" in rendered
    # The completion contract is described in reviewer-certification terms.
    assert "scope: final_submission" in rendered


def test_default_objective_is_coherent_paper_submission_goal() -> None:
    objective = default_objective("agent-emnlp-auto-research-v15")
    assert "the exact the" not in objective
    assert "passes the exact" not in objective
    # Supervisor long-horizon heuristics still key on broad paper terms.
    assert "EMNLP/ACL long-paper" in objective
    assert "submission package" in objective


def test_optimize_template_renders_without_paper_or_venue() -> None:
    """The optimize template renders a benchmark-optimization contract: it
    fills the same placeholders, mentions optimization, and — when the
    harness map is suppressed — carries no EMNLP/paper-venue prose."""
    from argus_skill.skills.builtins import builtin_skill_source_path

    template_path = (
        builtin_skill_source_path() / "agent-md-optimize-project-template.md"
    )
    rendered = render_agents_md(
        load_template_text(template_path),
        project_name="kbench-proj",
        objective="maximize kernelbench SOL score on B200",
        non_goals="Do not produce a paper or venue submission.",
        compute_budget="Run the real harness on real hardware.",
        append_harness_map=False,
    )
    assert "[write the target research problem and deliverable]" not in rendered
    assert "kernel" in rendered.lower()
    assert "SOL" in rendered
    assert "EMNLP" not in rendered
    assert "Anonymous EMNLP Submission" not in rendered


def test_render_agents_md_can_suppress_harness_map() -> None:
    """``append_harness_map=False`` drops the paper-centric Argus ownership map."""
    body_with = render_agents_md(
        load_template_text(),
        project_name="p",
        append_harness_map=True,
    )
    body_without = render_agents_md(
        load_template_text(),
        project_name="p",
        append_harness_map=False,
    )
    assert "## Argus harness modification map" in body_with
    assert "## Argus harness modification map" not in body_without


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


def test_ml_base_python_prefers_torch_bearing_override(
    tmp_path: Path, monkeypatch
) -> None:
    """`_ml_base_python()` selects the ARGUS_SKILL_ML_PYTHON override when it
    can import torch, so the project venv inherits the host CUDA stack instead
    of the (torch-less) framework venv.
    """

    override = tmp_path / "ml" / "bin" / "python"
    override.parent.mkdir(parents=True)
    override.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setenv("ARGUS_SKILL_ML_PYTHON", str(override))
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.setattr(narp, "_interpreter_has_torch", lambda p: p == override)

    assert narp._ml_base_python() == override


def test_ml_base_python_falls_back_to_conda_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    """With no override, `$CONDA_PREFIX/bin/python` is chosen when it has
    torch — this is the Azure ML ``ptca`` conda env case.
    """

    conda = tmp_path / "conda"
    conda_py = conda / "bin" / "python"
    conda_py.parent.mkdir(parents=True)
    conda_py.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.delenv("ARGUS_SKILL_ML_PYTHON", raising=False)
    monkeypatch.setenv("CONDA_PREFIX", str(conda))
    monkeypatch.setattr(narp, "_interpreter_has_torch", lambda p: p == conda_py)

    assert narp._ml_base_python() == conda_py


def test_ml_base_python_falls_back_to_framework_when_no_torch(
    tmp_path: Path, monkeypatch
) -> None:
    """When no candidate interpreter has torch, fall back to the framework
    python (prior behavior) rather than raising.
    """

    sentinel = tmp_path / "fw" / "bin" / "python"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.delenv("ARGUS_SKILL_ML_PYTHON", raising=False)
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path / "conda"))
    monkeypatch.setattr(narp, "_interpreter_has_torch", lambda p: False)
    monkeypatch.setattr(narp, "_argus_python", lambda: sentinel)

    assert narp._ml_base_python() == sentinel
