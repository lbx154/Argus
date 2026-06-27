from __future__ import annotations

import json
import os
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

        def execute(self, *, objective, sink):
            return _Outcome()

    monkeypatch.setattr(rt, "_SkillLoopRunner", _Runner)

    ok = te.run_one_engineer_mission("obj", cwd=str(tmp_path), life_dir=tmp_path / "life",
                                     max_rounds=1, timeout_s=10.0)
    assert ok is True
    assert intervals == [10.0]  # ONLY the soft watchdog; no hard self-kill timer


def test_teammate_forces_checkpoint_persist_off(tmp_path: Path, monkeypatch) -> None:
    # A teammate writes its events to its own life_dir, not <global_root>/projects/<fp>/.
    # The reviewer's engineer-log audit greps the latter, so it must be disabled for a
    # teammate (else it audits a co-located daemon's shared log → wrong verdicts). Forcing
    # it off also stops teammates sharing one checkpoint.json.
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

        def execute(self, *, objective, sink):
            return _Outcome()

    monkeypatch.setattr(rt, "_SkillLoopRunner", _Runner)
    te.run_one_engineer_mission("obj", cwd=str(tmp_path), life_dir=tmp_path / "life",
                                max_rounds=1, timeout_s=10.0)
    assert os.environ["ARGUS_SKILL_CHECKPOINT_PERSIST"] == "0"


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
    tb.claim_specific(root, "t1::a", "t1::w1", now=1.0)
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


def test_forced_profile_keeps_non_kernel_report(tmp_path: Path, monkeypatch) -> None:
    # A general profile (py-spy / cProfile / EXPLAIN ANALYZE) that never says "kernel"
    # must NOT be silently discarded.
    monkeypatch.setenv("ARGUS_TEAMMATE_FORCE_PROFILE", "1")
    monkeypatch.delenv("ARGUS_TEAMMATE_PROFILE_REQUIRE_SUBSTR", raising=False)
    report = ("Top function: parse_request 62% self time; allocate_buffer 18%; "
              "the hot path is JSON decoding in the request handler.")
    rf = tmp_path / "prof.txt"
    rf.write_text(report, encoding="utf-8")
    monkeypatch.setenv("ARGUS_TEAMMATE_PROFILE_CMD", f"cat {rf}")
    out = te._forced_profile("optimize the request parser", cwd=str(tmp_path))
    assert "parse_request" in out and "optimize the request parser" in out
    assert out != "optimize the request parser"  # i.e. the profile was kept, not dropped


class _FakeProc:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_forced_research_default_prompt_is_domain_neutral(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_TEAMMATE_FORCE_RESEARCH", "1")
    monkeypatch.delenv("ARGUS_TEAMMATE_RESEARCH_PROMPT", raising=False)
    captured: dict = {}

    def fake_run(argv, **k):
        captured["prompt"] = argv[-1]
        return _FakeProc("web search done; the SOTA approach is X using technique Y; source: paperZ.")

    monkeypatch.setattr(te.subprocess, "run", fake_run)
    te._forced_web_research("survey diffusion-policy robotics papers", cwd=str(tmp_path))
    p = captured["prompt"]
    for lib in ("vLLM", "CUTLASS", "Triton", "cuDNN", "FlashInfer"):
        assert lib not in p  # no hardcoded GPU-kernel domain in the default
    assert "survey diffusion-policy robotics papers" in p  # objective interpolated


def test_forced_research_prompt_is_operator_configurable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_TEAMMATE_FORCE_RESEARCH", "1")
    monkeypatch.setenv("ARGUS_TEAMMATE_RESEARCH_PROMPT", "DOMAIN: check libA, libB. TASK:\n{objective}")
    captured: dict = {}

    def fake_run(argv, **k):
        captured["prompt"] = argv[-1]
        return _FakeProc("web search: libA is best.")

    monkeypatch.setattr(te.subprocess, "run", fake_run)
    te._forced_web_research("optimize the GEMM epilogue", cwd=str(tmp_path))
    assert "libA" in captured["prompt"] and "optimize the GEMM epilogue" in captured["prompt"]


def _profile_out(tmp_path, monkeypatch, *, header_env=None) -> str:
    monkeypatch.setenv("ARGUS_TEAMMATE_FORCE_PROFILE", "1")
    if header_env is None:
        monkeypatch.delenv("ARGUS_TEAMMATE_PROFILE_HEADER", raising=False)
    else:
        monkeypatch.setenv("ARGUS_TEAMMATE_PROFILE_HEADER", header_env)
    report = "Top function parse_request 62% self; the hot path is JSON decoding in the request handler."
    rf = tmp_path / "p.txt"
    rf.write_text(report, encoding="utf-8")
    monkeypatch.setenv("ARGUS_TEAMMATE_PROFILE_CMD", f"cat {rf}")
    return te._forced_profile("optimize the parser", cwd=str(tmp_path))


def test_forced_profile_default_header_is_domain_neutral(tmp_path: Path, monkeypatch) -> None:
    out = _profile_out(tmp_path, monkeypatch)
    assert "NCU" not in out and "B200" not in out  # no GPU framing in the default
    assert "LIVE PROFILE" in out and "bottleneck" in out.lower()


def test_forced_profile_header_is_operator_configurable(tmp_path: Path, monkeypatch) -> None:
    out = _profile_out(tmp_path, monkeypatch, header_env="[MY NCU PROFILE — attack the kernel]")
    assert "[MY NCU PROFILE — attack the kernel]" in out


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
    tb.claim_specific(root, "t1::a", "t1::w1", now=1.0)
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
