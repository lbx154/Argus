"""Manager reads stay available while another process executes a mission.

Only legacy initialization writes need the daemon's pipeline boundary; they
must still wait and recheck state after obtaining that cross-process lock.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from argus.core.pipeline_state import (
    primary_pipeline_state_path,
    write_pipeline_state,
)

_HOLD_PIPELINE = r"""
import json
import sys
from pathlib import Path
from argus.core.pipeline_state import write_pipeline_state
from argus.manager._session_ops import manager_pipeline_lock

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


def test_self_question_completes_during_a_peer_mission(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.adapters.agent_cli_backend import AgentCliBackend
    from argus.core.models import RunnerResult
    from argus.core.session import SessionMeta, write_session_meta
    from argus.core.transcript import read_turns
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.webapi import manager_bridge, manager_state

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
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setattr(manager_state, "_STATES", {})
    monkeypatch.setattr(
        "argus.agent_cli.runner_backend.resolve_available_runner",
        lambda backend, runner_bin: (backend, runner_bin or backend),
    )
    calls = []

    def run_exec(_backend, **kwargs):
        calls.append(kwargs)
        if kwargs["run_label"] == "manager-frontdoor-classify":
            from argus.core.role_actions import PREFIX
            from argus.core.role_tool_bridge import bridge_request

            bridge_request(PREFIX, "hand_off", {
                "to": "manager", "intake": {"type": "ephemeral", "scope": "project"},
            }, env=kwargs["options"].extension_env)
            message = ""
        else:
            assert kwargs["run_label"] == "simple-1"
            from argus.core.role_actions import PREFIX
            from argus.core.role_tool_bridge import bridge_request

            # While the team works, an answer files that it changes nothing for the team.
            bridge_request(PREFIX, "hand_off", {
                "to": "manager", "summary": "Explained the running attempt.", "assumptions": [],
            }, env=kwargs["options"].extension_env)
            message = "The CTB experiment is still running; this node records its current attempt."
        return RunnerResult(exit_code=0, agent_messages=[message], thread_id="manager-question")

    monkeypatch.setattr(AgentCliBackend, "run_exec", run_exec)
    question = 'I do not understand this.\n[[Argus引用 ' + json.dumps({
        "item_id": task.id, "title": "CTB execution attempt",
    }) + ']]'
    result = {}
    finished = threading.Event()
    state_before = primary_pipeline_state_path(state).read_bytes()

    def ask():
        try:
            # The complete construction/classifier/SELF/transcript path, as
            # Web runs it. Only provider transport and replies are stubbed;
            # neither Manager is replaced.
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
        assert [call["run_label"] for call in calls] == ["simple-1"]
        assert question in calls[-1]["prompt"]
        turns = read_turns(state, limit=4)
        assert [turn["role"] for turn in turns] == ["operator", "argus"]
        assert turns[0]["text"] == question
        assert (state / "backlog.jsonl").read_bytes() == backlog_before
        assert primary_pipeline_state_path(state).read_bytes() == state_before
