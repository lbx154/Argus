"""Reviewer carry-over: its own findings only, its own thread only."""
from __future__ import annotations

import json
from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import ReviewDecision, RoundRecord
from argus.core.role_session import RoleSessionCapsule
from argus.engineer.reviewer_findings import (
    own_reviewer_thread,
    render_previous_findings,
)
from argus.engineer.round_reviewer import _previous_review_summary
from argus.engineer.round_state import RoundLoopState
from argus.engineer.round_stop_signals import (
    backend_failure_review_decision,
    idle_termination_review_decision,
    provider_turn_cap_review_decision,
)
from argus.life.context_packet import render_mission_brief
from argus.reviewer import Reviewer

_FINDINGS = "## Your previous findings"
# Longer than the old 600-character cut, with a distinctive tail.
_FINDING = (
    "The total row overcounts ERROR: the CSV says 370 but the source logs hold 20. "
    + "The per-day rows were checked one by one against the logs. " * 12
    + "TAIL-OF-THE-FINDING"
)


def _context(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "state" / "handoffs" / "mission-1"
    root.mkdir(parents=True)
    context = root / "mission.json"
    context.write_text(
        json.dumps({"kind": "mission_context", "mission_id": "mission-1"}),
        encoding="utf-8",
    )
    checkpoint = root / "CHECKPOINT.md"
    checkpoint.write_text("# Open Questions / Blockers\n", encoding="utf-8")
    return context, checkpoint


def _run(
    tmp_path: Path,
    *,
    policy: str,
    engineer_threads: tuple[str, str] = ("e1", "e1"),
) -> tuple[MemoryBackend, list[dict], object]:
    work = tmp_path / "work"
    work.mkdir(parents=True)
    context, checkpoint = _context(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(
        message="Wrote summary.csv.", thread_id=engineer_threads[0],
    ))
    backend.queue("reviewer", CannedResponse(
        review_action=("revise_review", {"review": _FINDING}), thread_id="v1",
    ))
    backend.queue("engineer-r2", CannedResponse(
        message="Recounted all rows.", thread_id=engineer_threads[1],
    ))
    backend.queue("reviewer", CannedResponse(
        review_action=("approve_review", {"review": "All rows match."}),
        thread_id="v1",
    ))
    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="model",
            reviewer_model="model",
            max_rounds=3,
            backend_failure_backoff_seconds=0,
            context_packet_path=str(context),
            checkpoint_path=checkpoint,
            role_session_policy=policy,
        ),
        on_event=events.append,
    )
    outcome = loop.run("summarize the logs into summary.csv", workdir=work)
    return backend, events, outcome


def _reviewer_prompts(backend: MemoryBackend) -> list[str]:
    return [prompt for label, prompt, _ in backend.history if label == "reviewer"]


def _reviewer_resumes(backend: MemoryBackend) -> list[str | None]:
    return [thread for label, thread in backend.resume_history if label == "reviewer"]


def _record(index: int, review: ReviewDecision) -> RoundRecord:
    return RoundRecord(
        round_index=index, engineer_message="", engineer_exit_code=0, review=review,
    )


def _reviewer_judgment(reason: str) -> ReviewDecision:
    return ReviewDecision(
        status="continue", reason=reason, next_action=reason, independent_review=True,
    )


# --- carry-over -------------------------------------------------------------


def test_fresh_reviewer_gets_its_full_previous_findings_once(tmp_path: Path) -> None:
    backend, _events, outcome = _run(tmp_path, policy="fresh")

    assert outcome.successful
    assert _reviewer_resumes(backend) == [None, None]
    second = _reviewer_prompts(backend)[1]
    assert _FINDINGS in second
    assert "Your judgment in round 1 (`continue`):" in second
    # Complete, not cut at 600 characters.
    assert "TAIL-OF-THE-FINDING" in second
    # One copy: the shared-context line and MissionBrief lines are not repeated.
    assert second.count("the CSV says 370") == 1
    assert "previous_review_summary" not in second
    assert "- Previous review:" not in second
    assert "- Previously requested action:" not in second
    # The incremental boundary that refers to the judgment "below" still applies.
    assert "## Incremental re-review boundary" in second


def test_resumed_reviewer_gets_one_copy_and_no_unverifiable_thread_claim(
    tmp_path: Path,
) -> None:
    backend, _events, outcome = _run(tmp_path, policy="mission")

    assert outcome.successful
    assert _reviewer_resumes(backend) == [None, "v1"]
    second = _reviewer_prompts(backend)[1]
    assert second.count("the CSV says 370") == 1
    assert "TAIL-OF-THE-FINDING" in second
    # Whether the provider truly resumed is only known after the call, so the
    # prompt never claims the reasoning is already in the thread.
    assert "earlier in this thread" not in second


def test_first_round_review_is_unchanged(tmp_path: Path) -> None:
    backend, _events, outcome = _run(tmp_path, policy="mission")

    first = _reviewer_prompts(backend)[0]
    assert _FINDINGS not in first
    assert "previous_review_summary" not in first
    assert render_previous_findings([], round_index=1) == ""
    kw = dict(
        objective="o", operator_messages=[], planner_review_instruction="",
        round_index=1, session_id=None, main_summary="S", main_error=None,
    )
    reviewer = Reviewer(runner=None, skill_store=None)
    assert reviewer._build_prompt(**kw) == reviewer._build_prompt(previous_findings="", **kw)
    # Only the independent Reviewer call's own judgment is marked as a finding.
    assert outcome.rounds[0].review.independent_review is True


