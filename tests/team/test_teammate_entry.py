from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus_skill.team import task_board as tb
from argus_skill.team import teammate_entry as te


def _form_claim(root: Path, member: str = "t1::w1", task: str = "t1::a") -> None:
    tb.form(root, [{"task_id": task, "objective": "do a", "owns_paths": ["a/**"]}])
    claimed = tb.claim_top(root, member, now=1.0)
    assert claimed is not None and claimed["task_id"] == task


def test_build_runner_ns_has_required_fields(tmp_path: Path, monkeypatch) -> None:
    # set model envs so it does not call resolve_route_model in the test env
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "m-eng")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "m-rev")
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    ns = te._build_runner_ns(str(tmp_path), max_rounds=7, paper_mission=False)
    assert ns.engineer_model == "m-eng" and ns.reviewer_model == "m-rev"
    assert ns.workdir == str(tmp_path) and ns.max_rounds == 7 and ns.paper_mission is False
    assert ns.project_state_dir == ""
    # every field _SkillLoopRunner / execute reads must exist
    for f in ("backend", "engineer_reasoning_effort", "skills_dir",
              "plan_mode", "plan_model", "color", "verbose", "quiet"):
        assert hasattr(ns, f), f


def test_build_runner_ns_uses_shared_default_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_MODEL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_REVIEWER_MODEL", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))

    ns = te._build_runner_ns(str(tmp_path), max_rounds=7, paper_mission=False)

    assert ns.engineer_model == "claude-sonnet-5"
    assert ns.reviewer_model == "claude-sonnet-5"


def test_main_inprocess_success_marks_done(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: True)
    rc = te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a",
                  "--cwd", str(tmp_path)])
    assert rc == 0
    assert {t["task_id"]: t for t in tb.snapshot(root)}["t1::a"]["state"] == "done"


def test_main_marks_runtime_as_inside_one_team_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "outer-task")
    seen: dict[str, str] = {}

    def run(*_args, **_kwargs):
        seen["task_id"] = os.environ.get("ARGUS_SKILL_TEAM_TASK_ID", "")
        return True

    monkeypatch.setattr(te, "run_one_engineer_mission", run)

    assert te.main([
        "--root",
        str(root),
        "--member-id",
        "t1::w1",
        "--task-id",
        "t1::a",
        "--cwd",
        str(tmp_path),
    ]) == 0
    assert seen["task_id"] == "t1::a"
    assert os.environ["ARGUS_SKILL_TEAM_TASK_ID"] == "outer-task"


