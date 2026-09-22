"""Exercise teammate entry -> real runtime -> offline multi-round role calls."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.operator_context import (
    OperatorContextStore,
    append_directive,
    append_revoke,
)
from argus.life.failure_experience import FailureExperience, FailureExperienceStore
from argus.team import task_board, teammate_entry


def test_teammate_replaces_revised_and_revoked_context_between_role_calls(tmp_path, monkeypatch):
    import argus.apps._runtime as runtime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "tenant/projects/project-a"
    state.mkdir(parents=True)
    page = workspace / ".autors/project/wiki/pages/quartz.md"
    page.parent.mkdir(parents=True)
    (page.parent.parent / "INDEX.md").write_text("# Project Wiki\n")
    page.write_text("quartz measured under original conditions")
    first_digest = hashlib.sha256(page.read_bytes()).hexdigest()[:12]
    experiences = FailureExperienceStore(state / "failure_experiences.jsonl")
    experience = experiences.append(FailureExperience.new(
        mission_id="prior-mission", title="quartz", objective="verify quartz", status="done",
        factual_outcome="OLD_QUARTZ_EXPERIENCE", concepts=["quartz"],
    ))
    canonical = [experiences.path.read_bytes()]
    append_directive(state, "OLD_TEAM_GUIDANCE", applies_to_roles=("teammate",), expected_revision=0)
    append_directive(state, "REVIEWER_ONLY_GUIDANCE", applies_to_roles=("reviewer",), expected_revision=1)
    append_directive(state, "ENGINEER_ONLY_GUIDANCE", applies_to_roles=("engineer",), expected_revision=2)
    monkeypatch.setenv("ARGUS_OPERATOR_CONTEXT_DIR", str(state))
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "shared-skills"))
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "0")
    monkeypatch.setenv("ARGUS_SKILL_MAX_ROUNDS", "3")
    backend = MemoryBackend()
    digests = [first_digest]

    def revise(_prompt, _options):
        assert experiences.path.read_bytes() == canonical[-1]
        append_revoke(state, 1, reason="revised", expected_revision=3)
        append_directive(state, "NEW_TEAM_GUIDANCE", applies_to_roles=("teammate",),
                         expected_revision=4)
        page.write_text("quartz measured under revised conditions")
        digests.append(hashlib.sha256(page.read_bytes()).hexdigest()[:12])
        experiences.revise(experience.id, expected_revision=1, evidence_refs=["new measurement"],
                           factual_outcome="REVISED_QUARTZ_EXPERIENCE")
        canonical.append(experiences.path.read_bytes())
        return "first implementation"

    def remove(_prompt, _options):
        assert experiences.path.read_bytes() == canonical[-1]
        assert any(record.text == "NEW_TEAM_GUIDANCE" for record in
                   OperatorContextStore(state).project("teammate", consume_once=False).directives)
        append_revoke(state, 5, reason="withdrawn", expected_revision=5)
        page.unlink()
        experiences.retract(experience.id, expected_revision=2, evidence_refs=["invalid measurement"],
                            reason="unsupported")
        canonical.append(experiences.path.read_bytes())
        return "second implementation"

    backend.queue("engineer-r1", CannedResponse(message_factory=revise))
    backend.queue("engineer-r2", CannedResponse(message_factory=remove))
    backend.queue("engineer-r3", CannedResponse(message="final implementation"))
    for status in ("continue", "continue", "done"):
        backend.queue("reviewer", CannedResponse(review_action=(('approve_review' if status == 'done' else 'revise_review'), {'review': ('offline independent review') + '\n\n' + ('verify quartz')})))
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend",
                        lambda **_kwargs: backend)

    class OfflineRunner(runtime._SkillLoopRunner):
        def _run_bounded_planning(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(runtime, "_SkillLoopRunner", OfflineRunner)
    root = tmp_path / "team"
    task_board.form(root, [{"task_id": "t1", "objective": "verify quartz", "owns_paths": ["src/**"]}])
    assert task_board.claim_top(root, "worker", now=1)["task_id"] == "t1"
    assert teammate_entry.main([
        "--root", str(root), "--member-id", "worker", "--task-id", "t1", "--cwd", str(workspace),
    ]) == 0

    prompts = [prompt for label, prompt, _ in backend.history if label.startswith("engineer-")]
    assert len(prompts) == 3
    assert "OLD_TEAM_GUIDANCE" in prompts[0]
    assert "OLD_TEAM_GUIDANCE" not in prompts[1]
    assert "NEW_TEAM_GUIDANCE" in prompts[1]
    assert "OLD_TEAM_GUIDANCE" not in prompts[2] and "NEW_TEAM_GUIDANCE" not in prompts[2]
    assert str(page) in prompts[0] and digests[0] in prompts[0]
    assert str(page) in prompts[1] and digests[1] in prompts[1] and digests[0] not in prompts[1]
    assert str(page) not in prompts[2]
    assert "OLD_QUARTZ_EXPERIENCE" in prompts[0]
    assert "REVISED_QUARTZ_EXPERIENCE" in prompts[1] and "OLD_QUARTZ_EXPERIENCE" not in prompts[1]
    assert "REVISED_QUARTZ_EXPERIENCE" not in prompts[2] and "OLD_QUARTZ_EXPERIENCE" not in prompts[2]
    assert experiences.path.read_bytes() == canonical[-1]
    assert all("ENGINEER_ONLY_GUIDANCE" not in prompt and "REVIEWER_ONLY_GUIDANCE" not in prompt
               for prompt in prompts)
    reviewer_prompts = [prompt for label, prompt, _ in backend.history if label == "reviewer"]
    assert len(reviewer_prompts) == 3
    assert all("REVIEWER_ONLY_GUIDANCE" in prompt and "OLD_TEAM_GUIDANCE" not in prompt
               and "NEW_TEAM_GUIDANCE" not in prompt for prompt in reviewer_prompts)
    # Refreshing context must not turn the teammate into the inbox/checkpoint owner.
    assert not (state / "inbox.offset").exists()
    assert not (state / "CHECKPOINT.md").exists()
    assert not (workspace / "CHECKPOINT.md").exists()
    assert (root / "life/worker/knowledge-recall.sqlite3").is_file()
    assert not (state / "knowledge-recall.sqlite3").exists()
    assert OperatorContextStore(state).acknowledged_revision("teammate") == 0


def test_optional_recall_failure_preserves_current_teammate_policy(tmp_path, monkeypatch):
    import argus.apps._runtime as runtime

    append_directive(tmp_path, "CURRENT_TEAM_POLICY", applies_to_roles=("teammate",),
                     lifetime="once", expected_revision=0)
    monkeypatch.setenv("ARGUS_OPERATOR_CONTEXT_DIR", str(tmp_path))
    captured = []

    class Runner:
        def __init__(self, _ns):
            pass

        def execute(self, *, prelude_context_provider, **_kwargs):
            captured.append(prelude_context_provider())
            return SimpleNamespace(success=True, status="done")

    def unavailable(*_args, **_kwargs):
        raise OSError("optional knowledge index is unavailable")

    monkeypatch.setattr(runtime, "_SkillLoopRunner", Runner)
    monkeypatch.setattr("argus.life.knowledge_recall.render_memory_recall", unavailable)
    assert teammate_entry.run_one_engineer_mission(
        "verify quartz", cwd=str(tmp_path), life_dir=tmp_path / "worker",
        prelude_context="STATIC_VERTICAL_POLICY", max_rounds=1,
    ).success
    assert "CURRENT_TEAM_POLICY" in captured[0] and "STATIC_VERTICAL_POLICY" in captured[0]
    assert "Current recalled knowledge is unavailable." in captured[0]
    assert not OperatorContextStore(tmp_path).project("teammate", consume_once=False).directives


@pytest.mark.parametrize("revoked", [False, True])
def test_explicit_reviewer_context_does_not_fall_back_to_an_older_root(tmp_path, revoked):
    from argus.engineer.round_config import SupervisedConfig
    from argus.engineer.round_reviewer import _active_manager_directive_for_reviewer

    current, stale = tmp_path / "current", tmp_path / "stale"
    append_directive(stale, "STALE_REVIEWER_GUIDANCE", expected_revision=0)
    if revoked:
        append_directive(current, "REVOKED_REVIEWER_GUIDANCE", expected_revision=0)
        append_revoke(current, 1, reason="withdrawn", expected_revision=1)
    config = SupervisedConfig(operator_question_policy_root=current,
                              engineer_log_path=str(stale / "events.jsonl"))
    context = "\n".join(_active_manager_directive_for_reviewer(config))
    assert "STALE_REVIEWER_GUIDANCE" not in context and "REVOKED_REVIEWER_GUIDANCE" not in context


@pytest.mark.parametrize("role", ["engineer", "reviewer"])
@pytest.mark.parametrize("forbid", [False, True])
def test_teammate_enforces_parent_question_policy_at_actual_role_boundary(tmp_path, monkeypatch, role, forbid):
    import argus.apps._runtime as runtime
    from argus.manager.directive import set_active_manager_directive

    source = tmp_path / "parent"
    set_active_manager_directive(source, "parent question policy",
                                operator_question_policy="forbid" if forbid else "allow")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("ARGUS_OPERATOR_CONTEXT_DIR", str(source))
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "0")
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "question-task")
    backend = MemoryBackend()
    question = "Choose operator acceptance scope A or B?"
    backend.queue("engineer-r1", CannedResponse(
        message="implementation", exit_code=1 if role == "engineer" else 0,
        fatal_error="github-copilot: OAuth refresh failed: timeout" if role == "engineer" else None,
    ))
    backend.queue("engineer-r2", CannedResponse(message="follow-up implementation"))
    if role == "reviewer":
        backend.queue("reviewer", CannedResponse(review_action=('request_review_decision', {'review': ('operator-owned acceptance boundary') + '\n\n' + ('wait for scope decision'), 'question': question})))
    backend.queue("reviewer", CannedResponse(review_action=('approve_review', {'review': ('verified') + '\n\n' + ('none')})))
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", lambda **_kwargs: backend)

    class OfflineRunner(runtime._SkillLoopRunner):
        def _run_bounded_planning(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(runtime, "_SkillLoopRunner", OfflineRunner)
    outcome = teammate_entry.run_one_engineer_mission(
        "verify quartz", cwd=str(workspace), life_dir=tmp_path / "worker", max_rounds=3,
    )
    assert (outcome.operator_question == "") is forbid, [
        (label, options.model) for label, _prompt, options in backend.history
    ]
    if not forbid:
        assert ("OAuth refresh failed" in outcome.operator_question) if role == "engineer" else (
            outcome.operator_question == question
        )
    assert not (source / "inbox.offset").exists() and not (source / "CHECKPOINT.md").exists()


def test_teammate_and_reviewer_once_consumption_survives_reopening(tmp_path, monkeypatch):
    import argus.apps._runtime as runtime
    from argus.engineer.round_config import SupervisedConfig
    from argus.engineer.round_reviewer import _active_manager_directive_for_reviewer

    source = tmp_path / "parent"
    append_directive(source, "TEAM_ONCE", applies_to_roles=("teammate",), lifetime="once", expected_revision=0)
    append_directive(source, "REVIEW_ONCE", applies_to_roles=("reviewer",), lifetime="once", expected_revision=1)
    monkeypatch.setenv("ARGUS_OPERATOR_CONTEXT_DIR", str(source))
    teammate_prompts, reviewer_prompts = [], []

    class Runner:
        def __init__(self, _ns):
            pass

        def execute(self, *, prelude_context_provider, **_kwargs):
            config = SupervisedConfig(operator_question_policy_root=source)
            for _ in range(3):
                teammate_prompts.append(prelude_context_provider())
                reviewer_prompts.append("\n".join(_active_manager_directive_for_reviewer(config)))
            return SimpleNamespace(success=True, status="done")

    monkeypatch.setattr(runtime, "_SkillLoopRunner", Runner)
    for run in range(2):
        assert teammate_entry.run_one_engineer_mission(
            "verify quartz", cwd=str(tmp_path), life_dir=tmp_path / f"worker-{run}", max_rounds=3,
        ).success
    assert ["TEAM_ONCE" in prompt for prompt in teammate_prompts] == [True, False, False, False, False, False]
    assert ["REVIEW_ONCE" in prompt for prompt in reviewer_prompts] == [True, False, False, False, False, False]
    assert all("REVIEW_ONCE" not in prompt for prompt in teammate_prompts)
    assert all("TEAM_ONCE" not in prompt for prompt in reviewer_prompts)
    reopened = OperatorContextStore(source)
    assert not reopened.project("teammate", consume_once=False).directives
    assert not reopened.project("reviewer", consume_once=False).directives
    assert not (source / "inbox.offset").exists() and not (source / "CHECKPOINT.md").exists()


@pytest.mark.parametrize("corruption", ["missing", "invalid"])
def test_unavailable_required_context_stops_before_teammate_provider(tmp_path, monkeypatch, corruption):
    import argus.apps._runtime as runtime

    source = tmp_path / "parent"
    append_directive(source, "REQUIRED_CURRENT_POLICY", expected_revision=0)
    ledger = source / "operator_context.jsonl"
    if corruption == "missing":
        ledger.unlink()
    else:
        ledger.write_text("{corrupt operator ledger")
    monkeypatch.setenv("ARGUS_OPERATOR_CONTEXT_DIR", str(source))
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "offline")
    backend = MemoryBackend()
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", lambda **_kwargs: backend)

    class OfflineRunner(runtime._SkillLoopRunner):
        def _run_bounded_planning(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(runtime, "_SkillLoopRunner", OfflineRunner)
    result = teammate_entry.run_one_engineer_mission(
        "verify quartz", cwd=str(tmp_path), life_dir=tmp_path / "worker", max_rounds=1,
    )
    assert not result.success and result.status == "error"
    assert "OperatorContext" in result.reason
    assert backend.history == []


@pytest.mark.parametrize("hook", ["extra_guidance_provider", "prelude_context_provider"])
def test_required_policy_error_at_either_prompt_hook_stops_before_provider(tmp_path, hook):
    from argus import SkillLoop, SkillLoopConfig
    from argus.core.operator_context import OperatorContextUnavailable

    def unavailable():
        raise OperatorContextUnavailable("current policy unavailable")

    backend = MemoryBackend()
    loop = SkillLoop(
        skills_dir=tmp_path / "skills", engineer_runner=backend,
        config=SkillLoopConfig(engineer_model="offline", reviewer_model="offline", max_rounds=1,
                               workflow_mode="direct", active_vertical="software",
                               require_post_task_learning=False, wiki_enabled=False),
        **{hook: unavailable},
    )
    with pytest.raises(OperatorContextUnavailable):
        loop.run("verify quartz", workdir=tmp_path)
    assert backend.history == []


def test_runtime_inbox_adapter_preserves_required_policy_error(tmp_path, monkeypatch):
    from argus.apps._runtime_execute import SkillLoopExecuteMixin
    from argus.core.operator_context import OperatorContextUnavailable

    def unavailable(*_args, **_kwargs):
        raise OperatorContextUnavailable("current policy unavailable")

    runner = SimpleNamespace(
        _args=SimpleNamespace(skills_dir=str(tmp_path / "skills"), project_state_dir=str(tmp_path)),
        _refresh_manager_skill_store=lambda *_args, **_kwargs: None,
        manager=object(), _backend=MemoryBackend(),
        _SkillLoop=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    state = SimpleNamespace(workdir=tmp_path, config=SimpleNamespace(active_vertical="software"))
    monkeypatch.setattr("argus.apps._runtime_execute._engineer_guidance", unavailable)
    SkillLoopExecuteMixin._build_execute_skill_store_and_loop(
        runner, state, sink=SimpleNamespace(handle_event=lambda _event: None),
    )
    with pytest.raises(OperatorContextUnavailable):
        state.loop.extra_guidance_provider()


@pytest.mark.parametrize("corruption", ["missing", "invalid"])
@pytest.mark.parametrize("inbox_pending", [False, True])
def test_standard_runtime_stops_before_provider_when_required_context_is_unavailable(tmp_path, monkeypatch, corruption, inbox_pending):
    from argus.apps._inbox import queue_inbox_message
    from argus.apps._runtime import _SkillLoopRunner
    from argus.core.operator_context import OperatorContextUnavailable

    source = tmp_path / "parent"
    append_directive(source, "REQUIRED_ENGINEER_POLICY", expected_revision=0)
    ledger = source / "operator_context.jsonl"
    if corruption == "missing":
        ledger.unlink()
    else:
        ledger.write_text("{corrupt operator ledger")
    if inbox_pending:
        queue_inbox_message(source, "New standing operator guidance", source="test")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "offline")
    backend = MemoryBackend()
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", lambda **_kwargs: backend)
    namespace = teammate_entry._build_runner_ns(
        str(workspace), max_rounds=1, paper_mission=False, project_state_dir=source,
    )
    namespace.operator_context_dir = ""
    runner = _SkillLoopRunner(namespace)
    runner._allow_chat_fast_path = False
    with pytest.raises(OperatorContextUnavailable):
        runner.execute(objective="verify quartz", sink=SimpleNamespace(handle_event=lambda _event: None),
                       preplanned=True, holds_stage_authority=False)
    assert backend.history == []