# --- provenance (host placeholders are never Reviewer findings) --------------


def _placeholders() -> dict[str, ReviewDecision]:
    return {
        "provider_turn_cap": provider_turn_cap_review_decision(
            fatal_error="provider turn cap", exit_code=1,
            wind_down_summary="ENGINEER-WIND-DOWN: all counts verified, ready to ship",
            streak=1, streak_limit=3,
        ),
        "backend_failure": backend_failure_review_decision(
            fatal_error="stream dropped", exit_code=1, streak=1, threshold=2,
        ),
        "silent_command": idle_termination_review_decision(
            fatal_error="idle limit reached", exit_code=1, streak=1, threshold=2,
        ),
    }


def test_host_placeholders_are_never_shown_as_reviewer_findings() -> None:
    for name, placeholder in _placeholders().items():
        assert placeholder.independent_review is False, name
        state = RoundLoopState(rounds=[_record(1, placeholder)])
        assert render_previous_findings(state.rounds, round_index=2) == "", name
        # Nor as the "settled" previous judgment the incremental boundary cites.
        assert _previous_review_summary(state) == "", name


def test_placeholders_after_a_real_review_do_not_displace_it() -> None:
    for name, placeholder in _placeholders().items():
        rounds = [
            _record(1, _reviewer_judgment("REAL-REVIEWER-FINDING")),
            _record(2, placeholder),
        ]
        findings = render_previous_findings(rounds, round_index=3)
        assert "Your judgment in round 1 (`continue`):" in findings, name
        assert "REAL-REVIEWER-FINDING" in findings, name
        assert placeholder.reason[:40] not in findings, name
        assert "ENGINEER-WIND-DOWN" not in findings, name
        summary = _previous_review_summary(RoundLoopState(rounds=rounds))
        assert summary.splitlines() == ["Round 1 — continue: REAL-REVIEWER-FINDING"], name


def test_self_reviews_are_not_reviewer_findings() -> None:
    self_review = ReviewDecision(
        status="done", reason="self check passed", next_action="",
        review_source="engineer_self_review",
    )
    assert render_previous_findings([_record(1, self_review)], round_index=2) == ""


# --- own thread only ----------------------------------------------------------


def test_reviewer_never_resumes_the_current_engineer_thread(tmp_path: Path) -> None:
    backend, events, outcome = _run(
        tmp_path, policy="mission", engineer_threads=("e1", "v1"),
    )

    assert outcome.successful
    assert _reviewer_resumes(backend) == [None, None]
    assert "TAIL-OF-THE-FINDING" in _reviewer_prompts(backend)[1]
    turns = [
        event for event in events
        if event.get("type") == "role.session.turn" and event.get("role") == "reviewer"
    ]
    assert turns[-1]["rotation_reason"] == "foreign_thread"


def test_reviewer_never_resumes_an_earlier_engineer_thread(tmp_path: Path) -> None:
    # Round 1's Engineer thread collides with the Reviewer's saved one; the
    # Engineer has since moved to another thread, and it is still refused.
    backend, events, outcome = _run(
        tmp_path, policy="mission", engineer_threads=("v1", "e2"),
    )
    assert outcome.successful
    assert _reviewer_resumes(backend) == [None, None]
    turns = [
        event for event in events
        if event.get("type") == "role.session.turn" and event.get("role") == "reviewer"
    ]
    assert turns[-1]["rotation_reason"] == "foreign_thread"


def test_engineer_thread_history_survives_a_capsule_reload(tmp_path: Path) -> None:
    path = tmp_path / "engineer.json"

    def capsule() -> RoleSessionCapsule:
        return RoleSessionCapsule.open(
            role="engineer", policy="mission", objective_revision="rev",
            workdir=tmp_path, backend="memory", model="m",
            checkpoint_path=None, path=path,
        )

    first = capsule()
    first.prepare(max_turns=0, max_input_tokens=0)
    first.complete(type("R", (), {"thread_id": "e1"})())
    first.rotate("turn_limit")
    first.prepare(max_turns=0, max_input_tokens=0)
    first.complete(type("R", (), {"thread_id": "e2"})())

    assert capsule().seen_thread_ids == ["e1", "e2"]


def test_own_reviewer_thread_rejects_other_roles_threads() -> None:
    assert own_reviewer_thread("v1", foreign_thread_ids=("e1", None)) == "v1"
    assert own_reviewer_thread("e1", foreign_thread_ids=("e1",)) is None
    assert own_reviewer_thread(None, foreign_thread_ids=("e1",)) is None


def test_mission_brief_can_omit_the_previous_review(tmp_path: Path) -> None:
    _run(tmp_path, policy="mission")
    context = tmp_path / "state" / "handoffs" / "mission-1" / "mission.json"
    assert "All rows match." in render_mission_brief(context)
    assert "All rows match." not in render_mission_brief(
        context, include_previous_review=False,
    )
