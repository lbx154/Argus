from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus_skill.apps.cli import main as cli_main
from argus_skill.apps.cli import _core as cli_core
from argus_skill.core.backend_readiness import (
    BackendProfile,
    BackendReadiness,
    ReadinessProblem,
    SETUP_EXIT_USAGE,
)
from argus_skill.tools import setup


def test_noninteractive_setup_requires_explicit_backend(capsys) -> None:
    rc = setup.run_setup(
        non_interactive=True,
        accept_house_rules=True,
    )

    assert rc == SETUP_EXIT_USAGE
    assert "requires --backend" in capsys.readouterr().err


def test_noninteractive_setup_requires_house_rule_acceptance(capsys) -> None:
    rc = setup.run_setup(
        backend="copilot",
        non_interactive=True,
    )

    assert rc == SETUP_EXIT_USAGE
    assert "--accept-house-rules" in capsys.readouterr().err


def test_interactive_setup_requires_choice_when_multiple_backends_exist(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"codex", "copilot"} else None,
    )
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {},
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")

    assert setup._configure_runner_backend() is None


def test_noninteractive_setup_validates_then_persists_without_global_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    report = BackendReadiness(
        profile=BackendProfile(
            backend="copilot",
            auth_mode="subscription_cli",
            backend_source="argument",
            auth_mode_source="argument",
        ),
        executable="/usr/bin/copilot",
        version="1.0.74",
        auth_checked=True,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        setup,
        "_ensure_default_house_rules_prompt",
        lambda: tmp_path / "10-house-rules.md",
    )
    monkeypatch.setattr(
        setup,
        "check_backend_readiness",
        lambda *_args, **_kwargs: calls.append("check") or report,
    )
    monkeypatch.setattr(
        setup,
        "persist_validated_profile",
        lambda _report: calls.append("persist") or True,
    )
    monkeypatch.setattr(
        setup,
        "_apply_git_identity",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("global Git must remain untouched")
        ),
    )
    monkeypatch.setattr(
        setup,
        "_seed_codex_config",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("backend auth files must remain untouched")
        ),
    )

    rc = setup.run_setup(
        backend="copilot",
        non_interactive=True,
        accept_house_rules=True,
    )

    assert rc == 0
    assert calls == ["check", "persist"]
    assert "No global Git identity or backend authentication files were changed" in (
        capsys.readouterr().out
    )


def test_cli_forwards_noninteractive_setup_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_setup(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(setup, "run_setup", fake_setup)

    rc = cli_main(
        [
            "--setup",
            "--non-interactive",
            "--backend",
            "codex",
            "--auth-mode",
            "subscription_cli",
            "--accept-house-rules",
            "--allow-prerelease",
        ]
    )

    assert rc == 0
    assert captured["backend"] == "codex"
    assert captured["auth_mode"] == "subscription_cli"
    assert captured["non_interactive"] is True
    assert captured["accept_house_rules"] is True
    assert captured["allow_prerelease"] is True


def test_daemon_readiness_failure_prevents_spawn(monkeypatch, capsys) -> None:
    report = BackendReadiness(
        profile=BackendProfile(
            backend="copilot",
            auth_mode="subscription_cli",
            backend_source="argument",
            auth_mode_source="default",
        ),
        problems=[
            ReadinessProblem(
                "authentication",
                "not logged in",
                "copilot login",
            )
        ],
    )
    monkeypatch.setattr(cli_core, "_lifetime_entry_error", lambda _args: None)
    monkeypatch.setattr(
        "argus_skill.core.backend_readiness.check_backend_readiness",
        lambda *_args, **_kwargs: report,
    )
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.run_foreground",
        lambda _cfg: (_ for _ in ()).throw(
            AssertionError("worker must not spawn before readiness")
        ),
    )
    args = SimpleNamespace(
        continuous=False,
        objective="",
        backend="copilot",
        auth_mode="subscription_cli",
        allow_prerelease=False,
    )

    rc = cli_core._cmd_daemon_start(args, foreground=True)

    assert rc == 3
    err = capsys.readouterr().err
    assert "failed capability: authentication" in err
    assert "configuration source" in err
    assert "copilot login" in err
