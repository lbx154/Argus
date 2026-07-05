"""LLM-judged periodic skill compaction: ``skills.compaction.llm_plan_compaction``.

The batched-clustering counterpart to ``tests/skills/test_skill_llm_duplicate_judge.py``
(create-time check). Here the LLM only decides WHICH skills group together in
one call per batch (mirroring the skill matcher's own batching); the harness
ALWAYS decides which one in each group to keep via ``_representative`` — a
mechanical pick from real usage data (``version``/``task_history``) the model
never sees, so which skill "wins" is never subject to model hallucination.
There is no lexical/scored fallback: a judge failure (no runner, exception,
malformed response) simply returns ``None`` — "nothing to do this round" —
never a mechanical clustering pass.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.skills.compaction import llm_plan_compaction
from argus_skill.skills.store import Skill

_HOW_TO_TMPL = "## When to use\n{w}\n\n## How to solve\n{h}\n"


def _skill(name: str, desc: str, *, category: str = "gpu-debug",
           when: str = "GPU runs out of memory", how: str = "reduce batch size, checkpoint",
           protected: bool = False, version: int = 1, task_history: list[str] | None = None) -> Skill:
    return Skill(
        name=name, description=desc, category=category,
        content=_HOW_TO_TMPL.format(w=when, h=how),
        protected=protected, version=version, task_history=task_history or [],
    )


def test_llm_groups_paraphrased_skills_and_mechanical_pick_wins(tmp_path: Path) -> None:
    """The LLM only groups; ``_representative`` (real usage data) decides
    which skill in the group survives — here the heavily-reused one."""
    weak = _skill("Debug CUDA OOM", "Fix out-of-memory errors during training.",
                   version=1, task_history=[])
    strong = _skill("Fix GPU memory overflow", "Resolve out-of-memory crashes during training.",
                     version=5, task_history=["m1", "m2", "m3"])
    unrelated = _skill("Write unit tests for REST API", "Add HTTP endpoint test coverage.",
                        category="testing", when="new endpoint lacks coverage",
                        how="spin up a test client, assert status/body")
    backend = MemoryBackend()
    backend.queue("skill.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [["Debug CUDA OOM", "Fix GPU memory overflow"]],
    })))
    plan = llm_plan_compaction([weak, strong, unrelated], judge_runner=backend, judge_model="m")
    assert plan is not None
    assert len(plan.clusters) == 1
    assert [s.name for s in plan.keep] == ["Fix GPU memory overflow"]
    assert [s.name for s in plan.archive] == ["Debug CUDA OOM"]


def test_llm_finds_no_clusters_returns_empty_not_none(tmp_path: Path) -> None:
    a = _skill("Debug CUDA OOM", "Fix out-of-memory errors.")
    b = _skill("Write unit tests", "Add coverage.", category="testing")
    backend = MemoryBackend()
    backend.queue("skill.compaction_batch", CannedResponse(message=json.dumps({"clusters": []})))
    plan = llm_plan_compaction([a, b], judge_runner=backend, judge_model="m")
    assert plan is not None
    assert plan.clusters == [] and plan.archive == []


def test_llm_never_archives_protected_skill_even_when_grouped(tmp_path: Path) -> None:
    """Protected skill wins the mechanical representative pick even against
    a FAR more reused ordinary skill — self-governance floor holds through
    the LLM-grouped path too."""
    protected = _skill(
        "Anti-cheat guardrail", "Never fabricate a benchmark metric result.",
        category="anti-cheat", when="before reporting any benchmark number",
        how="verify the metric against raw logs before reporting it",
        protected=True, version=1, task_history=[],
    )
    reused = _skill(
        "Never fake a metric", "Do not fabricate a benchmark metric result ever.",
        category="misc", when="before reporting any benchmark number",
        how="verify the metric against raw logs before reporting it ever",
        version=9, task_history=[f"m{i}" for i in range(9)],
    )
    backend = MemoryBackend()
    backend.queue("skill.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [["Anti-cheat guardrail", "Never fake a metric"]],
    })))
    plan = llm_plan_compaction([protected, reused], judge_runner=backend, judge_model="m")
    assert plan is not None
    assert [s.name for s in plan.keep] == ["Anti-cheat guardrail"]
    assert all(s.name != "Anti-cheat guardrail" for s in plan.archive)


def test_llm_hallucinated_names_drop_the_group(tmp_path: Path) -> None:
    a = _skill("Real Skill One", "Does a real thing.")
    b = _skill("Real Skill Two", "Does another real thing.")
    backend = MemoryBackend()
    backend.queue("skill.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [["Ghost Skill", "Also Ghost"]],
    })))
    plan = llm_plan_compaction([a, b], judge_runner=backend, judge_model="m")
    assert plan is not None
    assert plan.clusters == []


def test_malformed_json_returns_none(tmp_path: Path) -> None:
    a = _skill("A", "desc a")
    b = _skill("B", "desc b")
    backend = MemoryBackend()
    backend.queue("skill.compaction_batch", CannedResponse(message="not valid json"))
    plan = llm_plan_compaction([a, b], judge_runner=backend, judge_model="m")
    assert plan is None


def test_no_judge_runner_returns_none(tmp_path: Path) -> None:
    a = _skill("A", "desc a")
    b = _skill("B", "desc b")
    assert llm_plan_compaction([a, b], judge_runner=None) is None


def test_judge_runner_exception_returns_none(tmp_path: Path) -> None:
    a = _skill("A", "desc a")
    b = _skill("B", "desc b")

    class _BrokenRunner:
        def run_exec(self, **_kwargs):
            raise RuntimeError("backend exploded")

    plan = llm_plan_compaction([a, b], judge_runner=_BrokenRunner(), judge_model="m")
    assert plan is None


def test_a_skill_cannot_be_claimed_by_two_groups(tmp_path: Path) -> None:
    """If the model (incorrectly) puts the same skill in two groups, the
    SECOND claim is dropped rather than double-processed."""
    a = _skill("Debug CUDA OOM", "Fix out-of-memory errors.", version=1, task_history=[])
    b = _skill("Fix GPU memory overflow", "Resolve out-of-memory crashes.",
               version=5, task_history=["m1"])
    c = _skill("GPU crash mitigation", "Handle GPU crashes during training.",
               version=1, task_history=[])
    backend = MemoryBackend()
    backend.queue("skill.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [
            ["Debug CUDA OOM", "Fix GPU memory overflow"],
            ["Fix GPU memory overflow", "GPU crash mitigation"],  # re-claims b
        ],
    })))
    plan = llm_plan_compaction([a, b, c], judge_runner=backend, judge_model="m")
    assert plan is not None
    # Only the FIRST group should have been honored.
    assert len(plan.clusters) == 1
    assert {s.name for s in plan.clusters[0]} == {"Debug CUDA OOM", "Fix GPU memory overflow"}
