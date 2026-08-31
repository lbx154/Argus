from __future__ import annotations

import subprocess

from argus_skill.core import backend_readiness as readiness


def _completed(
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["backend"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _fake_codex(monkeypatch, version: str, *, auth_returncode: int = 0) -> None:
    from argus_skill.tools import capability_vault

    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/codex")
    monkeypatch.setattr(
        capability_vault,
        "read_codex_provider_config",
        lambda _env=None: None,
    )

    def run(command, *, timeout_s, input_text=None):
        del timeout_s, input_text
        if command[-1] == "--version":
            return _completed(f"codex-cli {version}\n")
        return _completed(
            "Logged in\n",
            returncode=auth_returncode,
            stderr="benign shutdown diagnostic\n",
        )

    monkeypatch.setattr(readiness, "_run_text", run)


def test_windows_install_commands_are_powershell_safe() -> None:
    assert readiness.backend_install_command(
        "copilot",
        platform_name="nt",
    ) == "npm.cmd install -g @github/copilot"
    assert "curl" not in readiness.backend_install_command(
        "opencode",
        platform_name="nt",
    )
    assert readiness.backend_install_command(
        "qoder",
        platform_name="nt",
    ) == "npm.cmd install -g @qoder-ai/qodercli"
    assert readiness.backend_install_command(
        "dsh",
        platform_name="nt",
    ) == "npm.cmd install -g @deepseek-ai/dsh"


def test_default_timeout_allows_slow_cli_cold_start(monkeypatch) -> None:
    seen_timeouts = []
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/copilot")

    def run(command, *, timeout_s, input_text=None):
        del command, input_text
        seen_timeouts.append(timeout_s)
        return _completed("GitHub Copilot CLI 1.0.78\n")

    monkeypatch.setattr(readiness, "_run_text", run)

    report = readiness.check_backend_readiness("copilot", probe_auth=False)

    assert report.ok
    assert seen_timeouts == [readiness.DEFAULT_READINESS_TIMEOUT_S]
    assert readiness.DEFAULT_READINESS_TIMEOUT_S == 30.0


def test_explicit_backend_ignores_another_backends_persisted_runner(
    monkeypatch,
) -> None:
    resolved: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        readiness,
        "read_persisted_knobs",
        lambda: {
            "ARGUS_SKILL_RUNNER_BACKEND": "dsh",
            "ARGUS_SKILL_RUNNER_BIN": "/opt/bin/dsh",
        },
    )

    def resolve(backend: str, configured: str | None = None) -> str:
        resolved.append((backend, configured))
        return "/opt/bin/copilot"

    monkeypatch.setattr(readiness, "resolve_runner_bin", resolve)
    monkeypatch.setattr(
        readiness,
        "_run_text",
        lambda *_args, **_kwargs: _completed("GitHub Copilot CLI 1.0.82\n"),
    )

    report = readiness.check_backend_readiness(
        "copilot",
        probe_auth=False,
        env={},
    )

    assert report.ok
    assert resolved == [("copilot", None)]


def test_version_timeout_retries_once(monkeypatch) -> None:
    calls = 0
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/copilot")

    def run(command, *, timeout_s, input_text=None):
        nonlocal calls
        del input_text
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(command, timeout_s)
        return _completed("GitHub Copilot CLI 1.0.78\n")

    monkeypatch.setattr(readiness, "_run_text", run)

    report = readiness.check_backend_readiness("copilot", probe_auth=False)

    assert report.ok
    assert calls == 2


def test_codex_supported_floor_and_benign_stderr_pass(monkeypatch) -> None:
    _fake_codex(monkeypatch, "0.128.0")

    report = readiness.check_backend_readiness(
        "codex",
        "subscription_cli",
    )

    assert report.ok
    assert report.auth_checked
    assert report.version == "0.128.0"


def test_codex_tested_recommendation_passes(monkeypatch) -> None:
    _fake_codex(monkeypatch, readiness.CODEX_RECOMMENDED_VERSION)

    report = readiness.check_backend_readiness(
        "codex",
        "subscription_cli",
    )

    assert report.ok
    assert report.warnings == []


def test_codex_below_supported_floor_fails_with_upgrade(monkeypatch) -> None:
    _fake_codex(monkeypatch, "0.125.0")

    report = readiness.check_backend_readiness(
        "codex",
        "subscription_cli",
        probe_auth=False,
    )

    assert not report.ok
    assert ">=0.128.0" in report.problems[0].detail
    assert "@openai/codex@latest" in report.problems[0].remediation


def test_codex_prerelease_requires_explicit_allow(monkeypatch) -> None:
    _fake_codex(monkeypatch, "0.146.0-alpha.3")

    refused = readiness.check_backend_readiness(
        "codex",
        "subscription_cli",
        probe_auth=False,
    )
    allowed = readiness.check_backend_readiness(
        "codex",
        "subscription_cli",
        probe_auth=False,
        allow_prerelease=True,
    )

    assert not refused.ok
    assert "prerelease" in refused.problems[0].detail
    assert allowed.ok


def test_auth_failure_uses_exit_status(monkeypatch) -> None:
    _fake_codex(monkeypatch, "0.144.5", auth_returncode=1)

    report = readiness.check_backend_readiness(
        "codex",
        "subscription_cli",
    )

    assert not report.ok
    assert report.problems[0].capability == "authentication"
    assert "codex login" in report.problems[0].remediation


def test_codex_custom_provider_can_own_auth_without_openai_login(
    monkeypatch,
) -> None:
    from argus_skill.tools import capability_vault

    _fake_codex(monkeypatch, readiness.CODEX_RECOMMENDED_VERSION, auth_returncode=1)
    monkeypatch.setattr(
        capability_vault,
        "read_codex_provider_config",
        lambda _env=None: capability_vault.CodexProviderConfig(
            name="local-relay",
            base_url="http://127.0.0.1:41419/v1",
            wire_api="responses",
            requires_openai_auth=False,
        ),
    )

    report = readiness.check_backend_readiness(
        "codex",
        "subscription_cli",
    )

    assert report.ok
    assert report.auth_checked
    assert any("requires_openai_auth=false" in warning for warning in report.warnings)


def test_pi_readiness_uses_model_listing_without_spending_a_turn(monkeypatch) -> None:
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/pi")

    def run(command, *, timeout_s, input_text=None):
        del timeout_s, input_text
        if command[-1] == "--version":
            return _completed("0.83.0\n")
        assert command[-1] == "--list-models"
        return _completed(
            "provider model context max-out thinking images\n"
            "github-copilot gpt-5.6-sol 1.1M 128K yes yes\n"
        )

    monkeypatch.setattr(readiness, "_run_text", run)

    report = readiness.check_backend_readiness("pi", "subscription_cli")

    assert report.ok
    assert report.auth_checked
    assert report.version == "0.83.0"


def test_pi_below_supported_floor_fails(monkeypatch) -> None:
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/pi")
    monkeypatch.setattr(
        readiness,
        "_run_text",
        lambda command, **_kwargs: _completed("0.82.0\n"),
    )

    report = readiness.check_backend_readiness(
        "pi",
        "subscription_cli",
        probe_auth=False,
    )

    assert not report.ok
    assert ">=0.83.0" in report.problems[0].detail
    assert "pi update --self" in report.problems[0].remediation


def test_grok_readiness_accepts_api_key_without_spending_a_turn(
    monkeypatch,
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    # An xAI-catalog id, as a real Grok install carries: with no model set at
    # all the shared OpenAI default resolves instead, which readiness now
    # rejects on purpose (see test_claude_readiness_fails_on_the_openai_...).
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "grok-4")
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/grok")

    def run(command, *, timeout_s, input_text=None):
        del timeout_s, input_text
        assert command[-1] == "--version"
        return _completed("grok 0.1.0\n")

    monkeypatch.setattr(readiness, "_run_text", run)

    report = readiness.check_backend_readiness("grok", "subscription_cli")

    assert report.ok
    assert report.auth_checked
    assert report.version == "0.1.0"


def test_grok_readiness_reports_login_when_no_credentials_exist(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/grok")
    monkeypatch.setattr(
        readiness,
        "_run_text",
        lambda command, **_kwargs: _completed("grok 0.1.0\n"),
    )

    report = readiness.check_backend_readiness("grok", "subscription_cli")

    assert not report.ok
    assert report.problems[0].capability == "authentication"
    assert "grok login" in report.problems[0].remediation


def test_qoder_readiness_accepts_pat_without_spending_a_turn(
    monkeypatch,
) -> None:
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "pat-token")
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/qodercli")

    def run(command, *, timeout_s, input_text=None):
        del timeout_s, input_text
        # PAT short-circuits auth, so the only subprocess is the version probe.
        assert command[-1] == "--version"
        return _completed("qodercli 1.1.20\n")

    monkeypatch.setattr(readiness, "_run_text", run)

    report = readiness.check_backend_readiness("qoder", "subscription_cli")

    assert report.ok
    assert report.auth_checked
    assert report.version == "1.1.20"


def test_qoder_readiness_reports_login_when_unauthenticated(monkeypatch) -> None:
    monkeypatch.delenv("QODER_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/qodercli")

    def run(command, *, timeout_s, input_text=None):
        del timeout_s, input_text
        if command[-1] == "--list-models":
            # qodercli exits non-zero from --list-models when not logged in.
            return _completed("", returncode=1, stderr="not authenticated")
        return _completed("qodercli 1.1.20\n")

    monkeypatch.setattr(readiness, "_run_text", run)

    report = readiness.check_backend_readiness("qoder", "subscription_cli")

    assert not report.ok
    assert report.problems[0].capability == "authentication"
    assert "qodercli login" in report.problems[0].remediation


def test_subscription_mode_never_loads_model_api_vault(monkeypatch) -> None:
    _fake_codex(monkeypatch, "0.144.5")
    monkeypatch.setattr(
        readiness,
        "_check_model_api_routes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("subscription mode must not inspect the vault")
        ),
    )

    assert readiness.check_backend_readiness(
        "codex",
        "subscription_cli",
    ).ok


