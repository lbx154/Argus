"""Reviewer carry-over: its own findings only, its own thread only."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import ReviewDecision, RoundRecord
from argus.engineer.reviewer_findings import (
    render_previous_findings,
)
from argus.reviewer import Reviewer
from tests.hand_off import hand_off

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

    backend.queue("engineer-r1-mission-1", writes("ERROR,370\n", "Wrote summary.csv.", engineer_threads[0]))
    backend.queue("reviewer", CannedResponse(
        action=hand_off("engineer", "A finding remains.", _FINDING), thread_id="v1",
    ))
    backend.queue("engineer-r2-mission-1", writes("ERROR,20\n", "Recounted all rows.", engineer_threads[1]))
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
    assert "not fact" in boundary
    assert "If current evidence shows your earlier judgment was wrong" in boundary


# --- own thread only ----------------------------------------------------------


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
