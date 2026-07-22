from __future__ import annotations

import signal
from types import SimpleNamespace

from argus_skill.agent_cli import agent_cli_runner
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner


class _FakeProcess:
    pid = 4242

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        raise AssertionError("POSIX termination must target the process group")

    def kill(self):
        raise AssertionError("POSIX termination must target the process group")


def test_terminate_process_escalates_the_whole_posix_group(monkeypatch) -> None:
    signals: list[tuple[int, signal.Signals]] = []
    waits = iter([False, True])

    monkeypatch.setattr(agent_cli_runner.os, "name", "posix")
    monkeypatch.setattr(
        agent_cli_runner.os,
        "killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_wait_process_group_exit",
        classmethod(lambda cls, pgid, timeout: next(waits)),
    )

    AgentCliRunner._terminate_process(_FakeProcess())

    assert signals == [
        (4242, signal.SIGTERM),
        (4242, signal.SIGKILL),
    ]


def test_normal_turn_exit_cleans_only_its_remaining_process_group(
    monkeypatch,
) -> None:
    alive = iter([True, False])
    terminated: list[int] = []
    state = SimpleNamespace(
        orphan_process_group_id=0,
        orphan_process_group_cleanup_succeeded=False,
        process_group_cleanup_checked=False,
    )

    monkeypatch.setattr(agent_cli_runner.os, "name", "posix")
    monkeypatch.setattr(
        AgentCliRunner,
        "_process_group_alive",
        staticmethod(lambda _pgid: next(alive)),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_terminate_process",
        classmethod(lambda cls, process: terminated.append(process.pid)),
    )

    AgentCliRunner._cleanup_orphan_process_group(_FakeProcess(), state)

    assert terminated == [4242]
    assert state.orphan_process_group_id == 4242
    assert state.orphan_process_group_cleanup_succeeded is True