def test_model_api_mode_requires_configured_routes(monkeypatch) -> None:
    _fake_codex(monkeypatch, "0.144.5")
    monkeypatch.setattr(
        "argus_skill.tools.capability_vault.load_model_api_route",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "argus_skill.tools.capability_vault.default_vault_path",
        lambda: "/tmp/model_api.json",
    )

    report = readiness.check_backend_readiness(
        "codex",
        "model_api",
        probe_vault=False,
    )

    assert not report.ok
    assert {problem.capability for problem in report.problems} == {
        "model_api:engineer",
        "model_api:reviewer",
        "model_api:text",
    }
    assert "ARGUS_SKILL_CAPABILITY_VAULT" in report.problems[0].remediation


def test_profile_persistence_only_accepts_ready_report(monkeypatch) -> None:
    saved: list[dict[str, str]] = []
    monkeypatch.setattr(
        readiness,
        "write_persisted_knobs",
        lambda values: saved.append(dict(values)) or True,
    )
    profile = readiness.BackendProfile(
        backend="copilot",
        auth_mode="subscription_cli",
        backend_source="argument",
        auth_mode_source="argument",
    )
    ready = readiness.BackendReadiness(
        profile=profile,
        executable="/bin/copilot",
        version="1.0.74",
    )
    failed = readiness.BackendReadiness(
        profile=profile,
        problems=[
            readiness.ReadinessProblem(
                "authentication",
                "not logged in",
                "copilot login",
            )
        ],
    )

    assert readiness.persist_validated_profile(failed) is False
    assert readiness.persist_validated_profile(ready) is True
    assert saved == [
        {
            "ARGUS_SKILL_RUNNER_BACKEND": "copilot",
            "ARGUS_SKILL_BACKEND_AUTH_MODE": "subscription_cli",
            "ARGUS_SKILL_BACKEND_VALIDATED_VERSION": "1.0.74",
        }
    ]


