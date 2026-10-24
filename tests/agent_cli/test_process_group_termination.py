from __future__ import annotations

import signal
from types import SimpleNamespace

from argus.agent_cli import _process_control
from argus.agent_cli.agent_cli_runner import AgentCliRunner


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
    signals: list[tuple[int, int | signal.Signals]] = []
    waits = iter([False, True])
    sigkill = getattr(signal, "SIGKILL", 9)

    monkeypatch.setattr(
        _process_control,
        "os",
        SimpleNamespace(
            name="posix",
            killpg=lambda pgid, sig: signals.append((pgid, sig)),
        ),
    )
    monkeypatch.setattr(
        _process_control,
        "signal",
        SimpleNamespace(SIGTERM=signal.SIGTERM, SIGKILL=sigkill),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_wait_process_group_exit",
        classmethod(lambda cls, pgid, timeout: next(waits)),
    )

    AgentCliRunner._terminate_process(_FakeProcess())

    assert signals == [
        (4242, signal.SIGTERM),
        (4242, sigkill),
    ]
