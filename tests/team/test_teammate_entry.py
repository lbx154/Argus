from __future__ import annotations

from pathlib import Path

from argus_skill.team import task_board as tb
from argus_skill.team import teammate_entry as te


def _form_claim(root: Path, member: str = "t1::w1", task: str = "t1::a") -> None:
    tb.form(root, [{"task_id": task, "objective": "do a", "owns_paths": ["a/**"]}])
    tb.claim_specific(root, task, member, now=1.0)


def test_build_runner_ns_has_required_fields(tmp_path: Path, monkeypatch) -> None:
    # set model envs so it does not call resolve_route_model in the test env
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "m-eng")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "m-rev")
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    ns = te._build_runner_ns(str(tmp_path), max_rounds=7, paper_mission=False)
    assert ns.engineer_model == "m-eng" and ns.reviewer_model == "m-rev"
    assert ns.workdir == str(tmp_path) and ns.max_rounds == 7 and ns.paper_mission is False
    # every field _SkillLoopRunner / execute reads must exist
    for f in ("backend", "engineer_reasoning_effort", "skills_dir",
              "plan_mode", "plan_model", "check", "check_commands", "color", "verbose", "quiet"):
        assert hasattr(ns, f), f


def test_main_inprocess_success_marks_done(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: True)
    rc = te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a",
                  "--cwd", str(tmp_path)])
    assert rc == 0
    assert {t["task_id"]: t for t in tb.snapshot(root)}["t1::a"]["state"] == "done"


def test_main_inprocess_failure_marks_failed(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: False)
    rc = te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a",
                  "--cwd", str(tmp_path)])
    assert rc == 1
    assert {t["task_id"]: t for t in tb.snapshot(root)}["t1::a"]["state"] == "failed"


def test_main_stub_path_still_works(tmp_path: Path) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    rc = te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a",
                  "--cwd", str(tmp_path), "--mission-cmd", "true"])
    assert rc == 0
    assert {t["task_id"]: t for t in tb.snapshot(root)}["t1::a"]["state"] == "done"


def test_main_no_task_returns_2(tmp_path: Path) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tb.form(root, [{"task_id": "t1::a", "objective": "x", "owns_paths": ["a/**"]}])
    rc = te.main(["--root", str(root), "--member-id", "t1::ghost", "--cwd", str(tmp_path),
                  "--mission-cmd", "true"])
    assert rc == 2
