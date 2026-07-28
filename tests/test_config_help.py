"""The operator-facing ARGUS_* knob registry + `--config-help` (roadmap #9).

The audit found ~120 knobs with ~15 documented — steering Argus was a
grep-the-source exercise. These pin the curated control-surface registry and the
`--config-help` command so the dials stay discoverable.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from argus_skill.core.knobs import (
    KNOBS,
    format_config_help,
    normalize_cockpit_knob_value,
    resolve_budget_caps,
    resolve_role_model,
)


@pytest.fixture(autouse=True)
def _isolated_argus_skill_home(tmp_path, monkeypatch):
    """resolve_role_model() now also consults core.knob_store's persisted
    config (~/.argus-skill/config.json by default) as a fallback layer —
    isolate ARGUS_SKILL_HOME so these tests never read (or, via a future
    /backend or /config switch made on this same machine, race against) a
    REAL operator's persisted knobs."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-skill-home"))


def test_registry_is_well_formed() -> None:
    names = [k.name for k in KNOBS]
    assert len(names) == len(set(names)), "duplicate knob names"
    assert all(k.name.startswith("ARGUS_") for k in KNOBS)
    assert all(k.doc and k.default and k.group for k in KNOBS), "every knob needs doc/default/group"


def test_registry_covers_the_key_operator_knobs() -> None:
    names = {k.name for k in KNOBS}
    for must in (
        "ARGUS_SKILL_LIFE_BACKEND",
        "ARGUS_SKILL_MODEL",
        "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
        "ARGUS_SKILL_MAX_ROUNDS",
        "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
        "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
        "ARGUS_SKILL_SELF_REASONING_EFFORT",
        "ARGUS_SKILL_PLAN_PREVIEW_MODEL",
        "ARGUS_SKILL_PLAN_PREVIEW_REASONING_EFFORT",
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        "ARGUS_SKILL_ADAPTER_REASONING_EFFORT",
        "ARGUS_SKILL_REQUIRE_RELEASE_MATCH",
    ):
        assert must in names
    # HAPI's per-role backend knobs are registered too (so they stop being invisible).
    assert "ARGUS_SKILL_REVIEWER_BACKEND" in names
    assert "ARGUS_SKILL_PLANNER_RUNNER_BIN" in names


def test_config_help_does_not_advertise_formal_vertical_override() -> None:
    assert all(k.name != "ARGUS_SKILL_VERTICAL" for k in KNOBS)
    assert "ARGUS_SKILL_VERTICAL" not in format_config_help(env={})


def test_registry_covers_the_team_teammate_knobs() -> None:
    """The teammate forced-grounding + leaderboard-direction control surface is
    documented — an operator running a non-GPU team needs these to be discoverable."""
    names = {k.name for k in KNOBS}
    for must in (
        "ARGUS_TEAMMATE_RESEARCH_PROMPT",
        "ARGUS_TEAMMATE_PROFILE_HEADER",
        "ARGUS_TEAMMATE_PROFILE_REQUIRE_SUBSTR",
        "ARGUS_TEAMMATE_PAPER_MISSION",
        "ARGUS_LEADERBOARD_LOWER_IS_BETTER",
    ):
        assert must in names, must


def test_format_shows_default_when_unset() -> None:
    out = format_config_help(env={})
    assert "ARGUS_SKILL_LIFE_BACKEND" in out
    assert "default: codex" in out
    assert "[backend]" in out
    assert "[budget]" in out


