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

from argus_skill.core.bootstrap import structured_research_bootstrap_requested
from argus_skill.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus_skill.life.memory import BacklogItem
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


def test_bootstrap_refreshes_only_managed_block_on_existing_agents(tmp_path: Path) -> None:
    """Runtime refresh changes authority state without clobbering user text."""
    sentinel = "# AGENTS.md\n\noperator-authored contract — do not overwrite\n"
    (tmp_path / "AGENTS.md").write_text(sentinel, encoding="utf-8")

    worker = _worker("maximize the KernelBench SOL score on B200")
    worker._seed_project_agents_and_venv(tmp_path)
    first = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "operator-authored contract — do not overwrite" in first
    assert "maximize the KernelBench SOL score on B200" in first

    worker.config.continuous_objective = "new Manager objective"
    worker._seed_project_agents_and_venv(tmp_path)
    second = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "operator-authored contract — do not overwrite" in second
    assert "new Manager objective" in second
    assert "maximize the KernelBench SOL score on B200" not in second
    assert second.count("<!-- argus-managed:runtime-contract:start -->") == 1


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


def test_quant_vertical_seeds_paper_contract(tmp_path: Path) -> None:
    """Regression: ``quant`` is a research-KIND vertical per the Manager's own
    ``Manager._kind_for`` classification (``manager/_core.py``: it maps
    ``quant`` into the same ``"research"`` bucket as ``"research"`` itself,
    because a quant mission's deliverable is a reviewer-certified factor
    report gated on ``full_paper``, not a lean numeric metric).

    A prior fix (commit ``1545128``) replaced the old ``is_optimize``
    allowlist with a NEW, independently-maintained literal check
    (``vertical == "research"``) that does not consult ``Manager._kind_for``
    and therefore does not match ``"quant"`` — silently regressing quant
    projects onto the lean/optimize contract instead of the paper/EMNLP one
    they need. The fix is for the daemon bootstrap to delegate to
    ``Manager._kind_for`` directly instead of re-deriving a second
    classification, so there is only one place ("research" | "quant") is
    decided.
    """
    persist_vertical(tmp_path, "quant")
    worker = _worker("build a reviewer-certified quant factor report")
    worker._seed_project_agents_and_venv(tmp_path)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    # Paper contract retains the EMNLP/auto-research framing + harness map —
    # same assertions as the existing `research` vertical case above.
    assert "EMNLP" in agents
    assert "## Argus harness modification map" in agents

    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "quant"
    assert state["current_stage"] == "research"


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


class _StubBacklog:
    def __init__(self, items: list[BacklogItem] | None = None) -> None:
        self.items = list(items or [])

    def all(self) -> list:
        return list(self.items)

    def add(self, item: BacklogItem) -> BacklogItem:
        self.items.append(item)
        return item


class _StubMemory:
    def __init__(self, items: list[BacklogItem] | None = None) -> None:
        self.backlog = _StubBacklog(items)


class _StubSink:
    def handle_event(self, event: dict) -> None:  # noqa: D401 - test stub
        pass


def test_bootstrap_seed_race_closed_by_deferring_seed_past_manager_divide(
    tmp_path: Path,
) -> None:
    """Repro of the write-order race documented in GROUND_TRUTH.md
    CLASSIFY_BY_VERTICAL §(f): ``LifeWorker.run`` computes the bootstrap
    preflight (``inspect_project_bootstrap``) while the project is still
    genuinely empty and the vertical is unresolved, but previously ALSO
    rendered ``AGENTS.md`` (via ``_seed_bootstrap_task`` ->
    ``_seed_project_agents_and_venv``) at that same unresolved moment —
    before ``Manager.divide()``/``decide_vertical()`` a few dozen lines later
    in ``run`` had any chance to persist the real vertical. Because the
    ``AGENTS.md`` write is write-once (``agents_path.exists()`` guard), a
    project that should resolve to a research-kind vertical (e.g. ``quant``)
    got permanently sealed onto the lean/optimize contract instead.

    The fix (this file's companion change in ``daemon/life_worker.py``)
    splits "classify" from "act": the preflight is still computed early
    (before any writes — computing it late would itself break detection,
    since ``persist_vertical`` writes ``research/PIPELINE_STATE.json``, one
    of ``core.bootstrap._RESEARCH_BOOTSTRAP_ARTIFACTS``), but the actual
    ``_seed_bootstrap_task`` call is deferred until AFTER ``Manager.divide()``
    has run. This test reproduces exactly that ordering directly (preflight
    captured pre-persistence, seed call invoked post-persistence) and proves
    the previously-unresolved-at-seed-time vertical now renders correctly.
    """
    from argus_skill.core.bootstrap import inspect_project_bootstrap

    # (1) The daemon's EARLY preflight classification — the project is still
    # completely empty, nothing persisted yet (matches `run`'s call site,
    # which runs before Manager.divide()).
    assert not (tmp_path / "research" / "PIPELINE_STATE.json").exists()
    preflight = inspect_project_bootstrap(
        tmp_path,
        objective_hint="quant factor research",
        research_requested=True,
    )
    assert preflight.should_bootstrap is True

    # (2) Manager.divide()/decide_vertical() resolving + persisting the
    # vertical — this is what now runs BETWEEN preflight capture and the
    # deferred seed call in the fixed `run()` ordering.
    persist_vertical(tmp_path, "quant")

    # (3) The deferred seed call: same preflight object captured in step (1),
    # but invoked only now — after the vertical is already persisted. This is
    # the exact call `run()` makes via `bootstrap_preflight_pending`.
    worker = _worker("quant factor research")
    worker._seed_bootstrap_task(_StubMemory(), _StubSink(), preflight)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    # Would be the LEAN/optimize contract (the closed bug) if the seed had
    # read `vertical=None` at render time instead of the now-resolved "quant".
    assert "EMNLP" in agents
    assert "## Argus harness modification map" in agents


