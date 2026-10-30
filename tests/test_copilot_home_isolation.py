"""Argus's Copilot workers must not write into the operator's ~/.copilot.

The Copilot CLI keeps its entire working state — session transcripts, the
session-store database, logs — under ``COPILOT_HOME``, which defaults to the
operator's personal ``~/.copilot``. Argus runs Copilot-backed roles
continuously, so with the default every daemon writes there. Measured on the
host this was found: 46,220 session directories and 47 GB in the operator's
home, growing ~115/hour, against 10 in the Argus-owned one.

Current CLI releases keep authentication fields in ``config.json`` under
``COPILOT_HOME``. Argus mirrors only those fields into its isolated home; it
must not copy unrelated operator state or expose token values in diagnostics.

These tests pin the fix and the two ways it must stay out of the way: it applies
to Copilot only, and an explicitly chosen home always wins.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.agent_cli.copilot_home import (
    COPILOT_ACCOUNT_HOME_KNOB,
    COPILOT_HOME_ENV,
    apply_copilot_account,
    apply_copilot_home,
    argus_copilot_home,
    copilot_log_dir,
    prepare_copilot_home,
    prune_copilot_sessions,
)


def _argus_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".copilot").mkdir(parents=True, exist_ok=True)
    return {
        "ARGUS_SKILL_HOME": str(tmp_path / "argus"),
        "HOME": str(home),
        **extra,
    }


def _managed_config(path: Path) -> dict:
    payload = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )
    return json.loads(payload)


@pytest.mark.parametrize("empty_auth_tokens", [False, True])
def test_operator_logout_removes_stale_isolated_auth(
    tmp_path: Path, empty_auth_tokens: bool,
) -> None:
    env = _argus_env(tmp_path)
    personal = Path(env["HOME"]) / ".copilot"
    personal_config = {"firstLaunchAt": "now"}
    if empty_auth_tokens:
        personal_config["authTokens"] = {}
    personal.joinpath("config.json").write_text(json.dumps(personal_config), encoding="utf-8")
    home = argus_copilot_home(env)
    home.mkdir(parents=True)
    home.joinpath("config.json").write_text(
        '{"argusOnly":true,"copilotTokens":{"github.com":"stale"},'
        '"loggedInUsers":[{"login":"old"}],'
        '"lastLoggedInUser":{"login":"old"},'
        '"authTokens":{"old-account":{"token":"fake-stale-token"}}}',
        encoding="utf-8",
    )

    prepare_copilot_home(env)

    config = _managed_config(home / "config.json")
    expected = {"argusOnly": True}
    if empty_auth_tokens:
        expected["authTokens"] = {}
    assert config == expected


def test_an_unusable_location_leaves_the_default_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail open: a worker that cannot get its own home should still run, using
    # the CLI default, rather than be pointed at a directory that is not there.
    env = _argus_env(tmp_path)
    monkeypatch.setattr(
        Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs"))
    )

    assert apply_copilot_home(env).get(COPILOT_HOME_ENV) is None


def test_persisted_account_overrides_ambient_credentials_without_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.core.knob_store import write_persisted_knob

    chosen = tmp_path / "dedicated"
    chosen.mkdir()
    credentials = '{"authTokens":{"account-two":{"token":"fake-account-two"}}}'
    (chosen / "config.json").write_text(credentials)
    assert write_persisted_knob(COPILOT_ACCOUNT_HOME_KNOB, str(chosen))
    monkeypatch.setenv("HOME", str(tmp_path))
    personal = tmp_path / ".copilot"
    personal.mkdir()
    (personal / "config.json").write_text('{"authTokens":{"original":"fake-original"}}')
    env = dict(os.environ)
    env.update({
        "COPILOT_HOME": str(personal),
        "COPILOT_GITHUB_TOKEN": "fake-ambient",
        "GH_TOKEN": "fake-gh",
        "GITHUB_TOKEN": "fake-github",
        "GH_CONFIG_DIR": str(tmp_path / "personal-gh"),
        "COPILOT_PROVIDER_BASE_URL": "https://provider.example",
        "COPILOT_PROVIDER_API_KEY": "fake-provider",
        "COPILOT_OFFLINE": "true",
    })
    original = dict(env)

    result = apply_copilot_home(env)

    assert result["COPILOT_HOME"] == str(chosen)
    assert result["GH_CONFIG_DIR"] == str(chosen / "gh")
    assert all(key not in result for key in (
        "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
        "COPILOT_PROVIDER_BASE_URL", "COPILOT_PROVIDER_API_KEY", "COPILOT_OFFLINE",
    ))
    assert (chosen / "config.json").read_text() == credentials
    assert sorted(path.name for path in chosen.iterdir()) == ["config.json"]
    assert (personal / "config.json").read_text() == '{"authTokens":{"original":"fake-original"}}'
    assert original["COPILOT_GITHUB_TOKEN"] == "fake-ambient"
    assert copilot_log_dir(original) == chosen / "logs"


def test_unavailable_dedicated_home_fails_instead_of_using_another_account(
    tmp_path: Path,
) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied")
    env = _argus_env(tmp_path, **{COPILOT_ACCOUNT_HOME_KNOB: str(blocked)})

    with pytest.raises(RuntimeError, match="Cannot use dedicated Copilot home"):
        apply_copilot_home(env)

    assert not argus_copilot_home(env).exists()




# --- wiring: the runner must actually use it -------------------------------


def _child_env_for(backend: str, **option_kwargs):
    from argus.agent_cli import _prompt_delivery

    mixin = next(
        obj
        for obj in vars(_prompt_delivery).values()
        if isinstance(obj, type) and hasattr(obj, "_child_env")
    )
    holder = mixin.__new__(mixin)
    holder.backend = backend
    options = SimpleNamespace(
        sandbox_mode=option_kwargs.get("sandbox_mode", ""),
        isolate_workdir=option_kwargs.get("isolate_workdir", False),
        dangerous_yolo=option_kwargs.get("dangerous_yolo", False),
        full_auto=option_kwargs.get("full_auto", False),
        disable_tools=option_kwargs.get("disable_tools", False),
        working_dir=option_kwargs.get("working_dir"),
    )
    return mixin._child_env(holder, options)


# --- retention: relocating the growth is not the same as bounding it -------


def _session(home: Path, name: str, *, age_days: float) -> Path:
    d = home / "session-state" / name
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    stamp = time.time() - age_days * 86400
    os.utime(d, (stamp, stamp))
    return d


def test_retention_window_is_configurable_and_zero_disables(tmp_path: Path) -> None:
    home = tmp_path / "copilot-home"
    _session(home, "a", age_days=3)

    assert prune_copilot_sessions(
        home, env={"ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS": "0"}
    ) == 0
    assert (home / "session-state" / "a").exists()
    assert prune_copilot_sessions(
        home, env={"ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS": "1"}
    ) == 1


def test_a_bad_retention_value_falls_back_rather_than_deleting_everything(
    tmp_path: Path,
) -> None:
    home = tmp_path / "copilot-home"
    _session(home, "recent", age_days=1)

    assert prune_copilot_sessions(
        home, env={"ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS": "not-a-number"}
    ) == 0
    assert (home / "session-state" / "recent").exists()


# --- headless opt-in: operator-supplied Copilot token -----------------------

_TOKEN_KNOB = "ARGUS_SKILL_COPILOT_TOKEN_FROM_ENV"


def _headless_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, opt_in: bool) -> None:
    for key, value in _argus_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(COPILOT_ACCOUNT_HOME_KNOB, raising=False)
    monkeypatch.delenv(COPILOT_HOME_ENV, raising=False)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "fake-copilot-token")
    monkeypatch.setenv("GH_TOKEN", "fake-gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-github-token")
    if opt_in:
        monkeypatch.setenv(_TOKEN_KNOB, "1")
    else:
        monkeypatch.delenv(_TOKEN_KNOB, raising=False)


def test_without_opt_in_plain_copilot_child_inherits_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _headless_env(tmp_path, monkeypatch, opt_in=False)

    env = _child_env_for("copilot")

    assert env["GH_TOKEN"] == "fake-gh-token"
    assert env["GITHUB_TOKEN"] == "fake-github-token"
    assert env["COPILOT_GITHUB_TOKEN"] == "fake-copilot-token"


def test_opted_in_token_survives_a_dedicated_account_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _headless_env(tmp_path, monkeypatch, opt_in=True)
    monkeypatch.setenv(COPILOT_ACCOUNT_HOME_KNOB, str(tmp_path / "dedicated"))

    env = apply_copilot_account(dict(os.environ))

    assert env["COPILOT_GITHUB_TOKEN"] == "fake-copilot-token"
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env


def test_readiness_reports_token_source_and_missing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from argus.core import backend_readiness as br

    _headless_env(tmp_path, monkeypatch, opt_in=True)
    monkeypatch.setattr(br, "resolve_runner_bin", lambda *a, **k: "/fake/copilot")
    monkeypatch.setattr(
        br,
        "_run_backend_version",
        lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="1.0.0\n", stderr=""),
    )

    ready = br.check_backend_readiness("copilot", "subscription", probe_auth=False)
    rendered = br.format_backend_readiness(ready)
    assert "copilot auth source: COPILOT_GITHUB_TOKEN from environment" in rendered
    assert "fake-copilot-token" not in rendered

    monkeypatch.delenv("COPILOT_GITHUB_TOKEN")
    missing = br.check_backend_readiness("copilot", "subscription", probe_auth=False)
    assert not missing.ok
    assert missing.problems[0].capability == "authentication"
    assert "COPILOT_GITHUB_TOKEN is not set" in missing.problems[0].detail
