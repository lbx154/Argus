"""Daemon bootstrap seeds the AGENTS contract by vertical (paper vs. optimize).

The ``argus start`` hardcoded paper bypass was retired; the only entry is the
daemon bootstrap, which must classify the continuous objective's vertical and
seed the matching AGENTS.md — a lean benchmark-optimization contract for the
optimize-family verticals and the paper/auto-research contract for research —
and persist the resolved vertical into ``research/PIPELINE_STATE.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.daemon import life_worker as lw
from argus_skill.daemon.life_worker import LifeWorker, LifeWorkerConfig


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

    # Vertical persisted, with a clean optimize pipeline state — no paper fields.
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "kernelbench"
    assert state["current_stage"] == "research"
    assert "target_venue" not in state
    assert "paper_scope" not in state
    # No paper 8-stage `stages` map seeded by the optimize bootstrap.
    assert "submission" not in (state.get("stages") or {})


def test_research_objective_seeds_paper_contract(tmp_path: Path) -> None:
    worker = _worker("write an EMNLP long paper surveying agent memory")
    worker._seed_project_agents_and_venv(tmp_path)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    # Paper contract retains the EMNLP/auto-research framing + harness map.
    assert "EMNLP" in agents
    assert "## Argus harness modification map" in agents

    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"
    assert state["current_stage"] == "research"


def test_bootstrap_is_idempotent_on_existing_agents(tmp_path: Path) -> None:
    """An existing AGENTS.md is never clobbered on a re-bootstrap."""
    sentinel = "# AGENTS.md\n\noperator-authored contract — do not overwrite\n"
    (tmp_path / "AGENTS.md").write_text(sentinel, encoding="utf-8")

    worker = _worker("maximize the KernelBench SOL score on B200")
    worker._seed_project_agents_and_venv(tmp_path)

    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == sentinel
