"""Tests for the per-role backend/model/effort + live-activity resolver
(cli/roles_status.py). Deterministic: every test passes an explicit ``env`` and
writes a synthetic ``events.jsonl``, so nothing depends on the real vault, real
env, or wall clock.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus_skill.cli.roles_status import (
    ROLES,
    format_roles_panel,
    is_reasoning_model,
    resolve_all_roles,
    resolve_role_config,
    role_activity,
)
from argus_skill.cli.theme import Theme


@pytest.fixture(autouse=True)
def _isolated_argus_skill_home(tmp_path, monkeypatch):
    """resolve_role_config's backend/model/effort resolution now also falls
    back to core.knob_store's persisted config.json (~/.argus-skill/config.json
    by default) when a test passes an env={} with nothing set for a given
    knob — isolate ARGUS_SKILL_HOME so these "falls back to the hard-coded
    default" tests never read (or race against) a REAL operator's persisted
    switches on this machine."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-skill-home"))


@pytest.fixture(autouse=True)
def _hermetic_capability_vault(monkeypatch, tmp_path):
    """Honor this module's "nothing depends on the real vault" contract even on a
    box that HAS a ``~/.argus-skill/capabilities/model_api.json``.

    Model resolution derives the vault path from the passed ``env``, and an
    ``env={}`` falls back to ``~/.argus-skill`` (NOT the ARGUS_SKILL_HOME
    isolated above) — so a developer whose local vault routes to (say)
    ``claude-sonnet-5`` would see the default-model assertions fail locally while
    they pass on CI (which has no such file). Pointing the vault at a nonexistent
    path makes every test read the code default (``gpt-5.5``) deterministically,
    on any box.
    """
    from argus_skill.tools import capability_vault

    monkeypatch.setattr(
        capability_vault,
        "default_vault_path",
        lambda env=None: tmp_path / "no-such-vault.json",
    )


# ── backend resolution + fallback chain ───────────────────────────────────

def test_backend_defaults_to_codex_when_unset():
    c = resolve_role_config("engineer", env={})
    assert c.backend == "codex" and c.backend_label == "Codex"


def test_per_role_backend_overrides_runner_and_life():
    env = {
        "ARGUS_SKILL_LIFE_BACKEND": "codex",
        "ARGUS_SKILL_RUNNER_BACKEND": "claude",
        "ARGUS_SKILL_REVIEWER_BACKEND": "copilot",
    }
    assert resolve_role_config("reviewer", env=env).backend_label == "Copilot"
    # engineer has no per-role override → falls back to RUNNER_BACKEND (claude)
    assert resolve_role_config("engineer", env=env).backend_label == "Claude Code"
    # planner also falls back to RUNNER_BACKEND
    assert resolve_role_config("planner", env=env).backend == "claude"


def test_life_backend_is_last_resort():
    env = {"ARGUS_SKILL_LIFE_BACKEND": "copilot"}
    assert resolve_role_config("manager", env=env).backend == "copilot"


def test_memory_backend_preserved():
    env = {"ARGUS_SKILL_LIFE_BACKEND": "memory"}
    assert resolve_role_config("engineer", env=env).backend == "memory"


# ── model resolution ──────────────────────────────────────────────────────

def test_explicit_role_model_env_wins():
    env = {"ARGUS_SKILL_ENGINEER_MODEL": "gpt-5.5-codex"}
    assert resolve_role_config("engineer", env=env).model == "gpt-5.5-codex"


def test_planner_reads_plan_model_env():
    env = {"ARGUS_SKILL_PLAN_MODEL": "o3"}
    assert resolve_role_config("planner", env=env).model == "o3"


def test_model_defaults_to_gpt55():
    # No env, no vault override in the test env → the offline default.
    c = resolve_role_config("reviewer", env={})
    assert c.model == "gpt-5.5"


# ── reasoning effort ──────────────────────────────────────────────────────

def test_effort_shown_for_reasoning_model_defaults_xhigh():
    c = resolve_role_config("engineer", env={"ARGUS_SKILL_ENGINEER_MODEL": "gpt-5.5"})
    assert c.effort == "xhigh"


def test_effort_none_for_non_reasoning_model():
    c = resolve_role_config("engineer", env={"ARGUS_SKILL_ENGINEER_MODEL": "gpt-4o-mini"})
    assert c.effort is None


def test_effort_env_override():
    env = {"ARGUS_SKILL_ENGINEER_MODEL": "gpt-5.5",
           "ARGUS_SKILL_ENGINEER_REASONING_EFFORT": "xhigh"}
    assert resolve_role_config("engineer", env=env).effort == "xhigh"


