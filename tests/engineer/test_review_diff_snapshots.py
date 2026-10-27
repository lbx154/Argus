"""HO-7b: the Reviewer is shown the task's diff, from its baseline to the candidate under review."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from argus.core.models import ReviewDecision, RunnerResult
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import ReviewerConfig
from tests.hand_off import hand_off, submit


class Engineer:
    def __init__(self, workdir: Path) -> None:
        self.workdir, self.calls = workdir, 0

    def run_exec(self, **kwargs: Any) -> RunnerResult:
        self.calls += 1
        (self.workdir / "train.py").write_text(f"epochs = {self.calls}\n", encoding="utf-8")
        submit(kwargs["options"], hand_off("reviewer", f"Set epochs to {self.calls}."))
        return RunnerResult(exit_code=0, agent_messages=[f"Set epochs to {self.calls}."])


class Reviewer:
    def __init__(self) -> None:
        self.saw: list[Any] = []

    def evaluate(self, *, config: ReviewerConfig, **_kwargs: Any) -> ReviewDecision:
        self.saw.append(config.snapshots.hunks("baseline", "candidate", ["train.py"]))
        if len(self.saw) % 2:
            return ReviewDecision(status="engineer", reason="Train longer.", next_action="Raise epochs.")
        return ReviewDecision(status="done", reason="Epochs raised.", next_action="")


def _run(workdir: Path, packet: Path, reviewer: Reviewer) -> str:
    loop = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    loop.engineer_runner, loop.reviewer = Engineer(workdir), reviewer
    loop.engineer_config, loop.reviewer_config = EngineerConfig(model="stub"), ReviewerConfig(model="stub")
    return loop.run(
        objective="Raise the epochs", workdir=workdir,
        engineer_prompt_builder=lambda action, include_static=True: action or "Start",
        supervised_config=SupervisedConfig(
            max_rounds=4, require_independent_review=True, context_packet_path=packet,
        ),
    )[0]


def test_every_review_sees_the_whole_task_diff_and_resuming_keeps_the_baseline(tmp_path):
    packet = tmp_path / "state" / "handoffs" / "m1" / "mission.json"
    packet.parent.mkdir(parents=True)
    packet.write_text(json.dumps({"mission_id": "m1", "node_key": "node-7"}), encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "train.py").write_text("epochs = 0\n", encoding="utf-8")
    reviewer = Reviewer()

    assert _run(workdir, packet, reviewer) == "done"
    # Someone touched the work since it was accepted, so the task runs again
    # (unchanged, its acceptance would simply stand).
    (workdir / "notes.txt").write_text("resumed\n", encoding="utf-8")
    assert _run(workdir, packet, reviewer) == "done"

    # Each review covers everything since the task began, not just its round;
    # the resumed task still diffs against the workspace it started from.
    assert reviewer.saw == [
        [["@@ -1 +1 @@", "-epochs = 0", f"+epochs = {calls}"]] for calls in (1, 2, 1, 2)
    ]
    assert (tmp_path / "state" / "snapshots.git" / "refs" / "argus" / "node-7" / "baseline").is_file()
    assert not (workdir / ".git").exists()
