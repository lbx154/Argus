"""Tests for the setup wizard's GPU keep-alive (anti-reclaim) integration."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.tools import gpu_load
from argus_skill.tools import setup as _wizard


def test_gpu_load_help_exits_clean() -> None:
    with pytest.raises(SystemExit) as exc:
        gpu_load.main(["--help"])
    assert exc.value.code == 0


def test_gpu_load_arg_defaults() -> None:
    args = gpu_load._parse_args([])
    assert args.util == 20.0
    assert args.mem == 10.0
    assert args.duration == 0.0
    args2 = gpu_load._parse_args(["--gpus", "0,2", "--mem", "5", "--util", "15"])
    assert args2.gpus == "0,2"
    assert args2.mem == 5.0
    assert args2.util == 15.0


def test_setup_defaults_to_only_installed_copilot(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        lambda name: "/usr/local/bin/copilot" if name == "copilot" else None,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    from argus_skill.core.knob_store import read_persisted_knobs

    assert _wizard._configure_runner_backend() == "copilot"
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in read_persisted_knobs()


def test_setup_defaults_to_only_installed_pi(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    from argus_skill.core.knob_store import read_persisted_knobs

    assert _wizard._configure_runner_backend() == "pi"
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in read_persisted_knobs()


def test_setup_defaults_to_only_installed_opencode(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        lambda name: "/usr/local/bin/opencode" if name == "opencode" else None,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    from argus_skill.core.knob_store import read_persisted_knobs

    assert _wizard._configure_runner_backend() == "opencode"
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in read_persisted_knobs()


def test_setup_preserves_runner_bound_to_opencod_alias(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    from argus_skill.core.knob_store import write_persisted_knobs

    assert write_persisted_knobs(
        {
            "ARGUS_SKILL_RUNNER_BACKEND": "opencod",
            "ARGUS_SKILL_RUNNER_BIN": "/custom/opencode",
        }
    )
    calls = []

    def resolve(backend: str, configured: str | None = None):
        calls.append((backend, configured))
        return configured or "/path/opencode"

    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        resolve,
    )

    assert _wizard._resolve_setup_runner_bin("opencode") == "/custom/opencode"
    assert calls == [("opencode", "/custom/opencode")]


def test_setup_rejects_selected_backend_missing_from_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        lambda _name: None,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "copilot")
    from argus_skill.core.knob_store import read_persisted_knobs

    assert _wizard._configure_runner_backend() is None
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in read_persisted_knobs()


def test_setup_does_not_replace_persisted_backend_before_readiness(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    from argus_skill.core.knob_store import read_persisted_knobs, write_persisted_knob

    assert write_persisted_knob("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        lambda name: "/usr/local/bin/copilot" if name == "copilot" else None,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")

    assert _wizard._configure_runner_backend() == "copilot"
    assert read_persisted_knobs()["ARGUS_SKILL_RUNNER_BACKEND"] == "codex"
