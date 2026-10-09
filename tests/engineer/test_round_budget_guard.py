from __future__ import annotations

import time
from pathlib import Path

import pytest

from argus.core.budget_signal import take_mission_budget_pause
from argus.core.usage import UsageLedger, UsageRecord
from argus.engineer.round_budget_guard import mission_budget_terminal
from argus.engineer.round_config import SupervisedConfig
from argus.engineer.round_state import RoundLoopState


def _spend(root: Path, item_id: str, count: int) -> None:
    now = time.time()
    UsageLedger(root, migrate_legacy=False).append_many([
        UsageRecord(
            call_id=f"{item_id}-{index}", project_id="p", mission_id=f"{item_id}:attempt:1",
            provider="copilot", model="m", run_label="engineer", started_at=now - 1,
            completed_at=now, status="succeeded", input_tokens=1, cached_input_tokens=0,
            output_tokens=1, reasoning_output_tokens=0, premium_requests=1.0,
            pricing_status="priced", pricing_tier="t", cost_usd=0.04, cost_basis="provider",
        )
        for index in range(count)
    ])


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_MISSION_BUDGET_USD", raising=False)


def _config(root: Path) -> SupervisedConfig:
    return SupervisedConfig(session_id="item1", operator_question_policy_root=root)


def test_no_budget_never_pauses_however_much_was_spent(tmp_path: Path) -> None:
    _spend(tmp_path, "item1", 50)
    assert mission_budget_terminal(_config(tmp_path), RoundLoopState()) is None
    assert take_mission_budget_pause(tmp_path, "item1") is None


def test_under_budget_continues(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", "10")
    _spend(tmp_path, "item1", 9)
    _spend(tmp_path, "other", 30)
    assert mission_budget_terminal(_config(tmp_path), RoundLoopState()) is None


def test_reached_budget_pauses_for_the_operator_and_leaves_a_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", "10")
    _spend(tmp_path, "item1", 10)
    state = RoundLoopState(last_engineer_message="halfway")
    terminal = mission_budget_terminal(_config(tmp_path), state)
    assert terminal is not None
    status, rounds, message, reason, _thread = terminal
    assert status == "paused_operator"
    assert message == "halfway"
    assert "10 of 10 premium requests" in reason
    marker = take_mission_budget_pause(tmp_path, "item1")
    assert marker is not None and marker["spent"]["premium_requests"] == 10


def test_unreadable_ledger_does_not_stop_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MISSION_BUDGET_USD", "0.01")

    def broken(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk gone")

    monkeypatch.setattr("argus.core.budget_signal.mission_usage_summary", broken)
    assert mission_budget_terminal(_config(tmp_path), RoundLoopState()) is None


def test_budget_without_project_state_is_reported_not_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MISSION_BUDGET_REQUESTS", "1")
    config = SupervisedConfig(session_id="no-state-item", operator_question_policy_root=None)
    with caplog.at_level("WARNING"):
        assert mission_budget_terminal(config, RoundLoopState()) is None
    assert "cannot be enforced" in caplog.text


def test_enforceability_follows_checkpoint_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    from argus.core.budget_signal import mission_budget_enforceable

    monkeypatch.setenv("ARGUS_SKILL_CHECKPOINT_PERSIST", "0")
    assert mission_budget_enforceable() is False
    monkeypatch.setenv("ARGUS_SKILL_CHECKPOINT_PERSIST", "1")
    assert mission_budget_enforceable() is True
