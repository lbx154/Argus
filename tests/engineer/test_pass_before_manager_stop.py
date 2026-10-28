"""A Manager STOP that lands while the Reviewer judges keeps a pass (HO-5).

A verdict is a completed role operation, and the Manager's STOP applies only
between role operations. The STOP became current while the Reviewer judged; a
pass settles, and a verdict that goes on parks at the next boundary, before
the Engineer runs again.
"""
from __future__ import annotations

from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.life.memory import Backlog, BacklogItem
from argus.manager import supervision
from argus.reviewer import Reviewer, ReviewerConfig
from tests.hand_off import hand_off

STOP = "The Manager stopped this run: the dataset split is not settled."


def _write(workdir: Path, text: str) -> CannedResponse:
    def turn(_prompt, _options) -> str:
        (workdir / "parser.py").write_text(text, encoding="utf-8")
        return "Changed the parser."

    return CannedResponse(message_factory=turn, action=hand_off("reviewer", "Changed the parser."))


def _run(tmp_path: Path, backend: MemoryBackend):
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    backlog = Backlog(tmp_path / "state" / "backlog.jsonl")
    backlog.add(BacklogItem.new(item_id="parser-task", title="t", objective="repair the parser"))
    backlog.mark_running("parser-task")
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    status, _rounds, _final, reason, _tid = engineer.run(
        objective="repair the parser",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=4, state_root=tmp_path / "state", session_id="parser-task",
        ),
        workdir=workdir,
    )
    return status, reason, [label for label, _prompt, _options in backend.history]


def _stop_once_reviewed(monkeypatch, backend: MemoryBackend) -> None:
    """The Manager's STOP on this run is current from the first verdict on."""
    monkeypatch.setattr(
        supervision, "mission_stop_reason",
        lambda _root, _mission: STOP if any(label == "reviewer" for label, *_ in backend.history) else "",
    )


def test_a_stop_while_the_reviewer_judges_keeps_the_pass(tmp_path: Path, monkeypatch) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    backend.queue("reviewer", CannedResponse(action=hand_off("done", "The parser handles every case.")))
    _stop_once_reviewed(monkeypatch, backend)

    status, _reason, labels = _run(tmp_path, backend)

    # Parked as manager_stop, the pass was lost: a resumed run would redo the
    # work, and the Planner could retire a task that had already passed.
    assert status == "done"
    assert labels == ["engineer-r1", "reviewer"]


def test_a_stop_after_a_verdict_that_goes_on_parks_before_the_engineer(tmp_path: Path, monkeypatch) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    backend.queue("reviewer", CannedResponse(action=hand_off("engineer", "Handle the empty line.")))
    _stop_once_reviewed(monkeypatch, backend)

    status, reason, labels = _run(tmp_path, backend)

    assert (status, reason) == ("manager_stop", STOP)
    assert labels == ["engineer-r1", "reviewer"]
