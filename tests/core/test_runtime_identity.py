"""Source-root identity resolution and its startup preflight.

The 2026-09-05 incident: a user-site editable install rewrote the ``argus``
launcher so restarts loaded a stale worktree. ``ARGUS_SKILL_SOURCE_ROOT`` was
never set anywhere, so the existing configured-root diagnostics never fired.
These pin the env > persisted > unset resolution chain and the fail-closed
preflight that turns a configured root into a startup contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.core import knob_store
from argus_skill.core.runtime_identity import (
    configured_source_root,
    runtime_identity,
    source_root,
    source_root_preflight_error,
)


def test_configured_source_root_prefers_env_over_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knob_store.write_persisted_knob("ARGUS_SKILL_SOURCE_ROOT", "/persisted/checkout")

    assert configured_source_root() == "/persisted/checkout"

    monkeypatch.setenv("ARGUS_SKILL_SOURCE_ROOT", "/env/checkout")
    assert configured_source_root() == "/env/checkout"


def test_runtime_identity_reads_the_persisted_source_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_SOURCE_ROOT", raising=False)
    knob_store.write_persisted_knob("ARGUS_SKILL_SOURCE_ROOT", str(source_root()))

    identity = runtime_identity()

    assert identity["configured_source_root"] == str(source_root())
    assert identity["source_root_matches_config"] is True


def test_source_root_preflight_is_permissive_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_SOURCE_ROOT", raising=False)

    assert source_root_preflight_error() == ""


def test_source_root_preflight_accepts_the_loaded_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SOURCE_ROOT", str(source_root()))

    assert source_root_preflight_error() == ""


def test_source_root_preflight_accepts_a_symlink_to_the_loaded_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    require_symlink_support: None,
) -> None:
    link = tmp_path / "deploy-root"
    link.symlink_to(source_root(), target_is_directory=True)
    monkeypatch.setenv("ARGUS_SKILL_SOURCE_ROOT", str(link))

    assert source_root_preflight_error() == ""


def test_source_root_preflight_refuses_a_foreign_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = tmp_path / "other-worktree"
    monkeypatch.setenv("ARGUS_SKILL_SOURCE_ROOT", str(other))

    error = source_root_preflight_error()

    assert str(source_root()) in error
    assert str(other) in error


def test_runtime_identity_survives_a_corrupt_knob_store_but_the_preflight_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime_identity() is pure diagnostics — webapi /api/meta, the daemon
    status writer, and handoff-candidate reads catch only OSError around it —
    so a corrupt config.json must read as "unconfigured" there. The startup
    preflight stays strict: the corruption is itself a fail-closed refusal."""
    from argus_skill.core.paths import config_path

    monkeypatch.delenv("ARGUS_SKILL_SOURCE_ROOT", raising=False)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    identity = runtime_identity()

    assert identity["configured_source_root"] is None
    assert identity["source_root_matches_config"] is None
    with pytest.raises(knob_store.KnobStoreCorruptError):
        source_root_preflight_error()


def test_source_root_preflight_enforces_the_persisted_knob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_SOURCE_ROOT", raising=False)
    knob_store.write_persisted_knob("ARGUS_SKILL_SOURCE_ROOT", str(tmp_path / "stale"))

    assert str(tmp_path / "stale") in source_root_preflight_error()
