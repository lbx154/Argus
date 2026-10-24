"""The continuous-resume gate: a fresh/manual daemon must NOT silently adopt a
project's persisted continuous campaign. Only an explicit resume intent
(``--continuous`` / ``--resume-continuous``) — which supervisors pass on a
crash/reboot self-heal — resumes it. A bare cockpit/daemon may still wait for
the Manager to derive an objective from the first substantive user prompt.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.daemon._life_worker_identity import (
    _refresh_file_backed_objective_for_resume,
    _write_manager_handoff_identity,
)
from argus.daemon.life_worker import (
    LifeWorker,
    LifeWorkerConfig,
    _apply_continuous_suppression,
    _rearm_operator_drain_for_resume,
    _RunForeverState,
)
from argus.daemon.state import (
    read_continuous_state,
    write_continuous_config,
)

# ---- parser: the daemon-level opt-in flag exists, off by default -----------


# ---- suppression helper ----------------------------------------------------


def test_suppression_lifts_when_campaign_disabled():
    state = {"active": True, "objective": "run the campaign"}
    # The campaign being turned off is also a change -> lifts suppression.
    assert _apply_continuous_suppression(state, False, "run the campaign") == (
        False, "run the campaign",
    )
    assert state["active"] is False


def test_resume_continuous_rearms_operator_drain_stop(tmp_path: Path) -> None:
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="continue the campaign",
    )
    write_continuous_config(
        tmp_path,
        enabled=False,
        objective="continue the campaign",
        done_reason="operator drain-stop",
    )

    state = _rearm_operator_drain_for_resume(
        cfg=SimpleNamespace(continuous=False, resume_continuous=True),
        runtime_root=tmp_path,
        state=read_continuous_state(tmp_path),
    )

    assert state.enabled is True
    assert state.objective == "continue the campaign"
    assert state.done_reason == ""


@pytest.mark.parametrize("persisted_campaign", [False, True])
def test_default_worker_keeps_queued_work_bounded_without_adopting_a_campaign(
    tmp_path, monkeypatch, persisted_campaign,
):
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    if persisted_campaign:
        write_continuous_config(
            tmp_path, enabled=True, objective="unrelated old campaign", open_ended=True,
        )
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    state = _RunForeverState()
    state.cfg = worker.config
    state.runtime_root = tmp_path
    state.runner = SimpleNamespace(
        _args=SimpleNamespace(open_ended=True, continuous_objective="stale"),
    )

    worker._rf_resolve_continuous_boot_state(state)

    assert state.cfg.continuous_open_ended is True
    assert state.init_continuous is False
    assert state.runner._args.open_ended is False
    assert state.runner._args.continuous_objective == ""
    assert read_continuous_state(tmp_path).enabled is persisted_campaign


def test_resume_continuous_refreshes_changed_objective_file(tmp_path: Path) -> None:
    objective_file = tmp_path / "OBJECTIVE.md"
    objective_file.write_text("original operator objective", encoding="utf-8")
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="Manager-clean execution task",
    )
    state = read_continuous_state(tmp_path)
    assert _write_manager_handoff_identity(
        tmp_path,
        objective=state.objective,
        vertical="kernel_engineering",
        domain="",
        continuous_generation=state.generation,
        intent_id="intent-1",
        source_objective=objective_file.read_text(encoding="utf-8"),
        source_objective_path=str(objective_file),
    )
    objective_file.write_text("updated operator objective", encoding="utf-8")
    cfg = SimpleNamespace(
        continuous=False,
        resume_continuous=True,
        continuous_objective="",
        continuous_objective_file=None,
    )

    changed = _refresh_file_backed_objective_for_resume(
        cfg=cfg,
        runtime_root=tmp_path,
        state=state,
    )

    assert changed is True
    assert cfg.continuous is True
    assert cfg.continuous_objective == "updated operator objective"
    assert cfg.continuous_objective_file == objective_file.resolve()


def test_resume_continuous_keeps_unchanged_file_fast_path(tmp_path: Path) -> None:
    objective_file = tmp_path / "OBJECTIVE.md"
    objective_file.write_text("operator objective", encoding="utf-8")
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="Manager-clean execution task",
    )
    state = read_continuous_state(tmp_path)
    assert _write_manager_handoff_identity(
        tmp_path,
        objective=state.objective,
        vertical="kernel_engineering",
        domain="",
        continuous_generation=state.generation,
        intent_id="intent-1",
        source_objective=objective_file.read_text(encoding="utf-8"),
        source_objective_path=str(objective_file),
    )
    cfg = SimpleNamespace(
        continuous=False,
        resume_continuous=True,
        continuous_objective="",
        continuous_objective_file=None,
    )

    changed = _refresh_file_backed_objective_for_resume(
        cfg=cfg,
        runtime_root=tmp_path,
        state=state,
    )

    assert changed is False
    assert cfg.continuous is False
    assert cfg.continuous_objective == ""
