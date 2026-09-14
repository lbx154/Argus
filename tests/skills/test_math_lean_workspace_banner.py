"""Regression test: the math Engineer must be told the host's Mathlib exists.

``math-research-execution.md`` promises `import Mathlib` resolves
automatically. It does — through ``_resolve_lake_workspace``, whose first and
highest-priority step is "a lakefile above the source". An Engineer that
cannot see the host workspace does the obvious thing, writes its own lakefile
into the project root, and thereby *shadows* the built library with an empty
one. Observed live in run 7 (s-2962d053): 7.5 GB of Mathlib re-fetched into the
project while a built v4.34.0-rc1 workspace sat unread.

Citations:
- argus/verticals/math/stages.py — ``_lean_workspace_note``
- argus/tools/lean_check.py — ``_resolve_lake_workspace`` search order
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.verticals.math import stages


@pytest.fixture
def host_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "mathlib"
    workspace.mkdir()
    (workspace / "lakefile.toml").write_text("name = \"mathlib\"\n", encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_MATHLIB_WORKSPACE", str(workspace))
    return workspace


@pytest.mark.parametrize("role", ["engineer", "reviewer"])
def test_compiling_roles_are_told_where_the_built_mathlib_is(
    role: str, host_workspace: Path
) -> None:
    banner = stages.role_banner(role)
    assert str(host_workspace) in banner, (
        f"the {role} banner does not name the host's Lake workspace, so the "
        "agent's only way to get Mathlib is to build its own — which shadows "
        "this one"
    )
    assert "shadows" in banner


@pytest.mark.parametrize("role", ["planner", "manager", "scientist"])
def test_non_compiling_roles_do_not_carry_the_note(
    role: str, host_workspace: Path
) -> None:
    """Prompt weight only where it can be acted on."""
    assert str(host_workspace) not in stages.role_banner(role)


def test_banner_is_silent_when_no_workspace_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No paragraph beats a paragraph about a directory that is not there."""
    monkeypatch.setenv("ARGUS_SKILL_MATHLIB_WORKSPACE", str(tmp_path / "absent"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    banner = stages.role_banner("engineer")
    assert "This host's Lean environment" not in banner
    # The skill itself is unchanged — only the appended note is conditional.
    assert banner.strip()


def test_note_never_fails_a_mission(monkeypatch: pytest.MonkeyPatch) -> None:
    """A banner is not load-bearing enough to be worth an exception."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("workspace probe blew up")

    monkeypatch.setattr(
        "argus.verticals.math.lean_evidence.resolved_mathlib_workspace",
        explode,
    )
    assert stages.role_banner("engineer").strip()