def test_manager_effort_mirrors_engineer():
    env = {"ARGUS_SKILL_ENGINEER_REASONING_EFFORT": "max"}
    assert resolve_role_config("manager", env=env).effort == "max"


def test_is_reasoning_model():
    assert is_reasoning_model("gpt-5.5")
    assert is_reasoning_model("gpt-5.5-codex")
    assert is_reasoning_model("o3")
    assert is_reasoning_model("o4-mini")
    assert not is_reasoning_model("gpt-4o")
    assert not is_reasoning_model("claude-3.5")
    assert not is_reasoning_model("")


# ── live activity from events.jsonl ────────────────────────────────────────

def _write_events(life_dir, events):
    (life_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


def test_activity_marks_latest_role_active(tmp_path):
    now = time.time()
    _write_events(tmp_path, [
        {"type": "round.review.completed", "status": "done", "ts": now - 120},
        {"type": "round.start", "round_index": 2, "ts": now - 30},
        {"type": "engineer.progress", "text": "/bin/bash -lc \"pytest -q\"", "ts": now - 5},
    ])
    acts = role_activity(tmp_path, now=now)
    assert acts["engineer"].active is True
    assert "run" in acts["engineer"].label and "pytest" in acts["engineer"].label
    # a completed reviewer verdict is NOT active
    assert acts["reviewer"].active is False
    assert acts["reviewer"].status == "done"


def test_review_deferral_is_engineer_activity(tmp_path):
    now = time.time()
    _write_events(tmp_path, [{
        "type": "round.review.deferred",
        "next_step": "wire the parser into the runner",
        "ts": now - 1,
    }])

    acts = role_activity(tmp_path, now=now)
    assert acts["engineer"].active is True
    assert acts["engineer"].label == "continuing before review"
    assert acts["reviewer"].active is False


def test_activity_reads_only_the_event_log_tail(tmp_path, monkeypatch):
    events = tmp_path / "events.jsonl"
    events.write_text(
        ("x" * (2 * 1024 * 1024))
        + "\n"
        + json.dumps({
            "type": "engineer.progress",
            "text": "tail event",
            "ts": time.time(),
        })
        + "\n",
        encoding="utf-8",
    )
    original_read_text = Path.read_text

    def reject_full_event_read(path, *args, **kwargs):
        if path == events:
            raise AssertionError("role activity must not read the whole event log")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_full_event_read)
    assert role_activity(tmp_path)["engineer"].label == "thinking · tail event"


def test_activity_orders_multiple_rollovers_chronologically(tmp_path):
    oldest = tmp_path / "events.jsonl.2"
    newer = tmp_path / "events.jsonl.3"
    oldest.write_text(
        "\n".join(
            json.dumps({
                "type": "engineer.progress",
                "text": f"old event {index}",
                "ts": 1.0,
            })
            for index in range(199)
        )
        + "\n",
        encoding="utf-8",
    )
    newer.write_text(
        json.dumps({
            "type": "engineer.progress",
            "text": "newest retained event",
            "ts": 2.0,
        })
        + "\n",
        encoding="utf-8",
    )

    assert role_activity(tmp_path, now=2.0)["engineer"].label == (
        "thinking · newest retained event"
    )


def test_activity_unwraps_shell_command(tmp_path):
    now = time.time()
    _write_events(tmp_path, [
        {"type": "engineer.progress", "text": "/bin/bash -lc \"git status --short\"", "ts": now - 2},
    ])
    label = role_activity(tmp_path, now=now)["engineer"].label
    assert "git status --short" in label
    assert "/bin/bash" not in label  # boilerplate stripped


def test_activity_stale_event_not_active(tmp_path):
    now = time.time()
    _write_events(tmp_path, [
        {"type": "engineer.progress", "text": "thinking", "ts": now - 600},
    ])
    acts = role_activity(tmp_path, now=now, active_window_s=90)
    assert acts["engineer"].active is False  # too old to be "now"


def test_activity_empty_when_no_events(tmp_path):
    acts = role_activity(tmp_path)
    for r in ROLES:
        assert acts[r].status == "idle" and acts[r].active is False


