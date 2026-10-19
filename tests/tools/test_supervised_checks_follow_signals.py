"""A supervised run is judged when its signals move, not when the clock ticks.

The health check once asked the supervisor model about the same stdout/stderr
tails on every interval: a silent or wedged run got a model call every two
minutes for as long as it lived, and holding a GPU lease capped every run's
interval at a third of the lease TTL however healthy it was.
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
        timeout=None,
        monitor_interval=monitor_interval,
        model="model",
        cwd=str(tmp_path),
        preflight=False,
    )
    return calls


def test_unchanged_signals_are_judged_once(monkeypatch, tmp_path) -> None:
    calls = _run(monkeypatch, tmp_path, _TimesOut(waits=40), tails=lambda *_a: "step 7 loss 0.4")
    assert len(calls) == 1


def test_every_new_signal_is_judged(monkeypatch, tmp_path) -> None:
    lines = iter(f"step {n}" for n in range(1000))
    calls = _run(monkeypatch, tmp_path, _TimesOut(waits=6), tails=lambda *_a: next(lines))
    assert len(calls) == 6


def test_a_silent_run_gets_one_more_look_then_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(_supervised_run, "_SILENT_RUN_RECHECK_SECONDS", 0.0)
    calls = _run(monkeypatch, tmp_path, _TimesOut(waits=40), tails=lambda *_a: "")
    assert len(calls) == 2
    assert calls[0]["silent_seconds"] == 0.0
    assert calls[1]["silent_seconds"] >= 0.0


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
