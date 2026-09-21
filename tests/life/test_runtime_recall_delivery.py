"""Current memory must survive the real runtime-to-SkillLoop prompt boundary."""
from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.apps._runtime_backends import _Outcome
from argus.apps._runtime_execute import SkillLoopExecuteMixin
from argus.core.operator_context import (
    OperatorContextStore,
    append_directive,
    append_preference,
    append_revoke,
)
from argus.core.pipeline_state import write_pipeline_state
from argus.life.event_log import JsonlEventSink
from argus.life.experience_tools import ExperienceToolService
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.manager.supervision import shutdown_supervision


class OfflineBackend(MemoryBackend):
    # Exercise both fresh prompts and the actual Pi rolling-session policy,
    # while all inference remains the in-process deterministic backend.
    backend = "pi"


class RuntimeRunner(SkillLoopExecuteMixin):
    """Keep execute/context/loop construction real; omit unrelated deployment setup."""
    _SkillLoop = SkillLoop
    _allow_chat_fast_path = False
    _next_seed_thread_id = None
    manager = None

    def __init__(self, memory, workdir, backend, mode, policy):
        self.memory, self.workdir, self._backend = memory, workdir, backend
        self.mode, self.policy, self.max_rounds = mode, policy, 1
        self._args = SimpleNamespace(
            skills_dir=str(memory.root.parent.parent / "skills"),
            project_state_dir=str(memory.root),
        )
        self.mission_texts = []

    def _build_execute_config(self, state, **kwargs):
        state.workdir = self.workdir
        state.config = SkillLoopConfig(
            engineer_model="offline", reviewer_model="offline",
            workflow_mode=self.mode, active_vertical="software",
            vertical_state_root=self.memory.root, role_session_policy=self.policy,
            max_rounds=self.max_rounds, require_independent_review=True,
            require_post_task_learning=False, wiki_enabled=False, auto_init_wiki=False,
            session_id=kwargs["mission_id"], context_packet_path=kwargs["context_packet_path"],
            engineer_log_path=str(self.memory.root / "engineer.jsonl"),
            operator_question_policy_root=self.memory.root,
        )

    def _refresh_manager_skill_store(self, *_args, **_kwargs):
        pass

    def _invoke_execute_loop(self, state, **kwargs):
        # Same SkillLoop public entry used by production; artifact isolation,
        # planning and provider deployment are outside this regression.
        self.mission_texts.append(state.full_task)
        state.outcome = state.loop.run(
            state.full_task, workdir=state.workdir, seed_thread_id=state.seed,
            objective_for_skill=kwargs["objective"], review_objective=state.review_objective,
            original_objective=kwargs["original_objective"], scope=state.mission_scope,
        )

    def _extract_execute_outcome_fields(self, _state):
        pass

    def _maybe_decide_stage_transition(self, _state, **_kwargs):
        pass

    def _build_execute_outcome(self, state):
        result = state.outcome
        review = result.rounds[-1].review
        return _Outcome(
            success=result.successful, status=result.status, rounds=len(result.rounds),
            final_review_status=review.status, final_review_source="reviewer",
            final_review_reason=review.reason, final_message=result.final_message,
        )


def verdict(status="done"):
    return CannedResponse(
        review_action=(('approve_review' if status == 'done' else 'revise_review'), {'review': ('Synthetic bounded check.') + '\n\n' + ('Check the next bounded observation.' if status == 'continue' else '')}),
        thread_id="offline-reviewer",
    )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            return original_connect(sock, address)
        raise AssertionError("This integration regression cannot use an external network")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("This integration regression must remain offline")

    monkeypatch.setattr(socket.socket, "connect", local_only)
    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    memory = LifeMemory.open(tmp_path / "user/projects/synthetic")
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    for root in (memory.root, workdir):
        write_pipeline_state(root, {"active_vertical": "software", "stage": "implementation"})
    yield memory, workdir
    shutdown_supervision(memory.root)


def task(memory, identity, mode):
    memory.backlog.add(BacklogItem.new(
        item_id=identity, title=identity, objective="Validate synthetic grouping.",
        tags=["scope:bounded"],
        manager_decision={"routed": True, "vertical": "software", "workflow_mode": mode},
    ))


