from __future__ import annotations

import http.client
import json
from pathlib import Path
from typing import Any

import pytest

from argus.provider_integrations import copilot_account_quota as quota_mod
from argus.provider_integrations.copilot_account_quota import (
    account_quota,
    active_copilot_credential,
    classify_billing,
    parse_account_quota,
    probe_enabled,
)

SECRET = "gho_test_secret_value"


def _user(*, entitlement: float, remaining: float, credits_used: float = 0, unlimited: bool = False,
          token_based: bool = True, percent: float | None = None) -> dict[str, Any]:
    premium = {
        "entitlement": entitlement,
        "remaining": int(remaining),
        "quota_remaining": remaining,
        "credits_used": credits_used,
        "unlimited": unlimited,
        "overage_permitted": False,
        "token_based_billing": token_based,
        "percent_remaining": percent if percent is not None else (
            100.0 * remaining / entitlement if entitlement else 0.0
        ),
    }
    return {
        "login": "someone",
        "copilot_plan": "enterprise",
        "quota_reset_date": "2026-11-01",
        "token_based_billing": token_based,
        "quota_snapshots": {"premium_interactions": premium},
    }


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    for name in (
        "GH_TOKEN", "GITHUB_TOKEN",
        "ARGUS_SKILL_ACCOUNT_BILLING_MODE", "ARGUS_SKILL_COPILOT_HOME", "COPILOT_HOME",
        "ARGUS_SKILL_COPILOT_TOKEN_FROM_ENV", "COPILOT_GITHUB_TOKEN", "ARGUS_SKILL_COPILOT_TRIAL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_large_token_billed_entitlement_is_credit_billing() -> None:
    mode, _ = classify_billing(_user(entitlement=1_000_000, remaining=576_234.7, credits_used=423_765))
    assert mode == "credit"
    quota = parse_account_quota(_user(entitlement=1_000_000, remaining=576_234.7, credits_used=423_765, percent=57.6))
    assert quota.billing_mode == "credit"
    assert quota.percent_remaining == pytest.approx(57.6)
    assert quota.used == pytest.approx(423_765)
    assert quota.reset_date == "2026-11-01"


def test_request_count_entitlement_is_request_billing_even_when_flagged_token_based() -> None:
    quota = parse_account_quota(_user(entitlement=300, remaining=37))
    assert quota.billing_mode == "request"
    assert quota.remaining == 37
    assert quota.used == 263


def test_unlimited_and_missing_quota() -> None:
    assert classify_billing(_user(entitlement=0, remaining=0, unlimited=True))[0] == "unlimited"
    assert classify_billing({"login": "x"})[0] == "unknown"


def test_operator_can_correct_the_detected_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_BILLING_MODE", "credit")
    quota = parse_account_quota(_user(entitlement=300, remaining=37))
    assert quota.billing_mode == "credit"
    assert quota.mode_source == "operator"


def test_low_flag_follows_warn_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    assert parse_account_quota(_user(entitlement=300, remaining=20)).low is True
    assert parse_account_quota(_user(entitlement=300, remaining=200)).low is False
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_QUOTA_WARN_PERCENT", "70")
    assert parse_account_quota(_user(entitlement=300, remaining=200)).low is True


def _write_cli_config(home: Path, login: str = "someone", token: str = SECRET) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(
        "// managed\n" + json.dumps({
            "lastLoggedInUser": {"host": "https://github.com", "login": login},
            "authTokens": {f"https://github.com:{login}": {"token": token}},
        }),
        encoding="utf-8",
    )


def test_credential_follows_the_cli_last_logged_in_user(tmp_path: Path) -> None:
    _write_cli_config(tmp_path / "user" / ".copilot")
    assert active_copilot_credential() == (SECRET, "someone", "https://github.com")


def test_dedicated_account_home_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_cli_config(tmp_path / "user" / ".copilot", login="personal")
    _write_cli_config(tmp_path / "bound", login="bound")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_HOME", str(tmp_path / "bound"))
    credential = active_copilot_credential()
    assert credential is not None and credential[1] == "bound"


def test_probe_is_off_under_pytest_unless_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ACCOUNT_QUOTA_PROBE", raising=False)
    assert probe_enabled() is False
    assert account_quota() is None
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_QUOTA_PROBE", "on")
    assert probe_enabled() is True


def test_quota_is_cached_and_the_cache_holds_no_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_QUOTA_PROBE", "on")
    _write_cli_config(tmp_path / "user" / ".copilot")
    calls: list[str] = []

    def fake_fetch(token: str, host: str = "https://github.com") -> dict[str, Any]:
        calls.append(host)
        assert token == SECRET
        return _user(entitlement=300, remaining=37)

    monkeypatch.setattr(quota_mod, "fetch_copilot_user", fake_fetch)
    first = account_quota(blocking=True)
    second = account_quota()
    assert first is not None and second is not None
    assert first.remaining == 37 and second.remaining == 37
    assert len(calls) == 1
    cache = (tmp_path / "home" / "copilot-account-quota.json").read_text(encoding="utf-8")
    assert SECRET not in cache


def test_failed_probe_is_cached_without_provider_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_QUOTA_PROBE", "on")
    _write_cli_config(tmp_path / "user" / ".copilot")

    def failing(token: str, host: str = "https://github.com") -> dict[str, Any]:
        raise OSError(f"boom {token}")

    monkeypatch.setattr(quota_mod, "fetch_copilot_user", failing)
    quota = account_quota(blocking=True)
    assert quota is not None and quota.error == "OSError" and quota.billing_mode == "unknown"
    cache = (tmp_path / "home" / "copilot-account-quota.json").read_text(encoding="utf-8")
    assert SECRET not in cache


def test_nonblocking_read_returns_cache_and_never_waits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_QUOTA_PROBE", "on")
    started: list[bool] = []

    class _Thread:
        def __init__(self, target: Any, name: str, daemon: bool) -> None:
            self.target = target

        def start(self) -> None:
            started.append(True)

    monkeypatch.setattr(quota_mod.threading, "Thread", _Thread)
    _write_cli_config(tmp_path / "user" / ".copilot")
    assert account_quota(blocking=False) is None
    assert started == [True]


@pytest.mark.parametrize("failure", [
    http.client.IncompleteRead(b""),
    http.client.RemoteDisconnected("closed"),
    RuntimeError("anything"),
])
def test_every_probe_failure_is_cached_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_QUOTA_PROBE", "on")
    _write_cli_config(tmp_path / "user" / ".copilot")

    def failing(token: str, host: str = "https://github.com") -> dict[str, Any]:
        raise failure

    monkeypatch.setattr(quota_mod, "fetch_copilot_user", failing)
    quota = quota_mod.refresh_account_quota()
    assert quota is not None and quota.error == type(failure).__name__


def test_ambient_cli_token_is_the_account_probed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _write_cli_config(tmp_path / "user" / ".copilot")
    monkeypatch.setenv("GH_TOKEN", "ambient-token")
    assert active_copilot_credential() == ("ambient-token", "", "https://github.com")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot-token")
    assert active_copilot_credential() == ("copilot-token", "", "https://github.com")


def test_bound_account_home_ignores_ambient_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _write_cli_config(tmp_path / "bound", login="bound")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_HOME", str(tmp_path / "bound"))
    monkeypatch.setenv("GH_TOKEN", "ambient-token")
    credential = active_copilot_credential()
    assert credential is not None and credential[1] == "bound"


def test_cache_is_kept_per_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ACCOUNT_QUOTA_PROBE", "on")
    _write_cli_config(tmp_path / "user" / ".copilot", login="first", token="first-token")
    answers = {"first-token": 37.0, "second-token": 250.0}
    monkeypatch.setattr(
        quota_mod, "fetch_copilot_user",
        lambda token, host="https://github.com": _user(entitlement=300, remaining=answers[token]),
    )
    assert account_quota(blocking=True).remaining == 37  # type: ignore[union-attr]
    _write_cli_config(tmp_path / "user" / ".copilot", login="second", token="second-token")
    # A different account never sees the first account's cached figures.
    monkeypatch.setattr(quota_mod, "threading", _NoThreads)
    assert account_quota(blocking=False) is None
    assert account_quota(blocking=True).remaining == 250  # type: ignore[union-attr]
    _write_cli_config(tmp_path / "user" / ".copilot", login="first", token="first-token")
    assert account_quota().remaining == 37  # type: ignore[union-attr]


class _NoThreads:
    class Thread:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            pass


def test_enterprise_server_hosts_use_the_api_v3_prefix() -> None:
    assert quota_mod._api_base("https://github.com") == "https://api.github.com"
    assert quota_mod._api_base("https://git.example.com") == "https://git.example.com/api/v3"


def test_redirects_are_refused_so_the_token_stays_put() -> None:
    import urllib.request

    request = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "token t"})
    handler = quota_mod._NoRedirect()
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/") is None
