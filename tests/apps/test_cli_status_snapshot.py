"""Tests for D3: gate + lifecycle snapshot rendered into ``argus --status``.

The new helpers are pure projections of observable state — render facts
the agent already acts on, don't make new decisions. These tests verify
the rendering is correct and fail-soft.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from argus.apps.cli._core import (
    _render_lifecycle_status_lines,
    _resolve_research_workdir,
)

# ---------------------------------------------------------------------------
# _resolve_research_workdir
# ---------------------------------------------------------------------------


def _bundle(root: Path):
    return Namespace(project=Namespace(root=root))


def test_resolve_workdir_prefers_env_var(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "external"
    custom.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_WORKDIR", str(custom))
    bundle = _bundle(tmp_path / "life")
    assert _resolve_research_workdir(bundle) == custom


def test_resolve_workdir_picks_code_subdir_when_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_WORKDIR", raising=False)
    life = tmp_path / "life"
    (life / "code").mkdir(parents=True)
    bundle = _bundle(life)
    assert _resolve_research_workdir(bundle) == life / "code"


# ---------------------------------------------------------------------------
# _render_lifecycle_status_lines
# ---------------------------------------------------------------------------


def test_lifecycle_lines_safe_on_missing_workdir(tmp_path: Path) -> None:
    # `infer_observable_status` handles missing dirs gracefully (returns
    # INCUBATING with `now` as created_at), so a missing workdir is
    # actually the normal "fresh project not yet on disk" case — render
    # the lifecycle block normally rather than silently skipping it.
    lines = _render_lifecycle_status_lines(
        tmp_path / "does-not-exist",
        state_root=tmp_path / "state",
    )
    text = "\n".join(lines)
    assert "lifecycle:" in text
    assert "state         : incubating" in text


def test_lifecycle_lines_mark_persisted_state(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from argus.life.project_lifecycle import ProjectState, ProjectStatus
    from argus.life.project_lifecycle_io import write_persisted

    worktree = tmp_path / "code"
    worktree.mkdir()
    state_root = tmp_path / "state"
    status = ProjectStatus(
        project_id=state_root.name,
        state=ProjectState.QUARANTINED,
        created_at=datetime.now(timezone.utc),
    )
    write_persisted(state_root, status=status, history=[])

    lines = _render_lifecycle_status_lines(worktree, state_root=state_root)
    text = "\n".join(lines)
    assert "state         : quarantined  (persisted)" in text
    assert "allocatable   : False" in text


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Subprocess: full `python -m argus --status` smoke
# ---------------------------------------------------------------------------
