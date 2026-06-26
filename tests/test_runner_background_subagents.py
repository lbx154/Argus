"""Runner-level integration for the background-subagent advisory + cadence wait.

Drives :class:`SupervisedEngineer` with the deterministic ``MemoryBackend`` to
verify the engineer prompt gets the advisory, the ``WAIT_FOR_SUBAGENT`` sentinel
yields without burning a reviewer round, and that the feature is a no-op (no
behaviour change) when there are no in-flight subagents.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)


def _write_record(reg: Path, task_id: str, **fields) -> None:
    reg.mkdir(parents=True, exist_ok=True)
    record = {
        "task_id": task_id,
        "description": f"job {task_id}",
        "mode": "supervised",
        "state": "running",
        "last_supervisor_health": "healthy",
        "last_supervisor_decision": "continue",
        "monitor_interval": 30,
        "elapsed_seconds": 1000,
        "worker_pid": os.getpid(),
    }
    record.update(fields)
    (reg / f"{task_id}.json").write_text(json.dumps(record), encoding="utf-8")


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "Met criterion.",
        "next_action": "—",
        "round_summary_markdown": "# done\n",
        "completion_summary_markdown": "Done.",
    })


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def test_advisory_injected_and_wait_sentinel_skips_reviewer(tmp_path: Path, monkeypatch) -> None:
    # Never really sleep during the cadence yield.
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    _write_record(tmp_path / ".argus_subagents", "train-1", description="full GRPO run")

    backend = MemoryBackend()
    # Round 1: engineer yields to the self-watched subagent's cadence.
    backend.queue("engineer-r1", CannedResponse(
        message="WAIT_FOR_SUBAGENT: train-1", thread_id="t1",
    ))
    # Round 2: engineer does independent work; reviewer accepts it.
    backend.queue("engineer-r2", CannedResponse(
        message="Repaired the full evaluator while the run trains.", thread_id="t1",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="finish the supervised run and evaluate",
        engineer_prompt_builder=lambda _na: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=5, check_commands=[]),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for (label, _p, _o) in backend.history]
    prompts = {label: p for (label, p, _o) in backend.history}

    # Advisory reached the round-1 engineer prompt.
    assert "Background subagents in flight" in prompts["engineer-r1"]
    assert "train-1" in prompts["engineer-r1"]

    # The sentinel round skipped the reviewer: engineer-r2 is called directly
    # after engineer-r1, with no "reviewer" call in between.
    assert labels[0] == "engineer-r1"
    assert labels[1] == "engineer-r2"
    assert "reviewer" not in labels[:1]

    # A cadence wait happened and the mission completed on the working round.
    assert any(e.get("type") == "round.background_wait.started" for e in events)
    assert any(e.get("type") == "round.background_wait.completed" for e in events)
    assert status == "done"


def test_unknown_wait_target_falls_through_to_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    # No registry: the named job is not waitable, so the sentinel is ignored and
    # the round is reviewed normally.
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="WAIT_FOR_SUBAGENT: ghost", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="do the thing",
        engineer_prompt_builder=lambda _na: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3, check_commands=[]),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for (label, _p, _o) in backend.history]
    assert labels[0] == "engineer-r1"
    assert "reviewer" in labels  # the round WAS reviewed (no spurious wait)
    assert not any(e.get("type") == "round.background_wait.started" for e in events)
    assert status == "done"


def test_no_advisory_without_inflight_subagents(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Did the work.", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    _engineer(backend).run(
        objective="do the thing",
        engineer_prompt_builder=lambda _na: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3, check_commands=[]),
        workdir=tmp_path,
        on_event=None,
    )

    prompts = {label: p for (label, p, _o) in backend.history}
    assert "Background subagents in flight" not in prompts["engineer-r1"]


def test_flag_disables_advisory(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "train-1")
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Did the work.", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    _engineer(backend).run(
        objective="do the thing",
        engineer_prompt_builder=lambda _na: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3, check_commands=[], background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=None,
    )

    prompts = {label: p for (label, p, _o) in backend.history}
    assert "Background subagents in flight" not in prompts["engineer-r1"]
