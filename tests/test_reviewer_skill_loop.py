"""Phase 1 reviewer→skill feedback loop tests.

Covers:
- ReviewDecision schema accepts the new optional fields and parser
  populates them.
- Parser gates ``mission_lesson`` on ``failure_cause == "skill_gap"``.
- ``status == "done"`` clears ``failure_cause`` / ``mission_lesson``.
- ``MissionLoopEngine`` carries ``mission_lesson`` and raw verification
  evidence into the next round's continue prompt.
- ``record_pending_lesson`` writes a markdown file under
  ``<dir>/<skill_slug>/`` only when ``failure_cause == skill_gap`` and a
  lesson is present.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.mission import prompts as mission_prompts
from argus_skill.mission.reviewer import parse_decision_text
from argus_skill.skills.lessons import (
    default_pending_lessons_dir,
    record_pending_lesson,
)


# ---------------------------------------------------------------------------
# parse_decision_text — new fields
# ---------------------------------------------------------------------------


def test_parser_populates_new_fields_for_continue_with_skill_gap() -> None:
    payload = json.dumps({
        "status": "continue",
        "confidence": 0.6,
        "reason": "Tests still red.",
        "next_action": "Re-run pytest after fixing CSV parsing.",
        "round_summary_markdown": "- partial",
        "completion_summary_markdown": "",
        "failure_cause": "skill_gap",
        "mission_lesson": "Use the csv module instead of str.split for quoted CSV input.",
        "verification_summary": "pytest exit 1; assertion in test_csv.py.",
    })
    d = parse_decision_text(payload)
    assert d is not None
    assert d.status == "continue"
    assert d.failure_cause == "skill_gap"
    assert "csv module" in d.mission_lesson
    assert d.verification_summary.startswith("pytest exit 1")


def test_parser_drops_mission_lesson_when_failure_cause_is_not_skill_gap() -> None:
    """Parser is the second gate: even if the reviewer leaks a lesson under
    a non-skill_gap cause, we discard it."""
    payload = json.dumps({
        "status": "continue",
        "confidence": 0.5,
        "reason": "Network was unreachable.",
        "next_action": "Retry once connectivity is restored.",
        "failure_cause": "environment",
        "mission_lesson": "Never trust the network.",
    })
    d = parse_decision_text(payload)
    assert d is not None
    assert d.failure_cause == "environment"
    assert d.mission_lesson == ""


def test_parser_clears_failure_fields_on_done() -> None:
    payload = json.dumps({
        "status": "done",
        "confidence": 0.95,
        "reason": "All green.",
        "next_action": "No further action needed.",
        "failure_cause": "skill_gap",  # nonsense alongside done
        "mission_lesson": "Should not appear.",
    })
    d = parse_decision_text(payload)
    assert d is not None
    assert d.status == "done"
    assert d.failure_cause == ""
    assert d.mission_lesson == ""


def test_parser_normalises_unknown_failure_cause_to_unknown() -> None:
    payload = json.dumps({
        "status": "continue",
        "confidence": 0.4,
        "reason": "Something happened.",
        "next_action": "Try again.",
        "failure_cause": "alien_attack",
    })
    d = parse_decision_text(payload)
    assert d is not None
    assert d.failure_cause == "unknown"
    assert d.mission_lesson == ""


def test_parser_legacy_payload_without_new_fields_still_works() -> None:
    payload = json.dumps({
        "status": "continue",
        "confidence": 0.5,
        "reason": "Not yet done.",
        "next_action": "Keep going.",
    })
    d = parse_decision_text(payload)
    assert d is not None
    assert d.failure_cause == ""
    assert d.mission_lesson == ""
    assert d.verification_summary == ""


# ---------------------------------------------------------------------------
# continue_main_prompt — overlay injection
# ---------------------------------------------------------------------------


def test_continue_prompt_includes_lesson_and_evidence() -> None:
    review = ReviewDecision(
        status="continue",
        confidence=0.5,
        reason="Tests red.",
        next_action="Fix CSV parser.",
        failure_cause="skill_gap",
        mission_lesson="Prefer csv.reader over str.split for quoted fields.",
    )
    prompt = mission_prompts.continue_main_prompt(
        objective="Write a CSV parser.",
        review=review,
        checks_ok=False,
        mission_lesson=review.mission_lesson,
        verification_evidence={
            "cmd": "pytest -q",
            "exit_code": 1,
            "stderr_tail": "AssertionError: expected 3 fields, got 2",
        },
    )
    assert "Lesson for this mission" in prompt
    assert "csv.reader" in prompt
    assert "Verification evidence from previous round" in prompt
    assert "pytest -q" in prompt
    assert "AssertionError" in prompt


def test_continue_prompt_omits_lesson_section_when_empty() -> None:
    review = ReviewDecision(
        status="continue",
        confidence=0.5,
        reason="Not yet done.",
        next_action="Keep going.",
    )
    prompt = mission_prompts.continue_main_prompt(
        objective="Do the thing.",
        review=review,
        checks_ok=True,
        mission_lesson="",
        verification_evidence=None,
    )
    assert "Lesson for this mission" not in prompt
    assert "Verification evidence from previous round" not in prompt


# ---------------------------------------------------------------------------
# record_pending_lesson
# ---------------------------------------------------------------------------


def _make_decision(**overrides) -> ReviewDecision:
    base = dict(
        status="continue",
        confidence=0.6,
        reason="Tests red.",
        next_action="Fix it.",
        failure_cause="skill_gap",
        mission_lesson="Prefer csv.reader over manual splits.",
    )
    base.update(overrides)
    return ReviewDecision(**base)


def test_record_pending_lesson_writes_markdown(tmp_path: Path) -> None:
    decision = _make_decision()
    written = record_pending_lesson(
        pending_dir=tmp_path,
        skill_id="Parse CSV files",
        mission_id="mission-abc-123",
        objective="Parse a CSV file with quoted fields.",
        decision=decision,
        verification_context={
            "cmd": "pytest -q",
            "exit_code": 1,
            "stderr_tail": "AssertionError",
        },
        round_index=1,
    )
    assert written is not None
    assert written.exists()
    body = written.read_text(encoding="utf-8")
    assert "csv.reader" in body
    assert "skill_id" in body
    assert written.parent.name == "parse-csv-files"


def test_record_pending_lesson_skips_when_no_lesson(tmp_path: Path) -> None:
    decision = _make_decision(mission_lesson="")
    written = record_pending_lesson(
        pending_dir=tmp_path,
        skill_id="x",
        mission_id="m",
        objective="o",
        decision=decision,
        verification_context={},
        round_index=1,
    )
    assert written is None


def test_record_pending_lesson_skips_when_cause_not_skill_gap(tmp_path: Path) -> None:
    decision = _make_decision(failure_cause="environment", mission_lesson="ignored")
    written = record_pending_lesson(
        pending_dir=tmp_path,
        skill_id="x",
        mission_id="m",
        objective="o",
        decision=decision,
        verification_context={},
        round_index=1,
    )
    assert written is None


def test_record_pending_lesson_swallows_disk_errors(tmp_path: Path) -> None:
    bad = tmp_path / "lessons.txt"
    bad.write_text("file, not a directory")
    # ``bad`` is a file; treating it as the parent dir should fail. The
    # recorder swallows the error and returns None.
    decision = _make_decision()
    written = record_pending_lesson(
        pending_dir=bad,
        skill_id="x",
        mission_id="m",
        objective="o",
        decision=decision,
        verification_context={},
        round_index=1,
    )
    assert written is None


def test_default_pending_lessons_dir_uses_skills_dir_sibling(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    out = default_pending_lessons_dir(skills)
    assert out == tmp_path / "pending_lessons"


def test_default_pending_lessons_dir_falls_back_to_home() -> None:
    out = default_pending_lessons_dir(None)
    assert out.name == "pending_lessons"
    assert "argus-skill" in str(out)


# ---------------------------------------------------------------------------
# MissionLoopEngine — overlay carries into next round
# ---------------------------------------------------------------------------


class _FakeSkillStore:
    def find_relevant(self, task, *, on_event=None):  # noqa: ANN001
        return [], None

    def render_skill(self, skill):  # noqa: ANN001
        return ""

    def save_distilled(self, **kwargs):  # noqa: ANN003
        raise AssertionError("save_distilled should not be invoked")


class _FakeDistiller:
    def distill(self, **kwargs):  # noqa: ANN003
        raise AssertionError("distill should not be invoked")


class _ScriptedEngineer:
    """Returns one canned RunnerResult per call. Records every prompt."""

    def __init__(self) -> None:
        self.prompts_seen: list[str] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ANN001
        self.prompts_seen.append(prompt)
        return RunnerResult(
            exit_code=0,
            agent_messages=[f"engineer round {len(self.prompts_seen)} reply"],
        )


class _FailingFallback:
    def run_exec(self, **kwargs):  # noqa: ANN003
        raise AssertionError("fallback should not run")


class _ScriptedReviewer:
    def __init__(self, verdicts: list[ReviewDecision]) -> None:
        self._verdicts = list(verdicts)
        self.calls: list[dict] = []

    def evaluate(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self._verdicts.pop(0)


def _make_skill_loop_runner(*, mission_objective: str, workdir: Path):
    from argus_skill.adapters.skill_loop_runner import (
        EngineerCallConfig,
        SkillLoopRunner,
        SkillLoopRunnerConfig,
    )
    from argus_skill.scientist.distiller import DistillerConfig

    return SkillLoopRunner(
        config=SkillLoopRunnerConfig(
            mission_objective=mission_objective,
            workdir=workdir,
            engineer=EngineerCallConfig(model="fake", reasoning_effort="medium"),
            distiller=DistillerConfig(model="fake", reasoning_effort="high"),
            distill_on_miss=False,
        ),
        skill_store=_FakeSkillStore(),
        distiller=_FakeDistiller(),
        engineer_runner=_ScriptedEngineer(),
        fallback_runner=_FailingFallback(),
    )


def test_engine_carries_skill_gap_lesson_into_round_two(tmp_path: Path) -> None:
    """Round 1 fails a check; reviewer says skill_gap with a lesson;
    round 2's continue prompt must contain that lesson and the raw
    verification evidence; pending_lessons file is written."""
    pytest.importorskip("codex_autoloop.core.state_store")
    from codex_autoloop.core.state_store import LoopStateStore

    from argus_skill.mission.engine import MissionLoopConfig, MissionLoopEngine

    mission_objective = "Parse a CSV file with quoted fields."
    runner = _make_skill_loop_runner(mission_objective=mission_objective, workdir=tmp_path)
    reviewer = _ScriptedReviewer([
        ReviewDecision(
            status="continue",
            confidence=0.6,
            reason="Pytest red.",
            next_action="Re-run after applying lesson.",
            failure_cause="skill_gap",
            mission_lesson="Use csv.reader for quoted CSV.",
        ),
        ReviewDecision(
            status="blocked",  # terminate the loop after we've examined R2's prompt
            confidence=0.95,
            reason="Stopping the test.",
            next_action="(test stop)",
        ),
    ])
    state_store = LoopStateStore(objective=mission_objective)
    config = MissionLoopConfig(
        objective=mission_objective,
        max_rounds=3,
        check_commands=["false"],  # fails on round 1, will fail on round 2 too
        check_timeout_seconds=10,
        plan_mode="off",
        workdir=str(tmp_path),
        pending_lessons_dir=str(tmp_path / "pending_lessons"),
        mission_id="mission-test-001",
    )
    engine = MissionLoopEngine(
        runner=runner, reviewer=reviewer, planner=None,
        config=config, state_store=state_store,
    )
    engine.run()

    engineer = runner.engineer_runner  # type: ignore[attr-defined]
    assert len(engineer.prompts_seen) >= 2
    round2_prompt = engineer.prompts_seen[1]
    assert "Lesson for this mission" in round2_prompt
    assert "csv.reader" in round2_prompt
    assert "Verification evidence from previous round" in round2_prompt
    # Raw `false` exit_code should appear in the evidence block.
    assert "exit_code: 1" in round2_prompt or "exit_code:" in round2_prompt

    # Pending-lesson markdown was written.
    pending = tmp_path / "pending_lessons"
    assert pending.exists()
    files = list(pending.rglob("*.md"))
    assert files, "expected at least one pending lesson markdown"
    body = files[0].read_text(encoding="utf-8")
    assert "csv.reader" in body
    assert "skill_gap" in body


def test_engine_clears_lesson_on_successful_round(tmp_path: Path) -> None:
    """If a later round succeeds, the lesson overlay must be dropped."""
    pytest.importorskip("codex_autoloop.core.state_store")
    from codex_autoloop.core.state_store import LoopStateStore

    from argus_skill.mission.engine import MissionLoopConfig, MissionLoopEngine

    mission_objective = "anything"
    runner = _make_skill_loop_runner(mission_objective=mission_objective, workdir=tmp_path)
    reviewer = _ScriptedReviewer([
        ReviewDecision(
            status="continue", confidence=0.5, reason="r", next_action="n",
            failure_cause="skill_gap", mission_lesson="Lesson A",
        ),
        ReviewDecision(
            status="done", confidence=0.95, reason="ok",
            next_action="No further action needed.",
        ),
    ])
    state_store = LoopStateStore(objective=mission_objective)
    config = MissionLoopConfig(
        objective=mission_objective,
        max_rounds=3,
        check_commands=[],  # checks_ok always True
        plan_mode="off",
        workdir=str(tmp_path),
        pending_lessons_dir=str(tmp_path / "pl"),
    )
    engine = MissionLoopEngine(
        runner=runner, reviewer=reviewer, planner=None,
        config=config, state_store=state_store,
    )
    engine.run()
    assert engine._active_lesson == ""
    assert engine._active_verification_evidence is None


def test_engine_does_not_record_lesson_for_non_skill_gap_cause(tmp_path: Path) -> None:
    """Reviewer says environment failure → no lesson recorded, no overlay."""
    pytest.importorskip("codex_autoloop.core.state_store")
    from codex_autoloop.core.state_store import LoopStateStore

    from argus_skill.mission.engine import MissionLoopConfig, MissionLoopEngine

    mission_objective = "do something"
    runner = _make_skill_loop_runner(mission_objective=mission_objective, workdir=tmp_path)
    reviewer = _ScriptedReviewer([
        ReviewDecision(
            status="continue", confidence=0.5, reason="net",
            next_action="retry",
            failure_cause="environment",
            mission_lesson="ignored",  # parser would strip; engine double-gates
        ),
        ReviewDecision(
            status="blocked", confidence=0.9, reason="stop",
            next_action="(test stop)",
        ),
    ])
    state_store = LoopStateStore(objective=mission_objective)
    pending_dir = tmp_path / "pl"
    config = MissionLoopConfig(
        objective=mission_objective, max_rounds=3,
        check_commands=["false"], check_timeout_seconds=10,
        plan_mode="off", workdir=str(tmp_path),
        pending_lessons_dir=str(pending_dir),
    )
    # Engine receives the ReviewDecision as-is; ``mission_lesson`` is
    # already ``""`` for environment because the dataclass field carries
    # whatever the reviewer set, but our engine also gates on
    # cause == skill_gap before persisting/overlay-ing. To prove that
    # gate, we hand a decision with a non-skill_gap cause AND a
    # non-empty lesson manually:
    reviewer._verdicts[0].mission_lesson = "synthetic lesson"
    reviewer._verdicts[0].failure_cause = "environment"
    engine = MissionLoopEngine(
        runner=runner, reviewer=reviewer, planner=None,
        config=config, state_store=state_store,
    )
    engine.run()
    assert engine._active_lesson == ""
    # No pending lesson file should have been written.
    if pending_dir.exists():
        assert not list(pending_dir.rglob("*.md"))
