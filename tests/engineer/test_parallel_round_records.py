"""Two Engineers in round 1 at the same time keep their own command records.

s-efe9deac ran task 9aa61a7009c7 (the length-1024 diagnostic) beside task
ec540a8ca32e (the allocation replay) on 2026-10-26. Both Engineer calls were
labelled `engineer-r1`, and a round's command record takes every stream line
its label reports, so each round recorded the other's commands too: ec54's
Reviewer (call 82761812) read 44 shell commands, among them 9aa6's
validate_diagnostic and verify.py runs of 12:23:36 UTC on
results/development/length1024/attempt-002.

The fixtures are both calls' CLI streams over ec54's round (12:21:42-12:30:11
UTC) as the host logged them, and the project's events for the same window.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import argus.verticals.research  # noqa: F401 - registers the round-log provider
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.adapters.stream_progress import make_stream_progress_callback
from argus.engineer.round_evidence import RoundEvidenceRequest
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig
from argus.tools.efficiency_report import role_of
from argus.verticals.research.round_log import round_log_evidence
from argus.webapi.map_trace import task_trace
from tests.hand_off import hand_off

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "parallel_rounds"
LENGTH, ALLOCATION = "9aa61a7009c7", "ec540a8ca32e"


def _rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURE / name).read_text(encoding="utf-8").splitlines()]


def _replay_streams(labels: dict[str, str]) -> None:
    """Each call's recorded stdout, in time order, under the run label its round gave it."""
    sink = SimpleNamespace(handle_stream_line=lambda *_: None, handle_event=lambda _event: None)
    callbacks = {item: make_stream_progress_callback(sink) for item in labels}
    lines = sorted(
        ((row["ts"], item, row["line"]) for item in labels
         for row in _rows(f"agent_io_{item}.jsonl") if "line" in row),
        key=lambda entry: entry[0],
    )
    for _ts, item, line in lines:
        callbacks[item](f"{labels[item]}.stdout", line)


@dataclass
class _RecordedCall(MemoryBackend):
    """An Engineer whose call reports what the recorded CLI reported.

    Each call waits until the other has started, so both rounds' records are
    open while the two streams are replayed.
    """

    item: str = ""
    both_started: threading.Barrier | None = None
    labels: dict = field(default_factory=dict)

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        if run_label.startswith("engineer"):
            self.labels[self.item] = run_label
            if self.both_started.wait(timeout=30) == 0:
                _replay_streams(self.labels)
            self.both_started.wait(timeout=30)
        return super().run_exec(prompt=prompt, options=options, run_label=run_label,
                                resume_thread_id=resume_thread_id)


def _review_prompts(tmp_path: Path) -> dict[str, str]:
    reviewers = MemoryBackend(default=CannedResponse(action=hand_off("done", "Done.")))
    both_started, labels, errors = threading.Barrier(2), {}, []

    def run(item: str) -> None:
        packet = tmp_path / "state" / "handoffs" / item / "mission.json"
        packet.parent.mkdir(parents=True)
        packet.write_text(json.dumps({"mission_id": item}), encoding="utf-8")
        engineer = SupervisedEngineer(
            engineer_runner=_RecordedCall(
                default=CannedResponse(message="round done"), history=reviewers.history,
                item=item, both_started=both_started, labels=labels,
            ),
            reviewer=Reviewer(runner=reviewers.fork()),
            engineer_config=EngineerConfig(model="m"), reviewer_config=ReviewerConfig(model="m"),
        )
        try:
            engineer.run(
                objective=f"Replay task {item}.",
                engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
                supervised_config=SupervisedConfig(max_rounds=1, context_packet_path=str(packet)),
                workdir=tmp_path / item,
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    (tmp_path / LENGTH).mkdir()
    (tmp_path / ALLOCATION).mkdir()
    threads = [threading.Thread(target=run, args=(item,)) for item in (LENGTH, ALLOCATION)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not errors, errors
    return {
        item: next(prompt for label, prompt, _ in reviewers.history if label == "reviewer" and item in prompt)
        for item in (LENGTH, ALLOCATION)
    }


def _round_log(prompt: str) -> str:
    return prompt.split("Engineer's commands this round", 1)[1].split("Main agent fatal error", 1)[0]


def test_each_review_reads_only_its_own_rounds_commands(tmp_path) -> None:
    prompts = _review_prompts(tmp_path)

    allocation = _round_log(prompts[ALLOCATION])
    assert "\n32 shell commands" in allocation and "length1024" not in allocation
    assert "results/development/allocation256/attempt-002" in allocation
    assert "unverified" not in allocation and "conflicting" not in allocation
    length = _round_log(prompts[LENGTH])
    assert "results/development/length1024/attempt-002" in length
    assert "allocation" not in length


def test_the_event_log_fallback_reads_only_the_task_under_review(tmp_path) -> None:
    request = RoundEvidenceRequest(
        workdir=tmp_path, life_dir=tmp_path, round_index=1,
        events_path=FIXTURE / "events.jsonl", item_id=ALLOCATION,
    )

    text = round_log_evidence(request).reviewer_text

    assert "\n32 shell commands" in text and "length1024" not in text  # 44 with 9aa6's rows
    assert "results/development/allocation256/attempt-002" in text


def _life(tmp_path: Path, label) -> Path:
    """The map's view of the project, the Engineer calls labelled by ``label(item)``."""
    calls = {row["call_id"]: item for item in (LENGTH, ALLOCATION)
             for row in _rows(f"agent_io_{item}.jsonl") if row.get("type") == "agent.io.start"}

    def relabel(row: dict) -> dict:
        return {**row, "run_label": label(calls[row["call_id"]])} if row.get("type") == "agent.io.start" else row

    life = tmp_path / "life"
    life.mkdir(parents=True)
    for name, rows in (
        ("events.jsonl", _rows("events.jsonl")),
        ("agent_io.jsonl", [*_rows(f"agent_io_{LENGTH}.jsonl"), *_rows(f"agent_io_{ALLOCATION}.jsonl")]),
    ):
        rows = sorted((relabel(row) for row in rows), key=lambda row: row["ts"])
        (life / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return life


def test_the_map_finds_both_engineer_calls_under_either_label(tmp_path) -> None:
    for name, label in (("recorded", lambda _item: "engineer-r1"), ("by task", lambda item: f"engineer-r1-{item}")):
        life = _life(tmp_path / name, label)
        for item in (LENGTH, ALLOCATION):
            groups = task_trace(life, item)["groups"]
            assert [(group["role"], group["round"]) for group in groups] == [("engineer", 1)], (name, item)
            commands = " ".join(entry["command"] for entry in groups[0]["entries"])
            assert ("length1024" in commands) == (item == LENGTH), (name, item)

    assert role_of(f"engineer-r1-{ALLOCATION}") == "engineer"
