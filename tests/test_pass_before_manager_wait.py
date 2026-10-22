"""A Manager WAIT that lands right after a pass keeps the pass (HO-5).

The Reviewer passed the work, and the Manager's WAIT on the task became
current before the round settled. The pass settles; the WAIT does not park
the task, so no resume runs the Engineer again over work that already passed.
"""
from __future__ import annotations

from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.life.memory import Backlog, BacklogItem
from argus.manager import supervision
from argus.reviewer import Reviewer, ReviewerConfig

WAIT = "Manager is waiting for the requested operator facts: which dataset split?"


def _write(workdir: Path, text: str) -> CannedResponse:
    def turn(_prompt, _options) -> str:
        (workdir / "parser.py").write_text(text, encoding="utf-8")
        return "Changed the parser.\n→ reviewer"

    return CannedResponse(message_factory=turn)


def _review(line: str) -> CannedResponse:
    return CannedResponse(review_action=("submit_review", {"review": f"Judged the parser.\n{line}"}))


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


def _wait_once_reviewed(monkeypatch, backend: MemoryBackend) -> None:
    """The Manager's WAIT on this task is current from the first verdict on."""
    monkeypatch.setattr(
        supervision, "mission_wait_reason",
        lambda _root, _mission: WAIT if any(label == "reviewer" for label, *_ in backend.history) else "",
    )


def test_a_wait_right_after_a_pass_settles_the_pass(tmp_path: Path, monkeypatch) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    backend.queue("reviewer", _review("→ done"))
    _wait_once_reviewed(monkeypatch, backend)

    status, _reason, labels = _run(tmp_path, backend)

    # Parked as manager_wait, the resumed run ran engineer-r1 again, and the
    # pass, refused once the row was sent back, climbed to the Planner.
    assert status == "done"
    assert labels == ["engineer-r1", "reviewer"]


def test_a_wait_after_a_verdict_that_goes_on_still_parks(tmp_path: Path, monkeypatch) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", _write(tmp_path / "work", "v1"))
    backend.queue("reviewer", _review("→ engineer: handle the empty line"))
    _wait_once_reviewed(monkeypatch, backend)

    status, reason, labels = _run(tmp_path, backend)

    assert (status, reason) == ("manager_wait", WAIT)
    assert labels == ["engineer-r1", "reviewer"]
