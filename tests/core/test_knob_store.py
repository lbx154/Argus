"""Tests for ``core.knob_store`` — persisted operator knob overrides.

A ``/backend``/``/config`` or natural-language hyperparameter switch used to
only set ``os.environ`` for the CURRENT process — a restart (of the REPL, or
the next time the daemon boots) silently reverted to the default. This module
is the persisted layer that makes "change it once, read it consistently from
then on" actually true; see ``core.knobs.resolve_role_model`` for the
resolver that consumes it.
"""
from __future__ import annotations

import multiprocessing
import os
import threading
import time
from pathlib import Path

import pytest

from argus_skill.core import knob_store


def _write_knob_in_spawned_process(home: str, ready, name: str) -> None:
    os.environ["ARGUS_SKILL_HOME"] = home
    ready.set()
    knob_store.write_persisted_knob(name, "child")


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-skill-home"))


def test_read_persisted_knobs_empty_when_missing() -> None:
    assert knob_store.read_persisted_knobs() == {}


def test_write_then_read_roundtrips() -> None:
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "gpt-5.5")
    knob_store.write_persisted_knob("ARGUS_SKILL_MANAGER_BACKEND", "copilot")
    assert knob_store.read_persisted_knobs() == {
        "ARGUS_SKILL_MODEL": "gpt-5.5",
        "ARGUS_SKILL_MANAGER_BACKEND": "copilot",
    }


def test_write_persisted_knob_overwrites_only_that_key() -> None:
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "gpt-5.5")
    knob_store.write_persisted_knob("ARGUS_SKILL_MANAGER_BACKEND", "copilot")
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    assert knob_store.read_persisted_knobs() == {
        "ARGUS_SKILL_MODEL": "claude-sonnet-5",
        "ARGUS_SKILL_MANAGER_BACKEND": "copilot",
    }


def test_concurrent_writes_serialize_the_full_read_modify_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read = knob_store.read_persisted_knobs
    start = threading.Barrier(3)
    guard = threading.Lock()
    active = 0
    max_active = 0

    def slow_read() -> dict[str, str]:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return original_read()
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(knob_store, "read_persisted_knobs", slow_read)

    def write(name: str) -> None:
        start.wait()
        knob_store.write_persisted_knob(name, "set")

    threads = [
        threading.Thread(target=write, args=("ARGUS_SKILL_MODEL",)),
        threading.Thread(target=write, args=("ARGUS_SKILL_MANAGER_BACKEND",)),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert max_active == 1
    assert original_read() == {
        "ARGUS_SKILL_MANAGER_BACKEND": "set",
        "ARGUS_SKILL_MODEL": "set",
    }


@pytest.mark.skipif(knob_store.fcntl is None, reason="requires POSIX flock")
def test_writer_waits_for_cross_process_lock() -> None:
    from argus_skill.core.paths import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    knob_store.fcntl.flock(lock_fd, knob_store.fcntl.LOCK_EX)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    process = ctx.Process(
        target=_write_knob_in_spawned_process,
        args=(str(path.parent), ready, "ARGUS_SKILL_MODEL"),
    )
    try:
        process.start()
        assert ready.wait(timeout=5)
        time.sleep(0.1)
        assert process.is_alive()
        assert not path.exists()
    finally:
        knob_store.fcntl.flock(lock_fd, knob_store.fcntl.LOCK_UN)
        os.close(lock_fd)
    process.join(timeout=5)

    assert process.exitcode == 0
    assert knob_store.read_persisted_knobs() == {"ARGUS_SKILL_MODEL": "child"}


def test_write_persisted_knob_is_atomic_no_tmp_file_left_behind():
    from argus_skill.core.paths import config_path

    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "gpt-5.5")
    path = config_path()
    assert path.exists()
    leftover_tmp = list(path.parent.glob("*.tmp"))
    assert leftover_tmp == [], f"atomic write left a temp file behind: {leftover_tmp}"


def test_read_persisted_knobs_tolerates_malformed_json():
    from argus_skill.core.paths import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert knob_store.read_persisted_knobs() == {}


def test_read_persisted_knobs_tolerates_non_dict_json():
    from argus_skill.core.paths import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert knob_store.read_persisted_knobs() == {}


def test_write_persisted_knob_empty_name_is_a_noop():
    assert knob_store.write_persisted_knob("", "value") is False
    assert knob_store.read_persisted_knobs() == {}


def test_persisted_knob_env_wins_over_persisted_file():
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    assert knob_store.persisted_knob(
        "ARGUS_SKILL_MODEL", env={"ARGUS_SKILL_MODEL": "gpt-5.4-mini"},
    ) == "gpt-5.4-mini"


def test_persisted_knob_falls_back_to_file_when_env_unset():
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    assert knob_store.persisted_knob("ARGUS_SKILL_MODEL", env={}) == "claude-sonnet-5"


def test_persisted_knob_empty_when_neither_set():
    assert knob_store.persisted_knob("ARGUS_SKILL_MODEL", env={}) == ""
