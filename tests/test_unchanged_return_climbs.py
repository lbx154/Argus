"""An unchanged return climbs (docs/role-handoff-design.md, "The baton", invariant 3).

Work that would go back to a holder that already held it as it is now, with no
new operator or Manager input since, is not handed to that model again: it goes
one rung up the ladder Engineer -> Reviewer -> Planner -> Manager -> operator.
"Unchanged" is read from the workspace snapshots and the operator ledger, on
the task's lineage, so a new mission on the same work does not reset it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.apps._inbox import queue_inbox_message
from argus.core.operator_context import append_directive
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.life.context_packet import create_mission_context, render_mission_brief
from argus.life.memory import Backlog, BacklogItem
from argus.reviewer import Reviewer, ReviewerConfig


def _write(workdir: Path, text: str) -> CannedResponse:
    """An Engineer turn that changes the workspace, then hands it to the Reviewer."""

    def turn(_prompt, _options) -> str:
        (workdir / "parser.py").write_text(text, encoding="utf-8")
        return "Changed the parser.\n→ reviewer"

    return CannedResponse(message_factory=turn)


def _review(line: str) -> CannedResponse:
    return CannedResponse(review_action=("submit_review", {"review": f"Judged the parser.\n{line}"}))


def _run(
    tmp_path: Path, backend: MemoryBackend, *, snapshots: bool = True, max_rounds: int = 6,
    packet: Path | None = None,
):
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    status, rounds, _final, _reason, _tid = engineer.run(
        objective="repair the parser",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=max_rounds,
            state_root=(tmp_path / "state") if snapshots else None,
            session_id="parser-task",
            context_packet_path=str(packet or ""),
        ),
        workdir=workdir,
    )
    return status, rounds, [label for label, _prompt, _options in backend.history]


def _state(tmp_path: Path) -> Path:
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "work").mkdir(exist_ok=True)
    return tmp_path / "state"


def test_a_turn_that_changed_nothing_goes_to_the_reviewer_not_the_engineer(tmp_path: Path) -> None:
    _state(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Thinking it over.\n→ engineer"))
    backend.queue("reviewer", _review("→ done"))

    status, _rounds, labels = _run(tmp_path, backend)

    assert labels == ["engineer-r1", "reviewer"]
    assert status == "done"


def test_an_unchanged_candidate_is_not_reviewed_again(tmp_path: Path) -> None:
    _state(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    backend.queue("reviewer", _review("→ engineer"))
    backend.queue("engineer-r2", CannedResponse(message="Looked again; it holds.\n→ reviewer"))

    status, rounds, labels = _run(tmp_path, backend)

    assert labels == ["engineer-r1", "reviewer", "engineer-r2"]
    assert status == "replan_requested"
    climbed = rounds[-1].review
    assert (climbed.review_source, climbed.host_placeholder) == ("unchanged_return", "unchanged_return")
    assert "reviewer" in climbed.reason and "planner" in climbed.reason


def test_a_review_the_engineer_has_not_read_is_new_input(tmp_path: Path) -> None:
    _state(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Read the parser; nothing to change.\n→ reviewer"))
    backend.queue("reviewer", _review("The empty-date case still fails.\n→ engineer"))
    backend.queue("engineer-r2", CannedResponse(message="Looked again; it holds.\n→ reviewer"))

    status, rounds, labels = _run(tmp_path, backend)

    # The Engineer reads the review once; returning the work unchanged again
    # passes the Reviewer, who already judged it, and reaches the Planner.
    assert labels == ["engineer-r1", "reviewer", "engineer-r2"]
    assert status == "replan_requested"
    assert rounds[-1].review.review_source == "unchanged_return"
    assert "reviewer" in rounds[-1].review.reason


def test_a_changed_candidate_is_new_work(tmp_path: Path) -> None:
    _state(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    backend.queue("engineer-r2", _write(tmp_path / "work", "v2"))
    backend.queue("reviewer", _review("→ engineer"), _review("→ done"))

    status, _rounds, labels = _run(tmp_path, backend)

    assert labels == ["engineer-r1", "reviewer", "engineer-r2", "reviewer"]
    assert status == "done"


@pytest.mark.parametrize("arrives", [
    lambda state: append_directive(state, "Keep the old date format.", expected_revision=0),
    lambda state: queue_inbox_message(state, "Keep the old date format.", source="operator"),
], ids=["directive", "note"])
def test_new_operator_input_is_new_work(tmp_path: Path, arrives) -> None:
    state = _state(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", _write(tmp_path / "work", "v1"))

    def operator_writes(_prompt, _options) -> str:
        arrives(state)
        return "Nothing to change for that.\n→ reviewer"

    backend.queue("engineer-r2", CannedResponse(message_factory=operator_writes))
    backend.queue("reviewer", _review("→ engineer"), _review("→ done"))

    status, _rounds, labels = _run(tmp_path, backend)

    assert labels == ["engineer-r1", "reviewer", "engineer-r2", "reviewer"]
    assert status == "done"


def test_a_new_mission_on_the_same_work_does_not_reset_the_count(tmp_path: Path) -> None:
    """s-757b9dac: the Engineer found two rounding rules, changed nothing and
    handed the task to the Planner. The same task ran again unchanged and went
    straight to a Reviewer, who judged an implementation that did not exist."""
    _state(tmp_path)
    first = MemoryBackend()
    first.queue("engineer-r1", CannedResponse(message="Two rounding rules conflict.\n→ planner"))
    assert _run(tmp_path, first)[0] == "replan_requested"

    # The work comes back unchanged: the Engineer's line still names the
    # Planner, who already held it, so it climbs to the Manager. No model runs.
    status, rounds, labels = _run(tmp_path, MemoryBackend())

    assert labels == []
    assert status == "blocked"
    (record,) = rounds
    assert record.review.review_source == "unchanged_return"
    assert "the engineer and the planner" in record.review.reason
    assert "to the manager" in record.review.reason


def test_the_brief_says_where_the_engineer_handed_the_work(tmp_path: Path) -> None:
    """The next role read s-757b9dac's `→ planner` turn as work awaiting review."""
    state = _state(tmp_path)
    packet = create_mission_context(life_dir=state, mission_id="parser-1", stage="", objective="repair the parser")
    first = MemoryBackend()
    first.queue("engineer-r1", CannedResponse(message="Two rounding rules conflict.\n→ planner"))
    _run(tmp_path, first, packet=packet)

    brief = render_mission_brief(packet)

    assert "awaiting review" not in brief and "Unreviewed" not in brief
    assert "- Latest Engineer work (→ planner): `" in brief
    assert "- Engineer work (→ planner): Two rounding rules conflict." in brief