def test_profile_persistence_records_the_adopted_model(monkeypatch) -> None:
    """The gap this closes: only the Pi path ever persisted a model, so
    `--setup --backend claude` left model resolution on the shared OpenAI
    default and every later call died on an id the CLI cannot serve."""
    saved: list[dict[str, str]] = []
    monkeypatch.setattr(
        readiness,
        "write_persisted_knobs",
        lambda values: saved.append(dict(values)) or True,
    )
    ready = readiness.BackendReadiness(
        profile=readiness.BackendProfile(
            backend="claude",
            auth_mode="subscription_cli",
            backend_source="argument",
            auth_mode_source="argument",
        ),
        executable="/bin/claude",
        version="2.1.232",
    )

    assert readiness.persist_validated_profile(ready, model="claude-opus-5") is True

    assert saved[0]["ARGUS_SKILL_MODEL"] == "claude-opus-5"


def test_backend_default_model_is_adopted_only_when_nobody_chose_one() -> None:
    assert (
        readiness.default_model_for_backend("claude", env={}, persisted={})
        == "claude-opus-5"
    )
    # An operator choice on either layer is never second-guessed.
    assert (
        readiness.default_model_for_backend(
            "claude", env={"ARGUS_SKILL_MODEL": "claude-sonnet-5"}, persisted={}
        )
        == ""
    )
    assert (
        readiness.default_model_for_backend(
            "claude", env={}, persisted={"ARGUS_SKILL_ENGINEER_MODEL": "my-model"}
        )
        == ""
    )
    # A backend with no verified default keeps whatever is configured; the
    # catalog check below is what makes a wrong id visible there.
    assert readiness.default_model_for_backend("copilot", env={}, persisted={}) == ""


