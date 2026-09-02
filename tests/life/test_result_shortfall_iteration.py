from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus_skill.skills.vertical_select import persist_vertical


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    final_review_status: str = "done"
    final_review_source: str = "reviewer"
    final_review_reason: str = "The submitted artifact satisfies its checklist."
    final_submission_certified: bool = True
    research_result: dict[str, Any] | None = None
    stage_transition: dict[str, Any] = field(default_factory=dict)


class _Runner:
    backend = None

    def __init__(self, research_result: dict[str, Any]) -> None:
        self.research_result = research_result

    def execute(self, *, sink: Any, **kwargs: Any) -> _Outcome:
        sink.handle_event({
            "type": "round.main.completed",
            "input_tokens": 1_000,
            "cached_input_tokens": 0,
            "output_tokens": 100,
        })
        sink.handle_event({
            "type": "round.review.completed",
            "input_tokens": 500,
            "cached_input_tokens": 0,
            "output_tokens": 100,
        })
        falls_short = self.research_result["significance_status"] != "publishable"
        return _Outcome(
            research_result=self.research_result,
            stage_transition={
                "action": "hold" if falls_short else "complete",
                "target_stage": "review",
                "reason": (
                    "chartered result remains below target"
                    if falls_short
                    else "chartered result is certified"
                ),
            },
        )


def _result(
    *,
    result_class: str = "structured_failure_report",
    novelty: str = "not_applicable",
    significance: str = "exploratory",
) -> dict[str, Any]:
    return {
        "result_class": result_class,
        "correctness_status": "verified",
        "novelty_status": novelty,
        "significance_status": significance,
        "statement_fidelity_status": "verified",
        "evidence": ["method scored 0.792; chartered baseline scored 0.812"],
        "limitations": ["optimization has not closed the 0.020 score gap"],
    }


def _supervisor(
    tmp_path: Path,
    research_result: dict[str, Any],
    *,
    max_cycles: int = 6,
) -> tuple[LifeSupervisor, LifeMemory, BacklogItem, _Sink, Path]:
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(
        project,
        "research",
        research_target_level="publishable",
        workflow_mode="staged",
    )
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "review"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    memory = LifeMemory.open(tmp_path / "life")
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(research_result),
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=project,
            artifact_root=project,
        ),
    )
    item = memory.backlog.add(BacklogItem.new(
        title="certify the chartered result",
        objective="Submit the completed result.",
        tags=["planner", "review:required", "scope:final_submission"],
        manager_decision={
            "routed": True,
            "vertical": "research",
            "workflow_mode": "staged",
        },
        iteration_max_cycles=max_cycles,
    ))
    return supervisor, memory, item, sink, project


def _stored(memory: LifeMemory, item_id: str) -> BacklogItem:
    return next(item for item in memory.backlog.all() if item.id == item_id)


def test_fell_short_result_rearms_same_item_with_specific_objective(tmp_path: Path) -> None:
    supervisor, memory, item, sink, _project = _supervisor(tmp_path, _result())

    outcome = supervisor.tick()

    assert outcome is not None
    assert outcome["iteration"]["requeued"] is True
    assert outcome["overall_complete"] is False
    stored = _stored(memory, item.id)
    assert stored.status == "pending"
    assert stored.iteration_cycles_done == 1
    assert stored.iteration_cost_usd > 0
    assert "0.792" in stored.objective
    assert "0.812" in stored.objective
    assert "Keep the pipeline in Review" in stored.objective
    assert "Blocking issue:" in stored.objective
    assert stored.original_objective == "Submit the completed result."
    assert any(
        event.get("type") == "life.iteration.continued"
        for event in sink.events
    )


def test_iteration_budget_exhaustion_settles_with_visible_reason(tmp_path: Path) -> None:
    supervisor, memory, item, _sink, _project = _supervisor(
        tmp_path, _result(), max_cycles=1
    )
    memory.backlog.update(
        item.id,
        iteration_cycles_done=1,
        iteration_cost_usd=4.25,
    )

    outcome = supervisor.tick()

    assert outcome is not None
    assert outcome["iteration"]["status"] == "budget_exhausted"
    assert outcome["overall_complete"] is False
    assert "iteration budget ran out" in outcome["iteration"]["stop_reason"]
    stored = _stored(memory, item.id)
    assert stored.status == "done"
    assert stored.iteration_cycles_done == 1
    assert stored.iteration_cost_usd == 4.25
    assert "iteration budget ran out" in stored.outcome["iteration"]["stop_reason"]


def test_genuine_success_settles_without_iteration(tmp_path: Path) -> None:
    supervisor, memory, item, _sink, _project = _supervisor(
        tmp_path,
        _result(
            result_class="verified_new_result",
            novelty="verified_new",
            significance="publishable",
        ),
    )

    outcome = supervisor.tick()

    assert outcome is not None
    assert outcome["iteration"] is None
    assert outcome["overall_complete"] is True
    stored = _stored(memory, item.id)
    assert stored.status == "done"
    assert stored.iteration_cycles_done == 0


def test_settlement_layer_imports_no_named_vertical() -> None:
    path = (
        Path(__file__).parents[2]
        / "argus_skill/life/supervisor/_mission_execution_settlement.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any("verticals.research" in module for module in imported_modules)