def test_main_passes_task_timeout_to_mission(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tb.form(root, [{
        "task_id": "t1::a",
        "objective": "do a",
        "owns_paths": ["a/**"],
        "timeout_s": 600,
    }])
    assert tb.claim_top(root, "t1::w1", now=1.0) is not None
    captured: dict[str, object] = {}

    def run(*_args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(te, "run_one_engineer_mission", run)
    rc = te.main([
        "--root", str(root),
        "--member-id", "t1::w1",
        "--task-id", "t1::a",
        "--cwd", str(tmp_path),
    ])

    assert rc == 0
    assert captured["timeout_s"] == 600.0


def test_research_teammate_inherits_paper_mission_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    persist_vertical(tmp_path, "research")
    captured: dict[str, object] = {}

    def run(*_args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(te, "run_one_engineer_mission", run)

    assert te.main([
        "--root",
        str(root),
        "--member-id",
        "t1::w1",
        "--task-id",
        "t1::a",
        "--cwd",
        str(tmp_path),
    ]) == 0
    assert captured["paper_mission"] is True


def test_teammate_runtime_uses_isolated_layered_skill_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import argus_skill.apps._runtime as rt

    for role in ("ENGINEER", "REVIEWER"):
        monkeypatch.setenv(f"ARGUS_SKILL_{role}_MODEL", "m")
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    captured: dict[str, object] = {}

    class Outcome:
        success = True

    class Runner:
        def __init__(self, ns):
            captured["paper_mission"] = ns.paper_mission
            captured["project_state_dir"] = ns.project_state_dir
            captured["project_skills_dir"] = os.environ.get(
                "ARGUS_SKILL_PROJECT_SKILLS_DIR"
            )

        def execute(self, **_kwargs):
            return Outcome()

    monkeypatch.setattr(rt, "_SkillLoopRunner", Runner)
    life_dir = tmp_path / "life"

    result = te.run_one_engineer_mission(
        "obj",
        cwd=str(tmp_path),
        life_dir=life_dir,
        paper_mission=True,
        max_rounds=1,
    )

    assert result.success is True
    assert captured == {
        "paper_mission": True,
        "project_state_dir": str(tmp_path),
        "project_skills_dir": str(life_dir / "skills"),
    }


def test_main_inprocess_failure_marks_failed(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: False)
    rc = te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a",
                  "--cwd", str(tmp_path)])
    assert rc == 1
    assert {t["task_id"]: t for t in tb.snapshot(root)}["t1::a"]["state"] == "failed"


def test_main_no_task_returns_2(tmp_path: Path) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tb.form(root, [{"task_id": "t1::a", "objective": "x", "owns_paths": ["a/**"]}])
    rc = te.main(["--root", str(root), "--member-id", "t1::ghost", "--cwd", str(tmp_path)])
    assert rc == 2


def test_run_one_mission_has_no_hard_self_sigkill_timer(tmp_path: Path, monkeypatch) -> None:
    # The teammate no longer SIGKILLs ITSELF on a hard deadline — the Curator owns
    # the process and is the single reaper. So only the SOFT watchdog timer is armed.
    import argus_skill.apps._runtime as rt
    for var in ("ENGINEER", "REVIEWER", "AUTHOR"):
        monkeypatch.setenv(f"ARGUS_SKILL_{var}_MODEL", "m")
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))

    intervals: list[float] = []
    real_timer = te.threading.Timer

    def rec(interval, fn, *a, **k):
        intervals.append(interval)
        return real_timer(interval, lambda: None)  # never actually fires

    monkeypatch.setattr(te.threading, "Timer", rec)

    class _Outcome:
        success = True

    class _Runner:
        def __init__(self, ns):
            pass

        def execute(self, *, objective, sink, prelude_context="", **kwargs):
            seen_kwargs.update(kwargs)
            return _Outcome()

    seen_kwargs: dict = {}
    monkeypatch.setattr(rt, "_SkillLoopRunner", _Runner)

    ok = te.run_one_engineer_mission("obj", cwd=str(tmp_path), life_dir=tmp_path / "life",
                                     max_rounds=1, timeout_s=10.0)
    assert ok.success is True
    assert intervals == [10.0]  # ONLY the soft watchdog; no hard self-kill timer
    # A teammate's cwd is the project root, so the Manager's stage-transition
    # pass would write the campaign's .argus/PIPELINE_STATE.json from a worker
    # holding one task. ``**kwargs`` above so this stub does not have to track
    # every future execute() keyword, but the one that matters is asserted.
    assert seen_kwargs.get("holds_stage_authority") is False


def test_teammate_forces_checkpoint_persist_off(tmp_path: Path, monkeypatch) -> None:
    # A teammate writes its events to its own life_dir, not <global_root>/projects/<fp>/.
    # The reviewer's engineer-log audit greps the latter, so it must be disabled for a
    # teammate (else it audits a co-located daemon's shared log → wrong verdicts). Forcing
    # it off also stops teammates sharing one CHECKPOINT.md.
    import argus_skill.apps._runtime as rt
    for var in ("ENGINEER", "REVIEWER"):
        monkeypatch.setenv(f"ARGUS_SKILL_{var}_MODEL", "m")
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("ARGUS_SKILL_CHECKPOINT_PERSIST", "1")  # operator/daemon default

    class _Outcome:
        success = True

    class _Runner:
        def __init__(self, ns):
            pass

        def execute(self, *, objective, sink, prelude_context="", **kwargs):
            return _Outcome()

    monkeypatch.setattr(rt, "_SkillLoopRunner", _Runner)
    te.run_one_engineer_mission("obj", cwd=str(tmp_path), life_dir=tmp_path / "life",
                                max_rounds=1, timeout_s=10.0)
    assert os.environ["ARGUS_SKILL_CHECKPOINT_PERSIST"] == "1"


