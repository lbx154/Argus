"""The continuous-resume gate: a fresh/manual daemon must NOT silently adopt a
project's persisted continuous campaign. Only an explicit resume intent
(``--continuous`` / ``--resume-continuous``) — which supervisors pass on a
crash/reboot self-heal — resumes it. A bare cockpit/daemon may still wait for
the Manager to derive an objective from the first substantive user prompt.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus_skill.daemon._life_worker_identity import (
    _refresh_file_backed_objective_for_resume,
    _write_manager_handoff_identity,
)
from argus_skill.daemon.life_worker import (
    _apply_continuous_suppression,
    _rearm_operator_drain_for_resume,
)
from argus_skill.daemon.state import (
    GRACEFUL_STOP_REASON,
    read_continuous_state,
    write_continuous_config,
)

# ---- parser: the daemon-level opt-in flag exists, off by default -----------

def test_resume_continuous_flag_parses():
    from argus_skill.apps.cli._parser import build_parser

    p = build_parser()
    assert p.parse_args(["--daemon-fg"]).resume_continuous is False
    assert p.parse_args(["--daemon-fg", "--resume-continuous"]).resume_continuous is True


# ---- suppression helper ----------------------------------------------------

def test_suppression_hides_stale_boot_campaign():
    # A fresh daemon booted with a persisted enabled campaign it did not resume:
    state = {"active": True, "objective": "run the campaign"}
    # The same stale campaign is reported DISABLED — not adopted.
    assert _apply_continuous_suppression(state, True, "run the campaign") == (
        False, "run the campaign",
    )
    assert state["active"] is True  # still suppressing


def test_suppression_lifts_on_operator_rearm():
    state = {"active": True, "objective": "run the campaign"}
    # Operator re-arms live with a DIFFERENT objective -> suppression lifts and
    # the new campaign is honored.
    assert _apply_continuous_suppression(state, True, "a NEW objective") == (
        True, "a NEW objective",
    )
    assert state["active"] is False
    # once lifted, subsequent reads pass through unchanged
    assert _apply_continuous_suppression(state, True, "run the campaign") == (
        True, "run the campaign",
    )


def test_suppression_lifts_on_same_objective_new_generation():
    state = {
        "active": True,
        "objective": "run the campaign",
        "generation": 4,
    }

    assert _apply_continuous_suppression(
        state,
        True,
        "run the campaign",
        generation=5,
    ) == (True, "run the campaign")
    assert state["active"] is False


def test_suppression_lifts_when_campaign_disabled():
    state = {"active": True, "objective": "run the campaign"}
    # The campaign being turned off is also a change -> lifts suppression.
    assert _apply_continuous_suppression(state, False, "run the campaign") == (
        False, "run the campaign",
    )
    assert state["active"] is False


def test_no_suppression_is_passthrough():
    # A resume-intent daemon (or no stale campaign) never suppresses.
    state = {"active": False, "objective": ""}
    assert _apply_continuous_suppression(state, True, "obj") == (True, "obj")


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


def test_resume_continuous_rearms_a_graceful_operator_stop(tmp_path: Path) -> None:
    """Restarting a daemon onto new code must not retire its campaign.

    SIGTERM is how an operator restarts a daemon, and it quiesced continuous
    mode with a different reason string than drain did. Only drain was
    re-armed, so the daemon came back, drained its backlog and went quiet
    forever while still reporting healthy.
    """
    write_continuous_config(tmp_path, enabled=True, objective="continue the campaign")
    write_continuous_config(
        tmp_path,
        enabled=False,
        objective="continue the campaign",
        done_reason=GRACEFUL_STOP_REASON,
    )

    state = _rearm_operator_drain_for_resume(
        cfg=SimpleNamespace(continuous=False, resume_continuous=True),
        runtime_root=tmp_path,
        state=read_continuous_state(tmp_path),
    )

    assert state.enabled is True
    assert state.objective == "continue the campaign"
    assert state.done_reason == ""


def test_a_finished_campaign_is_not_restarted_by_a_restart(tmp_path: Path) -> None:
    """Stopping the process is resumable; finishing the work is not."""
    write_continuous_config(tmp_path, enabled=True, objective="continue the campaign")
    write_continuous_config(
        tmp_path,
        enabled=False,
        objective="continue the campaign",
        done_reason="planner declared project done",
    )

    state = _rearm_operator_drain_for_resume(
        cfg=SimpleNamespace(continuous=False, resume_continuous=True),
        runtime_root=tmp_path,
        state=read_continuous_state(tmp_path),
    )

    assert state.enabled is False


def test_resume_continuous_preserves_operator_authority_hold(
    tmp_path: Path,
) -> None:
    write_continuous_config(
        tmp_path,
        enabled=False,
        objective="continue the campaign",
        done_reason="operator authority hold: new scope is not authorized",
    )
    before = read_continuous_state(tmp_path)

    state = _rearm_operator_drain_for_resume(
        cfg=SimpleNamespace(continuous=False, resume_continuous=True),
        runtime_root=tmp_path,
        state=before,
    )

    assert state == before
    assert read_continuous_state(tmp_path) == before


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


def test_stale_file_identity_does_not_override_newer_objective(
    tmp_path: Path,
) -> None:
    objective_file = tmp_path / "OBJECTIVE.md"
    objective_file.write_text("old source objective", encoding="utf-8")
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="old Manager task",
    )
    old_state = read_continuous_state(tmp_path)
    assert _write_manager_handoff_identity(
        tmp_path,
        objective=old_state.objective,
        vertical="kernel_engineering",
        domain="",
        continuous_generation=old_state.generation,
        intent_id="intent-1",
        source_objective=objective_file.read_text(encoding="utf-8"),
        source_objective_path=str(objective_file),
    )
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="new cockpit objective",
    )
    state = read_continuous_state(tmp_path)
    objective_file.write_text("changed old source", encoding="utf-8")
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
