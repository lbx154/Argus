"""Current memory must survive the real runtime-to-SkillLoop prompt boundary."""
from __future__ import annotations

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
    append_preference,
    append_revoke,
)
from argus.core.pipeline_state import write_pipeline_state
from argus.life.event_log import JsonlEventSink
from argus.life.experience_tools import ExperienceToolService
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.manager.supervision import shutdown_supervision
from tests.hand_off import hand_off


class OfflineBackend(MemoryBackend):
    # Exercise both fresh prompts and the actual Pi rolling-session policy,
    # while all inference remains the in-process deterministic backend.
    backend = "pi"

    def run_exec(self, *, prompt, options, run_label, **kwargs):
        if run_label.startswith("engineer"):
            # Every Engineer turn leaves new work: none returns it unchanged.
            with (Path(options.working_dir) / "progress.log").open("a") as log:
                log.write(run_label + "\n")
        return super().run_exec(prompt=prompt, options=options, run_label=run_label, **kwargs)


class RuntimeRunner(SkillLoopExecuteMixin):
    """Keep execute/context/loop construction real; omit unrelated deployment setup."""
    _SkillLoop = SkillLoop
    _next_seed_thread_id = None
    manager = None

    def __init__(self, memory, workdir, backend, mode):
        self.memory, self.workdir, self._backend = memory, workdir, backend
        self.mode, self.max_rounds = mode, 1
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
            vertical_state_root=self.memory.root,
            max_rounds=self.max_rounds, require_independent_review=True,
            require_post_task_learning=False, wiki_enabled=False, auto_init_wiki=False,
            session_id=kwargs["mission_id"], context_packet_path=kwargs["context_packet_path"],
            engineer_log_path=str(self.memory.root / "engineer.jsonl"),
            state_root=self.memory.root,
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
        action=hand_off(*(("engineer", "Synthetic bounded check.", "Check the next bounded observation.") if status == "continue" else ("done", "Synthetic bounded check."))),
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
@pytest.mark.parametrize("resumable", [False, True])
def test_settled_success_reaches_next_runtime_role_and_refreshes_after_corrections(runtime, mode, resumable):
    memory, workdir = runtime
    backend = OfflineBackend()
    if not resumable:
        backend.backend = "memory"
    runner = RuntimeRunner(memory, workdir, backend, mode)
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
    if resumable:
        assert "## Continuation turn" in prompts[1]
    reviews = [prompt for label, prompt, _ in backend.history if label == "reviewer"]
    assert global_value in reviews[0] and local_value not in reviews[0]
    assert all(local_value not in prompt and global_value not in prompt for prompt in reviews[1:])
    assert service.store.get(experience.id).state == "retracted"


def test_failed_memory_refresh_does_not_restore_the_old_mission_snapshot(runtime, monkeypatch):
    memory, workdir = runtime
    backend = OfflineBackend()
    runner = RuntimeRunner(memory, workdir, backend, "direct")
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