def test_teammate_restores_checkpoint_env_when_setup_fails(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_CHECKPOINT_PERSIST", "1")
    monkeypatch.setattr(
        te,
        "_build_runner_ns",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert te.run_one_engineer_mission(
        "obj", cwd=str(tmp_path), life_dir=tmp_path / "life", timeout_s=0.01
    ).success is False
    assert os.environ["ARGUS_SKILL_CHECKPOINT_PERSIST"] == "1"


def _shard(root: Path, member: str = "t1::w1") -> dict:
    return json.loads((root / "shards" / (member.replace(":", "_") + ".jsonl")).read_text().strip())


def test_shard_carries_metric_mechanism_target_from_result_file(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"metric": 1.85, "mechanism": "fused softmax"}), encoding="utf-8")
    monkeypatch.setenv("ARGUS_TEAMMATE_RESULT_FILE", str(result))
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: True)
    rc = te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a",
                  "--cwd", str(tmp_path)])
    assert rc == 0
    rec = _shard(root)
    assert rec["metric"] == 1.85 and rec["mechanism"] == "fused softmax"
    assert rec["target"] == "t1::a" and rec["success"] is True


def test_shard_metric_null_without_result_file(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    monkeypatch.delenv("ARGUS_TEAMMATE_RESULT_FILE", raising=False)
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: True)
    te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a",
             "--cwd", str(tmp_path)])
    rec = _shard(root)
    assert rec["metric"] is None and rec["mechanism"] == "" and rec["target"] == "t1::a"


def test_shard_carries_lower_is_better_from_task(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tb.form(root, [{"task_id": "t1::a", "objective": "x", "target": "kLat", "lower_is_better": True}])
    assert tb.claim_top(root, "t1::w1", now=1.0)["task_id"] == "t1::a"
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: True)
    te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a", "--cwd", str(tmp_path)])
    rec = _shard(root)
    assert rec["lower_is_better"] is True and rec["target"] == "kLat"


def test_shard_omits_lower_is_better_when_task_unset(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)  # task with no lower_is_better
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: True)
    te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a", "--cwd", str(tmp_path)])
    rec = _shard(root)
    assert "lower_is_better" not in rec  # absent → leaderboard uses its global default


def test_paper_mission_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "m")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "m")
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    # a paper-fan-out team can turn the EMNLP gates ON per teammate
    monkeypatch.setenv("ARGUS_TEAMMATE_PAPER_MISSION", "1")
    assert te._build_runner_ns(str(tmp_path), max_rounds=1, paper_mission=False).paper_mission is True
    # ...and force them off explicitly
    monkeypatch.setenv("ARGUS_TEAMMATE_PAPER_MISSION", "0")
    assert te._build_runner_ns(str(tmp_path), max_rounds=1, paper_mission=True).paper_mission is False
    # unset → the passed default
    monkeypatch.delenv("ARGUS_TEAMMATE_PAPER_MISSION", raising=False)
    assert te._build_runner_ns(str(tmp_path), max_rounds=1, paper_mission=True).paper_mission is True


def test_teammate_inherits_leaderboard_block_in_objective(tmp_path: Path, monkeypatch) -> None:
    from argus_skill.team import leaderboard as lb
    root = tmp_path / ".argus_team" / "t1"
    tb.form(root, [{"task_id": "t1::a", "objective": "optimize kA", "target": "kA"}])
    assert tb.claim_top(root, "t1::w1", now=1.0)["task_id"] == "t1::a"
    d = root / "shards"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prev.jsonl").write_text(json.dumps(
        {"target": "kA", "metric": 1.9, "mechanism": "persistent", "success": True}) + "\n",
        encoding="utf-8")
    lb.fold(root)

    captured: dict = {}

    def _capture(objective, **k):
        captured["obj"] = objective
        return True

    monkeypatch.setattr(te, "run_one_engineer_mission", _capture)
    te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a",
             "--cwd", str(tmp_path)])
    # the fresh teammate sees what's already been tried, plus its own objective
    assert "persistent" in captured["obj"] and "optimize kA" in captured["obj"]


def test_operator_wait_blocks_without_failing_and_can_resume(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    waiting = te.TeammateMissionResult(
        False,
        "blocked",
        "operator choice required",
        "Choose A or B",
        ({"id": "a", "label": "A", "description": "Use A"},),
        "thread-1",
    )
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: waiting)

    rc = te.main(
        [
            "--root",
            str(root),
            "--member-id",
            "t1::w1",
            "--task-id",
            "t1::a",
            "--cwd",
            str(tmp_path),
        ]
    )

    task = {row["task_id"]: row for row in tb.snapshot(root)}["t1::a"]
    assert rc == 0
    assert task["state"] == "blocked"
    assert task["owner"] == "t1::w1"
    assert task["pending_question"] == "Choose A or B"
    assert task["last_thread_id"] == "thread-1"
    assert tb.count_in_flight(root) == 0

    tb.resume(root, "t1::a", answer="Use A")
    resumed = {row["task_id"]: row for row in tb.snapshot(root)}["t1::a"]
    assert resumed["state"] == "pending"
    assert resumed["owner"] == ""
    assert resumed["operator_answer"] == "Use A"
    assert tb.claim_top(root, "t1::w2", now=2.0)["task_id"] == "t1::a"


