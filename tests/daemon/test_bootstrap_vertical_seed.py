"""Daemon bootstrap seeds the AGENTS contract by vertical (paper vs. optimize).

The Manager AGENT decides + persists the vertical; the daemon bootstrap READS
that persisted vertical (no keyword classifier) and seeds the matching AGENTS.md
— a lean benchmark-optimization contract for the optimize-family verticals and
the paper/auto-research contract for research.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus_skill.skills.vertical_select import persist_vertical


@pytest.fixture(autouse=True)
def _no_real_venv(monkeypatch: pytest.MonkeyPatch):
    """Building a real ``.venv`` is slow and host-dependent; stub it out.

    The bootstrap under test is the AGENTS/vertical seeding, not venv creation.
    """
    monkeypatch.setattr(
        "argus_skill.tools.new_auto_research_project.init_project_venv",
        lambda project_dir: project_dir / ".venv",
    )


def _worker(objective: str) -> LifeWorker:
    return LifeWorker(LifeWorkerConfig(life_dir=Path("/tmp"), continuous_objective=objective))


def test_kernel_objective_seeds_optimize_contract(tmp_path: Path) -> None:
    # The Manager decided kernelbench + persisted it; seeding reads that.
    persist_vertical(tmp_path, "kernelbench")
    worker = _worker("maximize the KernelBench SOL score for these CUDA kernels on B200")
    worker._seed_project_agents_and_venv(tmp_path)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    # Optimize contract: mentions the optimization mission + kernel/SOL metric.
    assert "kernel" in agents.lower()
    assert "SOL" in agents
    assert "optimiz" in agents.lower()
    # And carries NO paper/venue prose.
    assert "EMNLP" not in agents
    assert "Anonymous EMNLP Submission" not in agents
    assert "venue submission" in agents.lower() or "no venue" in agents.lower()

    # The persisted vertical (Manager's decision) is a clean optimize pipeline
    # state — no paper fields.
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "kernelbench"
    assert state["current_stage"] == "research"
    assert "target_venue" not in state
    assert "paper_scope" not in state
    assert "submission" not in (state.get("stages") or {})


def test_research_objective_seeds_paper_contract(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research")
    worker = _worker("write an EMNLP long paper surveying agent memory")
    worker._seed_project_agents_and_venv(tmp_path)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    # Paper contract retains the EMNLP/auto-research framing + harness map.
    assert "EMNLP" in agents
    assert "## Argus harness modification map" in agents

    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"
    assert state["current_stage"] == "research"


def test_env_forced_vertical_seeds_optimize_contract_on_a_fresh_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an operator-forced ``ARGUS_SKILL_VERTICAL`` must be honored at
    SEED time too, not just by the later ``resolve_vertical`` read.

    On a genuinely FRESH project (nothing persisted to ``PIPELINE_STATE.json``
    yet — the Manager has not run its first turn), seeding used to consult
    ONLY the persisted state and always fall back to the ~5x larger paper
    contract, permanently baking in irrelevant paper/citation/figure prose for
    the rest of the mission (``AGENTS.md`` is never regenerated once written).
    The env var must win here exactly like it does in ``resolve_vertical``.
    """
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "kernelbench")
    assert not (tmp_path / "research" / "PIPELINE_STATE.json").exists()

    worker = _worker("maximize the SOL score on SOL-ExecBench kernels")
    worker._seed_project_agents_and_venv(tmp_path)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "kernel" in agents.lower()
    assert "optimiz" in agents.lower()
    assert "EMNLP" not in agents
    assert "## Argus harness modification map" not in agents


def test_bootstrap_is_idempotent_on_existing_agents(tmp_path: Path) -> None:
    """An existing AGENTS.md is never clobbered on a re-bootstrap."""
    sentinel = "# AGENTS.md\n\noperator-authored contract — do not overwrite\n"
    (tmp_path / "AGENTS.md").write_text(sentinel, encoding="utf-8")

    worker = _worker("maximize the KernelBench SOL score on B200")
    worker._seed_project_agents_and_venv(tmp_path)

    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == sentinel


def test_fresh_undecided_mission_does_not_default_to_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The harness must NOT default an undecided mission (no persisted vertical,
    no research profile) into the paper contract — a paper is a research judgment
    that must be positively declared, not the fallback."""
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    assert not (tmp_path / "research" / "PIPELINE_STATE.json").exists()  # nothing decided

    worker = _worker("improve the widget throughput")
    worker._seed_project_agents_and_venv(tmp_path)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    # Lean contract, NOT the paper/auto-research one.
    assert "EMNLP" not in agents
    assert "## Argus harness modification map" not in agents


def test_research_profile_env_seeds_paper_contract_without_persisted_vertical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator-configured research profile IS a positive research signal, so a
    research daemon still gets the paper contract even before the Manager has
    persisted the vertical."""
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "emnlp2026-tierharness")
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    assert not (tmp_path / "research" / "PIPELINE_STATE.json").exists()

    worker = _worker("write an EMNLP long paper surveying agent memory")
    worker._seed_project_agents_and_venv(tmp_path)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "EMNLP" in agents
