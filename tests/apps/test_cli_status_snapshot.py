"""Tests for D3: gate + lifecycle snapshot rendered into ``argus-skill --status``.

The new helpers are pure projections of observable state — render facts
the agent already acts on, don't make new decisions. These tests verify
the rendering is correct and fail-soft.
"""

from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from argus_skill.apps.cli._core import (
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


def test_resolve_workdir_falls_back_to_project_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_WORKDIR", raising=False)
    life = tmp_path / "life"
    life.mkdir()
    bundle = _bundle(life)
    assert _resolve_research_workdir(bundle) == life


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


def test_lifecycle_lines_show_state_and_allocatability(tmp_path: Path) -> None:
    lines = _render_lifecycle_status_lines(tmp_path, state_root=tmp_path)
    text = "\n".join(lines)
    # Fresh tmp dir → incubating, allocatable.
    assert "lifecycle:" in text
    assert "state         : incubating" in text
    assert "allocatable   : True" in text


def test_lifecycle_lines_mark_persisted_state(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from argus_skill.life.project_lifecycle import ProjectState, ProjectStatus
    from argus_skill.life.project_lifecycle_io import write_persisted

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


def _write_bundle(root: Path, name: str, *, condition: str, reward: float, dataset_id: str) -> None:
    bundle = root / "benchmarks" / "evidence" / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "summary.tsv").write_text(
        "row_kind\tcondition\treward\tn_total_trials\tn_completed_trials\tn_errored_trials\n"
        f"aggregate\t{condition}\t{reward}\t89\t89\t0\n",
        encoding="utf-8",
    )
    (bundle / "BUILD_INFO.md").write_text("# Build Info\n", encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps({"dataset_id": dataset_id, "condition": condition}),
        encoding="utf-8",
    )


def _write_claims_tsv(root: Path, rows: list[dict[str, str]]) -> None:
    cols = ["claim_id", "status", "claim", "evidence_1", "evidence_2", "evidence_3", "notes"]
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(row.get(c, "") for c in cols))
    path = root / "paper" / "claims_to_evidence.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Subprocess: full `python -m argus_skill --status` smoke
# ---------------------------------------------------------------------------


def test_status_subprocess_includes_lifecycle_block(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill", "--status"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "ARGUS_SKILL_HOME": str(home)},
    )

    assert proc.returncode == 0, proc.stderr
    # New lifecycle block must be present even with no daemon running.
    assert "lifecycle:" in proc.stdout
    assert "state         :" in proc.stdout
    assert "allocatable   :" in proc.stdout