def test_only_a_handoff_to_the_reviewer_resumes_at_review(tmp_path: Path) -> None:
    _state(tmp_path)
    first = MemoryBackend()
    first.queue("engineer-r1", CannedResponse(message="It already holds.\n→ reviewer"))
    # The daemon stops before the Reviewer reads it.
    first.queue("reviewer", CannedResponse(exit_code=1, fatal_error="daemon stop requested"))
    assert _run(tmp_path, first)[0] == "parked"

    second = MemoryBackend()
    second.queue("reviewer", _review("→ done"))
    status, _rounds, labels = _run(tmp_path, second)

    assert labels == ["reviewer"]
    assert status == "done"


def test_an_unchanged_rerun_of_accepted_work_keeps_its_acceptance(tmp_path: Path) -> None:
    _state(tmp_path)
    first = MemoryBackend()
    first.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    first.queue("reviewer", _review("→ done"))
    assert _run(tmp_path, first)[0] == "done"

    status, rounds, labels = _run(tmp_path, MemoryBackend())

    # No model is called, nothing climbs: the Reviewer's pass on this very
    # candidate stands, and the record names the verdict it reused.
    assert labels == []
    assert status == "done"
    (record,) = rounds
    assert (record.review.status, record.review.reason) == ("done", "Judged the parser.")
    (reused,) = record.review.interventions
    assert reused["action"] == "reused_verdict"
    assert "accepted it in round 1 of parser-task" in reused["note"]


@pytest.mark.parametrize("refuse", [
    lambda backlog: backlog.mark_failed("parser-1", error="The Manager held the stage."),
    lambda backlog: backlog.requeue_for_iteration(
        "parser-1", new_objective="Repair the parser fully.", cost_delta_usd=0.0,
    ),
], ids=["failed", "sent-back"])
def test_an_acceptance_the_host_refused_is_not_reused(tmp_path: Path, refuse) -> None:
    state = _state(tmp_path)
    backlog = Backlog(state / "backlog.jsonl")
    backlog.add(BacklogItem.new(item_id="parser-1", title="Repair the parser", objective="repair the parser"))
    packet = create_mission_context(
        life_dir=state, mission_id="parser-1", stage="", objective="repair the parser",
    )
    first = MemoryBackend()
    first.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    first.queue("reviewer", _review("→ done"))
    assert _run(tmp_path, first, packet=packet)[0] == "done"
    refuse(backlog)

    second = MemoryBackend()
    second.queue("engineer-r1", CannedResponse(message="Nothing to change.\n→ reviewer"))
    status, rounds, labels = _run(tmp_path, second, packet=packet)

    # The host did not take the acceptance, so it does not stand again; the
    # Reviewer already judged this very candidate, so the work climbs past it.
    assert labels == ["engineer-r1"]
    assert status == "replan_requested"
    climbed = rounds[-1].review
    assert climbed.review_source == "unchanged_return"
    assert "reviewer" in climbed.reason and "planner" in climbed.reason


def test_the_top_rung_is_the_operator(tmp_path: Path) -> None:
    _state(tmp_path)
    first = MemoryBackend()
    first.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    first.queue("reviewer", _review("→ planner"))
    _run(tmp_path, first)
    second = MemoryBackend()
    second.queue("engineer-r1", CannedResponse(message="Nothing to change.\n→ reviewer"))
    _run(tmp_path, second)

    # Every holder below the operator has held this work as it is.
    status, rounds, labels = _run(tmp_path, MemoryBackend())

    assert labels == []
    assert status == "blocked"
    assert rounds[-1].review.operator_question
    assert "engineer and the reviewer and the planner and the manager" in rounds[-1].review.reason


def test_without_snapshots_nothing_climbs(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Thinking.\n→ engineer"))
    backend.queue("engineer-r2", CannedResponse(message="Done thinking.\n→ reviewer"))
    backend.queue("reviewer", _review("→ done"))

    status, _rounds, labels = _run(tmp_path, backend, snapshots=False)

    assert labels == ["engineer-r1", "engineer-r2", "reviewer"]
    assert status == "done"
