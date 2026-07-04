"""Tests for the per-role backend/model/effort + live-activity resolver
(cli/roles_status.py). Deterministic: every test passes an explicit ``env`` and
writes a synthetic ``events.jsonl``, so nothing depends on the real vault, real
env, or wall clock.
"""
from __future__ import annotations

import json
import time

from argus_skill.cli.roles_status import (
    ROLES,
    format_roles_panel,
    is_reasoning_model,
    resolve_all_roles,
    resolve_role_config,
    role_activity,
)
from argus_skill.cli.theme import Theme


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

def test_effort_shown_for_reasoning_model_defaults_high():
    c = resolve_role_config("engineer", env={"ARGUS_SKILL_ENGINEER_MODEL": "gpt-5.5"})
    assert c.effort == "high"


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
    assert "跑命令" in acts["engineer"].label and "pytest" in acts["engineer"].label
    # a completed reviewer verdict is NOT active
    assert acts["reviewer"].active is False
    assert acts["reviewer"].status == "done"


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


# ── panel rendering ────────────────────────────────────────────────────────

def test_panel_lists_all_roles_backends_models(tmp_path):
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
    assert "Codex" in out and "Copilot" in out
    assert "gpt-5.5" in out
    assert "effort high" in out
    # the active engineer is marked with the filled dot
    assert "●" in out and "○" in out


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
    assert "Codex" in out and "gpt-5.5" in out and "effort high" in out
    assert "/roles" in out  # pointer to the live panel


def test_banner_collapse_folds_identical_roles():
    from argus_skill.cli.roles_status import format_roles_banner
    out = format_roles_banner(Theme(enabled=False, width=100), collapse=True, env={})
    # Collapsed → single line, role names not spelled out.
    assert out.count("\n") == 0
    assert "Codex" in out and "gpt-5.5" in out


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
