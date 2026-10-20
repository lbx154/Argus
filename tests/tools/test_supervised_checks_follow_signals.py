"""Holding a GPU lease does not pace the supervisor's looks.

Lease renewal once capped every run's check interval at a third of the lease
TTL however healthy it was. Looks on unchanged inputs are covered in
``test_subagent_supervisor.py``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus.tools.subagent import _supervised_run


class _TimesOut:
    """A child that is still running for ``waits`` waits, then exits cleanly."""

    pid = 4400
    returncode = 0

    def __init__(self, waits: int) -> None:
        self.waits = waits
        self.timeouts: list[float] = []

    def wait(self, timeout: float | None = None) -> int:
        if len(self.timeouts) < self.waits:
            self.timeouts.append(float(timeout or 0))
            raise subprocess.TimeoutExpired("training", timeout)
        return 0


def _healthy(*_args: object, **_kwargs: object) -> _supervised_run.SupervisorCheck:
    return _supervised_run.SupervisorCheck(
        decision="continue",
        health="healthy",
        concern="",
        thread_id=None,
        usage=(0, 0, 0, 0),
        error=None,
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    proc: _TimesOut,
    *,
    tails,
    monitor_interval: int = 1,
) -> list[dict]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        _supervised_run, "experiment_launch_preflight", lambda **_kwargs: (False, ""),
    )
    monkeypatch.setattr(_supervised_run, "_launch_durable_command", lambda **_kwargs: proc)
    monkeypatch.setattr(
        _supervised_run, "_persist_experiment_record", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(_supervised_run, "_alert_engineer", lambda *_args: "report")
    monkeypatch.setattr(_supervised_run, "_tail_file", tails)
    calls: list[dict] = []

    def check(*args: object, **kwargs: object) -> _supervised_run.SupervisorCheck:
        calls.append(dict(kwargs))
        return _healthy()

    monkeypatch.setattr(_supervised_run, "_supervisor_check_with_usage", check)
    _supervised_run._run_supervised(
        "training",
        "python train.py",
        "long GPU run",
        monitor_interval=monitor_interval,
        model="model",
        cwd=str(tmp_path),
        preflight=False,
    )
    return calls


def test_lease_renewal_never_triggers_a_model_check(monkeypatch, tmp_path) -> None:
    class _Lease:
        ttl_seconds = 3.0

        def __init__(self) -> None:
            self.renewals = 0

        def renew(self) -> bool:
            self.renewals += 1
            return True

        def release(self) -> None:
            pass

    lease = _Lease()
    monkeypatch.setattr(_supervised_run, "acquire_for_task", lambda *_a, **_k: lease)
    monkeypatch.setattr(_supervised_run, "command_env", lambda _lease: None)
    lines = iter(f"step {n}" for n in range(1000))
    proc = _TimesOut(waits=30)
    calls = _run(
        monkeypatch, tmp_path, proc, tails=lambda *_a: next(lines), monitor_interval=600,
    )
    # Every wait here ended at a renewal (1 s), never at the 600 s look, so the
    # changing logs were not judged: renewal has its own cadence.
    assert lease.renewals == 30
    assert calls == []
    assert max(proc.timeouts) <= 1.0
