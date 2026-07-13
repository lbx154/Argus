"""Planner-emitted DAG → backlog mapping (key → real item id).

The planner can emit a batch of ``new_tasks`` carrying local ``key`` /
``deps`` references (see ``planner_schema.json`` / ``TaskSpec``). The
supervisor's consumption loop must, in two passes, (1) create a backlog item
per surviving task and remember each local ``key`` → real ``item.id``, then
(2) rewrite every task's local dep keys to the real ids before enqueuing. This
is what makes the backlog DAG (deps + topological ``claim_next``) actually
used. Flat tasks (no key/deps) must keep enqueuing exactly as before.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor._config import LifeSupervisorConfig
from argus_skill.life.supervisor._core import LifeSupervisor
from argus_skill.life.supervisor._helpers import _resolve_task_dep_ids

# ---------------------------------------------------------------------------
# _resolve_task_dep_ids — the load-bearing local-key → real-id mapping
# ---------------------------------------------------------------------------


def test_resolve_dep_ids_maps_local_keys() -> None:
    key_map = {"a": "id-a", "b": "id-b", "c": "id-c"}
    resolved, unresolved = _resolve_task_dep_ids(["a", "b"], key_map)
    assert resolved == ["id-a", "id-b"]
    assert unresolved == []


def test_resolve_dep_ids_empty_deps_is_flat() -> None:
    resolved, unresolved = _resolve_task_dep_ids([], {"a": "id-a"})
    assert resolved == []
    assert unresolved == []


def test_resolve_dep_ids_drops_unknown_keys() -> None:
    # A key not defined in THIS batch (typo / unsupported cross-cycle ref) is
    # dropped and reported, never silently turned into a dead dep id.
    resolved, unresolved = _resolve_task_dep_ids(["a", "ghost"], {"a": "id-a"})
    assert resolved == ["id-a"]
    assert unresolved == ["ghost"]


def test_resolve_dep_ids_dedupes_preserving_order() -> None:
    resolved, _ = _resolve_task_dep_ids(["a", "b", "a"], {"a": "id-a", "b": "id-b"})
    assert resolved == ["id-a", "id-b"]


# ---------------------------------------------------------------------------
# End-to-end: planner DAG verdict → supervisor consumption loop → backlog
# ---------------------------------------------------------------------------


class _DagPlannerRunner:
    """Fake planner backend that returns one fixed JSON verdict."""

    def __init__(self, verdict_json: str) -> None:
        self._verdict_json = verdict_json

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        return RunnerResult(
            exit_code=0,
            agent_messages=[self._verdict_json],
            stdout_lines=[],
            stderr_lines=[],
            thread_id=None,
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


class _NullSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event):  # pragma: no cover - trivial
        self.events.append(event)


class _NullRunner:
    """Mission runner; never invoked in the planning-only path under test."""


def _make_supervisor(tmp_path: Path, monkeypatch, verdict_json: str) -> LifeSupervisor:
    memory = LifeMemory.open(tmp_path / "life")
    config = LifeSupervisorConfig(
        continuous=True,
        continuous_objective="keep improving the project",
        # Drive the verdict straight to the consumption loop: no paper gate,
        # no open-ended retry, no external-blocker short circuit.
        paper_mission=False,
        full_paper_gate=False,
        open_ended=False,
    )
    sink = _NullSink()
    sup = LifeSupervisor(
        memory=memory,
        runner=_NullRunner(),
        sink=sink,
        config=config,
        planner_runner=_DagPlannerRunner(verdict_json),
    )
    sup._test_sink = sink  # type: ignore[attr-defined]

    # Stub the pre-loop gates so the test exercises ONLY the new two-pass
    # key→id mapping, not the unrelated vertical / wiki / journal machinery.
    monkeypatch.setattr(sup, "_maybe_idle_after_unchanged_open_ended_done", lambda: None)
    monkeypatch.setattr(sup, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(sup, "_wiki_collect_task_if_due_under_blocker", lambda: None)
    monkeypatch.setattr(sup, "_render_journal_for_planner", lambda: "")
    monkeypatch.setattr(sup, "_recent_no_progress_failures", lambda: {})
    monkeypatch.setattr(sup, "_effective_full_paper_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(sup, "_planner_runtime_with_idle_note", lambda: "")
    # Budget always plentiful.
    monkeypatch.setattr(config.budget, "remaining_today", lambda *_a, **_k: 1000.0)
    return sup


def _dag_verdict_json() -> str:
    """2 parallel runs (a, b) + 1 fan-in analysis (c, deps=[a, b])."""
    def task(key, deps, title, objective):
        return {
            "key": key,
            "deps": deps,
            "title": title,
            "impact_score": 5,
            "impact_area": "reliability",
            "evidence": "multi-seed variance needs aggregation",
            "scope": "bounded",
            "objective": objective,
        }

    return json.dumps({
        "project_done": False,
        "reason": "fan out two runs then summarize",
        "restart_daemon": False,
        "restart_reason": "",
        "waiting": False,
        "waiting_reason": "",
        "new_tasks": [
            task("a", [], "run seed 0", "train seed=0; write experiments/run-a/summary.tsv"),
            task("b", [], "run seed 1", "train seed=1; write experiments/run-b/summary.tsv"),
            task(
                "c", ["a", "b"], "analyze",
                "read experiments/run-a/summary.tsv and experiments/run-b/summary.tsv; "
                "write analysis/RESULTS.md",
            ),
        ],
    })


def test_dag_verdict_maps_keys_to_real_item_ids(tmp_path, monkeypatch) -> None:
    sup = _make_supervisor(tmp_path, monkeypatch, _dag_verdict_json())

    result = sup._plan_next_work()
    assert result is True  # new work enqueued

    items = {it.title: it for it in sup.memory.backlog.all()}
    assert set(items) == {"run seed 0", "run seed 1", "analyze"}

    a = items["run seed 0"]
    b = items["run seed 1"]
    c = items["analyze"]

    # Parallel tasks have no deps.
    assert a.deps == []
    assert b.deps == []
    # The fan-in task's local deps [a, b] are mapped to the REAL item ids.
    assert c.deps == [a.id, b.id]
    # The local keys themselves never leak into the backlog.
    assert "a" not in c.deps and "b" not in c.deps

    # And the DAG actually schedules: a/b are ready, c is gated until both done.
    ready_titles = {it.title for it in sup.memory.backlog.ready()}
    assert ready_titles == {"run seed 0", "run seed 1"}


def test_planner_can_enqueue_dynamic_math_route_as_a_dag(tmp_path, monkeypatch) -> None:
    def task(key, deps, title, objective):
        return {
            "key": key,
            "deps": deps,
            "title": title,
            "impact_score": 5,
            "impact_area": "discovery",
            "evidence": "the open conjecture needs this problem-specific route",
            "scope": "bounded",
            "objective": objective,
        }

    verdict = json.dumps({
        "project_done": False,
        "reason": "use a problem-specific mathematical research route",
        "restart_daemon": False,
        "restart_reason": "",
        "waiting": False,
        "waiting_reason": "",
        "new_tasks": [
            task("literature", [], "literature search", "Find and assess relevant prior results"),
            task("experiment", [], "computational experiment", "Search examples and counterexamples"),
            task(
                "proof",
                ["literature", "experiment"],
                "proof construction",
                "Use the grounded evidence to construct or refute the conjecture",
            ),
            task(
                "review",
                ["proof"],
                "independent proof review",
                "Audit statement fidelity, proof correctness, and remaining uncertainty",
            ),
        ],
    })
    sup = _make_supervisor(tmp_path, monkeypatch, verdict)

    assert sup._plan_next_work() is True
    items = {item.title: item for item in sup.memory.backlog.all()}
    assert set(items) == {
        "literature search",
        "computational experiment",
        "proof construction",
        "independent proof review",
    }
    assert items["proof construction"].deps == [
        items["literature search"].id,
        items["computational experiment"].id,
    ]
    assert items["independent proof review"].deps == [items["proof construction"].id]
    assert {item.title for item in sup.memory.backlog.ready()} == {
        "literature search",
        "computational experiment",
    }


def test_planner_events_carry_manager_intent_context(tmp_path, monkeypatch) -> None:
    sup = _make_supervisor(tmp_path, monkeypatch, _dag_verdict_json())
    intent = {
        "intent_id": "intent-1",
        "source": "user",
        "objective": "study B200 skill",
        "execution_task": "study the B200 skill artifacts",
        "continuous_generation": 7,
        "vertical": "learning",
        "kind": "custom",
        "stages": ["ingest", "study"],
        "reason": "manager routed to learning",
    }
    sup.memory.root.mkdir(parents=True, exist_ok=True)
    (sup.memory.root / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": "x", "generation": 7}),
        encoding="utf-8",
    )
    (sup.memory.root / "events.jsonl").write_text(
        json.dumps({"type": "life.manager.intent.completed", **intent}) + "\n",
        encoding="utf-8",
    )

    assert sup._plan_next_work() is True

    events = sup._test_sink.events  # type: ignore[attr-defined]
    planner_start = next(e for e in events if e["type"] == "life.planner.start")
    task_added = next(e for e in events if e["type"] == "life.planner.task_added")
    verdict = next(e for e in events if e["type"] == "life.planner.verdict")
    assert planner_start["manager_intent"]["vertical"] == "learning"
    assert task_added["manager_intent"]["intent_id"] == "intent-1"
    assert verdict["manager_intent"]["reason"] == "manager routed to learning"
    block = sup._manager_intent_prompt_block(planner_start["manager_intent"])
    assert "execution_objective: study the B200 skill artifacts" in block
    assert "study B200 skill" not in block


def test_planner_ignores_newer_uncompleted_manager_intent(
    tmp_path, monkeypatch,
) -> None:
    sup = _make_supervisor(tmp_path, monkeypatch, _dag_verdict_json())
    sup.memory.root.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "type": "life.manager.intent.completed",
            "intent_id": "completed",
            "objective": "raw completed request",
            "execution_task": "clean completed handoff",
            "continuous_generation": 8,
        },
        {
            "type": "life.manager.intent.started",
            "intent_id": "in-flight",
            "objective": "raw in-flight request; Manager owns the sidebar",
        },
    ]
    (sup.memory.root / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (sup.memory.root / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": "x", "generation": 8}),
        encoding="utf-8",
    )

    intent = sup._manager_intent_context()
    assert intent["intent_id"] == "completed"
    block = sup._manager_intent_prompt_block(intent)
    assert "clean completed handoff" in block
    assert "raw in-flight request" not in block


def test_planner_ignores_legacy_completed_intent_without_execution_task(
    tmp_path, monkeypatch,
) -> None:
    sup = _make_supervisor(tmp_path, monkeypatch, _dag_verdict_json())
    sup.memory.root.mkdir(parents=True, exist_ok=True)
    (sup.memory.root / "events.jsonl").write_text(
        json.dumps({
            "type": "life.manager.intent.completed",
            "intent_id": "legacy",
            "objective": "raw request; Manager owns the sidebar",
        }) + "\n",
        encoding="utf-8",
    )

    assert sup._manager_intent_context() == {}


def test_dag_topological_claim_order(tmp_path, monkeypatch) -> None:
    sup = _make_supervisor(tmp_path, monkeypatch, _dag_verdict_json())
    assert sup._plan_next_work() is True
    backlog = sup.memory.backlog

    items = {it.title: it for it in backlog.all()}
    c = items["analyze"]

    # First two claims hand out the parallel runs; c is gated.
    first = backlog.claim_next()
    second = backlog.claim_next()
    assert {first.title, second.title} == {"run seed 0", "run seed 1"}
    assert backlog.claim_next() is None  # c blocked (no dep done)

    # Finishing one dep is not enough.
    backlog.mark_done(first.id)
    assert backlog.claim_next() is None

    # Both deps done → c becomes claimable.
    backlog.mark_done(second.id)
    third = backlog.claim_next()
    assert third is not None and third.id == c.id


def test_flat_verdict_enqueues_with_empty_deps(tmp_path, monkeypatch) -> None:
    # Back-compat: a verdict with no key/deps yields plain flat items, scheduled
    # exactly as before the DAG existed.
    flat = json.dumps({
        "project_done": False,
        "reason": "two independent fixes",
        "restart_daemon": False,
        "restart_reason": "",
        "waiting": False,
        "waiting_reason": "",
        "new_tasks": [
            {
                "title": "fix loader",
                "impact_score": 5,
                "impact_area": "correctness",
                "evidence": "loader crashes on empty input",
                "scope": "bounded",
                "objective": "patch code/loader.py and add a regression test",
            },
            {
                "title": "fix writer",
                "impact_score": 5,
                "impact_area": "correctness",
                "evidence": "writer drops the last row",
                "scope": "bounded",
                "objective": "patch code/writer.py and add a regression test",
            },
        ],
    })
    sup = _make_supervisor(tmp_path, monkeypatch, flat)
    assert sup._plan_next_work() is True

    items = sup.memory.backlog.all()
    assert {it.title for it in items} == {"fix loader", "fix writer"}
    assert all(it.deps == [] for it in items)
    # Both are immediately ready (no gating).
    assert {it.title for it in sup.memory.backlog.ready()} == {"fix loader", "fix writer"}


def test_unresolved_dep_key_is_dropped_not_wedged(tmp_path, monkeypatch) -> None:
    # A task referencing a key not defined in this batch must enqueue with the
    # bad key dropped (so it is not wedged forever on a dead dependency).
    bad = json.dumps({
        "project_done": False,
        "reason": "one good dep, one stray key",
        "restart_daemon": False,
        "restart_reason": "",
        "waiting": False,
        "waiting_reason": "",
        "new_tasks": [
            {
                "key": "a",
                "deps": [],
                "title": "produce input",
                "impact_score": 5,
                "impact_area": "reliability",
                "evidence": "upstream artifact",
                "scope": "bounded",
                "objective": "write data/input.tsv",
            },
            {
                "key": "c",
                "deps": ["a", "ghost"],
                "title": "consume input",
                "impact_score": 5,
                "impact_area": "reliability",
                "evidence": "needs upstream",
                "scope": "bounded",
                "objective": "read data/input.tsv; write out/result.txt",
            },
        ],
    })
    sup = _make_supervisor(tmp_path, monkeypatch, bad)
    assert sup._plan_next_work() is True

    items = {it.title: it for it in sup.memory.backlog.all()}
    a = items["produce input"]
    c = items["consume input"]
    # Only the resolvable dep survives; "ghost" is dropped.
    assert c.deps == [a.id]
