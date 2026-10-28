"""A delivery's files are the ones the Engineer's hand_off declared, never words picked from its text.

Replays two settlements of s-efe9deac (trial-1023). In 17957d783e27 (10-26)
the Engineer wrote "主要交付：README.md…、METHOD.md 当前逐字副本…" about files
inside its package; the host read the shared root METHOD.md out of that text and
recorded it as the primary target with source ``reviewer_evidence``. In
99d44fc3006a (10-24) the only target, docs/acl-paper-plan.md, came from "…已明确…
无需修改".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from argus.engineer.round_routing import engineer_actions
from argus.engineer.runner import SupervisedConfig
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from tests.hand_off import hand_off

RECORDS = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "delivery_replay" / "s-efe9deac.json").read_text(encoding="utf-8")
)
PACKAGE = "results/stage_packages/experiment-001"
DECLARED = [f"{PACKAGE}/README.md", f"{PACKAGE}/length_comparison.json", f"{PACKAGE}/METHOD.md"]


class _Runner:
    class manager:  # noqa: N801 - the supervisor binds the Manager it is handed
        @staticmethod
        def bind_execution_workdir(_workdir):
            return None


def _settle(tmp_path: Path, record: dict, *, deliverables=(), review_source="reviewer", tags=None) -> dict:
    """Settle one task on its recorded Engineer handoff; returns its ``life.mission.completed``."""

    @dataclass
    class _Outcome:
        success: bool = True
        status: str = "done"
        stop_reason: str = ""
        stop_kind: str | None = None
        recoverable: bool = False
        rounds: int = 1
        final_review_status: str = "done"
        final_review_source: str = review_source
        final_review_reason: str = record["summary"]
        final_residual_risk: str = ""
        final_message: str = record["final_output"]
        summary: str = record["final_output"]
        final_output: str = record["final_output"]

    life = tmp_path / "life"
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(life), runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=life, verbosity="full"),
        config=LifeSupervisorConfig(budget=LifeBudget(max_missions=1), poll_interval_seconds=0.01),
    )
    workdir = supervisor._project_workdir()
    for path in ["METHOD.md", "docs/acl-paper-plan.md", *DECLARED, *record["recorded_targets"]]:
        (workdir / path).parent.mkdir(parents=True, exist_ok=True)
        (workdir / path).write_text("recorded file\n", encoding="utf-8")
    supervisor.memory.backlog.add(BacklogItem.new(
        title=record["title"], objective=record["title"], tags=record["tags"] if tags is None else tags,
    ))
    outcome = _Outcome()
    outcome.deliverables = list(deliverables)
    supervisor.runner.execute = lambda **_kwargs: outcome
    supervisor.tick()
    events = [json.loads(line) for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return next(event for event in events if event.get("type") == "life.mission.completed")


@pytest.mark.parametrize("item_id", ["17957d783e27", "99d44fc3006a"])
def test_files_the_handoff_text_names_are_not_delivered(tmp_path: Path, item_id: str) -> None:
    record = RECORDS[item_id]

    completed = _settle(tmp_path, record)

    # The text names files that exist, but none was declared: no receipt.
    assert completed["success"] is True
    assert completed["delivery"] is None and completed["delivery_candidates"] == []


def test_the_declared_package_is_the_delivery_the_reviewer_accepted(tmp_path: Path) -> None:
    completed = _settle(tmp_path, RECORDS["17957d783e27"], deliverables=DECLARED)

    delivery = completed["delivery"]
    assert delivery["primary_target"]["path"] == f"{PACKAGE}/README.md"
    assert [target["path"] for target in delivery["targets"]] == DECLARED
    assert {target["source"] for target in delivery["targets"]} == {"reviewer_accepted"}
    assert completed["delivery_candidates"] == DECLARED


def test_work_the_engineer_accepted_itself_is_delivered_as_its_declaration(tmp_path: Path) -> None:
    completed = _settle(
        tmp_path, RECORDS["17957d783e27"], deliverables=DECLARED[:1],
        review_source="engineer_self_review", tags=["manager_direct", "scope:bounded"],
    )

    assert completed["delivery"]["targets"] == [{
        "path": f"{PACKAGE}/README.md", "label": "README.md", "source": "engineer_declared",
        "why": "Declared by the Engineer; no independent review accepted it.",
    }]


def test_the_engineer_declares_existing_workspace_files_when_it_hands_work_on(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / PACKAGE).mkdir(parents=True)
    (tmp_path / "outside.md").write_text("not this workspace\n", encoding="utf-8")
    actions = engineer_actions(SupervisedConfig(require_independent_review=True), workspace)
    note = RECORDS["17957d783e27"]["final_output"]

    with pytest.raises(ValueError, match="files"):
        actions.dispatch("hand_off", {"to": "reviewer", "summary": "阶段包已交付。", "note": note, "assumptions": []})
    for paths in ([f"{PACKAGE}/README.md"], ["../outside.md"], [str(tmp_path / "outside.md")], [PACKAGE]):
        with pytest.raises(ValueError, match="not a file in the workspace"):
            actions.dispatch(*hand_off("reviewer", "阶段包已交付。", note, files=paths))
    (workspace / PACKAGE / "README.md").write_text("# 阶段包\n", encoding="utf-8")

    actions.dispatch(*hand_off("reviewer", "阶段包已交付。", note, files=[f"{PACKAGE}/README.md"]))

    assert actions.handoff.get("files") == [f"{PACKAGE}/README.md"]
    # Work the Engineer keeps names no files.
    actions.dispatch(*hand_off("engineer", "继续。", note))
