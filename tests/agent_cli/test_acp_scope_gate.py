"""Scope gate: only Manager front-door labels take the warm ACP path.

With ``ARGUS_SKILL_COPILOT_ACP=1`` and the copilot backend, a
classifier or direct Manager reply routes through the warm ``copilot --acp``
client (never spawns a one-shot CLI); engineer/reviewer/mission labels still go
through the ``Popen`` CLI path.
"""

from __future__ import annotations

import json
import queue

import pytest

from argus_skill.agent_cli import agent_cli_runner, copilot_acp
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import BACKEND_COPILOT


class _FakeAcpProc:
    def __init__(self) -> None:
        self._q: "queue.Queue" = queue.Queue()
        self.written: list[dict] = []
        self.stdin = self._Stdin(self)
        self.stdout = self._Stdout(self)

    def poll(self):
        return None

    def _on_write(self, obj):
        self.written.append(obj)
        m = obj.get("method")
        if m == "initialize":
            self._q.put(
                json.dumps({"jsonrpc": "2.0", "id": obj["id"], "result": {"agentCapabilities": {}}})
                + "\n"
            )
        elif m == "session/new":
            self._q.put(
                json.dumps({"jsonrpc": "2.0", "id": obj["id"], "result": {"sessionId": "s1"}})
                + "\n"
            )
        elif m == "session/prompt":
            self._q.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "s1",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "CONFIG: NONE\nROUTE: SELF"},
                            },
                        },
                    }
                )
                + "\n"
            )
            self._q.put(
                json.dumps(
                    {"jsonrpc": "2.0", "id": obj["id"], "result": {"stopReason": "end_turn"}}
                )
                + "\n"
            )

    class _Stdin:
        def __init__(self, p):
            self.p = p

        def write(self, s):
            for line in s.splitlines():
                line = line.strip()
                if line:
                    self.p._on_write(json.loads(line))

        def flush(self):
            pass

    class _Stdout:
        def __init__(self, p):
            self.p = p

        def __iter__(self):
            return self

        def __next__(self):
            item = self.p._q.get()
            if item is None:
                raise StopIteration
            return item


@pytest.fixture(autouse=True)
def _reset_clients():
    copilot_acp._CLIENTS.clear()
    yield
    copilot_acp._CLIENTS.clear()


