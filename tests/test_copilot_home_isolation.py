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

from argus_skill.agent_cli.copilot_home import (
    COPILOT_HOME_ENV,
    apply_copilot_home,
    argus_copilot_home,
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


def test_home_sits_beside_the_rest_of_argus_state(tmp_path: Path) -> None:
    env = _argus_env(tmp_path)

    assert argus_copilot_home(env) == tmp_path / "argus" / "copilot-home"


def test_operator_config_is_seeded_so_behaviour_is_unchanged(tmp_path: Path) -> None:
    # A home without these would silently run on Copilot defaults instead of the
    # operator's settings — a behaviour change disguised as a storage change.
    env = _argus_env(tmp_path)
    personal = Path(env["HOME"]) / ".copilot"
    personal.joinpath("config.json").write_text('{"a":1}', encoding="utf-8")
    personal.joinpath("settings.json").write_text('{"b":2}', encoding="utf-8")

    home = prepare_copilot_home(env)

    assert home is not None
    assert (home / "config.json").read_text(encoding="utf-8") == '{"a":1}'
    assert (home / "settings.json").read_text(encoding="utf-8") == '{"b":2}'


def test_seeding_never_overwrites_what_is_already_there(tmp_path: Path) -> None:
    env = _argus_env(tmp_path)
    personal = Path(env["HOME"]) / ".copilot"
    personal.joinpath("config.json").write_text('{"personal":true}', encoding="utf-8")
    home = argus_copilot_home(env)
    home.mkdir(parents=True)
    home.joinpath("config.json").write_text('{"argus":true}', encoding="utf-8")

    prepare_copilot_home(env)

    assert home.joinpath("config.json").read_text(encoding="utf-8") == '{"argus":true}'


def _managed_config(path: Path) -> dict:
    payload = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )
    return json.loads(payload)


def test_auth_fields_refresh_without_overwriting_argus_state(tmp_path: Path) -> None:
    env = _argus_env(tmp_path)
    personal = Path(env["HOME"]) / ".copilot"
    personal.joinpath("config.json").write_text(
        '// managed\n{"copilotTokens":{"github.com":"fresh"},'
        '"loggedInUsers":[{"login":"operator"}],'
        '"lastLoggedInUser":{"login":"operator"},'
        '"trustedFolders":["/operator-only"]}',
        encoding="utf-8",
    )
    home = argus_copilot_home(env)
    home.mkdir(parents=True)
    home.joinpath("config.json").write_text(
        '{"argusOnly":"keep","copilotTokens":{"github.com":"stale"}}',
        encoding="utf-8",
    )

    prepare_copilot_home(env)

    config = _managed_config(home / "config.json")
    assert config["argusOnly"] == "keep"
    assert config["copilotTokens"] == {"github.com": "fresh"}
    assert config["loggedInUsers"] == [{"login": "operator"}]
    assert config["lastLoggedInUser"] == {"login": "operator"}
    assert "trustedFolders" not in config
    assert (home / "config.json").stat().st_mode & 0o777 == 0o600


def test_operator_logout_removes_stale_isolated_auth(tmp_path: Path) -> None:
    env = _argus_env(tmp_path)
    personal = Path(env["HOME"]) / ".copilot"
    personal.joinpath("config.json").write_text('{"firstLaunchAt":"now"}', encoding="utf-8")
    home = argus_copilot_home(env)
    home.mkdir(parents=True)
    home.joinpath("config.json").write_text(
        '{"argusOnly":true,"copilotTokens":{"github.com":"stale"},'
        '"loggedInUsers":[{"login":"old"}],'
        '"lastLoggedInUser":{"login":"old"}}',
        encoding="utf-8",
    )

    prepare_copilot_home(env)

    config = _managed_config(home / "config.json")
    assert config == {"argusOnly": True}


