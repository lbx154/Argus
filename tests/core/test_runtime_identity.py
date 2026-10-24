"""Source-root identity resolution and its startup preflight.

The 2026-09-05 incident: a user-site editable install rewrote the ``argus``
launcher so restarts loaded a stale worktree. ``ARGUS_SKILL_SOURCE_ROOT`` was
never set anywhere, so the existing configured-root diagnostics never fired.
These pin the env > persisted > unset resolution chain and the fail-closed
preflight that turns a configured root into a startup contract.
"""
from __future__ import annotations

import pytest

from argus.core import knob_store
from argus.core.runtime_identity import (
    runtime_identity,
    source_root,
    source_root_preflight_error,
)


def test_source_root_preflight_accepts_the_loaded_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SOURCE_ROOT", str(source_root()))

    assert source_root_preflight_error() == ""


def test_runtime_identity_survives_a_corrupt_knob_store_but_the_preflight_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime_identity() is pure diagnostics — webapi /api/meta, the daemon
    status writer, and handoff-candidate reads catch only OSError around it —
    so a corrupt config.json must read as "unconfigured" there. The startup
    preflight stays strict: the corruption is itself a fail-closed refusal."""
    from argus.core.paths import config_path

    monkeypatch.delenv("ARGUS_SKILL_SOURCE_ROOT", raising=False)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    identity = runtime_identity()

    assert identity["configured_source_root"] is None
    assert identity["source_root_matches_config"] is None
    with pytest.raises(knob_store.KnobStoreCorruptError):
        source_root_preflight_error()
