from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.manager.directive import (
    ACTIVE_MANAGER_DIRECTIVE_FILENAME,
    ACTIVE_MANAGER_DIRECTIVE_PREFIX,
    active_manager_directive_message,
    clear_active_manager_directive,
    load_active_manager_directive,
    set_active_manager_directive,
)


def _set_objective(root: Path, objective: str) -> None:
    (root / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": objective}),
        encoding="utf-8",
    )


def test_directive_persists_until_replaced_or_cleared(tmp_path: Path) -> None:
    _set_objective(tmp_path, "prove the theorem")

    first = set_active_manager_directive(tmp_path, "stop row-by-row work")
    assert load_active_manager_directive(tmp_path) == first
    assert active_manager_directive_message(tmp_path) == (
        ACTIVE_MANAGER_DIRECTIVE_PREFIX + "stop row-by-row work"
    )

    second = set_active_manager_directive(tmp_path, "use a structural batch")
    assert second.revision != first.revision
    assert load_active_manager_directive(tmp_path) == second

    assert clear_active_manager_directive(tmp_path) is True
    assert clear_active_manager_directive(tmp_path) is False
    assert active_manager_directive_message(tmp_path) == ""


def test_directive_is_scoped_to_the_objective(tmp_path: Path) -> None:
    _set_objective(tmp_path, "first objective")
    set_active_manager_directive(tmp_path, "first-objective policy")

    _set_objective(tmp_path, "replacement objective")

    assert load_active_manager_directive(tmp_path) is None
    assert (tmp_path / ACTIVE_MANAGER_DIRECTIVE_FILENAME).exists()


def test_empty_directive_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        set_active_manager_directive(tmp_path, "  ")


def test_malformed_directive_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ACTIVE_MANAGER_DIRECTIVE_FILENAME).write_text(
        "{not json",
        encoding="utf-8",
    )

    assert load_active_manager_directive(tmp_path) is None
