"""Tests for the slim Argus spine — the soul (frozen judge) + tree + done logic."""
from __future__ import annotations
from argus_skill.argus import (
    Task, Node, Evidence, FrozenJudge, JudgeConfig, HypothesisTree, Session, Run, RunConfig,
)


def test_judge_is_the_only_win_and_noise_gate():
    j = FrozenJudge(JudgeConfig(floor=0.9879, noise=0.0015))
    assert j.score(0.9855).passed is True             # clears floor by 0.0024 > noise
    assert j.score(0.9874).passed is False            # 0.0005 within noise = coin flip, not a win
    assert j.score(0.9990).passed is False            # misses


def test_anti_cheat_disqualifies():
    j = FrozenJudge(JudgeConfig(floor=0.99, forbidden=("optimized_from_karpathy",)))
    ev = j.score(0.50, refs=["read optimized_from_karpathy/train.py"])
    assert ev.passed is False and "DISQUALIFIED" in ev.note


def test_tree_lesson_propagation_and_done():
    t = HypothesisTree()
    a = t.add(Node(id="a", hypothesis="loss tiling", family="loss"))
    t.attach_evidence("a", Evidence(metric=0.999, passed=False), lesson="forward-bound")
    assert "forward-bound" in t.lessons_for("loss")   # propagates to siblings
    assert t.frontier_exhausted() is True             # all measured -> done


def test_run_reaches_pass_a_via_frozen_judge():
    seq = iter([0.9899, 0.98740, 0.9855])             # 0.9874 within noise (not a win); 0.9855 clears
    j = FrozenJudge(JudgeConfig(floor=0.98788, noise=0.0015))
    run = Run(judge=j, candidate_fn=lambda n: (next(seq, 1.0), []),
              cfg=RunConfig(budget_loops=3))
    r = run.run(Task("optimize bpb"))
    assert r["best_metric"] == 0.9855                 # the win came from the judge


def test_session_checkpoint_roundtrip(tmp_path):
    s = Session(root=tmp_path)
    s.write_checkpoint({"headline": "PASS-A 0.9874", "frontier": 0})
    assert s.load_checkpoint()["headline"] == "PASS-A 0.9874"
    nxt = s.rollover({"headline": "carry"})
    assert "resumed" in nxt.trace[0]