def test_bootstrap_seed_before_divide_reproduces_the_closed_race(
    tmp_path: Path,
) -> None:
    """Control case: calling the seed BEFORE the vertical is persisted (the
    OLD, now-fixed call order) reproduces the exact bug this stage closes —
    proving the fix in the companion test above is about ordering, not about
    the classification rule from Part A.
    """
    from argus_skill.core.bootstrap import inspect_project_bootstrap

    assert not (tmp_path / "research" / "PIPELINE_STATE.json").exists()
    preflight = inspect_project_bootstrap(
        tmp_path,
        objective_hint="quant factor research",
        research_requested=True,
    )
    assert preflight.should_bootstrap is True

    worker = _worker("quant factor research")
    # Seed BEFORE the Manager ever resolves the vertical (the old ordering).
    worker._seed_bootstrap_task(_StubMemory(), _StubSink(), preflight)
    # Manager resolves + persists the vertical only afterward — too late, the
    # write-once AGENTS.md is already sealed.
    persist_vertical(tmp_path, "quant")

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "EMNLP" not in agents  # sealed onto LEAN — this is the bug, pre-fix


def test_bounded_manager_objective_drives_bootstrap_agents_contract(
    tmp_path: Path,
) -> None:
    """A bounded Manager handoff must not fall back to the demo paper goal."""
    from argus_skill.core.bootstrap import inspect_project_bootstrap

    preflight = inspect_project_bootstrap(
        tmp_path,
        research_requested=True,
    )
    persist_vertical(tmp_path, "research")
    item = BacklogItem.new(
        title="Prove one simple Erdos conjecture",
        objective="Planner-local implementation step",
        priority=-1,
        tags=["manager", "planner", "bounded_dag_node", "scope:bounded"],
    )
    item.original_objective = "找一个简单的 Erdős 猜想并尝试证明"
    memory = _StubMemory([item])

    worker = _worker("")
    assert worker._seed_bootstrap_task(memory, _StubSink(), preflight)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Bootstrap-time Manager objective: 找一个简单的 Erdős 猜想并尝试证明" in agents
    assert "Start " + tmp_path.name + " as a clean-slate EMNLP/ACL" not in agents


def test_empty_bootstrap_objective_waits_for_manager_instead_of_demo_goal(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "research")
    worker = _worker("")
    worker._seed_project_agents_and_venv(tmp_path)

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "No Manager-authored objective was active at bootstrap" in agents
    assert "Start " + tmp_path.name + " as a clean-slate EMNLP/ACL" not in agents


def test_bootstrap_prefers_highest_priority_active_manager_objective() -> None:
    older = BacklogItem.new(
        title="Older task",
        objective="older planner step",
        priority=0,
    )
    older.original_objective = "older operator objective"
    newer = BacklogItem.new(
        title="Newer task",
        objective="newer planner step",
        priority=-1,
    )
    newer.original_objective = "newer operator objective"

    assert LifeWorker._active_manager_objective(_StubMemory([older, newer])) == (
        "newer operator objective"
    )


def test_bootstrap_gate_rejects_custom_vertical_and_accepts_research_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    custom_root = tmp_path / "custom"
    custom_root.mkdir()
    persist_vertical(custom_root, "learning")
    assert structured_research_bootstrap_requested(custom_root) is False

    research_root = tmp_path / "research"
    research_root.mkdir()
    persist_vertical(research_root, "quant")
    assert structured_research_bootstrap_requested(research_root) is True
