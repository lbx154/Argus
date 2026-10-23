"""Reviewer carry-over: its own findings only, its own thread only."""
from __future__ import annotations

import json
from dataclasses import replace
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
from tests.hand_off import hand_off
from tests.role_rules import rule

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
    resumable: bool,
    engineer_threads: tuple[str, str] = ("e1", "e1"),
) -> tuple[MemoryBackend, list[dict], object]:
    work = tmp_path / "work"
    work.mkdir(parents=True)
    context, checkpoint = _context(tmp_path)
    backend = MemoryBackend()
    if resumable:
        backend.backend = "pi"  # type: ignore[misc]
    def writes(rows: str, message: str, thread_id: str) -> CannedResponse:
        def turn(_prompt, _options) -> str:
            (work / "summary.csv").write_text(rows, encoding="utf-8")
            return message

        return CannedResponse(message_factory=turn, thread_id=thread_id)

    backend.queue("engineer-r1", writes("ERROR,370\n", "Wrote summary.csv.", engineer_threads[0]))
    backend.queue("reviewer", CannedResponse(
        action=hand_off("engineer", "A finding remains.", _FINDING), thread_id="v1",
    ))
    backend.queue("engineer-r2", writes("ERROR,20\n", "Recounted all rows.", engineer_threads[1]))
    backend.queue("reviewer", CannedResponse(
        action=hand_off("done", "All rows match."),
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
        status="continue", reason=reason, next_action=reason, reviewer_authored=True,
    )


# --- carry-over -------------------------------------------------------------


def test_fresh_reviewer_gets_its_full_previous_findings_once(tmp_path: Path) -> None:
    backend, _events, outcome = _run(tmp_path, resumable=False)

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
    # The re-review rule that refers to the judgment "below" still applies.
    assert "read re-review before reusing it" in second


def test_resumed_reviewer_gets_one_copy_and_no_unverifiable_thread_claim(
    tmp_path: Path,
) -> None:
    backend, _events, outcome = _run(tmp_path, resumable=True)

    assert outcome.successful
    assert _reviewer_resumes(backend) == [None, "v1"]
    second = _reviewer_prompts(backend)[1]
    assert second.count("the CSV says 370") == 1
    assert "TAIL-OF-THE-FINDING" in second
    # Whether the provider truly resumed is only known after the call, so the
    # prompt never claims the reasoning is already in the thread.
    assert "earlier in this thread" not in second


def test_first_round_review_is_unchanged(tmp_path: Path) -> None:
    backend, _events, outcome = _run(tmp_path, resumable=True)

    first = _reviewer_prompts(backend)[0]
    assert _FINDINGS not in first
    assert "previous_review_summary" not in first
    assert not render_previous_findings([], round_index=1)
    kw = dict(
        objective="o", operator_messages=[], planner_review_instruction="",
        round_index=1, session_id=None, main_summary="S", main_error=None,
    )
    reviewer = Reviewer(runner=None, skill_store=None)
    assert reviewer._build_prompt(**kw) == reviewer._build_prompt(previous_findings="", **kw)
    # Only the independent Reviewer call's own judgment is marked as a finding.
    assert outcome.rounds[0].review.reviewer_authored is True


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


_UNJUDGED_LINES = {
    "provider_turn_cap": (
        "Round 2 was not reviewed: the Engineer's session reached its per-call turn limit."
    ),
    "backend_failure": (
        "Round 2 was not reviewed: the model service dropped the Engineer's session."
    ),
    "silent_command": (
        "Round 2 was not reviewed: Argus stopped the Engineer's session after a "
        "command made no progress for a long time."
    ),
}


def test_host_placeholders_are_never_shown_as_reviewer_findings() -> None:
    for name, placeholder in _placeholders().items():
        assert placeholder.reviewer_authored is False, name
        assert placeholder.host_placeholder == name
        state = RoundLoopState(rounds=[_record(2, placeholder)])
        findings = render_previous_findings(state.rounds, round_index=3)
        assert not findings.has_reviewer_findings, name
        assert _FINDINGS not in findings.text, name
        assert placeholder.reason[:40] not in findings.text, name
        assert "ENGINEER-WIND-DOWN" not in findings.text, name
        # Nor as the "settled" previous judgment the incremental boundary cites.
        assert _previous_review_summary(state) == "", name


def test_unjudged_rounds_appear_as_one_host_fact_line_each() -> None:
    for name, placeholder in _placeholders().items():
        rounds = [
            _record(1, _reviewer_judgment("REAL-REVIEWER-FINDING")),
            _record(2, placeholder),
        ]
        text = render_previous_findings(rounds, round_index=3).text
        assert "Your judgment in round 1 (`continue`):" in text, name
        assert "REAL-REVIEWER-FINDING" in text, name
        host = text.split("## Host facts since then (not your words)\n", 1)[1]
        assert host.splitlines() == ["- " + _UNJUDGED_LINES[name]], name
        assert placeholder.reason[:40] not in text, name
        assert "ENGINEER-WIND-DOWN" not in text, name
        summary = _previous_review_summary(RoundLoopState(rounds=rounds))
        assert summary.splitlines() == ["Round 1 — continue: REAL-REVIEWER-FINDING"], name


def test_self_reviews_are_not_reviewer_findings() -> None:
    self_review = ReviewDecision(
        status="done", reason="self check passed", next_action="",
        review_source="engineer_self_review",
    )
    findings = render_previous_findings([_record(1, self_review)], round_index=2)
    assert not findings.has_reviewer_findings
    assert "self check passed" not in findings.text
    assert "checked only by the Engineer's self-review" in findings.text
    # A flag alone is not enough: the source must be the Reviewer as well.
    flagged = replace(self_review, reviewer_authored=True)
    assert not render_previous_findings([_record(1, flagged)], round_index=2).has_reviewer_findings


# --- host rewrites keep the Reviewer's own words ------------------------------


def test_venue_enforcement_keeps_the_reviewer_words_and_labels_the_host_change(
    tmp_path: Path,
) -> None:
    from argus.core.models import RunnerResult
    from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
    from argus.core.role_actions import PREFIX
    from argus.core.role_tool_bridge import bridge_request
    from argus.reviewer import ReviewerConfig
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research", target_venue="ICLR")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    state["venue_acceptance_minimum"] = "strong_accept"
    write_pipeline_state(tmp_path, state)
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper/main.tex").write_text("Current manuscript")
    (tmp_path / "paper/main.pdf").write_bytes(b"Current PDF")
    prose = "REAL: my recommendation for this version is weak accept."

    class Runner:
        backend = "pi"

        def run_exec(self, **kwargs):
            bridge_request(
                PREFIX, "hand_off",
                hand_off("done", "Accepted.", prose, recommendation="weak_accept")[1],
                env=kwargs["options"].extension_env,
            )
            return RunnerResult(exit_code=0, agent_messages=["submitted"])

    review = Reviewer(Runner()).evaluate(
        objective="Polish the paper.", round_index=1, session_id=None,
        main_summary="Revised.", main_error=None, scope="final_submission",
        config=ReviewerConfig(
            model="m", active_vertical="research",
            working_dir=str(tmp_path), vertical_state_root=str(tmp_path),
        ),
    )
    # The verdict stays the Reviewer's; the operator's bar is the host's fact beside it.
    assert review.status == "done"
    assert review.next_action == prose
    review.reviewer_authored = True
    text = render_previous_findings([_record(1, review)], round_index=2).text
    findings, host = text.split("## Host facts since then (not your words)\n", 1)
    assert f"Your judgment in round 1 (`done`):\nAccepted.\nYour note:\n{prose}" in findings
    assert "strong accept or better" not in findings
    assert "the operator's bar (strong accept or better) is not met" in host


# --- earlier words are open to correction -------------------------------------


def test_findings_are_framed_as_correctable_earlier_words() -> None:
    reason = "First line of the finding.\n\n\nSecond   paragraph keeps its break."
    text = render_previous_findings(
        [_record(1, _reviewer_judgment(reason))], round_index=2,
    ).text
    assert "These are your earlier words, not verified facts." in text
    assert "say so and correct it" in text
    assert "First line of the finding.\n\nSecond paragraph keeps its break." in text
    boundary = Reviewer(runner=None, skill_store=None)._build_round_delta(
        resumed=False, objective="o", operator_messages=[], planner_review_instruction="",
        round_index=2, session_id=None, main_summary="S", main_error=None,
        prev_review_summary="Round 1 — continue: x",
    )
    assert "read re-review before reusing it" in boundary
    assert "your previous judgment is not fact. If current evidence shows it was wrong" in rule(
        "reviewer", "re-review/SKILL.md")


# --- own thread only ----------------------------------------------------------


def test_reviewer_never_resumes_the_current_engineer_thread(tmp_path: Path) -> None:
    backend, events, outcome = _run(
        tmp_path, resumable=True, engineer_threads=("e1", "v1"),
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
        tmp_path, resumable=True, engineer_threads=("v1", "e2"),
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
            role="engineer", policy="rolling", objective_revision="rev",
            workdir=tmp_path, backend="memory", model="m",
            checkpoint_path=None, path=path,
        )

    first = capsule()
    first.prepare()
    first.complete(type("R", (), {"thread_id": "e1"})())
    first.rotate("branch_changed")
    first.prepare()
    first.complete(type("R", (), {"thread_id": "e2"})())

    assert capsule().seen_thread_ids == ["e1", "e2"]


def test_own_reviewer_thread_rejects_other_roles_threads() -> None:
    assert own_reviewer_thread("v1", foreign_thread_ids=("e1", None)) == "v1"
    assert own_reviewer_thread("e1", foreign_thread_ids=("e1",)) is None
    assert own_reviewer_thread(None, foreign_thread_ids=("e1",)) is None


def test_mission_brief_can_omit_the_previous_review(tmp_path: Path) -> None:
    _run(tmp_path, resumable=True)
    context = tmp_path / "state" / "handoffs" / "mission-1" / "mission.json"
    assert "All rows match." in render_mission_brief(context)
    assert "All rows match." not in render_mission_brief(
        context, include_previous_review=False,
    )


def _engineer_capsule(tmp_path: Path, *, model: str = "m", policy: str = "rolling"):
    return RoleSessionCapsule.open(
        role="engineer", policy=policy, objective_revision="rev",
        workdir=tmp_path, backend="memory", model=model,
        checkpoint_path=None,
        path=tmp_path / "engineer.json" if policy != "fresh" else None,
    )


def test_thread_history_survives_a_context_change_and_ignores_a_bare_string(
    tmp_path: Path,
) -> None:
    first = _engineer_capsule(tmp_path)
    first.prepare()
    first.complete(type("R", (), {"thread_id": "thr-abc"})())
    # A changed model rotates the capsule; the thread history stays.
    rotated = _engineer_capsule(tmp_path, model="other")
    assert rotated.action == "rotated"
    assert rotated.seen_thread_ids == ["thr-abc"]
    assert json.loads((tmp_path / "engineer.json").read_text())["seen_thread_ids"] == [
        "thr-abc"
    ]
    payload = json.loads((tmp_path / "engineer.json").read_text())
    payload["seen_thread_ids"] = "thr-xyz"
    payload["thread_id"] = ""
    (tmp_path / "engineer.json").write_text(json.dumps(payload))
    # Never split into characters.
    assert _engineer_capsule(tmp_path, model="other").seen_thread_ids == []


def test_fresh_session_still_records_engineer_threads(tmp_path: Path) -> None:
    capsule = _engineer_capsule(tmp_path, policy="fresh")
    capsule.prepare()
    capsule.complete(type("R", (), {"thread_id": "e-fresh"})())
    assert capsule.thread_id == ""
    assert capsule.seen_thread_ids == ["e-fresh"]