def test_activity_inactive_stale_role_decays_to_idle(tmp_path):
    # LIVE bug: an inactive role whose last event is ~2.7h old must decay to a
    # clean "idle" — not freeze its last (possibly verbose) label until it
    # scrolls out of the 200-line tail. Manager/Engineer were stuck on stale
    # content while Planner/Reviewer (no recent events) correctly read "idle".
    now = time.time()
    _write_events(tmp_path, [
        {"type": "life.manager.decision", "action": "hold",
         "reason": "The operator's only message was a greeting ('你好'), which "
                   "the engineer should not be interrupted for.",
         "ts": now - 9966},
        {"type": "loop.done", "status": "done", "ts": now - 9966},
    ])
    acts = role_activity(tmp_path, now=now)
    assert acts["manager"].active is False and acts["manager"].label == "idle"
    assert acts["engineer"].active is False and acts["engineer"].label == "idle"
    # age_s stays recorded (the panel de-emphasizes it, it is not zeroed)
    assert acts["manager"].age_s is not None and acts["manager"].age_s > 9000


def test_activity_manager_label_is_terse_not_prose(tmp_path):
    # A manager decision carries its reasoning as prose in text/reason; the
    # compact role panel must show a TERSE state token (its action), never a
    # truncated sentence — even while the manager is active/fresh.
    now = time.time()
    _write_events(tmp_path, [
        {"type": "life.manager.decision", "action": "hold",
         "reason": "The operator's only message was a greeting ('你好'), which "
                   "the engineer should not be interrupted for.",
         "ts": now - 3},
    ])
    lab = role_activity(tmp_path, now=now)["manager"].label
    assert lab == "hold"
    assert "operator" not in lab and "greeting" not in lab


def test_manager_stage_decision_is_terminal_not_active(tmp_path):
    now = time.time()
    _write_events(tmp_path, [{
        "type": "life.manager.stage_decision",
        "action": "advance",
        "current_stage": "inspect",
        "target_stage": "implement_cli",
        "ts": now - 3,
    }])

    manager = role_activity(tmp_path, now=now)["manager"]
    assert manager.label == "advance"
    assert manager.status == "done"
    assert manager.active is False


def test_activity_engineer_done_not_duplicated(tmp_path):
    # A terminal loop.done carrying status=="done" must render a single clean
    # "done", never the redundant "done · done".
    now = time.time()
    _write_events(tmp_path, [
        {"type": "loop.done", "status": "done", "ts": now - 3},
    ])
    lab = role_activity(tmp_path, now=now)["engineer"].label
    assert lab == "done"
    assert "·" not in lab


def test_activity_recognizes_concurrent_agent_io_without_leaking_stream_text(tmp_path):
    now = time.time()
    _write_events(tmp_path, [
        {"type": "agent.io.stream", "run_label": "engineer-r4",
         "line": "SECRET INTERNAL PAYLOAD", "ts": now - 2},
        {"type": "agent.io.stream", "run_label": "simple-1",
         "line": "[SESSION HANDOFF SECRET]", "ts": now - 1},
    ])
    acts = role_activity(tmp_path, now=now)
    assert acts["engineer"].active is True
    assert acts["engineer"].label == "round 4"
    assert acts["manager"].active is True
    assert acts["manager"].label == "handling your message"
    assert "SECRET" not in acts["engineer"].label + acts["manager"].label


def test_activity_does_not_put_assistant_prose_in_role_bar(tmp_path):
    now = time.time()
    _write_events(tmp_path, [{
        "type": "engineer.progress", "kind": "assistant_message",
        "agent_layer": "reviewer", "text": "a very long private review paragraph",
        "ts": now - 1,
    }])
    assert role_activity(tmp_path, now=now)["reviewer"].label == "reporting progress"


# ── panel rendering ────────────────────────────────────────────────────────

def test_panel_default_is_compact_activity_only(tmp_path):
    now = time.time()
    _write_events(tmp_path, [
        {"type": "engineer.progress", "text": "pytest -q", "ts": now - 3},
    ])
    env = {"ARGUS_SKILL_REVIEWER_BACKEND": "copilot"}
    configs = resolve_all_roles(env=env)
    acts = role_activity(tmp_path, now=now)
    out = format_roles_panel(Theme(enabled=False, width=90), configs, acts)
    for title in ("Manager", "Planner", "Engineer", "Reviewer"):
        assert title in out
    assert "pytest -q" in out
    assert "Codex" not in out and "Copilot" not in out
    assert "gpt-5.5" not in out
    assert "ARGUS_SKILL_<ROLE>" not in out
    # the active engineer is marked with the filled dot
    assert "●" in out and "○" in out


