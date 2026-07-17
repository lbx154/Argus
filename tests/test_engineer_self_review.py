from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig, SkillStore
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.engineer.self_review import (
    parse_engineer_completion_decision,
    verbatim_verification_output,
)
from argus_skill.reviewer import ReviewerConfig
from argus_skill.skills.layered import LayeredSkillStore
from argus_skill.skills.skill_router import SkillRouter
from argus_skill.skills.vertical_select import persist_vertical


def _engineer_message(*, review: str = "skip", skill_action: str = "none") -> str:
    decision = {
        "review": review,
        "reason": "the bounded change is covered by one deterministic test",
        "verification": "pytest reported 1 passed",
        "skill_action": skill_action,
        "skill_name": "",
        "skill_reason": (
            "the verified repair pattern generalizes to similar parser changes"
            if skill_action != "none"
            else ""
        ),
    }
    return (
        "## Verification (verbatim)\n"
        "```text\n1 passed in 0.04s\n```\n\n"
        "## Summary\n- Fixed the bounded parser behavior.\n\n"
        "ARGUS_ENGINEER_DECISION: "
        + json.dumps(decision, separators=(",", ":"))
    )


def _done_review() -> ReviewDecision:
    return ReviewDecision(
        status="done",
        reason="independently reviewed",
        next_action="",
        round_summary_markdown="# Review\n\n- done\n",
        completion_summary_markdown="Done.",
    )


class _ExplodingReviewer:
    def evaluate(self, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("Reviewer must be skipped after accepted self-review")


class _DoneReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, **_kwargs):
        self.calls += 1
        return _done_review()


def test_engineer_decision_parser_requires_structured_marker_and_verbatim_output() -> None:
    message = _engineer_message()

    decision = parse_engineer_completion_decision(message)

    assert decision is not None
    assert decision.requests_review_skip is True
    assert decision.skill_action == "none"
    assert verbatim_verification_output(message) == "1 passed in 0.04s"
    assert parse_engineer_completion_decision("done") is None


def test_self_verified_engineer_can_skip_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_engineer_message(), thread_id="engineer-1"),
    )
    events: list[dict] = []
    engine = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=_ExplodingReviewer(),
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )

    status, rounds, _message, reason, thread_id = engine.run(
        objective="fix one bounded parser test",
        engineer_prompt_builder=lambda _next, _static=True: "do it",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            allow_engineer_self_review=True,
            effective_progress_timeout_seconds=0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    assert len(rounds) == 1
    assert rounds[0].review.status == "done"
    assert "self-verification" in reason
    assert thread_id == "engineer-1"
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert any(e["type"] == "engineer.self_review.accepted" for e in events)
    completed = [e for e in events if e["type"] == "round.review.completed"]
    assert completed and completed[0]["review_skipped"] is True
    assert completed[0]["review_source"] == "engineer_self_review"


def test_required_independent_review_ignores_engineer_skip_request(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_engineer_message(), thread_id="engineer-1"),
    )
    reviewer = _DoneReviewer()
    engine = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )

    status, rounds, *_ = engine.run(
        objective="close the current stage",
        engineer_prompt_builder=lambda _next, _static=True: "do it",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            allow_engineer_self_review=False,
            effective_progress_timeout_seconds=0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert reviewer.calls == 1
    assert rounds[0].review.reason == "independently reviewed"


def test_engineer_skip_is_not_second_guessed_for_missing_verbatim_block(tmp_path: Path) -> None:
    backend = MemoryBackend()
    message = _engineer_message().replace(
        "## Verification (verbatim)\n```text\n1 passed in 0.04s\n```\n\n",
        "",
    )
    backend.queue("engineer-r1", CannedResponse(message=message, thread_id="e1"))
    reviewer = _DoneReviewer()
    events: list[dict] = []
    engine = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )

    status, *_ = engine.run(
        objective="fix it",
        engineer_prompt_builder=lambda _next, _static=True: "do it",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            allow_engineer_self_review=True,
            effective_progress_timeout_seconds=0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    assert reviewer.calls == 0
    assert any(e["type"] == "engineer.self_review.accepted" for e in events)


def test_final_submission_engineer_skip_is_honored(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_engineer_message(), thread_id="e1"),
    )
    reviewer = _DoneReviewer()
    engine = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=reviewer,
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )

    status, *_ = engine.run(
        objective="prepare final submission",
        engineer_prompt_builder=lambda _next, _static=True: "do it",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            allow_engineer_self_review=True,
            effective_progress_timeout_seconds=0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        scope="final_submission",
    )

    assert status == "done"
    assert reviewer.calls == 0