def _fake_claude(monkeypatch, *, version: str = "2.1.232") -> None:
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/claude")

    def run(command, *, timeout_s, input_text=None):
        del timeout_s, input_text
        if command[-1] == "--version":
            return _completed(f"{version} (Claude Code)\n")
        return _completed("Logged in\n")

    monkeypatch.setattr(readiness, "_run_text", run)


def test_claude_readiness_fails_on_the_openai_shared_default(
    monkeypatch, tmp_path
) -> None:
    """The regression this locks down: `argus --setup --backend claude`
    reported ready with model=gpt-5.5, and every message then came back as
    "[not dispatched] Manager could not classify this message"."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    # The shared default under test is Argus's OpenAI fallback, not the
    # developer machine's personal Codex custom-provider configuration.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.delenv("ARGUS_SKILL_MODEL", raising=False)
    _fake_claude(monkeypatch)

    report = readiness.check_backend_readiness("claude", "subscription_cli")

    assert not report.ok
    problem = next(p for p in report.problems if p.capability == "model selector")
    assert "gpt-5.5" in problem.detail
    assert "anthropic" in problem.detail
    assert "claude-opus-5" in problem.remediation


def test_claude_readiness_accepts_an_anthropic_model(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "claude-opus-5")
    _fake_claude(monkeypatch)

    report = readiness.check_backend_readiness("claude", "subscription_cli")

    assert report.ok, [p.detail for p in report.problems]
    assert not report.warnings


def test_an_operator_chosen_foreign_model_only_warns(monkeypatch, tmp_path) -> None:
    """A hand-set id may be served by a private gateway the CLI points at, so
    it must not turn the doctor red — but it still gets said out loud."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "gpt-5.5")
    _fake_claude(monkeypatch)

    report = readiness.check_backend_readiness("claude", "subscription_cli")

    assert report.ok, [p.detail for p in report.problems]
    assert any("gpt-5.5" in warning for warning in report.warnings), report.warnings


