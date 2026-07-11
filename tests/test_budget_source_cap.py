"""Source-level per-mission budget cap.

The cap used to be checked only at ROUND START (the F3 breaker in
engineer/runner.py) — within a round, the engineer + reviewer + subagent calls
could all spend and overshoot before the next check. These pin the SOURCE gate:
``AgentCliRunner.run_exec`` refuses to spawn a new LLM call the moment the
composed interrupt provider (which now includes the live budget guard) trips, so
no round can overspend past the cap.
"""
from __future__ import annotations

import pytest

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.agent_cli import agent_cli_runner
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import BACKEND_COPILOT
from argus_skill.apps._runtime import _budget_reason_provider, _SkillLoopRunner
from argus_skill.core import cost_control


# ── the _budget_reason_provider helper ────────────────────────────────────────
class _Budget:
    def __init__(self, cap: float, spent: float, exceeded: bool) -> None:
        self.cap_usd = cap
        self._spent = spent
        self._exceeded = exceeded

    def exceeded(self) -> bool:
        return self._exceeded

    def spent(self) -> float:
        return self._spent


def test_budget_provider_trips_with_amounts() -> None:
    p = _budget_reason_provider(_Budget(5.0, 6.1, exceeded=True))
    reason = p()
    assert reason and "exhausted" in reason and "5.00" in reason and "6.10" in reason


def test_budget_provider_none_when_under_cap() -> None:
    p = _budget_reason_provider(_Budget(30.0, 2.0, exceeded=False))
    assert p() is None
    assert p.cap_usd == 30.0
    assert p.remaining_usd() == 28.0


def test_budget_provider_none_without_budget_or_exceeded() -> None:
    assert _budget_reason_provider(None) is None
    assert _budget_reason_provider(object()) is None  # no .exceeded → unenforced


# ── the run_exec source gate ─────────────────────────────────────────────────
def test_run_exec_refuses_when_provider_trips_without_spawning(monkeypatch) -> None:
    # If Popen is reached the gate FAILED to block, so make it explode.
    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")))
    r = AgentCliRunner("copilot-bin", backend=BACKEND_COPILOT)
    res = r.run_exec(
        prompt="x", resume_thread_id="t1",
        options=RunnerOptions(external_interrupt_reason_provider=lambda: "budget $5 exhausted"),
        run_label="engineer",
    )
    assert res.exit_code == -1
    assert res.turn_failed is True and res.turn_completed is False
    assert "refused before start: budget $5 exhausted" in (res.fatal_error or "")
    assert res.thread_id == "t1"


def test_run_exec_proceeds_when_provider_returns_none(monkeypatch) -> None:
    # A non-tripping provider must NOT block — the gate is a pure no-op, so Popen
    # IS reached (we sentinel it to prove the call got that far).
    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached-popen")))
    r = AgentCliRunner("copilot-bin", backend=BACKEND_COPILOT)
    with pytest.raises(RuntimeError, match="reached-popen"):
        r.run_exec(prompt="x", resume_thread_id=None,
                   options=RunnerOptions(external_interrupt_reason_provider=lambda: None),
                   run_label="engineer")


# ── AgentCliBackend composes the budget guard into the chain ─────────────────
def test_backend_budget_guard_refuses_run_exec(monkeypatch) -> None:
    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")))
    be = AgentCliBackend(backend="copilot")  # construction does not spawn
    be.set_budget_reason_provider(lambda: "per-mission budget $5.00 exhausted (spent $6.10)")
    res = be.run_exec(prompt="hi",
                      options=__import__("argus_skill.core.models", fromlist=["RunnerOptions"]).RunnerOptions(reasoning_effort="low"),
                      run_label="engineer")
    assert res.exit_code != 0
    assert "budget" in (res.fatal_error or "")


def test_backend_guard_clear_restores_normal(monkeypatch) -> None:
    from argus_skill.core.models import RunnerOptions as CoreOpts
    # AgentCliBackend.run_exec CATCHES runner exceptions and returns a result, so
    # a sentinel Popen error surfaces as fatal_error "reached-popen" — proving the
    # call got PAST the (cleared) gate to the spawn, not "refused before start".
    monkeypatch.setattr(agent_cli_runner.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached-popen")))
    be = AgentCliBackend(backend="copilot")
    be.set_budget_reason_provider(lambda: "over budget")
    be.set_budget_reason_provider(None)  # cleared
    res = be.run_exec(prompt="hi", options=CoreOpts(reasoning_effort="low"), run_label="engineer")
    assert "reached-popen" in (res.fatal_error or "")
    assert "refused before start" not in (res.fatal_error or "")


def test_backend_reservation_uses_effective_item_cap(monkeypatch, tmp_path) -> None:
    from argus_skill.core.models import RunnerOptions as CoreOpts

    seen = {}
    monkeypatch.setattr(cost_control, "cost_control_enabled", lambda: True)

    def _reserve(**kwargs):
        seen.update(kwargs)
        return None, "test stop after reservation capture"

    monkeypatch.setattr(cost_control, "reserve_call_budget", _reserve)
    be = AgentCliBackend(backend="copilot")
    be.set_usage_context(project_root=tmp_path, mission_id="mission-low-cap")
    be.set_budget_reason_provider(
        _budget_reason_provider(_Budget(2.5, 0.4, exceeded=False))
    )

    result = be.run_exec(
        prompt="hi",
        options=CoreOpts(reasoning_effort="low"),
        run_label="engineer-r1",
    )

    assert result.exit_code != 0
    assert seen["per_mission_cap_usd"] == 2.5
    assert seen["per_call_cap_usd"] == 5.0
    assert seen["mission_id"] == "mission-low-cap"


# ── _SkillLoopRunner sets/clears the guard on every role backend ─────────────
class _StubBackend:
    def __init__(self) -> None:
        self.provider = "unset"

    def set_budget_reason_provider(self, provider) -> None:
        self.provider = provider


def test_set_budget_guard_covers_all_role_backends() -> None:
    r = _SkillLoopRunner.__new__(_SkillLoopRunner)  # bypass heavy __init__
    r._backend = _StubBackend()
    r.engineer_backend = _StubBackend()
    r.reviewer_backend = _StubBackend()
    r.planner_backend = r.engineer_backend  # a shared instance appears ONCE
    r.curator_backend = None
    r.manager_backend = _StubBackend()

    guard = lambda: "boom"
    touched = r._set_budget_guard(guard)
    # distinct instances only (engineer==planner shared → counted once)
    assert len(touched) == 4
    assert all(b.provider is guard for b in touched)

    r._set_budget_guard(None)
    assert all(b.provider is None for b in touched)
