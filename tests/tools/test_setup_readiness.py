from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.apps.cli import _core as cli_core
from argus_skill.apps.cli import main as cli_main
from argus_skill.core.backend_readiness import (
    SETUP_EXIT_USAGE,
    BackendProfile,
    BackendReadiness,
    ReadinessProblem,
)
from argus_skill.tools import setup


def test_setup_banner_highlights_agent_assisted_installation(capsys) -> None:
    setup._banner()

    output = capsys.readouterr().out
    assert "★ Recommended / 推荐" in output
    assert "current Code Agent" in output
    assert "https://github.com/lbx154/Argus/blob/main/docs/agent-install.md" in output


def test_setup_banner_uses_bold_yellow_highlight_on_tty(monkeypatch) -> None:
    class TtyBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    output = TtyBuffer()
    monkeypatch.setattr(setup.sys, "stdout", output)

    setup._banner()

    assert "\033[1;33m★ Recommended / 推荐:" in output.getvalue()


def test_noninteractive_setup_requires_explicit_backend(capsys) -> None:
    rc = setup.run_setup(non_interactive=True)

    assert rc == SETUP_EXIT_USAGE
    assert "requires --backend" in capsys.readouterr().err


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
    special_prompts_dir = tmp_path / "special-prompts"
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(special_prompts_dir))

    def check(*_args, **kwargs):
        assert kwargs["runner_bin"] == "/usr/bin/copilot"
        calls.append("check")
        return report

    monkeypatch.setattr(setup, "check_backend_readiness", check)
    monkeypatch.setattr(
        setup,
        "_verify_setup_smoke",
        lambda backend, **_kwargs: calls.append(f"smoke:{backend}") or True,
    )
    monkeypatch.setattr(
        setup,
        "persist_validated_profile",
        lambda _report, **_kwargs: calls.append("persist") or True,
    )
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        lambda name, _configured=None: "/usr/bin/copilot" if name == "copilot" else None,
    )

    rc = setup.run_setup(
        backend="copilot",
        non_interactive=True,
    )

    assert rc == 0
    assert calls == ["check", "smoke:copilot", "persist"]
    assert not special_prompts_dir.exists()
    assert "Setup complete. Run `argus`." in capsys.readouterr().out


def test_missing_pi_is_installed_automatically(monkeypatch) -> None:
    installed = False

    def resolve(name: str, _configured: str | None = None):
        return "/usr/bin/pi" if name == "pi" and installed else None

    def install() -> bool:
        nonlocal installed
        installed = True
        return True

    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        resolve,
    )
    monkeypatch.setattr(setup, "_install_pi_cli", install)

    assert setup._configure_runner_backend("pi") == "pi"


def test_setup_uses_explicit_custom_runner_path(monkeypatch) -> None:
    custom = "/opt/agents/claude-custom"
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BIN", custom)
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {
            "ARGUS_SKILL_RUNNER_BACKEND": "codex",
            "ARGUS_SKILL_RUNNER_BIN": "/opt/agents/codex-old",
        },
    )
    calls: list[tuple[str, str | None]] = []

    def resolve(name: str, configured: str | None = None):
        calls.append((name, configured))
        return configured

    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        resolve,
    )

    assert setup._resolve_setup_runner_bin(
        "claude",
        explicit_selection=True,
    ) == custom
    assert calls == [("claude", custom)]


def test_windows_backend_install_hints_are_powershell_safe() -> None:
    assert setup._backend_install_hint(
        "copilot",
        platform_name="nt",
    ) == "npm.cmd install -g @github/copilot"
    assert "curl" not in setup._backend_install_hint("opencode", platform_name="nt")
    assert "https://opencode.ai/docs/#windows" in setup._backend_install_hint(
        "opencode",
        platform_name="nt",
    )