def test_unknown_model_ids_are_never_second_guessed(monkeypatch, tmp_path) -> None:
    """Only a POSITIVE match on a foreign catalog is actionable; a private id
    Argus has never heard of must stay silent."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "internal-gateway-v3")
    _fake_claude(monkeypatch)

    report = readiness.check_backend_readiness("claude", "subscription_cli")

    assert report.ok
    assert not report.warnings


def test_multi_catalog_backends_are_exempt(monkeypatch, tmp_path) -> None:
    """Copilot resells several vendors (Anthropic ids included), so its
    selector cannot be judged from the id alone."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "claude-opus-5")
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/copilot")
    monkeypatch.setattr(
        readiness,
        "_run_text",
        lambda command, *, timeout_s, input_text=None: _completed(
            "GitHub Copilot CLI 1.0.78\n"
        ),
    )

    report = readiness.check_backend_readiness("copilot", "subscription_cli", probe_auth=False)

    assert report.ok
    assert not report.warnings


def _fake_pi(monkeypatch, catalog: str, *, version: str = "0.83.0") -> None:
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/pi")

    def run(command, *, timeout_s, input_text=None):
        del timeout_s, input_text
        if command[-1] == "--version":
            return _completed(f"{version}\n")
        assert command[-1] == "--list-models"
        return _completed(catalog)

    monkeypatch.setattr(readiness, "_run_text", run)


_PI_CATALOG = (
    "provider model context max-out thinking images\n"
    "deepseek deepseek-chat 128K 8K yes no\n"
    "deepseek deepseek-reasoner 128K 64K yes no\n"
    "anthropic claude-opus-5 1M 128K yes yes\n"
    "copilot-forward claude-opus-5 1M 64K yes yes\n"
)


def test_pi_readiness_flags_a_provider_that_is_not_authenticated(
    monkeypatch, tmp_path
) -> None:
    """The gap this closes: ``--list-models`` succeeding meant READY, so a stale
    or mistyped provider prefix passed the doctor and then failed EVERY call
    with ``No API key found for <provider>``."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_PI_PROVIDER", "github-copilot")
    _fake_pi(monkeypatch, _PI_CATALOG)

    report = readiness.check_backend_readiness("pi", "subscription_cli")

    assert not report.ok
    problem = next(p for p in report.problems if p.capability == "pi provider")
    assert "github-copilot" in problem.detail
    assert "deepseek" in problem.detail  # names the providers that DO exist
    assert "ARGUS_SKILL_PI_PROVIDER" in problem.remediation


def test_pi_readiness_accepts_an_authenticated_provider(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_PI_PROVIDER", "deepseek")
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "deepseek-chat")
    _fake_pi(monkeypatch, _PI_CATALOG)

    report = readiness.check_backend_readiness("pi", "subscription_cli")

    assert report.ok, [p.detail for p in report.problems]
    assert not report.warnings


def test_pi_readiness_warns_when_a_bare_model_is_ambiguous(
    monkeypatch, tmp_path
) -> None:
    """Pi still resolves it, so this is a warning rather than a hard failure —
    but the operator should know which catalog they are actually buying from."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_PI_PROVIDER", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "claude-opus-5")
    _fake_pi(monkeypatch, _PI_CATALOG)

    report = readiness.check_backend_readiness("pi", "subscription_cli")

    assert report.ok
    assert any(
        "claude-opus-5" in warning and "ARGUS_SKILL_PI_PROVIDER" in warning
        for warning in report.warnings
    ), report.warnings


def test_pi_readiness_warns_when_no_catalog_carries_the_model(
    monkeypatch, tmp_path
) -> None:
    """A warning, not a failure: ``pi --model`` also accepts fuzzy patterns, so
    an id missing from the table can still resolve. The operator gets the
    diagnostic without a false red doctor."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_PI_PROVIDER", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "gpt-5.4-mini")
    _fake_pi(monkeypatch, _PI_CATALOG)

    report = readiness.check_backend_readiness("pi", "subscription_cli")

    assert report.ok
    assert any("gpt-5.4-mini" in warning for warning in report.warnings)


def test_pi_readiness_warns_once_per_distinct_model(monkeypatch, tmp_path) -> None:
    """Four roles usually share one id — say it once, not four times."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_PI_PROVIDER", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "claude-opus-5")
    _fake_pi(monkeypatch, _PI_CATALOG)

    report = readiness.check_backend_readiness("pi", "subscription_cli")

    assert len(report.warnings) == 1, report.warnings