SKILL_MD = """# Deterministic Parser Repair
## Description
Repair bounded parser behavior using a focused failing test and minimal change.
## Category
software-repair
## When to use
- A parser bug has a deterministic reproducer.
## When NOT to use
- The behavior requires product or security judgment.
## How to solve
1. Reproduce the failure with the narrowest official test.
2. Implement the smallest behavior-preserving fix.
3. Run the focused test and the adjacent regression suite.
## Pitfalls
- Do not weaken the assertion to make the test pass.
"""


def test_skill_creation_resumes_same_engineer_session(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "direct")
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=_engineer_message(skill_action="create"),
            thread_id="engineer-session",
        ),
    )
    backend.queue(
        "engineer-skill-maintenance",
        CannedResponse(message=SKILL_MD, thread_id="engineer-session"),
    )
    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=1,
            workflow_mode="direct",
            engineer_self_review_enabled=True,
            engineer_skill_maintenance_enabled=True,
        ),
        on_event=events.append,
    )

    outcome = loop.run(
        "repair one deterministic parser test",
        workdir=tmp_path,
    )

    assert outcome.status == "done"
    labels = [label for label, _prompt, _options in backend.history]
    assert labels == ["matcher", "engineer-r1", "engineer-skill-maintenance"]
    maintenance_options = next(
        options
        for label, _prompt, options in backend.history
        if label == "engineer-skill-maintenance"
    )
    assert maintenance_options.reasoning_effort == "low"
    resumes = dict(backend.resume_history)
    assert resumes["engineer-skill-maintenance"] == "engineer-session"
    summaries = SkillStore(skills_dir).list_summaries()
    assert any(item["name"] == "Deterministic Parser Repair" for item in summaries)
    assert any(
        event["type"] == "engineer.skill_maintenance.completed"
        and event["success"] is True
        for event in events
    )
    assert not any(event["type"] == "round.review.started" for event in events)


def test_skill_router_create_supports_layered_store(tmp_path: Path) -> None:
    store = LayeredSkillStore(
        project_dir=tmp_path / "project-skills",
        global_dir=tmp_path / "global-skills",
    )
    events: list[dict] = []

    counts = SkillRouter(skill_store=store).apply_ops(
        [{
            "op": "create",
            "content": SKILL_MD,
            "why": "verified reusable deterministic check",
        }],
        task="inspect one deterministic file",
        on_event=events.append,
    )

    assert counts["created"] == 1
    created = [event for event in events if event.get("type") == "skill.created"]
    assert created and created[0]["scope"] == "general"


def test_required_post_task_learning_forces_same_session_create(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "software", workflow_mode="direct")
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_engineer_message(), thread_id="learn-session"),
    )
    backend.queue(
        "engineer-skill-maintenance",
        CannedResponse(message=SKILL_MD, thread_id="learn-session"),
    )
    events: list[dict] = []
    outcome = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=1,
            workflow_mode="direct",
            require_post_task_learning=True,
            force_post_task_learning=True,
        ),
        on_event=events.append,
    ).run("repair one deterministic parser test", workdir=tmp_path)

    assert outcome.status == "done"
    labels = [label for label, _prompt, _options in backend.history]
    assert labels == ["matcher", "engineer-r1", "engineer-skill-maintenance"]
    assert any(
        event.get("type") == "engineer.skill_maintenance.completed"
        and event.get("success") is True
        for event in events
    )


def test_selective_post_task_learning_does_not_force_maintenance(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "software", workflow_mode="direct")
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_engineer_message(), thread_id="learn-session"),
    )
    outcome = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=1,
            workflow_mode="direct",
            require_post_task_learning=True,
        ),
    ).run("repair one deterministic parser test", workdir=tmp_path)

    assert outcome.status == "done"
    labels = [label for label, _prompt, _options in backend.history]
    assert labels == ["matcher", "engineer-r1"]
    engineer_prompt = next(
        prompt for label, prompt, _options in backend.history if label == "engineer-r1"
    )
    assert "Selective self-evolution" in engineer_prompt
    assert "skill_action=none" in engineer_prompt
    assert "inspect about 12 relevant files" in engineer_prompt
    assert "at most 3 focused verification commands" in engineer_prompt
