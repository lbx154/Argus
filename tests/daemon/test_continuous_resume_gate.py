"""The continuous-resume gate: a fresh/manual daemon must NOT silently adopt a
project's persisted continuous campaign. Only an explicit resume intent
(``--continuous`` / ``--resume-continuous``) — which supervisors pass on a
crash/reboot self-heal — resumes it. A bare cockpit/daemon may still wait for
the Manager to derive an objective from the first substantive user prompt.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

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


# ---- entry gate: objective may be supplied later by the Manager ------------


def _args(**kw):
    base = dict(objective="", continuous=False, resume_continuous=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_bare_daemon_can_wait_for_manager_objective(monkeypatch):
    import argus_skill.apps.cli._core as core

    monkeypatch.setattr(
        "argus_skill.life.special_prompts.describe_special_prompt_gate",
        lambda: (True, ""),
    )
    assert core._lifetime_entry_error(_args()) == ""


def test_lifetime_entry_still_requires_special_prompt(monkeypatch):
    import argus_skill.apps.cli._core as core

    monkeypatch.setattr(
        "argus_skill.life.special_prompts.describe_special_prompt_gate",
        lambda: (False, "trusted special prompt required"),
    )
    assert core._lifetime_entry_error(_args()) == "trusted special prompt required"


def test_resume_continuous_entry_allowed_with_special_prompt(monkeypatch):
    import argus_skill.apps.cli._core as core

    # special-prompt gate is orthogonal here — force it open so we isolate the
    # lifetime entry path.
    monkeypatch.setattr(
        "argus_skill.life.special_prompts.describe_special_prompt_gate",
        lambda: (True, ""),
    )
    assert core._lifetime_entry_error(_args(resume_continuous=True)) == ""


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
