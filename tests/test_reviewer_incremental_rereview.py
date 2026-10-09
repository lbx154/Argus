"""Round >= 2 Reviewer input: own findings, change set, own session only."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import ReviewDecision, RoundRecord
from argus.engineer.round_rereview import (
    build_rereview_context,
    engineer_commands_since,
    own_reviewer_thread,
)
from argus.life.context_packet import render_mission_brief
from argus.reviewer import Reviewer

_REREVIEW = "## Re-review (round 2)"
_FINDING = "The total row overcounts ERROR: the CSV says 370 but the logs hold 20."
_REQUEST = "Recount every row from the source logs and recheck the totals."


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


def _log_command(log: Path, text: str, *, layer: str = "engineer") -> None:
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "engineer.progress",
            "kind": "command_execution",
            "text": text,
            "actor": f"{layer}-r2",
            "agent_layer": layer,
            "ts": time.time(),
        }) + "\n")


def _scenario(
    tmp_path: Path,
    *,
    policy: str,
    engineer_r2_thread: str = "e1",
) -> tuple[MemoryBackend, list[dict], object]:
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "events.jsonl"
    log.write_text("", encoding="utf-8")
    context, checkpoint = _context(tmp_path)

    def engineer_one(_prompt, _options) -> str:
        (work / "summary.csv").write_text("total,ERROR,370\n", encoding="utf-8")
        (work / "notes.md").write_text("first pass\n", encoding="utf-8")
        old = time.time() - 60
        os.utime(work / "notes.md", (old, old))
        _log_command(log, "python3 count_round_one.py")
        return "Wrote summary.csv."

    def engineer_two(_prompt, _options) -> str:
        (work / "summary.csv").write_text("total,ERROR,20\n", encoding="utf-8")
        _log_command(log, "python3 recount.py --all-rows\n# second line hidden")
        _log_command(log, "rg -c ERROR logs", layer="reviewer")
        return "Recounted all rows; total ERROR is now 20."

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message_factory=engineer_one, thread_id="e1"))
    backend.queue("reviewer", CannedResponse(
        review_action=("revise_review", {"review": _FINDING + "\n\n" + _REQUEST}),
        thread_id="v1",
    ))
    backend.queue("engineer-r2", CannedResponse(
        message_factory=engineer_two, thread_id=engineer_r2_thread,
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
            engineer_log_path=str(log),
        ),
        on_event=events.append,
    )
    outcome = loop.run("summarize the logs into summary.csv", workdir=work)
    return backend, events, outcome


def _reviewer_prompts(backend: MemoryBackend) -> list[str]:
    return [prompt for label, prompt, _ in backend.history if label == "reviewer"]


def _reviewer_resumes(backend: MemoryBackend) -> list[str | None]:
    return [thread for label, thread in backend.resume_history if label == "reviewer"]


def test_resumed_round_two_carries_findings_pointer_and_change_set(tmp_path: Path) -> None:
    backend, _events, outcome = _scenario(tmp_path, policy="mission")

    assert outcome.successful
    # The Reviewer continues its own thread, never the Engineer's.
    assert _reviewer_resumes(backend) == [None, "v1"]
    second = _reviewer_prompts(backend)[1]
    assert _REREVIEW in second
    # Its own findings: compact, because the resumed thread holds them in full.
    assert "Your previous findings (round 1, `continue`)" in second
    assert "earlier in this thread" in second
    assert "What you asked for: " + _FINDING in second
    # The change set since that review: what changed and what the Engineer ran.
    change_line = next(
        line for line in second.splitlines()
        if line.startswith("- Workspace files modified")
    )
    assert "summary.csv" in change_line
    assert "notes.md" not in change_line
    assert "`python3 recount.py --all-rows …`" in second
    assert "count_round_one.py" not in second
    assert "rg -c ERROR logs" not in second
    assert "Recounted all rows" in second
    # Pointer to the full deliverable and explicit freedom to widen scope.
    assert f"Full deliverable: `{tmp_path / 'work'}`" in second
    assert "default focus, not a boundary" in second
    assert "suspect a regression" in second
    assert "Approve only on evidence you checked yourself" in second
    # The previous verdict is not repeated by the other blocks.
    assert "previous_review_summary" not in second
    assert "- Previous review:" not in second
    assert "- Previously requested action:" not in second


def test_fresh_round_two_gets_a_full_carry_over_of_findings(tmp_path: Path) -> None:
    backend, _events, outcome = _scenario(tmp_path, policy="fresh")

    assert outcome.successful
    assert _reviewer_resumes(backend) == [None, None]
    second = _reviewer_prompts(backend)[1]
    assert _REREVIEW in second
    assert "earlier in this thread" not in second
    assert f"Your previous findings (round 1, `continue`):\n{_FINDING} {_REQUEST}" in second
    assert "summary.csv" in second
    assert "`python3 recount.py --all-rows …`" in second


def test_first_round_review_is_unchanged(tmp_path: Path) -> None:
    backend, _events, _outcome = _scenario(tmp_path, policy="mission")

    first = _reviewer_prompts(backend)[0]
    assert "## Re-review" not in first
    assert "Your previous findings" not in first
    assert "Full deliverable:" not in first
    assert build_rereview_context(
        rounds=[], round_index=1, workdir=tmp_path,
        changes_since_ts=0.0, engineer_log_path=None, commands_since_ts=0.0,
    ) is None

    kw = dict(
        objective="o", operator_messages=[], planner_review_instruction="",
        round_index=1, session_id=None, main_summary="S", main_error=None,
        working_dir=str(tmp_path / "work"), round_started_ts=0.0,
    )
    reviewer = Reviewer(runner=None, skill_store=None)
    assert reviewer._build_prompt(**kw) == reviewer._build_prompt(rereview_context="", **kw)


def test_reviewer_never_resumes_the_engineer_thread(tmp_path: Path) -> None:
    # The Engineer's round-two thread collides with the Reviewer's saved one.
    backend, events, outcome = _scenario(
        tmp_path, policy="mission", engineer_r2_thread="v1",
    )

    assert outcome.successful
    assert _reviewer_resumes(backend) == [None, None]
    second = _reviewer_prompts(backend)[1]
    # A fresh thread gets the full carry-over of its own findings instead.
    assert f"{_FINDING} {_REQUEST}" in second
    turns = [
        event for event in events
        if event.get("type") == "role.session.turn" and event.get("role") == "reviewer"
    ]
    assert turns[-1]["rotation_reason"] == "foreign_thread"


def test_own_reviewer_thread_rejects_other_roles_threads() -> None:
    assert own_reviewer_thread("v1", foreign_thread_ids=("e1", None)) == "v1"
    assert own_reviewer_thread("e1", foreign_thread_ids=("e1", None)) is None
    assert own_reviewer_thread(None, foreign_thread_ids=("e1",)) is None


def test_carry_over_uses_only_independent_reviewer_judgments(tmp_path: Path) -> None:
    def record(index: int, source: str, reason: str) -> RoundRecord:
        return RoundRecord(
            round_index=index, engineer_message="", engineer_exit_code=0,
            review=ReviewDecision(
                status="continue", reason=reason, next_action=reason,
                review_source=source,
            ),
        )

    self_review_only = [record(1, "engineer_self_review", "self check")]
    assert build_rereview_context(
        rounds=self_review_only, round_index=2, workdir=tmp_path,
        changes_since_ts=None, engineer_log_path=None, commands_since_ts=None,
    ) is None

    rounds = [
        record(1, "reviewer", "first finding"),
        record(2, "engineer_self_review", "self check"),
    ]
    context = build_rereview_context(
        rounds=rounds, round_index=3, workdir=tmp_path,
        changes_since_ts=None, engineer_log_path=None, commands_since_ts=None,
    )
    assert context is not None
    assert "Your previous findings (round 1, `continue`):\nfirst finding" in context.full
    assert "self check" not in context.full


def test_engineer_commands_since_reads_only_engineer_commands(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text("not json\n", encoding="utf-8")
    before = time.time() - 1
    _log_command(log, "make test")
    _log_command(log, "make test")
    _log_command(log, "cat secret", layer="reviewer")
    assert engineer_commands_since(log, before) == ["make test"]
    assert engineer_commands_since(log, time.time() + 10) == []
    assert engineer_commands_since(tmp_path / "missing.jsonl", before) == []


def test_mission_brief_can_omit_the_previous_review(tmp_path: Path) -> None:
    _scenario(tmp_path, policy="mission")
    context = tmp_path / "state" / "handoffs" / "mission-1" / "mission.json"
    with_review = render_mission_brief(context)
    without_review = render_mission_brief(context, include_previous_review=False)
    assert "review:" in with_review.lower()
    assert "All rows match." in with_review
    assert "All rows match." not in without_review

