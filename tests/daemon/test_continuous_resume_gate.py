"""The continuous-resume gate: a fresh/manual daemon must NOT silently adopt a
project's persisted continuous campaign. Only an explicit resume intent
(``--continuous`` / ``--resume-continuous``) — which supervisors pass on a
crash/reboot self-heal — resumes it. See ``_apply_continuous_suppression`` and
``_lifetime_entry_error``.
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


# ---- entry gate: bare daemon does not adopt the persisted objective --------

class _Bundle:
    class _P:
        root = "/tmp/fake-life-dir"
    project = _P()
    global_root = "/tmp/fake-global"


def _args(**kw):
    base = dict(objective="", continuous=False, resume_continuous=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_bare_daemon_does_not_adopt_persisted_objective(monkeypatch):
    import argus_skill.apps.cli._core as core

    monkeypatch.setattr(core, "_resolve_project_bundle", lambda args: _Bundle())
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_continuous_config",
        lambda root: (True, "someone else's armed campaign"),
    )
    # No objective, no resume intent -> must NOT pick up the persisted campaign;
    # returns the actionable "no objective" error mentioning --resume-continuous.
    err = core._lifetime_entry_error(_args())
    assert err
    assert "resume-continuous" in err


def test_resume_continuous_adopts_persisted_objective(monkeypatch):
    import argus_skill.apps.cli._core as core

    monkeypatch.setattr(core, "_resolve_project_bundle", lambda args: _Bundle())
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_continuous_config",
        lambda root: (True, "the persisted campaign"),
    )
    # special-prompt gate is orthogonal here — force it open so we isolate the
    # objective-adoption path.
    monkeypatch.setattr(
        "argus_skill.life.special_prompts.describe_special_prompt_gate",
        lambda: (True, ""),
    )
    # With --resume-continuous the persisted objective IS adopted -> no error.
    assert core._lifetime_entry_error(_args(resume_continuous=True)) == ""
