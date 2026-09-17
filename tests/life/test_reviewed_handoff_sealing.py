"""Regression test: the round loop must SEAL the Reviewer half of the packet.

``record_engineer_handoff`` has always been wired (round_execution.py), so
``round-NNNN-engineer.json`` appeared on disk. ``record_reviewed_handoff`` —
which writes ``round-NNNN.json`` with ``kind="round_reviewed_handoff"`` and
``producer_role="reviewer"`` — had **zero production callers**. Two things
were dead as a result:

1. ``_latest_unassessed_review_for_current_stage`` (life/supervisor) scans for
   exactly that file, so the framework's only non-circular campaign-level
   stage-advance trigger could never fire. No mission in any vertical whose
   completion gate is not ``certified`` could leave its first stage.
2. ``TaskFrontier.apply`` is reached only from ``record_reviewed_handoff``, so
   the Reviewer's frontier reports never advanced the persisted frontier.

The seal is gated on ``review_source == "reviewer"``: the packet declares
itself independent evidence, so an Engineer self-review must never produce
one, or the Engineer would certify its own stage transition.

Citations:
- argus/engineer/round_settlement.py — the seal
- argus/life/context_packet.py — ``record_reviewed_handoff``
- argus/engineer/round_self_review.py — the self-review ``_settle_round``
  caller whose verdicts carry ``review_source="engineer_self_review"``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from argus.core.models import ReviewDecision, RunnerResult
from argus.core.task_frontier import (
    TaskFrontier,
    load_task_frontier,
    save_task_frontier,
)
from argus.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus.life.context_packet import FRONTIER_FILENAME
from argus.reviewer import ReviewerConfig


class _StubEngineer:
    def run_exec(self, **kwargs):  # noqa: D401, ANN003
        return RunnerResult(
            exit_code=0,
            agent_messages=["engineer: proved the mod 8 branch"],
        )


class _StubReviewer:
    """Reviewer stub returning a verdict with a caller-chosen provenance."""

    def __init__(self, review_source: str, frontier_report: dict | None = None) -> None:
        self._review_source = review_source
        self._frontier_report = frontier_report or {}

    def evaluate(self, **_kwargs) -> ReviewDecision:
        return ReviewDecision(
            status="done",
            reason="The mod 8 branch is established and checked.",
            next_action="",
            review_source=self._review_source,
            frontier_report=dict(self._frontier_report),
        )


def _make_supervised(reviewer) -> SupervisedEngineer:
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = _StubEngineer()
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = reviewer
    se.reviewer_config = ReviewerConfig(model="stub")
    return cast(SupervisedEngineer, se)


def _run_one_round(
    tmp_path: Path,
    reviewer,
    *,
    context_packet_path: str | None = None,
) -> Path:
    """Drive exactly one supervised round; return the mission packet root."""
    mission_root = tmp_path / "mission"
    mission_root.mkdir(parents=True, exist_ok=True)
    mission_context = mission_root / "context.json"
    mission_context.write_text(
        json.dumps({"kind": "mission_context", "mission_id": "mission"}),
        encoding="utf-8",
    )
    workdir = tmp_path / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    _make_supervised(reviewer).run(
        objective="prove p^2 = 1 mod 24",
        engineer_prompt_builder=lambda na, _include_static=True: "PROMPT",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            context_packet_path=(
                str(mission_context)
                if context_packet_path is None
                else context_packet_path
            ),
        ),
        workdir=workdir,
        on_event=None,
    )
    return mission_root


def test_independent_reviewer_verdict_seals_the_reviewed_handoff(
    tmp_path: Path,
) -> None:
    root = _run_one_round(tmp_path, _StubReviewer("reviewer"))

    sealed = root / "round-0001.json"
    assert sealed.exists(), (
        "the Reviewer half of the round packet was not sealed; "
        "campaign-level stage reconciliation has no evidence to replay. "
        f"present: {sorted(p.name for p in root.iterdir())}"
    )
    payload = json.loads(sealed.read_text(encoding="utf-8"))
    assert payload["kind"] == "round_reviewed_handoff"
    assert payload["producer_role"] == "reviewer"
    assert payload["round"] == 1
    assert payload["review"]["status"] == "done"

    # The Engineer half is unchanged — the two seals are symmetric, not
    # alternatives.
    assert (root / "round-0001-engineer.json").exists()


def test_engineer_self_review_never_seals_a_reviewed_handoff(
    tmp_path: Path,
) -> None:
    root = _run_one_round(tmp_path, _StubReviewer("engineer_self_review"))

    assert not (root / "round-0001.json").exists(), (
        "a self-review was sealed as independent Reviewer evidence; the "
        "Engineer would be able to certify its own stage transition"
    )
    # The Engineer handoff still lands: only the reviewed seal is withheld.
    assert (root / "round-0001-engineer.json").exists()


def test_sealing_is_skipped_without_a_mission_packet(tmp_path: Path) -> None:
    root = _run_one_round(tmp_path, _StubReviewer("reviewer"), context_packet_path="")

    assert not (root / "round-0001.json").exists()
    assert not (root / "round-0001-engineer.json").exists()


def test_reviewer_frontier_report_advances_the_persisted_frontier(
    tmp_path: Path,
) -> None:
    """The seal is also the only site that ever applies a frontier report."""
    mission_root = tmp_path / "mission"
    mission_root.mkdir(parents=True, exist_ok=True)
    frontier_path = mission_root / FRONTIER_FILENAME
    save_task_frontier(
        frontier_path,
        TaskFrontier.initial(
            mission_id="mission",
            objective="prove p^2 = 1 mod 24",
            remaining_work=["mod 8 branch", "mod 3 branch"],
        ),
    )
    before = load_task_frontier(frontier_path)
    assert before is not None and before.transition_count == 0

    root = _run_one_round(
        tmp_path,
        _StubReviewer(
            "reviewer",
            frontier_report={
                "change": "risk_reduced",
                "summary": "mod 8 branch discharged",
                "resolved_obligations": ["mod 8 branch"],
                "remaining_work": ["mod 3 branch"],
            },
        ),
    )
    assert root == mission_root

    payload = json.loads((root / "round-0001.json").read_text(encoding="utf-8"))
    assert payload["review"].get("frontier_transition"), (
        "the Reviewer's frontier report was discarded; TaskFrontier.apply is "
        "reachable only from the reviewed seal"
    )
    after = load_task_frontier(frontier_path)
    assert after is not None
    assert after.transition_count == 1
    assert after.resolved_obligations == ["mod 8 branch"]
    assert after.remaining_work == ["mod 3 branch"]
