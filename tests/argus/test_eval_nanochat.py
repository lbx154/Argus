"""The candidate_fn → real-eval seam (argus/eval_nanochat.py), B200-free.

Uses a mock ``eval_solution.sh`` so the parse + fail-closed + Judge wiring are
tested without touching the live B200.
"""
from __future__ import annotations

from argus_skill.argus.eval_nanochat import (
    candidate_refs,
    make_candidate_fn,
    run_nanochat_eval,
)
from argus_skill.argus.judge import FrozenJudge, JudgeConfig


def _mission(tmp_path, *, bpb_line: str) -> "tuple":
    """A tmp mission dir with a mock eval_solution.sh echoing one BPB line."""
    mission = tmp_path / "mission"
    mission.mkdir()
    (mission / "eval_solution.sh").write_text(
        "#!/usr/bin/env bash\n"
        'echo "scoring $1 with $2 seed(s)"\n'
        f'echo "{bpb_line}"\n',
        encoding="utf-8",
    )
    train = mission / "train.py"
    train.write_text("# candidate\nimport lib\nopen('data/shard.bin')\n", encoding="utf-8")
    return mission, train


def test_parses_measured_bpb(tmp_path):
    mission, train = _mission(tmp_path, bpb_line="MEAN_VAL_BPB=0.961500")
    res = run_nanochat_eval(train, mission, n_seed=1)
    assert res.ok and abs(res.metric - 0.961500) < 1e-9


def test_fail_closed_on_all_seeds_failed(tmp_path):
    # eval_solution.sh prints this when every seed crashed — must NOT parse as a score
    mission, train = _mission(tmp_path, bpb_line="MEAN_VAL_BPB=FAILED   (all seeds failed)")
    res = run_nanochat_eval(train, mission, n_seed=1)
    assert not res.ok and res.metric is None


def test_fail_closed_when_no_eval_script(tmp_path):
    (tmp_path / "train.py").write_text("x=1\n", encoding="utf-8")
    res = run_nanochat_eval(tmp_path / "train.py", tmp_path, n_seed=1)
    assert not res.ok and res.metric is None


def test_candidate_refs_surfaces_read_paths_for_anticheat(tmp_path):
    train = tmp_path / "train.py"
    train.write_text(
        "open('data/val.bin')\nimport json; json.load(open('optimized_from_karpathy.json'))\n",
        encoding="utf-8",
    )
    refs = candidate_refs(train)
    assert "data/val.bin" in refs
    assert any("karpathy" in r for r in refs)  # a forbidden read would be visible to the Judge


def test_make_candidate_fn_failed_eval_becomes_inf(tmp_path):
    # the spine adapter must hand the Judge +inf (a clean non-win), never fabricate
    cfn = make_candidate_fn(tmp_path)  # no eval_solution.sh → eval fails

    class _Node:
        artifact = str(tmp_path / "nope.py")

    metric, refs = cfn(_Node())
    assert metric == float("inf") and refs == []


def test_end_to_end_measured_metric_through_frozen_judge(tmp_path):
    # a clear win measured by the (mock) scorer, scored by the real FrozenJudge
    mission, train = _mission(tmp_path, bpb_line="MEAN_VAL_BPB=0.961000")
    cfn = make_candidate_fn(mission)

    class _Node:
        artifact = str(train)

    metric, refs = cfn(_Node())
    judge = FrozenJudge(JudgeConfig(floor=0.963634, noise=0.0015))
    ev = judge.score(metric, refs)
    assert ev.passed and judge.floor == 0.961000  # measured win moved the frozen floor
