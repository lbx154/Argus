"""The continuous-resume gate: a fresh/manual daemon must NOT silently adopt a
project's persisted continuous campaign. Only an explicit resume intent
(``--continuous`` / ``--resume-continuous``) — which supervisors pass on a
crash/reboot self-heal — resumes it. A bare cockpit/daemon may still wait for
the Manager to derive an objective from the first substantive user prompt.
"""
from __future__ import annotations

import argparse

from argus_skill.daemon.life_worker import _apply_continuous_suppression

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
