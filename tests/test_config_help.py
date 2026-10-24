"""The operator-facing ARGUS_* knob registry + `--config-help` (roadmap #9).

The audit found ~120 knobs with ~15 documented — steering Argus was a
grep-the-source exercise. These pin the curated control-surface registry and the
`--config-help` command so the dials stay discoverable.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.core.knobs import (
    KNOBS,
    format_config_help,
    normalize_cockpit_knob_value,
    resolve_role_model,
)

ROOT = Path(__file__).resolve().parents[1]


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
        "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
        "ARGUS_SKILL_MAX_ROUNDS",
        "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
        "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
        "ARGUS_SKILL_SELF_REASONING_EFFORT",
        "ARGUS_SKILL_LIGHT_MODEL",
        "ARGUS_SKILL_LIGHT_REASONING_EFFORT",
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        "ARGUS_SKILL_PROVIDER_TURN_CAP",
        "ARGUS_SKILL_SOURCE_ROOT",
        "ARGUS_SKILL_LETTER_INTERVAL_HOURS",
    ):
        assert must in names
    # Every role runs on the one shared backend.
    assert "ARGUS_SKILL_RUNNER_BACKEND" in names
    assert not [name for name in names if name.endswith(("_RUNNER_BIN", "_BACKEND"))
                and name not in {"ARGUS_SKILL_RUNNER_BIN", "ARGUS_SKILL_RUNNER_BACKEND",
                                 "ARGUS_SKILL_LIFE_BACKEND"}]


def test_mission_round_default_is_unbounded_and_consistent() -> None:
    from argus.engineer.round_config import SupervisedConfig
    from argus.loop import SkillLoopConfig

    max_rounds_knob = next(
        knob for knob in KNOBS if knob.name == "ARGUS_SKILL_MAX_ROUNDS"
    )

    assert max_rounds_knob.default == "0"
    assert SkillLoopConfig().max_rounds == 0
    assert SupervisedConfig().max_rounds == 0


def test_manager_planner_and_self_reasoning_defaults_are_high() -> None:
    defaults = {knob.name: knob.default for knob in KNOBS}

    assert defaults["ARGUS_SKILL_MANAGER_REASONING_EFFORT"] == "high"
    assert defaults["ARGUS_SKILL_PLANNER_REASONING_EFFORT"] == "high"
    assert defaults["ARGUS_SKILL_SELF_REASONING_EFFORT"] == "medium"


def test_config_help_does_not_advertise_formal_vertical_override() -> None:
    assert all(k.name != "ARGUS_SKILL_VERTICAL" for k in KNOBS)
    assert "ARGUS_SKILL_VERTICAL" not in format_config_help(env={})


def test_format_shows_default_when_unset() -> None:
    out = format_config_help(env={})
    assert "ARGUS_SKILL_LIFE_BACKEND" in out
    assert "default: codex" in out
    assert "[backend]" in out
    assert "[budget]" in out


def test_format_shows_current_value_when_set() -> None:
    out = format_config_help(env={"ARGUS_SKILL_MAX_ACTIVE_DAEMONS": "50"})
    assert "= 50" in out  # current effective value surfaced


def test_format_redacts_sensitive_current_values(redact_secrets_on) -> None:
    out = format_config_help(env={"ARGUS_SKILL_TELEGRAM_BOT_TOKEN": "super-secret"})
    assert "super-secret" not in out
    assert "= <redacted> (env)" in out


def test_format_shows_persisted_value_when_env_is_unset() -> None:
    from argus.core import knob_store

    knob_store.write_persisted_knob("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", "75")

    out = format_config_help(env={})

    assert "ARGUS_SKILL_MAX_ACTIVE_DAEMONS" in out
    assert "= 75 (persisted)" in out


def test_cockpit_value_normalization_is_typed() -> None:
    assert normalize_cockpit_knob_value("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", "4") == "4"
    assert normalize_cockpit_knob_value("ARGUS_SKILL_SAFE_MODE", "enabled") == "1"
    for raw, backend in (("COPILOT", "copilot"), ("CURSOR", "cursor"), ("opencod", "opencode"),
                         ("PI", "pi"), ("GROK", "grok"), ("QODER", "qoder"), ("DSH", "dsh")):
        assert normalize_cockpit_knob_value("ARGUS_SKILL_RUNNER_BACKEND", raw) == backend
    with pytest.raises(
        ValueError,
        match="codex, claude, copilot, cursor, opencode, pi, grok, qoder, dsh",
    ):
        normalize_cockpit_knob_value("ARGUS_SKILL_RUNNER_BACKEND", "magic")
    with pytest.raises(ValueError, match="non-negative integer"):
        normalize_cockpit_knob_value("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", "-1")


def test_cockpit_path_knob_requires_an_absolute_path(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    assert normalize_cockpit_knob_value(
        "ARGUS_SKILL_SOURCE_ROOT", f"{checkout}{os.sep}"
    ) == str(checkout)
    assert normalize_cockpit_knob_value("ARGUS_SKILL_SOURCE_ROOT", "~/argus") == str(
        Path("~/argus").expanduser()
    )
    # A missing directory is accepted on purpose: the startup preflight
    # fail-closes on it, so a typo refuses startup loudly instead of being
    # rejected only on hosts where the path happens to exist.
    assert normalize_cockpit_knob_value(
        "ARGUS_SKILL_SOURCE_ROOT", str(tmp_path / "not-yet-deployed")
    ) == str(tmp_path / "not-yet-deployed")
    with pytest.raises(ValueError, match="absolute path"):
        normalize_cockpit_knob_value("ARGUS_SKILL_SOURCE_ROOT", "relative/checkout")


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
    from argus.core import knob_store

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")

    assert resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env={}) == (
        "claude-sonnet-5"
    )


def test_persisted_role_specific_model_beats_persisted_shared(monkeypatch, tmp_path) -> None:
    from argus.core import knob_store

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
    from argus.core import knob_store

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")

    env = {"ARGUS_SKILL_MODEL": "gpt-5.4-mini"}
    assert resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env=env) == (
        "gpt-5.4-mini"
    )


def test_cli_config_help_exits_zero_and_prints_knobs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus", "--config-help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ARGUS_SKILL_LIFE_BACKEND" in proc.stdout
    assert "control knobs" in proc.stdout


def test_cli_config_snapshot_writes_file(tmp_path) -> None:
    out = tmp_path / "argus_runtime_settings.md"
    proc = subprocess.run(
        [sys.executable, "-m", "argus", "--config-snapshot", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )

    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Role Hyperparameters" in text
    assert "ARGUS_SKILL_MODEL" in text
    assert "config snapshot written" in proc.stdout
