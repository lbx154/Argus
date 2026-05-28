"""Tests for the setup wizard's codex config seeding helpers."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.tools import setup as _wizard


def test_render_codex_config_toml_contains_inputs() -> None:
    rendered = _wizard._render_codex_config_toml(
        "https://example.azure.com/openai/v1",
        "gpt-test",
    )
    assert 'model = "gpt-test"' in rendered
    # base_url should be normalized to a trailing slash for codex provider parsing
    assert 'base_url = "https://example.azure.com/openai/v1/"' in rendered
    assert "[model_providers.codex]" in rendered
    assert 'wire_api = "responses"' in rendered
    # resilience knobs argus-skill relies on
    assert "request_max_retries" in rendered
    assert "stream_idle_timeout_ms" in rendered
    assert "disable_response_storage = true" in rendered


def test_render_codex_config_toml_handles_trailing_slash() -> None:
    rendered = _wizard._render_codex_config_toml(
        "https://example.azure.com/openai/v1/",
        "gpt-test",
    )
    assert 'base_url = "https://example.azure.com/openai/v1/"' in rendered
    # should not double-slash
    assert "v1//" not in rendered


def test_seed_codex_config_writes_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    paths = _wizard._seed_codex_config(
        "https://example.azure.com/openai/v1/",
        "sk-test-1234",
        "gpt-engineer",
    )
    assert paths is not None
    cfg, auth = paths
    assert cfg.exists() and auth.exists()
    assert "gpt-engineer" in cfg.read_text()
    auth_data = json.loads(auth.read_text())
    assert auth_data["OPENAI_API_KEY"] == "sk-test-1234"
    # auth.json should be 0600
    mode = auth.stat().st_mode & 0o777
    assert mode == 0o600, f"auth.json mode should be 0600, got {oct(mode)}"


def test_seed_codex_config_requires_inputs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    # No api_key -> short-circuit, returns None, writes nothing
    result = _wizard._seed_codex_config(
        "https://example.azure.com/openai/v1/",
        "",
        "gpt-engineer",
    )
    assert result is None
    assert not (tmp_path / "codex" / "config.toml").exists()
    assert not (tmp_path / "codex" / "auth.json").exists()


def test_seed_codex_config_backs_up_existing(tmp_path: Path, monkeypatch, capsys) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    # Pre-existing config the user already crafted
    (codex_home / "config.toml").write_text("model = \"keepme\"\n", encoding="utf-8")
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY": "old"}\n', encoding="utf-8")

    # User accepts overwrite
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    paths = _wizard._seed_codex_config(
        "https://example.azure.com/openai/v1/",
        "sk-new",
        "gpt-new",
    )
    assert paths is not None
    cfg, auth = paths

    # Backups should be created
    assert (codex_home / "config.toml.bak").exists()
    assert (codex_home / "auth.json.bak").exists()
    assert "keepme" in (codex_home / "config.toml.bak").read_text()
    assert json.loads((codex_home / "auth.json.bak").read_text())["OPENAI_API_KEY"] == "old"
    # New files reflect the new inputs
    assert "gpt-new" in cfg.read_text()
    assert json.loads(auth.read_text())["OPENAI_API_KEY"] == "sk-new"


def test_seed_codex_config_respects_decline(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    original = "model = \"do-not-touch\"\n"
    (codex_home / "config.toml").write_text(original, encoding="utf-8")
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY": "keepme"}\n', encoding="utf-8")

    # User declines (default N)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    result = _wizard._seed_codex_config(
        "https://example.azure.com/openai/v1/",
        "sk-ignored",
        "gpt-ignored",
    )
    assert result is None
    # Files untouched
    assert (codex_home / "config.toml").read_text() == original
    assert json.loads((codex_home / "auth.json").read_text())["OPENAI_API_KEY"] == "keepme"
    # No backups created since nothing was overwritten
    assert not (codex_home / "config.toml.bak").exists()
    assert not (codex_home / "auth.json.bak").exists()
