from __future__ import annotations

import signal

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
