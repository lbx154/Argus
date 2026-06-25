"""Runnable demo: simulate the nanochat invention loop on the slim spine.

Shows the FrozenJudge giving the win (not the Reviewer), the noise gate killing a
sub-noise "win", and one single-mechanism hypothesis clearing the fair floor (PASS-A).
Run:  python -m argus_skill.argus.demo
"""
from __future__ import annotations
from .core import Task, Node
from .judge import FrozenJudge, JudgeConfig
from .orchestrator import Run, RunConfig

# real-ish per-hypothesis measured metrics from the actual nanochat run (lower=better).
# 0.98738 is the single-mechanism q/k seam — it sits 0.0005 below the floor, i.e. WITHIN
# the noise gate, so the frozen judge honestly refuses it as a clean win (matches the blog
# caveat). Only the stacked 0.98550 clears the floor by more than noise → PASS-A.
SEQ = [0.98982, 0.98961, 0.98934, 0.98738, 0.98550]
FLOOR = 0.98788


def main() -> None:
    judge = FrozenJudge(JudgeConfig(
        floor=FLOOR, noise=0.0015, n_seed=10,
        forbidden=("optimized_from_karpathy", "modded-nanogpt-leaderboard"),
    ))
    it = iter(SEQ)

    def candidate_fn(node: Node):
        try:
            return next(it), [node.artifact]      # honest refs; no forbidden answer read
        except StopIteration:
            return 1.0, []

    run = Run(judge=judge, candidate_fn=candidate_fn, cfg=RunConfig(budget_loops=len(SEQ)))
    result = run.run(Task("optimize nanochat val_bpb in 5 minutes"))

    print("\n".join(run.log))
    print("-" * 60)
    print("result:", result)
    assert result["best_metric"] == 0.98550, "only the stacked line clears the noise gate"
    print("noise gate: single-mechanism 0.98738 (within CI) honestly NOT a clean win;")
    print("PASS-A reached at 0.98550 by the FROZEN judge, not the Reviewer ✅")


if __name__ == "__main__":
    main()