def test_reform_rejects_changed_blocked_spec_and_preserves_question(tmp_path: Path) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    tb.block_for_operator(
        root,
        "t1::a",
        question="Approve access",
        reason="authorization required",
        last_thread_id="thread-1",
    )

    with pytest.raises(ValueError, match="materially changed spec"):
        tb.form(
            root,
            [
                {
                    "task_id": "t1::a",
                    "objective": "updated objective",
                    "owns_paths": ["a/**"],
                }
            ],
        )

    task = tb.snapshot(root)[0]
    assert task["state"] == "blocked"
    assert task["owner"] == "t1::w1"
    assert task["pending_question"] == "Approve access"
    assert task["last_thread_id"] == "thread-1"
    assert task["objective"] == "do a"


def test_reform_preserves_answer_while_resumed_task_is_pending(tmp_path: Path) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    tb.block_for_operator(root, "t1::a", question="Choose", reason="waiting")
    tb.resume(root, "t1::a", answer="Use A")

    tb.form(
        root,
        [{"task_id": "t1::a", "objective": "refreshed", "owns_paths": ["a/**"]}],
    )

    task = tb.snapshot(root)[0]
    assert task["state"] == "pending"
    assert task["operator_answer"] == "Use A"


def test_resumed_operator_answer_is_passed_to_new_teammate(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    tb.block_for_operator(root, "t1::a", question="Choose", reason="waiting")
    tb.resume(root, "t1::a", answer="Proceed with A")
    assert tb.claim_top(root, "t1::w2", now=2.0)
    captured: dict[str, str] = {}

    def run(objective: str, **_kwargs):
        captured["objective"] = objective
        return te.TeammateMissionResult(True, "done")

    monkeypatch.setattr(te, "run_one_engineer_mission", run)
    assert te.main(
        [
            "--root",
            str(root),
            "--member-id",
            "t1::w2",
            "--task-id",
            "t1::a",
            "--cwd",
            str(tmp_path),
        ]
    ) == 0
    assert "Proceed with A" in captured["objective"]


def test_team_cli_status_and_resume_preserve_wait_state(
    tmp_path: Path, capsys
) -> None:
    from argus_skill.tools import team as team_tool

    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    tb.block_for_operator(root, "t1::a", question="Choose route", reason="waiting")

    assert team_tool.main(["status", "--root", str(root)]) == 0
    status = json.loads(capsys.readouterr().out)
    task = status["tasks"][0]
    assert task["state"] == "blocked"
    assert task["owner"] == "t1::w1"
    assert task["pending_question"] == "Choose route"

    assert team_tool.main(
        [
            "resume",
            "--root",
            str(root),
            "--task-id",
            "t1::a",
            "--answer",
            "Use route A",
        ]
    ) == 0
    resumed = tb.snapshot(root)[0]
    assert resumed["state"] == "pending"
    assert resumed["operator_answer"] == "Use route A"


def test_fatal_mission_still_marks_failed(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    monkeypatch.setattr(
        te,
        "run_one_engineer_mission",
        lambda *a, **k: te.TeammateMissionResult(
            False, "error", "backend exited nonzero"
        ),
    )

    assert te.main(
        [
            "--root",
            str(root),
            "--member-id",
            "t1::w1",
            "--task-id",
            "t1::a",
            "--cwd",
            str(tmp_path),
        ]
    ) == 1
    task = {row["task_id"]: row for row in tb.snapshot(root)}["t1::a"]
    assert task["state"] == "failed"
    assert task["reason"] == "backend exited nonzero"


def _setup_verify(tmp_path: Path, monkeypatch, signed: dict):
    """Form/claim a task with target kA, write `signed` as result.json, set the
    verify key, and stub the mission. Returns the team root."""
    from argus_skill.team import result_provenance as rp
    root = tmp_path / ".argus_team" / "t1"
    tb.form(root, [{"task_id": "t1::a", "objective": "x", "target": "kA"}])
    assert tb.claim_top(root, "t1::w1", now=1.0)["task_id"] == "t1::a"
    priv, pub = rp.generate_keypair()
    (tmp_path / "pub.pem").write_bytes(pub)
    if signed.get("_sign"):  # sign with the matching private key
        signed = {k: v for k, v in signed.items() if k != "_sign"}
        signed["sig"] = rp.sign_result(signed, priv)
    result = tmp_path / "result.json"
    result.write_text(json.dumps(signed), encoding="utf-8")
    monkeypatch.setenv("ARGUS_TEAMMATE_RESULT_FILE", str(result))
    monkeypatch.setenv("ARGUS_TEAMMATE_RESULT_VERIFY_KEY", str(tmp_path / "pub.pem"))
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: True)
    return root


def test_verify_key_rejects_forged_result(tmp_path: Path, monkeypatch) -> None:
    # With a verify key set, a hand-forged result.json (no valid sig) is NOT banked.
    root = _setup_verify(tmp_path, monkeypatch,
                         {"target": "kA", "metric": 0.0001, "mechanism": "x", "correct": True})
    te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a", "--cwd", str(tmp_path)])
    assert _shard(root)["metric"] is None  # forged metric rejected


def test_verify_key_accepts_signed_result(tmp_path: Path, monkeypatch) -> None:
    # A result validly signed (target matches the task) IS banked.
    root = _setup_verify(tmp_path, monkeypatch,
                         {"target": "kA", "metric": 1.85, "mechanism": "official-eval",
                          "correct": True, "_sign": True})
    te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a", "--cwd", str(tmp_path)])
    assert _shard(root)["metric"] == 1.85


def test_verify_key_rejects_target_replay(tmp_path: Path, monkeypatch) -> None:
    # A result validly signed for a DIFFERENT kernel (kB) cannot bank under target kA.
    root = _setup_verify(tmp_path, monkeypatch,
                         {"target": "kB", "metric": 0.01, "mechanism": "official-eval",
                          "correct": True, "_sign": True})
    te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a", "--cwd", str(tmp_path)])
    assert _shard(root)["metric"] is None  # signed but wrong target → rejected


def test_no_verify_key_is_backward_compatible(tmp_path: Path, monkeypatch) -> None:
    # Without a verify key set, an unsigned result.json is banked as before.
    root = tmp_path / ".argus_team" / "t1"
    _form_claim(root)
    monkeypatch.delenv("ARGUS_TEAMMATE_RESULT_VERIFY_KEY", raising=False)
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"metric": 2.5, "mechanism": "m"}), encoding="utf-8")
    monkeypatch.setenv("ARGUS_TEAMMATE_RESULT_FILE", str(result))
    monkeypatch.setattr(te, "run_one_engineer_mission", lambda *a, **k: True)
    te.main(["--root", str(root), "--member-id", "t1::w1", "--task-id", "t1::a", "--cwd", str(tmp_path)])
    assert _shard(root)["metric"] == 2.5


