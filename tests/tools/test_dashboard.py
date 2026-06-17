"""Tests for ``argus_skill.tools.dashboard`` — built-in live dashboard."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.tools import dashboard


def _make_daemon(root: Path, fingerprint: str, *, project_dir: Path,
                 stages: dict, vertical: str | None = None,
                 current_stage: str = "run", pid: int = 999999) -> Path:
    """Create a fake life-dir + project so discovery/scrape can read it."""
    life = root / "projects" / fingerprint
    life.mkdir(parents=True)
    (life / "daemon.status.json").write_text(json.dumps(
        {"pid": pid, "backend": "codex"}), encoding="utf-8")
    (life / "project.md").write_text(f"# {project_dir}\n", encoding="utf-8")
    (life / "events.jsonl").write_text(
        json.dumps({"type": "life.mission.completed", "cost_usd": 1.5}) + "\n"
        + json.dumps({"type": "engineer.progress", "text": "hello"}) + "\n",
        encoding="utf-8")
    (life / "backlog.jsonl").write_text(
        json.dumps({"status": "running", "title": "do a thing"}) + "\n",
        encoding="utf-8")
    research = project_dir / "research"
    research.mkdir(parents=True, exist_ok=True)
    state = {"current_stage": current_stage, "stages": stages}
    if vertical:
        state["vertical"] = vertical
    (research / "PIPELINE_STATE.json").write_text(json.dumps(state), encoding="utf-8")
    return life


def test_discover_and_scrape(tmp_path, monkeypatch):
    root = tmp_path / "home"
    proj = tmp_path / "myproj"
    _make_daemon(root, "fp1", project_dir=proj,
                 stages={"setup": {"status": "done"}, "optimize": {"status": "running"},
                         "measure": {"status": "pending"}, "report": {"status": "pending"}},
                 vertical="speedrun", current_stage="optimize")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_SKILL_DASHBOARD_ROOTS", raising=False)

    lifes = dashboard.discover_life_dirs()
    assert len(lifes) == 1

    snap = dashboard.scrape_all()
    assert snap["n_projects"] == 1
    p = snap["projects"][0]
    assert p["vertical"] == "speedrun"
    assert p["current_stage"] == "optimize"
    assert p["missions"] == 1 and p["cost"] == 1.5
    assert [s["name"] for s in p["stages"]] == ["setup", "optimize", "measure", "report"]
    assert any(s["status"] == "running" for s in p["stages"])


def test_vertical_inferred_when_field_absent(tmp_path, monkeypatch):
    root = tmp_path / "home"
    proj = tmp_path / "paperproj"
    # no `vertical` field; research stage names present
    _make_daemon(root, "fp1", project_dir=proj,
                 stages={"research": {"status": "done"}, "draft": {"status": "running"},
                         "submission": {"status": "pending"}},
                 vertical=None, current_stage="draft")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_SKILL_DASHBOARD_ROOTS", raising=False)
    snap = dashboard.scrape_all()
    assert snap["projects"][0]["vertical"] == "research"


def test_dedup_symlinked_life_dirs(tmp_path, monkeypatch):
    root = tmp_path / "home"
    proj = tmp_path / "p"
    real = _make_daemon(root, "real", project_dir=proj,
                        stages={"setup": {"status": "done"}}, vertical="speedrun")
    # a second fingerprint dir that is a symlink to the real one
    link = root / "projects" / "linkfp"
    link.symlink_to(real)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_SKILL_DASHBOARD_ROOTS", raising=False)
    lifes = dashboard.discover_life_dirs()
    assert len(lifes) == 1  # symlink collapsed


def test_extra_roots_via_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    alt = tmp_path / "alt"
    _make_daemon(home, "a", project_dir=tmp_path / "pa",
                 stages={"setup": {"status": "done"}}, vertical="speedrun")
    _make_daemon(alt, "b", project_dir=tmp_path / "pb",
                 stages={"research": {"status": "done"}}, vertical="research")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.setenv("ARGUS_SKILL_DASHBOARD_ROOTS", str(alt))
    snap = dashboard.scrape_all()
    assert snap["n_projects"] == 2
    verts = {p["vertical"] for p in snap["projects"]}
    assert verts == {"speedrun", "research"}


def test_paper_enrichment_detected(tmp_path, monkeypatch):
    root = tmp_path / "home"
    proj = tmp_path / "paper"
    _make_daemon(root, "fp", project_dir=proj,
                 stages={"draft": {"status": "running"}}, vertical="research")
    paper = proj / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        "\\includegraphics{a}\n\\citep{x}\n\\TBD foo\n", encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_SKILL_DASHBOARD_ROOTS", raising=False)
    snap = dashboard.scrape_all()
    panels = snap["projects"][0]["enrich"]["panels"]
    assert any(pn["kind"] == "paper" for pn in panels)


def test_snapshot_html_is_self_contained(tmp_path, monkeypatch):
    # the served HTML must be a single string with no external asset refs
    html = dashboard._html()
    assert "<!DOCTYPE html>" in html
    assert "/data.json" in html  # it polls the json endpoint
    assert "setInterval" in html  # auto-refresh