@pytest.mark.parametrize("mode", ["direct", "staged"])
@pytest.mark.parametrize("policy", ["fresh", "rolling"])
def test_settled_success_reaches_next_runtime_role_and_refreshes_after_corrections(runtime, mode, policy):
    memory, workdir = runtime
    backend = OfflineBackend()
    runner = RuntimeRunner(memory, workdir, backend, mode, policy)
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=JsonlEventSink(None, life_dir=memory.root),
        config=LifeSupervisorConfig(project_worktree=workdir, role_skill_maintenance_enabled=False),
    )
    original, revised = "ORIGINAL_SCOPED_SUCCESS_LESSON", "REVISED_SCOPED_SUCCESS_LESSON"
    global_value, local_value = "GLOBAL_WORKFLOW_MARKER", "LOCAL_OVERRIDE_MARKER"
    shared = append_preference(memory.root, kind="workflow", value=global_value, scope="global", expected_revision=0)
    local = append_preference(memory.root, kind="workflow", value=local_value, expected_revision=0)
    backend.queue("engineer-r1", CannedResponse(message=original, thread_id="offline-seed"))
    backend.queue("reviewer", verdict())
    task(memory, "seed-success", mode)
    assert supervisor.tick()["success"]
    (experience,) = memory.failure_experiences.recent()
    assert experience.status == "done" and original in experience.research_narrative
    assert "Prior mission experiences" in memory.render_recall_context("synthetic grouping")
    backend.history.clear()
    (workdir / "correction.txt").write_text("Synthetic evidence corrects and then withdraws the interpretation.\n")
    service = ExperienceToolService(memory.root, role="engineer", parent_call_id="offline-correction", workspace=workdir)
    mutation = {"experience_id": experience.id, "expected_revision": 1,
                "evidence_refs": ["workspace:correction.txt"], "reason": "Synthetic evidence correction"}

    def first(prompt, _options):
        assert prompt.count(original) == 1
        assert prompt.count(local_value) == 1 and global_value not in prompt
        service.dispatch("revise", {**mutation, "changes": {
            "research_narrative": revised, "factual_outcome": "Updated bounded result.",
        }})
        append_revoke(memory.root, local.revision, reason="Remove override", expected_revision=OperatorContextStore(memory.root).revision)
        return "First follow-up check complete."

    def second(prompt, _options):
        assert original not in prompt and prompt.count(revised) == 1
        assert local_value not in prompt and prompt.count(global_value) == 1
        service.dispatch("retract", {**mutation, "expected_revision": 2})
        append_revoke(memory.root, shared.revision, scope="global", reason="Withdraw shared preference", expected_revision=1)
        assert not service.store.retrieve("synthetic grouping")
        return "Second follow-up check complete."

    def third(prompt, _options):
        assert all(value not in prompt for value in (original, revised, local_value, global_value))
        assert "replaces earlier host-recalled memory" in prompt
        assert "replaces earlier OperatorContext" in prompt
        return "Current bounded checks complete."

    runner.max_rounds = 3
    for index, factory in enumerate((first, second, third), 1):
        backend.queue(f"engineer-r{index}", CannedResponse(message_factory=factory, thread_id="offline-followup"))
        backend.queue("reviewer", verdict("continue" if index < 3 else "done"))
    task(memory, "followup", mode)
    result = supervisor.tick()
    assert result["success"] and result["rounds"] == 3
    assert original not in runner.mission_texts[-1]
    assert local_value not in runner.mission_texts[-1]
    prompts = [prompt for label, prompt, _ in backend.history if label.startswith("engineer-r")]
    assert len(prompts) == 3 and all(prompt.count("## Current host context") == 1 for prompt in prompts)
    if policy == "rolling":
        assert "## Continuation turn" in prompts[1]
    reviews = [prompt for label, prompt, _ in backend.history if label == "reviewer"]
    assert global_value in reviews[0] and local_value not in reviews[0]
    assert all(local_value not in prompt and global_value not in prompt for prompt in reviews[1:])
    assert service.store.get(experience.id).state == "retracted"


def test_failed_memory_refresh_does_not_restore_the_old_mission_snapshot(runtime, monkeypatch):
    memory, workdir = runtime
    backend = OfflineBackend()
    runner = RuntimeRunner(memory, workdir, backend, "direct", "rolling")
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=JsonlEventSink(None, life_dir=memory.root),
        config=LifeSupervisorConfig(project_worktree=workdir, role_skill_maintenance_enabled=False,
                                    runtime_context="REQUIRED_RUNTIME_BOUNDARY"),
    )
    original = "PREVIOUS_RECALL_MUST_NOT_BECOME_A_FALLBACK"
    backend.queue("engineer-r1", CannedResponse(message=original, thread_id="offline-seed"))
    backend.queue("reviewer", verdict())
    task(memory, "seed-success", "direct")
    assert supervisor.tick()["success"]

    def unavailable(**_kwargs):
        raise OSError("synthetic memory source unavailable")

    def first(prompt, _options):
        assert original in prompt
        monkeypatch.setattr(memory, "render_prelude", unavailable)
        return "Bounded observation complete."

    def second(prompt, _options):
        assert original not in prompt
        assert "REQUIRED_RUNTIME_BOUNDARY" in prompt
        assert "Current recalled memory is unavailable" in prompt
        assert "replaces earlier host-recalled memory" in prompt
        return "Current task complete."

    runner.max_rounds = 2
    backend.queue("engineer-r1", CannedResponse(message_factory=first, thread_id="offline-followup"))
    backend.queue("reviewer", verdict("continue"))
    backend.queue("engineer-r2", CannedResponse(message_factory=second, thread_id="offline-followup"))
    backend.queue("reviewer", verdict())
    task(memory, "followup", "direct")
    assert supervisor.tick()["success"]
    assert original not in runner.mission_texts[-1]


