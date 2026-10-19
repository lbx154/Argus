"""HO-2: a Reviewer is not called again on facts it already judged.

While a training job runs, it rewrites its own checkpoints and log, and the
Engineer has nothing new to show. The Reviewer last judged exactly these
facts, so calling it again could only repeat that verdict: the round waits on
the job instead, and the Reviewer reads the result once it arrives.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, cast

from argus.core.models import ReviewDecision, RunnerResult
from argus.engineer.change_facts import held_facts, mission_ledger
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import ReviewerConfig


class Trainer:
    """A training job as the host sees it: its registry record, checkpoints and log."""

    def __init__(self, root: Path) -> None:
        self.root, self.step, self.finished = root, 0, False
        self.run = root / "runs" / "train-1"

    def _record(self, **fields: Any) -> None:
        registry = self.root / ".argus_external_work"
        registry.mkdir(exist_ok=True)
        (registry / "train-1.json").write_text(json.dumps({
            "version": 1, "work_id": "train-1", "run_id": "run-1", "state": "running_healthy",
            "heartbeat_at": time.time(), "activity_paths": ["runs/train-1"],
            "evidence_paths": ["results/metrics.json"], **fields,
        }), encoding="utf-8")

    def launch(self) -> None:
        self.run.mkdir(parents=True)
        self.train(1)

    def train(self, steps: int) -> None:
        for _ in range(steps):
            self.step += 1
            (self.run / f"ckpt-{self.step:04d}.pt").write_bytes(os.urandom(256))
            with (self.run / "train.log").open("a", encoding="utf-8") as log:
                log.write(f"step {self.step} loss {1 / self.step:.4f}\n")
            self._record()

    def finish(self) -> None:
        (self.root / "results").mkdir()
        (self.root / "results" / "metrics.json").write_text(
            json.dumps({"steps": self.step, "loss": 1 / self.step}), encoding="utf-8",
        )
        self._record(state="terminal", outcome="succeeded")
        self.finished = True


class Engineer:
    def __init__(self, trainer: Trainer, workdir: Path) -> None:
        self.trainer, self.workdir, self.calls = trainer, workdir, 0

    def run_exec(self, **_kwargs: Any) -> RunnerResult:
        self.calls += 1
        if self.calls == 1:
            self.trainer.launch()
            message = "Launched training job train-1; it writes checkpoints under runs/train-1."
        elif not self.trainer.finished:
            message = "Training is still running; nothing to change yet."
        else:
            metrics = json.loads((self.workdir / "results" / "metrics.json").read_text())
            (self.workdir / "REPORT.md").write_text(f"Final loss {metrics['loss']:.4f}\n")
            message = "Read results/metrics.json and wrote REPORT.md."
        return RunnerResult(exit_code=0, agent_messages=[message])


class Reviewer:
    def __init__(self, trainer: Trainer) -> None:
        self.trainer, self.saw_result = trainer, []

    def evaluate(self, **_kwargs: Any) -> ReviewDecision:
        self.saw_result.append(self.trainer.finished)
        if self.trainer.finished:
            return ReviewDecision(status="done", reason="REPORT.md matches results/metrics.json.", next_action="")
        return ReviewDecision(status="continue", reason="The launch is sound.",
                              next_action="Report the result when training finishes.")


def test_reviewer_waits_out_a_running_job_and_reads_its_result_once(tmp_path, monkeypatch):
    from argus.engineer import runner

    packet = tmp_path / "state" / "handoffs" / "m1" / "mission.json"
    packet.parent.mkdir(parents=True)
    packet.write_text(json.dumps({"mission_id": "m1"}), encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir()
    trainer = Trainer(workdir)
    reviewer = Reviewer(trainer)
    engineer = Engineer(trainer, workdir)
    finish_on_wait = [False]

    def wait(**_kwargs: Any) -> tuple[str, float]:
        # Time passes while the round waits: the job keeps training.
        trainer.train(2)
        if finish_on_wait[0]:
            trainer.finish()
            return "terminal", 60.0
        return "cadence_elapsed", 60.0

    monkeypatch.setattr(runner, "_run_external_work_wait", wait)

    def run() -> tuple[Any, list[dict]]:
        loop = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
        loop.engineer_runner, loop.reviewer = engineer, reviewer
        loop.engineer_config, loop.reviewer_config = EngineerConfig(model="stub"), ReviewerConfig(model="stub")
        events: list[dict] = []
        result = loop.run(
            objective="Train a policy and report its final loss", workdir=workdir,
            engineer_prompt_builder=lambda action, include_static=True: action or "Start",
            supervised_config=SupervisedConfig(
                max_rounds=4, require_independent_review=True, context_packet_path=packet,
            ),
            on_event=events.append,
        )
        return result, events

    first, events = run()
    # The launch is reviewed; the next turn has nothing new, so the round
    # waits on the job and releases its slot while it still trains.
    assert first[0] == "paused_external_work"
    assert reviewer.saw_result == [False]
    skipped = [event for event in events if event["type"] == "round.review.skipped_unchanged"]
    assert [event["work_id"] for event in skipped] == ["train-1"]
    assert held_facts(mission_ledger(packet), "reviewer") is not None

    # Resumed later: the job wrote more checkpoints and log lines meanwhile,
    # which are its own activity, not news for the Reviewer.
    trainer.train(3)
    finish_on_wait[0] = True
    second, events = run()
    assert second[0] == "done"
    assert reviewer.saw_result == [False, True], "no review while it ran, one after its result"
    assert sum(event["type"] == "round.review.skipped_unchanged" for event in events) == 1
    assert len(list(trainer.run.glob("ckpt-*.pt"))) == trainer.step == 8
    assert (workdir / "REPORT.md").read_text() == "Final loss 0.1250\n"