def test_panel_detail_lists_roles_backends_models(tmp_path):
    env = {"ARGUS_SKILL_REVIEWER_BACKEND": "copilot"}
    configs = resolve_all_roles(env=env)
    acts = role_activity(tmp_path)
    out = format_roles_panel(
        Theme(enabled=False, width=90), configs, acts, show_config=True
    )
    assert "Codex" in out and "Copilot" in out
    assert "gpt-5.5" in out
    assert "effort xhigh" in out
    assert "ARGUS_SKILL_<ROLE>" in out


def test_panel_has_ansi_when_theme_enabled(tmp_path):
    configs = resolve_all_roles(env={})
    acts = role_activity(tmp_path)
    out = format_roles_panel(Theme(enabled=True, width=90), configs, acts)
    assert "\x1b[" in out


def test_panel_plain_when_theme_disabled(tmp_path):
    configs = resolve_all_roles(env={})
    acts = role_activity(tmp_path)
    out = format_roles_panel(Theme(enabled=False, width=90), configs, acts)
    assert "\x1b[" not in out


# ── no line ever reaches the terminal edge (live-redraw wrap guard) ─────────
# A panel line whose display width == the terminal width auto-wraps to a second
# screen row; the in-place `/roles watch` / live-mission redraw counts logical
# lines, so a wrap desyncs the cursor-up and duplicates the header. Guarantee
# every line stays ≤ width-1, at every width, plain and colored, even with
# pathologically long activity labels.

def _panel_max_width(theme, configs, acts, width, header_right):
    from argus_skill.cli.roles_status import _disp_width
    panel = format_roles_panel(
        theme, configs, acts, width=width, header_right=header_right
    )
    return max(_disp_width(ln) for ln in panel.split("\n"))


def test_panel_lines_never_reach_terminal_edge(tmp_path):
    from argus_skill.cli.roles_status import RoleActivity
    configs = resolve_all_roles(env={})
    long_cmd = ("python -m argus_skill.skills.pipeline_contracts "
                "refresh-manifest --project-root /a/very/long/path " + "跑基准测试" * 8)
    acts = {
        "engineer": RoleActivity(role="engineer", active=True, label=long_cmd,
                                 status="running", age_s=42.0),
        "reviewer": RoleActivity(role="reviewer", active=True, label="评审全链路" * 8,
                                 status="running", age_s=5.0),
    }
    for enabled in (False, True):
        theme = Theme(enabled=enabled, width=80)
        for width in (40, 60, 72, 80, 100, 120, 160, 200):
            for hr in ("● daemon 3880615", ""):
                mx = _panel_max_width(theme, configs, acts, width, hr)
                assert mx <= width - 1, (
                    f"panel line width {mx} reaches edge at width={width} "
                    f"enabled={enabled} header={bool(hr)}"
                )


def test_panel_colored_clip_keeps_ansi_balanced():
    # The ANSI-safe clamp must never cut inside an escape sequence, and when it
    # cuts inside a color run it must re-append a reset so truncation can't bleed
    # color into the rest of the screen.
    from argus_skill.cli.roles_status import _clip_ansi_line, _disp_width
    colored = "\x1b[35m" + ("跑基准测试" * 8) + "\x1b[0m"
    clipped = _clip_ansi_line(colored, 20)
    assert _disp_width(clipped) <= 20
    assert clipped.endswith("\x1b[0m")           # reset re-appended
    assert "\x1b[3" not in clipped[len("\x1b[35m"):] or clipped.count("\x1b[") >= 2
    # a plain string is returned untouched when it already fits
    assert _clip_ansi_line("short", 20) == "short"
    # never splits an escape: no lone "\x1b[" without a terminating letter
    import re
    leftover = re.sub(r"\x1b\[[0-9;]*m", "", clipped)
    assert "\x1b" not in leftover


# ── startup banner block ───────────────────────────────────────────────────

def test_banner_lists_all_four_roles_by_default():
    from argus_skill.cli.roles_status import format_roles_banner
    out = format_roles_banner(Theme(enabled=False, width=100), env={})
    for title in ("Manager", "Planner", "Engineer", "Reviewer"):
        assert title in out
    assert "Codex" in out and "gpt-5.5" in out and "effort xhigh" in out
    assert "/roles" in out  # pointer to the live panel


def test_banner_collapse_folds_identical_roles():
    from argus_skill.cli.roles_status import format_roles_banner
    out = format_roles_banner(Theme(enabled=False, width=100), collapse=True, env={})
    # Collapsed → single line, role names not spelled out.
    assert out.count("\n") == 0
    assert "Codex" in out and "gpt-5.5" in out