@pytest.mark.parametrize("unavailable", [False, True])
def test_bounded_planner_refreshes_shared_memory_at_its_actual_call_boundary(runtime, monkeypatch, unavailable):
    memory, workdir = runtime
    backend = OfflineBackend()

    class PlanningRuntime(RuntimeRunner):
        before_planning = None

        def _build_execute_config(self, state, **kwargs):
            super()._build_execute_config(state, **kwargs)
            state.config.checkpoint_path = Path(kwargs["context_packet_path"]).parent / "CHECKPOINT.md"
            if self.before_planning is not None:
                # The caller has supplied its context, but Planner has not run.
                self.before_planning(state)

        def _invoke_execute_loop(self, state, **kwargs):
            plan_kwargs = {key: kwargs[key] for key in (
                "sink", "objective", "original_objective", "preplanned", "mission_id",
            )}
            self._run_bounded_planning(state, **plan_kwargs)
            super()._invoke_execute_loop(state, **kwargs)

    runner = PlanningRuntime(memory, workdir, backend, "direct", "fresh")
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=JsonlEventSink(None, life_dir=memory.root),
        config=LifeSupervisorConfig(project_worktree=workdir, role_skill_maintenance_enabled=False,
                                    runtime_context="ENGINEER_RUNTIME_ONLY"),
    )
    backend.queue("engineer-r1", CannedResponse(message="Original bounded completion."))
    backend.queue("reviewer", verdict())
    task(memory, "seed-success", "direct")
    assert supervisor.tick()["success"]
    (experience,) = memory.failure_experiences.recent()
    marker = "RETRACTED_PLANNER_INTERPRETATION"
    (workdir / "correction.txt").write_text("Synthetic evidence corrects the interpretation.\n")
    service = ExperienceToolService(memory.root, role="engineer", parent_call_id="offline-correction", workspace=workdir)
    correction = {"experience_id": experience.id, "expected_revision": 1,
                  "evidence_refs": ["workspace:correction.txt"], "reason": "Synthetic correction"}
    service.dispatch("revise", {**correction, "changes": {"research_narrative": marker}})
    append_directive(memory.root, "ENGINEER_ROLE_ONLY", applies_to_roles=("engineer",),
                     expected_revision=0)

    def change_after_context_was_supplied(state):
        service.dispatch("retract", {**correction, "expected_revision": 2})
        state.config.checkpoint_path.write_text("CURRENT_SHARED_CHECKPOINT")
        memory.journal.path.write_text(memory.journal.path.read_text() + json.dumps({
            "type": "life.mission.completed", "item_id": "later-observation",
            "title": "Later observation", "summary": "LATEST_SHARED_HISTORY", "success": True,
        }) + "\n")
        if unavailable:
            def fail(_item):
                raise OSError("synthetic shared-memory source unavailable")

            monkeypatch.setattr(supervisor, "_build_planner_continuation_context", fail)

    runner.before_planning = change_after_context_was_supplied
    runner.mode = "staged"
    backend.queue("planner-bounded-plan", CannedResponse(message='{"steps":[{"title":"Validate current evidence"}]}'))
    backend.queue("engineer-r1", CannedResponse(message="Current bounded completion."))
    backend.queue("reviewer", verdict())
    task(memory, "followup", "staged")
    assert supervisor.tick()["success"]
    (prompt,) = [prompt for label, prompt, _ in backend.history if label == "planner-bounded-plan"]
    assert marker not in prompt
    assert "CURRENT_SHARED_CHECKPOINT" in prompt
    assert "ENGINEER_RUNTIME_ONLY" not in prompt and "ENGINEER_ROLE_ONLY" not in prompt
    if unavailable:
        assert "Current shared memory is unavailable" in prompt
    else:
        assert "LATEST_SHARED_HISTORY" in prompt