# ── the vertical's per-mission prelude reaches a dispatched teammate ──────────
# A teammate is an Engineer mission like any other and was the only one starting
# without whatever its vertical wanted every mission to know. It now goes through
# ``verticals._base.vertical_mission_prelude`` — the same seam the daemon's
# supervisor uses — so the two cannot resolve one project two ways. Most projects
# supply nothing, so "contributes exactly nothing" is the property under most of
# the load here, not the happy path.

def _math_project(tmp_path: Path, *claim_ids: str) -> Path:
    """A project root the math vertical will actually project from."""
    from argus_skill.proof_ledger import (
        ClaimVersion,
        ContextVersion,
        MathState,
        save_state,
    )

    cwd = tmp_path / "proj"
    (cwd / ".argus").mkdir(parents=True)
    (cwd / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "math", "current_stage": "solve"}), encoding="utf-8")
    state = MathState()
    context = state.add_context(ContextVersion(
        context_id="c1", version=1,
        statement="Fix a finite abelian group G.", definitions={}))
    for claim_id in claim_ids or ("udist-main",):
        state.add_claim(ClaimVersion(
            claim_id=claim_id, version=1, context=context.ref(),
            natural_statement="Every G carries a uniform distribution."))
    save_state(cwd, state)
    return cwd


def _dispatch(tmp_path: Path, monkeypatch, cwd: Path, **spec) -> dict:
    """Run one teammate over ``spec`` and capture what the runner was handed."""
    root = tmp_path / ".argus_team" / "t1"
    tb.form(root, [{"task_id": "t1::a", "owns_paths": ["a/**"], **spec}])
    assert tb.claim_top(root, "t1::w1", now=1.0)["task_id"] == "t1::a"
    seen: dict = {}

    def _capture(objective, **kwargs):
        seen["objective"] = objective
        seen["prelude"] = kwargs.get("prelude_context", "")
        return True

    monkeypatch.setattr(te, "run_one_engineer_mission", _capture)
    assert te.main(["--root", str(root), "--member-id", "t1::w1",
                    "--task-id", "t1::a", "--cwd", str(cwd)]) == 0
    return seen


