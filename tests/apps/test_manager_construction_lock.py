"""Manager reads stay available while another process executes a mission.

Only legacy initialization writes need the daemon's pipeline boundary; they
must still wait and recheck state after obtaining that cross-process lock.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from argus_skill.apps._runtime_construction import build_life_runner
from argus_skill.core.pipeline_state import (
    primary_pipeline_state_path,
    read_pipeline_state,
    write_pipeline_state,
)

_HOLD_PIPELINE = r"""
import json
import sys
from pathlib import Path
from argus_skill.core.pipeline_state import write_pipeline_state
from argus_skill.manager._session_ops import manager_pipeline_lock

root = Path(sys.argv[1])
with manager_pipeline_lock(root):
    (root / "lock-held").write_text("ready", encoding="utf-8")
    payload = json.loads(sys.stdin.readline())
    if payload is not None:
        write_pipeline_state(root, payload)
"""


@contextmanager
def _mission_holds_pipeline(root: Path):
    process = subprocess.Popen(
        [sys.executable, "-c", _HOLD_PIPELINE, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    released = False

    def release(payload=None):
        nonlocal released
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        released = True

    try:
        deadline = time.monotonic() + 10
        while not (root / "lock-held").is_file():
            if process.poll() is not None:
                pytest.fail(f"pipeline lock holder exited: {process.communicate()!r}")
            if time.monotonic() >= deadline:
                pytest.fail("pipeline lock holder did not start")
            time.sleep(0.02)
        yield release
    finally:
        if not released and process.poll() is None:
            release()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)


def _start_manager_build(state_root: Path, workdir: Path):
    result = {}
    finished = threading.Event()

    def build():
        try:
            result["runner"] = build_life_runner(argparse.Namespace(
                backend="memory",
                workdir=str(workdir),
                project_state_dir=str(state_root),
                manager_session_root=str(state_root),
            ))
        except BaseException as exc:
            result["error"] = exc
        finally:
            finished.set()

    thread = threading.Thread(target=build, daemon=True)
    thread.start()
    return thread, finished, result


@pytest.mark.parametrize("payload", [
    None,
    {},
    {"vertical": "math", "current_stage": "review"},
    {"vertical": "research", "current_stage": "experiment", "stages": {
        "idea": {"status": "done"}, "experiment": {"status": "in_progress"},
    }},
])
def test_manager_construction_does_not_wait_for_a_mission_without_migration(
    tmp_path: Path, payload: dict | None,
) -> None:
    state, workdir = tmp_path / "state", tmp_path / "workdir"
    workdir.mkdir()
    if payload is not None:
        write_pipeline_state(state, payload)
        # The existing target must also prevent importing a stale workdir copy.
        write_pipeline_state(workdir, {"vertical": "math", "current_stage": "solve"})
    state_path = primary_pipeline_state_path(state)
    before = state_path.read_bytes() if state_path.exists() else None
    with _mission_holds_pipeline(state):
        thread, finished, result = _start_manager_build(state, workdir)
        assert finished.wait(5), "Manager construction waited for the active mission"
        thread.join(timeout=1)
        assert "error" not in result, result.get("error")
        assert result["runner"].manager.project_root == state
        assert result["runner"].manager.execution_workdir == workdir
        assert (state_path.read_bytes() if state_path.exists() else None) == before


@pytest.mark.parametrize("migration", ["import", "stage"])
@pytest.mark.parametrize("peer_commits", [False, True])
def test_manager_migration_waits_and_rereads_after_the_peer_commits(
    tmp_path: Path, monkeypatch, migration: str, peer_commits: bool,
) -> None:
    from argus_skill.manager import _session_ops

    lock_requested = threading.Event()
    pipeline_lock = _session_ops.manager_pipeline_lock

    def observed_pipeline_lock(root):
        lock_requested.set()
        return pipeline_lock(root)

    monkeypatch.setattr(_session_ops, "manager_pipeline_lock", observed_pipeline_lock)
    state, workdir = tmp_path / "state", tmp_path / "workdir"
    workdir.mkdir()
    original = {"vertical": "research", "current_stage": "research", "stages": {
        "research": {"status": "done"},
    }}
    if migration == "import":
        original = {"vertical": "math", "current_stage": "solve"}
        write_pipeline_state(workdir, original)
    else:
        write_pipeline_state(state, original)
    state_path = primary_pipeline_state_path(state)
    before = state_path.read_bytes() if state_path.exists() else None
    replacement = {
        "vertical": "research", "current_stage": "experiment",
        "stages": {"experiment": {"status": "in_progress"}},
        "peer_commit": "must survive initialization",
    }
    with _mission_holds_pipeline(state) as release:
        thread, finished, result = _start_manager_build(state, workdir)
        assert lock_requested.wait(5), "Manager did not request the migration lock"
        assert not finished.wait(0.25), "Manager migration bypassed the write lock"
        assert (state_path.read_bytes() if state_path.exists() else None) == before
        release(replacement if peer_commits else None)
        assert finished.wait(5), "Manager migration did not resume after unlock"
        thread.join(timeout=1)
        assert "error" not in result, result.get("error")

    committed = read_pipeline_state(state)
    if peer_commits:
        assert committed == replacement
    elif migration == "import":
        assert committed == original
        assert read_pipeline_state(workdir) == original
    else:
        assert committed["current_stage"] == "idea"
        assert committed["stages"]["idea"]["status"] == "in_progress"
        assert committed["current_verdict"] == "mapped_stage_requires_current_review"


def test_prewarm_and_self_question_complete_during_a_peer_mission(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus_skill.adapters.agent_cli_backend import AgentCliBackend
    from argus_skill.core.models import RunnerResult
    from argus_skill.core.session import SessionMeta, write_session_meta
    from argus_skill.core.transcript import read_turns
    from argus_skill.life.memory import BacklogItem, LifeMemory
    from argus_skill.webapi import manager_bridge, manager_state

    sid = "s-live-manager-lock"
    state = tmp_path / "projects" / sid
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    write_pipeline_state(state, {"vertical": "research", "current_stage": "experiment"})
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(workdir), workdir=str(workdir)))
    memory = LifeMemory.open(state)
    task = memory.backlog.add(BacklogItem.new(title="CTB experiment", objective="Run the CTB experiment"))
    memory.backlog.mark_running(task.id)
    backlog_before = (state / "backlog.jsonl").read_bytes()
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_BACKEND", "copilot")
    monkeypatch.setattr(manager_state, "_STATES", {})
    monkeypatch.setattr(manager_state, "_MANAGER_PREWARM_OWNER", sid)
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_available_runner",
        lambda backend, runner_bin: (backend, runner_bin or backend),
    )
    prewarmed = []
    calls = []

    def prewarm(_backend, **kwargs):
        prewarmed.append(kwargs["run_label"])

    def run_exec(_backend, **kwargs):
        calls.append(kwargs)
        if kwargs["run_label"] == "manager-frontdoor-classify":
            message = "CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF\nSELF_MODE: inspect"
        else:
            assert kwargs["run_label"] == "simple-1"
            message = "The CTB experiment is still running; this node records its current attempt."
        return RunnerResult(exit_code=0, agent_messages=[message], thread_id="manager-question")

    monkeypatch.setattr(AgentCliBackend, "prewarm_acp_client", prewarm)
    monkeypatch.setattr(AgentCliBackend, "run_exec", run_exec)
    question = 'I do not understand this.\n[[Argus引用 ' + json.dumps({
        "item_id": task.id, "title": "CTB execution attempt",
    }) + ']]'
    result = {}
    finished = threading.Event()
    state_before = primary_pipeline_state_path(state).read_bytes()

    def ask():
        try:
            # Use the same locked prewarm + real construction path as Web,
            # then the complete classifier/SELF/transcript path. Only provider
            # transport and replies are stubbed; neither Manager is replaced.
            manager_state._prewarm_manager_context(sid, global_root=tmp_path)
            result["reply"] = manager_bridge.manager_message(sid, question, global_root=tmp_path)
        except BaseException as exc:
            result["error"] = exc
        finally:
            finished.set()

    with _mission_holds_pipeline(state):
        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        assert finished.wait(10), "Manager SELF question waited for the mission boundary"
        thread.join(timeout=1)
        assert "error" not in result, result.get("error")
        assert result["reply"] == {
            "kind": "chat",
            "reply": "The CTB experiment is still running; this node records its current attempt.",
        }
        assert len(prewarmed) == 2
        assert [call["run_label"] for call in calls] == ["manager-frontdoor-classify", "simple-1"]
        assert question in calls[-1]["prompt"]
        turns = read_turns(state, limit=4)
        assert [turn["role"] for turn in turns] == ["operator", "argus"]
        assert turns[0]["text"] == question
        assert (state / "backlog.jsonl").read_bytes() == backlog_before
        assert primary_pipeline_state_path(state).read_bytes() == state_before