def test_an_explicitly_chosen_home_always_wins(tmp_path: Path) -> None:
    # The self-maintenance sandbox sets up a private per-worktree Copilot home
    # and must keep pointing at its own copy; so must an operator who sets the
    # variable deliberately.
    chosen = str(tmp_path / "worktree" / ".argus-self-maintenance-runtime" / "copilot-home")
    env = _argus_env(tmp_path, **{COPILOT_HOME_ENV: chosen})

    assert apply_copilot_home(env)[COPILOT_HOME_ENV] == chosen


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_value_is_not_a_choice(tmp_path: Path, blank: str) -> None:
    env = _argus_env(tmp_path, **{COPILOT_HOME_ENV: blank})

    assert apply_copilot_home(env)[COPILOT_HOME_ENV] == str(argus_copilot_home(env))


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


# --- wiring: the runner must actually use it -------------------------------


def _child_env_for(backend: str, **option_kwargs):
    from argus_skill.agent_cli import _prompt_delivery

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
    )
    return mixin._child_env(holder, options)


def test_a_plain_copilot_mission_gets_the_argus_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This is the path that was leaking: no sandbox, no isolated workdir — the
    # ordinary mission, where `_child_env` used to return None and the child
    # simply inherited an environment with no COPILOT_HOME in it.
    for key, value in _argus_env(tmp_path).items():
        monkeypatch.setenv(key, value)

    env = _child_env_for("copilot")

    assert env is not None
    assert env[COPILOT_HOME_ENV] == str(tmp_path / "argus" / "copilot-home")


@pytest.mark.parametrize("backend", ["codex", "claude", "opencode"])
def test_other_backends_keep_inheriting_untouched(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Returning None means "inherit"; only Copilot needed relocating, and
    # rebuilding the environment for the others would be an unrelated risk.
    for key, value in _argus_env(tmp_path).items():
        monkeypatch.setenv(key, value)

    assert _child_env_for(backend) is None


def test_the_operators_personal_home_is_never_written_to(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The point of the change, stated as an assertion: nothing lands in
    # ~/.copilot as a side effect of preparing the Argus home.
    env = _argus_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    personal = Path(env["HOME"]) / ".copilot"
    personal.joinpath("config.json").write_text("{}", encoding="utf-8")
    before = sorted(p.name for p in personal.iterdir())

    _child_env_for("copilot")

    assert sorted(p.name for p in personal.iterdir()) == before
    assert os.environ.get(COPILOT_HOME_ENV) is None  # our own env is untouched


# --- retention: relocating the growth is not the same as bounding it -------


def _session(home: Path, name: str, *, age_days: float) -> Path:
    d = home / "session-state" / name
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    stamp = time.time() - age_days * 86400
    os.utime(d, (stamp, stamp))
    return d


def test_stale_sessions_are_pruned_and_recent_ones_kept(tmp_path: Path) -> None:
    # At the observed ~115 sessions/hour, moving the writes without bounding
    # them just relocates 2.6 GB/day. Only a recent session can be resumed.
    home = tmp_path / "copilot-home"
    old = _session(home, "old", age_days=30)
    edge = _session(home, "edge", age_days=6.9)
    fresh = _session(home, "fresh", age_days=0)

    assert prune_copilot_sessions(home, env={}) == 1
    assert not old.exists()
    assert edge.exists() and fresh.exists()


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


def test_the_operators_personal_home_is_never_swept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The sweep must be reachable only for the Argus-owned home. ~/.copilot is
    # the operator's data, including sessions Argus did not create.
    env = _argus_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    personal = Path(env["HOME"]) / ".copilot"
    ancient = personal / "session-state" / "operators-own"
    ancient.mkdir(parents=True)
    stamp = time.time() - 365 * 86400
    os.utime(ancient, (stamp, stamp))

    prepare_copilot_home(env)

    assert ancient.exists()


def test_the_sweep_is_throttled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # prepare_copilot_home runs on the child-env path, once per provider turn;
    # scanning tens of thousands of directories every time would be the cost of
    # the fix exceeding the problem.
    env = _argus_env(tmp_path)
    home = prepare_copilot_home(env)
    assert home is not None
    _session(home, "stale", age_days=30)

    prepare_copilot_home(env)  # second call within the hour

    assert (home / "session-state" / "stale").exists()