def test_teammate_receives_the_verticals_mission_prelude(tmp_path, monkeypatch) -> None:
    # The point of the change: a teammate whose task names a claim starts knowing
    # what the project already records about it instead of re-deriving it.
    cwd = _math_project(tmp_path)
    seen = _dispatch(tmp_path, monkeypatch, cwd,
                     objective="Close udist-main by the Fourier route.")

    assert "udist-main" in seen["prelude"]
    assert "MATH_STATE.json" in seen["prelude"]
    # It travels as prelude_context, NOT folded into the objective. The runner
    # reuses `objective` as the Reviewer's task, so prepending there would hand
    # the Reviewer the briefing to review instead of the work.
    assert seen["objective"] == "Close udist-main by the Fourier route."


def test_teammate_prelude_is_empty_for_a_project_with_no_vertical(
    tmp_path, monkeypatch
) -> None:
    # The overwhelmingly common case, and the one the change must not disturb:
    # no PIPELINE_STATE.json, so nothing is resolved and nothing is contributed.
    seen = _dispatch(tmp_path, monkeypatch, tmp_path, objective="optimize kA",
                     target="kA")

    assert seen["prelude"] == ""
    assert seen["objective"] == "optimize kA"


def test_teammate_prelude_never_raises_on_unreadable_project_state(
    tmp_path, monkeypatch
) -> None:
    # Corrupt Manager state makes vertical resolution raise. The supervisor lets
    # that end the run on purpose; a single subordinate teammate must not die for
    # a briefing it could do without — its parent already reports the same fault.
    cwd = tmp_path / "proj"
    (cwd / ".argus").mkdir(parents=True)
    (cwd / ".argus" / "PIPELINE_STATE.json").write_text("{not json", encoding="utf-8")

    seen = _dispatch(tmp_path, monkeypatch, cwd, objective="do a")

    assert seen["prelude"] == ""


def test_teammate_prelude_is_empty_when_the_task_names_no_claim(
    tmp_path, monkeypatch
) -> None:
    # Pinned deliberately. A route ("try the Fourier-analytic approach") names no
    # claim, and the projection is claim-scoped by design: guessing which claim a
    # route meant would produce a fragment about the wrong theorem that reads as
    # if it were right. Empty is the correct answer here — the fix belongs in what
    # the Engineer writes into the task, not in loosening the projection. There is
    # now somewhere to write it (``acceptance_check``, below); this task still
    # does not, so it still gets nothing, which is the point.
    cwd = _math_project(tmp_path)
    seen = _dispatch(tmp_path, monkeypatch, cwd,
                     title="Fourier-analytic route",
                     objective="Try the Fourier-analytic approach and report.")

    assert seen["prelude"] == ""


def test_teammate_and_supervisor_share_one_prelude_seam() -> None:
    # Two ways of computing this text is how a teammate and the Engineer that
    # dispatched it end up reading different projects. Both call sites route
    # through the one helper; nothing calls the hook directly.
    import inspect

    from argus_skill.life.supervisor import _mission_execution_runtime

    assert "vertical_mission_prelude" in inspect.getsource(_mission_execution_runtime)
    assert "vertical_mission_prelude" in inspect.getsource(te)
    assert "prepare_mission(" not in inspect.getsource(te)