def test_banner_can_omit_hint_for_minimal_startup():
    from argus_skill.cli.roles_status import format_roles_banner
    out = format_roles_banner(
        Theme(enabled=False, width=100), collapse=True, env={}, show_hint=False
    )
    assert "/roles" not in out
    assert "Codex" in out and "effort xhigh" in out


def test_banner_shows_rows_when_backends_differ_even_if_collapse():
    from argus_skill.cli.roles_status import format_roles_banner
    env = {"ARGUS_SKILL_REVIEWER_BACKEND": "claude"}
    out = format_roles_banner(Theme(enabled=False, width=100), collapse=True, env=env)
    assert "Reviewer" in out and "Claude Code" in out
    assert out.count("\n") >= 3  # per-role rows, not collapsed


def test_banner_plain_when_theme_disabled():
    from argus_skill.cli.roles_status import format_roles_banner
    out = format_roles_banner(Theme(enabled=False, width=100), env={})
    assert "\x1b[" not in out


# ── prompt status line (repeating, drawn every REPL turn) ─────────────────

def test_prompt_status_line_shows_shared_backend_and_model():
    from argus_skill.cli.roles_status import format_prompt_status_line
    out = format_prompt_status_line(Theme(enabled=False, width=100), env={})
    assert out == "Codex · gpt-5.5"


def test_prompt_status_line_reflects_a_switched_backend():
    from argus_skill.cli.roles_status import format_prompt_status_line
    env = {"ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_MODEL": "claude-sonnet-5"}
    out = format_prompt_status_line(Theme(enabled=False, width=100), env=env)
    assert out == "Copilot · claude-sonnet-5"


def test_prompt_status_line_shows_mixed_hint_when_roles_differ():
    from argus_skill.cli.roles_status import format_prompt_status_line
    env = {"ARGUS_SKILL_REVIEWER_BACKEND": "claude"}
    out = format_prompt_status_line(Theme(enabled=False, width=100), env=env)
    assert "mixed" in out
    assert "/roles" in out


def test_prompt_status_line_plain_when_theme_disabled():
    from argus_skill.cli.roles_status import format_prompt_status_line
    out = format_prompt_status_line(Theme(enabled=False, width=100), env={})
    assert "\x1b[" not in out


# ── prompt activity suffix: the multi-agent-specific "who's driving now" bit,
# missing from every single-agent reference tool (Codex CLI / Claude Code) by
# construction, since they only ever have one actor. ───────────────────────

def test_prompt_activity_suffix_empty_when_no_life_dir():
    from argus_skill.cli.roles_status import format_prompt_activity_suffix
    assert format_prompt_activity_suffix(None) == ""


def test_prompt_activity_suffix_empty_when_nothing_active(tmp_path):
    from argus_skill.cli.roles_status import format_prompt_activity_suffix
    # No events.jsonl at all — role_activity() reports every role idle.
    assert format_prompt_activity_suffix(tmp_path) == ""


def test_prompt_activity_suffix_names_the_active_role(tmp_path):
    from argus_skill.cli.roles_status import format_prompt_activity_suffix
    now = time.time()
    _write_events(tmp_path, [
        {"type": "engineer.progress", "text": "running pytest", "ts": now - 2},
    ])
    out = format_prompt_activity_suffix(tmp_path, Theme(enabled=False, width=100))
    assert out == "● Engineer"


def test_prompt_activity_suffix_fails_soft_on_unreadable_life_dir(tmp_path):
    from argus_skill.cli.roles_status import format_prompt_activity_suffix
    (tmp_path / "events.jsonl").write_text("not json at all{{{", encoding="utf-8")
    # Corrupt lines are skipped by role_activity's own JSON parsing (fail-soft
    # at that layer already); the suffix must still resolve to "" cleanly.
    assert format_prompt_activity_suffix(tmp_path) == ""


def test_prompt_status_line_appends_active_role_when_life_dir_given(tmp_path):
    from argus_skill.cli.roles_status import format_prompt_status_line
    now = time.time()
    _write_events(tmp_path, [
        {"type": "round.review.started", "ts": now - 1},
    ])
    out = format_prompt_status_line(
        Theme(enabled=False, width=100), env={}, life_dir=tmp_path,
    )
    assert out == "Codex · gpt-5.5  ● Reviewer"


def test_prompt_status_line_unchanged_when_life_dir_omitted():
    """Byte-for-byte prior behaviour when the caller does not pass life_dir
    (the default) — no regression for any existing caller/test."""
    from argus_skill.cli.roles_status import format_prompt_status_line
    out = format_prompt_status_line(Theme(enabled=False, width=100), env={})
    assert out == "Codex · gpt-5.5"