def test_noninteractive_api_url_configures_pi_without_backend_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = BackendReadiness(
        profile=BackendProfile(
            backend="pi",
            auth_mode="subscription_cli",
            backend_source="argument",
            auth_mode_source="argument",
        ),
        executable="/usr/bin/pi",
        version="0.84.1",
        auth_checked=True,
    )
    models_path = tmp_path / ".pi" / "agent" / "models.json"
    persisted: list[str] = []
    monkeypatch.setattr(setup, "_pi_models_path", lambda: models_path)
    monkeypatch.setattr(setup, "_configure_runner_backend", lambda backend: backend)
    monkeypatch.setattr(setup, "check_backend_readiness", lambda *_a, **_k: report)
    monkeypatch.setattr(setup, "_verify_setup_smoke", lambda *_a, **_k: True)
    monkeypatch.setattr(
        setup, "persist_validated_profile", lambda _report, **_kwargs: True
    )
    monkeypatch.setattr(
        setup,
        "_persist_pi_profile",
        lambda model: persisted.append(model) or True,
    )
    monkeypatch.setenv(
        "ARGUS_SKILL_SPECIAL_PROMPTS_DIR",
        str(tmp_path / "special-prompts"),
    )

    rc = setup.run_setup(
        non_interactive=True,
        api_url="https://api.example.com/v1",
        api_key="secret",
        api_model="model-x",
    )

    assert rc == 0
    provider = json.loads(models_path.read_text(encoding="utf-8"))["providers"]["argus"]
    assert provider == {
        "baseUrl": "https://api.example.com/v1",
        "api": "openai-completions",
        "apiKey": "secret",
        "models": [{"id": "model-x"}],
    }
    assert persisted == ["model-x"]


def test_setup_smoke_uses_real_agent_turn(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        lambda backend, _configured=None: (
            "/usr/bin/claude" if backend == "claude" else None
        ),
    )
    captured: dict[str, object] = {}

    def probe(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(ok=True, output="ARGUS_SETUP_OK", error="")

    monkeypatch.setattr(
        "argus_skill.core.agent_probe.run_read_only_agent_prompt",
        probe,
    )

    assert setup._verify_setup_smoke("claude") is True
    assert captured["backend"] == "claude"
    assert captured["model"] == ""
    assert captured["run_label"] == "setup-smoke"
    assert "Real Agent turn completed" in capsys.readouterr().out


def test_setup_smoke_uses_effective_backend_model(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "claude-sonnet-current")

    assert setup._setup_smoke_model("claude", None) == "claude-sonnet-current"
    assert setup._setup_smoke_model(
        "pi",
        ("api-model", Path("/tmp/models.json")),
    ) == "api-model"


def test_setup_smoke_failure_is_actionable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "argus_skill.agent_cli.runner_backend.resolve_runner_bin",
        lambda backend, _configured=None: (
            "/usr/bin/claude" if backend == "claude" else None
        ),
    )
    monkeypatch.setattr(
        "argus_skill.core.agent_probe.run_read_only_agent_prompt",
        lambda **_kwargs: SimpleNamespace(
            ok=False,
            output="",
            error="not authenticated",
        ),
    )

    assert setup._verify_setup_smoke("claude") is False
    output = capsys.readouterr().out
    assert "Step 2" in output
    assert "not authenticated" in output
    assert "argus --backend claude doctor --deep --advisor claude" in output
    assert "argus --setup --backend claude" in output
    assert "--advisor auto" not in output


def test_pi_provider_rejects_non_http_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        setup,
        "_pi_models_path",
        lambda: tmp_path / "models.json",
    )

    with pytest.raises(ValueError, match="absolute http"):
        setup._save_pi_provider("api.example.com/v1", "secret", "model-x")


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
            "--allow-prerelease",
        ]
    )

    assert rc == 0
    assert captured["backend"] == "codex"
    assert captured["auth_mode"] == "subscription_cli"
    assert captured["non_interactive"] is True
    assert captured["allow_prerelease"] is True
    assert captured["api_url"] is None
    assert captured["api_key"] is None
    assert captured["api_model"] is None


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


def test_setup_cannot_mutate_global_git_or_backend_auth() -> None:
    """Setup configures Argus; it does not reach into the machine.

    This used to be asserted by monkeypatching `_apply_git_identity` and
    `_seed_codex_config` to raise if setup ever called them. Both helpers were
    left behind by an earlier rewrite of `run_setup` and were unreachable from
    any entry point, so the tripwires guarded code that could not fire. The
    guarantee now holds by construction, and this pins it there: writing the
    machine's Git identity or a backend's auth file belongs to the tools that
    own those files, not to setup.
    """
    import inspect

    source = inspect.getsource(setup)
    for capability in ("git", "config", "user.name", "user.email"):
        assert f"_apply_{capability}_identity" not in source
    assert "_seed_codex_config" not in source
    assert "user.email" not in source