# ── a route task carries its own done condition, and that is what aims it ─────
# The case the whole path was built for: an Engineer dispatching one team task
# per proof route. A route objective names two claims the way a mathematician
# writes one down, so the vaguer fields are exactly where the projection refuses
# to guess. The board now carries ``acceptance_check``, which is the field the
# projection consults FIRST — and the only one a lead can make say one thing.


def test_a_route_task_reaches_its_goal_claim_through_the_acceptance_check(
    tmp_path, monkeypatch
) -> None:
    cwd = _math_project(tmp_path, "udist-main", "udist-lemma")
    seen = _dispatch(
        tmp_path, monkeypatch, cwd,
        title="Fourier-analytic route",
        objective="Reduce udist-main to udist-lemma by Fourier inversion.",
        acceptance_check="udist-lemma is recorded as a conditional kernel.",
    )

    # Briefed on the claim this route has to move, by name and by version.
    assert "claim `udist-lemma` v1" in seen["prelude"]
    assert "targeted by this mission's acceptance_check" in seen["prelude"]
    assert "MATH_STATE.json" in seen["prelude"]
    # ...and on no other claim. The other id in the objective is the route's
    # source, not its goal, and a fragment about it would be wrong in a way the
    # teammate could not detect.
    assert "udist-main" not in seen["prelude"]
    # Still prelude, never the objective: the runner reuses the objective as the
    # Reviewer's task.
    assert seen["objective"] == "Reduce udist-main to udist-lemma by Fourier inversion."


def test_an_ambiguous_route_objective_alone_still_refuses_to_guess(
    tmp_path, monkeypatch
) -> None:
    # The same task without its done condition, which is what (b) alone leaves
    # you with: title and objective are prose about a reduction, they name two
    # recorded claims, and the projection declines to pick one rather than aiming
    # a plausible fragment at the wrong theorem. Carrying the field did not loosen
    # that, and this pins that it did not.
    cwd = _math_project(tmp_path, "udist-main", "udist-lemma")
    seen = _dispatch(
        tmp_path, monkeypatch, cwd,
        title="Fourier-analytic route",
        objective="Reduce udist-main to udist-lemma by Fourier inversion.",
    )

    assert "Mathematical state not projected" in seen["prelude"]
    assert "claim `udist-lemma` v1" not in seen["prelude"]


def test_the_acceptance_check_changes_nothing_for_a_non_math_team(
    tmp_path, monkeypatch
) -> None:
    # The board learned a field, not a domain. A team whose project has no
    # vertical, and a kernel-engineering team whose vertical reads no mission
    # field at all, must be byte-identical with the field set and unset — the one
    # test that would fail if any of this had leaked into the team layer.
    kernel = tmp_path / "kproj"
    (kernel / ".argus").mkdir(parents=True)
    (kernel / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "kernel_engineering", "current_stage": "optimize"}),
        encoding="utf-8")

    for i, cwd in enumerate((tmp_path / "no_vertical", kernel)):
        cwd.mkdir(exist_ok=True)
        bare = _dispatch(tmp_path / f"bare{i}", monkeypatch, cwd,
                         objective="optimize kA", target="kA")
        with_check = _dispatch(tmp_path / f"check{i}", monkeypatch, cwd,
                               objective="optimize kA", target="kA",
                               acceptance_check="kA is 1.5x faster and still correct.")

        assert with_check["prelude"] == bare["prelude"] == ""
        assert with_check["objective"] == bare["objective"] == "optimize kA"


def test_a_per_task_cwd_below_the_project_tree_gets_no_vertical_at_all(
    tmp_path, monkeypatch
) -> None:
    # Documented because it is a live trap, not a property worth having. Both
    # roots the teammate passes to the vertical come from the task's ``cwd``, and
    # neither ``resolve_vertical`` nor the math store walks upward. So a task
    # pointed at a private subdirectory of the project reads a tree with no
    # PIPELINE_STATE.json, resolves the default vertical on a log line nobody is
    # watching, and is briefed on nothing — however well its acceptance check
    # names its claim. A task that shares the campaign's project state must keep
    # the campaign cwd and take its private directory through ``owns_paths``.
    cwd = _math_project(tmp_path, "udist-main")
    (cwd / "routes" / "R1").mkdir(parents=True)

    seen = _dispatch(tmp_path, monkeypatch, cwd / "routes" / "R1",
                     objective="Close udist-main by the Fourier route.",
                     acceptance_check="udist-main is a conditional kernel.")

    assert seen["prelude"] == ""
