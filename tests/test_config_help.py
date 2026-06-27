"""The operator-facing ARGUS_* knob registry + `--config-help` (roadmap #9).

The audit found ~120 knobs with ~15 documented — steering Argus was a
grep-the-source exercise. These pin the curated control-surface registry and the
`--config-help` command so the dials stay discoverable.
"""
from __future__ import annotations

import subprocess
import sys

from argus_skill.core.knobs import KNOBS, format_config_help


def test_registry_is_well_formed() -> None:
    names = [k.name for k in KNOBS]
    assert len(names) == len(set(names)), "duplicate knob names"
    assert all(k.name.startswith("ARGUS_") for k in KNOBS)
    assert all(k.doc and k.default and k.group for k in KNOBS), "every knob needs doc/default/group"


def test_registry_covers_the_key_operator_knobs() -> None:
    names = {k.name for k in KNOBS}
    for must in (
        "ARGUS_SKILL_LIFE_BACKEND",
        "ARGUS_SKILL_PER_MISSION_CAP_USD",
        "ARGUS_SKILL_DAILY_CAP_USD",
        "ARGUS_SKILL_MAX_ROUNDS",
        "ARGUS_SKILL_VERTICAL",
        "ARGUS_SKILL_DAEMON_AUTO_RESTART",
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    ):
        assert must in names
    # HAPI's per-role backend knobs are registered too (so they stop being invisible).
    assert "ARGUS_SKILL_REVIEWER_BACKEND" in names
    assert "ARGUS_SKILL_PLANNER_RUNNER_BIN" in names


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
    out = format_config_help(env={"ARGUS_SKILL_PER_MISSION_CAP_USD": "50"})
    assert "= 50" in out  # current effective value surfaced


def test_cli_config_help_exits_zero_and_prints_knobs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill", "--config-help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ARGUS_SKILL_LIFE_BACKEND" in proc.stdout
    assert "control knobs" in proc.stdout
