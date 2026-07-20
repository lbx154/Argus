from __future__ import annotations

import json

import pytest

from argus_skill.core.config_snapshot import (
    build_config_snapshot,
    format_config_snapshot_markdown,
    write_config_snapshot,
)


@pytest.fixture(autouse=True)
def _isolated_argus_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))


def test_config_snapshot_resolves_role_hyperparameters() -> None:
    snapshot = build_config_snapshot(
        env={
            "ARGUS_SKILL_RUNNER_BACKEND": "copilot",
            "ARGUS_SKILL_MODEL": "claude-sonnet-5",
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT": "high",
            "ARGUS_SKILL_TELEGRAM_BOT_TOKEN": "secret-token",
        },
        generated_at_utc="2026-07-07T00:00:00Z",
    )

    engineer = next(r for r in snapshot["roles"] if r["role"] == "engineer")
    assert engineer["backend"] == "copilot"
    assert engineer["backend_source"] == "ARGUS_SKILL_RUNNER_BACKEND"
    assert engineer["model"] == "claude-sonnet-5"
    assert engineer["model_source"] == "ARGUS_SKILL_MODEL"
    assert engineer["reasoning_effort"] is None
    assert engineer["reasoning_effort_source"] == "not applicable for this model"

    manager = next(r for r in snapshot["roles"] if r["role"] == "manager")
    assert manager["backend"] == "copilot"

    token = next(
        k for k in snapshot["operator_knobs"]
        if k["name"] == "ARGUS_SKILL_TELEGRAM_BOT_TOKEN"
    )
    assert token["value"] == "<redacted>"
    assert token["source"] == "env"

    default_snapshot = build_config_snapshot(
        env={},
        generated_at_utc="2026-07-07T00:00:00Z",
    )
    default_token = next(
        k for k in default_snapshot["operator_knobs"]
        if k["name"] == "ARGUS_SKILL_TELEGRAM_BOT_TOKEN"
    )
    assert default_token["value"] == "(unset)"
    assert default_token["source"] == "default"


def test_config_snapshot_markdown_names_argus_native_controls() -> None:
    markdown = format_config_snapshot_markdown(
        build_config_snapshot(env={}, generated_at_utc="2026-07-07T00:00:00Z")
    )

    assert "Role Hyperparameters" in markdown
    assert "把模型换成 <name>" in markdown
    assert "把backend换成 <name>" in markdown
    assert "effort 设为 <low|medium|high|xhigh>" in markdown


def test_config_snapshot_reports_persisted_values_and_sources() -> None:
    from argus_skill.core.knob_store import write_persisted_knob

    write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    write_persisted_knob("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "75")

    snapshot = build_config_snapshot(
        env={},
        generated_at_utc="2026-07-07T00:00:00Z",
    )

    engineer = next(r for r in snapshot["roles"] if r["role"] == "engineer")
    assert engineer["model"] == "claude-sonnet-5"
    assert engineer["model_source"] == "persisted:ARGUS_SKILL_MODEL"
    daily_cap = next(
        k for k in snapshot["operator_knobs"]
        if k["name"] == "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"
    )
    assert daily_cap["value"] == "75"
    assert daily_cap["source"] == "persisted"


def test_write_config_snapshot_json(tmp_path) -> None:
    out = write_config_snapshot(
        tmp_path / "snapshot.json",
        env={"ARGUS_SKILL_ENGINEER_MODEL": "gpt-5.5"},
        generated_at_utc="2026-07-07T00:00:00Z",
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    engineer = next(r for r in payload["roles"] if r["role"] == "engineer")
    assert engineer["model"] == "gpt-5.5"
    assert engineer["reasoning_effort"] == "xhigh"