def test_format_shows_current_value_when_set() -> None:
    out = format_config_help(env={"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "50"})
    assert "= 50" in out  # current effective value surfaced


def test_format_redacts_sensitive_current_values() -> None:
    out = format_config_help(env={"ARGUS_SKILL_TELEGRAM_BOT_TOKEN": "super-secret"})
    assert "super-secret" not in out
    assert "= <redacted> (env)" in out


def test_format_shows_persisted_value_when_env_is_unset() -> None:
    from argus_skill.core import knob_store

    knob_store.write_persisted_knob("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "75")

    out = format_config_help(env={})

    assert "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD" in out
    assert "= 75 (persisted)" in out


def test_budget_caps_share_env_persisted_default_precedence() -> None:
    from argus_skill.core import knob_store

    knob_store.write_persisted_knob("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "12.5")
    persisted = resolve_budget_caps(env={})
    overridden = resolve_budget_caps(env={"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "90"})

    assert persisted.global_daily_cap_usd == 12.5
    assert overridden.global_daily_cap_usd == 90.0


@pytest.mark.parametrize("value", ["nope", "-1", "nan", "inf"])
def test_budget_caps_reject_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        resolve_budget_caps(env={"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": value})


def test_cockpit_value_normalization_is_typed() -> None:
    assert normalize_cockpit_knob_value("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "$12.50") == "12.5"
    assert normalize_cockpit_knob_value("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", "4") == "4"
    assert normalize_cockpit_knob_value("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "250") == "250"
    assert normalize_cockpit_knob_value("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "12.5") == "12.5"
    assert normalize_cockpit_knob_value("ARGUS_SKILL_SAFE_MODE", "enabled") == "1"
    assert normalize_cockpit_knob_value("ARGUS_SKILL_ENGINEER_BACKEND", "COPILOT") == "copilot"
    assert normalize_cockpit_knob_value("ARGUS_SKILL_ENGINEER_BACKEND", "opencod") == "opencode"
    with pytest.raises(ValueError, match="codex, claude, copilot, or opencode"):
        normalize_cockpit_knob_value("ARGUS_SKILL_ENGINEER_BACKEND", "magic")
    with pytest.raises(ValueError, match="non-negative integer"):
        normalize_cockpit_knob_value("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", "-1")


def test_shared_model_default_feeds_role_model_resolution() -> None:
    env = {"ARGUS_SKILL_MODEL": "claude-sonnet-5"}

    assert (
        resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env=env)
        == "claude-sonnet-5"
    )
    assert (
        resolve_role_model("planner", role_env="ARGUS_SKILL_PLAN_MODEL", env=env)
        == "claude-sonnet-5"
    )


def test_role_model_override_beats_shared_default() -> None:
    env = {
        "ARGUS_SKILL_MODEL": "claude-sonnet-5",
        "ARGUS_SKILL_ENGINEER_MODEL": "gpt-5.4-mini",
    }

    assert (
        resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env=env)
        == "gpt-5.4-mini"
    )


def test_persisted_model_switch_survives_a_bare_env(monkeypatch, tmp_path) -> None:
    """Regression: a /backend or /config model switch used to only set
    os.environ for THIS process — restart the REPL (or let the daemon boot
    fresh) and the switch was gone. resolve_role_model must now ALSO fall
    back to core.knob_store's persisted config.json when NO env var is set
    at all for this process, so "change it once" actually holds."""
    from argus_skill.core import knob_store

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")

    assert resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env={}) == (
        "claude-sonnet-5"
    )


def test_persisted_role_specific_model_beats_persisted_shared(monkeypatch, tmp_path) -> None:
    from argus_skill.core import knob_store

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    knob_store.write_persisted_knob("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")

    assert resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env={}) == (
        "gpt-5.4-mini"
    )


def test_explicit_env_beats_a_persisted_switch(monkeypatch, tmp_path) -> None:
    """A deliberate, explicit env var for THIS process (a shell script, CI,
    a Docker -e flag) must always outrank a previously-persisted natural-
    language switch — a persisted "I said this in chat last week" default
    should never silently shadow a one-off override."""
    from argus_skill.core import knob_store

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")

    env = {"ARGUS_SKILL_MODEL": "gpt-5.4-mini"}
    assert resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env=env) == (
        "gpt-5.4-mini"
    )


def test_cli_config_help_exits_zero_and_prints_knobs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill", "--config-help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ARGUS_SKILL_LIFE_BACKEND" in proc.stdout
    assert "control knobs" in proc.stdout


def test_cli_config_snapshot_writes_file(tmp_path) -> None:
    out = tmp_path / "argus_runtime_settings.md"
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill", "--config-snapshot", str(out)],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Role Hyperparameters" in text
    assert "ARGUS_SKILL_MODEL" in text
    assert "config snapshot written" in proc.stdout