def test_copilot_manager_acp_defaults_on_with_explicit_rollback(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_COPILOT_ACP", raising=False)
    runner = AgentCliRunner("copilot-bin", backend=BACKEND_COPILOT)

    assert runner._acp_enabled("manager-frontdoor-classify") is True
    assert runner._acp_enabled("manager-classify-fast") is True
    assert runner._acp_enabled("manager-classify-grounded") is True
    assert runner._acp_enabled("simple-1") is True
    assert (
        runner._acp_enabled(
            "simple-1",
            RunnerOptions(sandbox_mode="read-only"),
        )
        is True
    )
    assert runner._acp_enabled("engineer-1") is False

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP", "0")
    assert runner._acp_enabled("simple-1") is False


def test_front_door_label_takes_acp_and_never_spawns_cli(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP", "1")
    acp_proc = _FakeAcpProc()
    commands: list[list[str]] = []

    # NOTE: copilot_acp.subprocess and agent_cli_runner.subprocess are the SAME
    # module object, so ONE patched Popen must dispatch by argv: an ``--acp``
    # command is the warm client; a plain copilot command is the CLI spawn (must
    # NOT happen for the front-door label).
    def _popen(cmd, *a, **k):
        if "--acp" in cmd:
            commands.append(cmd)
            return acp_proc
        raise AssertionError("front-door classify must NOT spawn the copilot CLI")

    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen", _popen)

    runner = AgentCliRunner("copilot-bin", backend=BACKEND_COPILOT)
    r = runner.run_exec(
        prompt="你好",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="manager-frontdoor-classify",
    )
    assert r.exit_code == 0 and r.turn_completed
    assert r.agent_messages == ["CONFIG: NONE\nROUTE: SELF"]
    assert commands == [
        [
            "copilot-bin",
            "--acp",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--available-tools=",
        ]
    ]
    assert any(w.get("method") == "session/prompt" for w in acp_proc.written)


def test_manager_fast_route_takes_lean_acp_and_never_spawns_cli(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP", "1")
    acp_proc = _FakeAcpProc()
    commands: list[list[str]] = []

    def _popen(cmd, *a, **k):
        if "--acp" in cmd:
            commands.append(cmd)
            return acp_proc
        raise AssertionError("Manager fast route must NOT spawn the one-shot CLI")

    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen", _popen)

    runner = AgentCliRunner("copilot-bin", backend=BACKEND_COPILOT)
    result = runner.run_exec(
        prompt="classify the handoff",
        resume_thread_id=None,
        options=RunnerOptions(model="model-x", reasoning_effort="low"),
        run_label="manager-classify-fast",
    )

    assert result.exit_code == 0 and result.turn_completed
    assert commands == [[
        "copilot-bin",
        "--acp",
        "--model",
        "model-x",
        "--reasoning-effort",
        "low",
        "--no-custom-instructions",
        "--disable-builtin-mcps",
        "--available-tools=",
    ]]


def test_manager_grounded_route_takes_read_only_acp(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP", "1")
    acp_proc = _FakeAcpProc()
    commands: list[list[str]] = []

    def _popen(cmd, *a, **k):
        if "--acp" in cmd:
            commands.append(cmd)
            return acp_proc
        raise AssertionError("Manager grounded route must NOT spawn the one-shot CLI")

    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen", _popen)

    runner = AgentCliRunner("copilot-bin", backend=BACKEND_COPILOT)
    result = runner.run_exec(
        prompt="inspect and classify the handoff",
        resume_thread_id=None,
        options=RunnerOptions(
            model="model-x",
            reasoning_effort="low",
            sandbox_mode="read-only",
        ),
        run_label="manager-classify-grounded",
    )

    assert result.exit_code == 0 and result.turn_completed
    assert commands == [[
        "copilot-bin",
        "--acp",
        "--model",
        "model-x",
        "--reasoning-effort",
        "low",
        "--available-tools",
        "view,grep,glob",
        "--allow-tool",
        "view,grep,glob",
    ]]


@pytest.mark.parametrize("run_label", ["simple-1", "chat-1"])
def test_manager_reply_labels_take_acp_and_never_spawn_cli(
    monkeypatch,
    run_label: str,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP", "1")
    acp_proc = _FakeAcpProc()
    commands: list[list[str]] = []

    def _popen(cmd, *a, **k):
        if "--acp" in cmd:
            commands.append(cmd)
            return acp_proc
        raise AssertionError("Manager reply must NOT spawn the one-shot CLI")

    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen", _popen)

    runner = AgentCliRunner("copilot-bin", backend=BACKEND_COPILOT)
    r = runner.run_exec(
        prompt="do X",
        resume_thread_id=None,
        options=RunnerOptions(
            model="model-x",
            reasoning_effort="xhigh",
            sandbox_mode="read-only",
            add_dirs=["/state/session"],
        ),
        run_label=run_label,
    )
    assert r.exit_code == 0 and r.turn_completed
    assert commands == [
        [
            "copilot-bin",
            "--acp",
            "--model",
            "model-x",
            "--reasoning-effort",
            "xhigh",
            "--available-tools",
            "view,grep,glob",
            "--allow-tool",
            "view,grep,glob",
            "--add-dir",
            "/state/session",
        ]
    ]
    assert any(w.get("method") == "session/prompt" for w in acp_proc.written)


def test_mission_label_stays_on_cli_even_with_acp_on(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP", "1")
    reached = {"cli": False}

    def _popen(cmd, *a, **k):
        if "--acp" in cmd:
            raise AssertionError("engineer mission must NOT use ACP")
        reached["cli"] = True
        raise RuntimeError("cli-path-reached")

    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen", _popen)

    runner = AgentCliRunner("copilot-bin", backend=BACKEND_COPILOT)
    with pytest.raises(RuntimeError, match="cli-path-reached"):
        runner.run_exec(
            prompt="do X",
            resume_thread_id=None,
            options=RunnerOptions(),
            run_label="engineer-1",
        )
    assert reached["cli"] is True
