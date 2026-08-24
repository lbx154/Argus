from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

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
                "target_stage": "submission",
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
    assert "Shortfall type: optimization" in stored.objective
    assert "What this cycle buys:" in stored.objective
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


@pytest.mark.parametrize("integrity_problem", ["contaminated", "stale"])
def test_untrusted_measurement_does_not_rearm(
    tmp_path: Path,
    integrity_problem: str,
) -> None:
    supervisor, memory, item, _sink, project = _supervisor(tmp_path, _result())
    if integrity_problem == "contaminated":
        training = project / "data/train.jsonl"
        evaluation = project / "data/eval.jsonl"
        training.parent.mkdir(parents=True)
        training.write_text('{"prompt_id": 7}\n', encoding="utf-8")
        evaluation.write_text('{"prompt_id": 7}\n', encoding="utf-8")
        assessment = project / "paper/PUBLICATION_SCALE_ASSESSMENT.json"
        assessment.parent.mkdir(parents=True)
        assessment.write_text(
            json.dumps({
                "claim_bearing_evidence": [{
                    "role": "primary",
                    "training_artifacts": ["data/train.jsonl"],
                    "evaluation_artifact": "data/eval.jsonl",
                }]
            }),
            encoding="utf-8",
        )
    else:
        manuscript = project / "paper/main.tex"
        manuscript.parent.mkdir(parents=True)
        manuscript.write_text("changed manuscript\n", encoding="utf-8")
        review = project / "analysis/final_review.json"
        review.parent.mkdir(parents=True)
        review.write_text(
            json.dumps({
                "source_snapshots": [{
                    "path": "paper/main.tex",
                    "sha256": hashlib.sha256(b"old manuscript\n").hexdigest(),
                }]
            }),
            encoding="utf-8",
        )

    outcome = supervisor.tick()

    assert outcome is not None
    assert outcome["iteration"]["status"] == "measurement_blocked"
    assert outcome["overall_complete"] is False
    issue_text = " ".join(outcome["iteration"]["blocking_issues"])
    assert ("contamination" if integrity_problem == "contaminated" else "stale") in issue_text
    stored = _stored(memory, item.id)
    assert stored.status == "done"
    assert stored.iteration_cycles_done == 0
    assert stored.objective == "Submit the completed result."


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
