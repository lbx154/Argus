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
    monkeypatch.setattr(readiness, "resolve_runner_bin", lambda *_args: "/bin/codex")

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
