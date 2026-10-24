"""Review once: a passed review stands until the delivered files change.

Built from the survey run of 2026-10-24 (project s-7d7b7d22): the survey
``reward-hacking-survey-zh.md`` passed its independent review in task
ddeb85499e56, and the Planner then queued closing steps (final acceptances)
on the same files, each with a full review of its own, over a wording in
``.argus/evidence/``. A closing step stands on the acceptance of the files it
judges; past the Engineer, whoever already held those very files holds them
for the closing step too, so an unchanged deliverable is not reviewed twice.
"""
from __future__ import annotations

from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.life.context_packet import create_mission_context
from argus.reviewer import Reviewer, ReviewerConfig
from tests.hand_off import hand_off

SURVEY = "reward-hacking-survey-zh.md"


def _edit(workdir: Path, relative: str, text: str, to: str = "reviewer") -> CannedResponse:
    """An Engineer turn that writes ``relative``, then hands the work on."""

    def turn(_prompt, _options) -> str:
        path = workdir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return ""

    return CannedResponse(message_factory=turn, action=hand_off(to, f"Edited {relative}."))


def _review(to: str, note: str = "") -> CannedResponse:
    return CannedResponse(action=hand_off(to, "Judged the survey.", note))


def _task(tmp_path: Path, mission_id: str, *, closing: bool) -> Path:
    """The task's mission packet; a closing step is the Planner's final acceptance."""
    return create_mission_context(
        life_dir=tmp_path / "state", mission_id=mission_id, stage="idea", objective="the survey",
        node_key="" if not closing else f"final_certification_{mission_id}",
        scope="final_submission" if closing else "",
        tags=["planner", "scope:final_submission", "stage_closing", "review:required"] if closing else [],
    )


def _run(tmp_path: Path, backend: MemoryBackend, packet: Path):
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    status, rounds, _final, _reason, _tid = engineer.run(
        objective="the survey",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=6, state_root=tmp_path / "state", session_id=packet.parent.name,
            context_packet_path=str(packet),
        ),
        workdir=tmp_path / "work",
    )
    return status, rounds, [label for label, _prompt, _options in backend.history]


def _delivered(tmp_path: Path) -> None:
    """Task ddeb85499e56: the survey is written and its Reviewer accepts it."""
    (tmp_path / "work").mkdir()
    backend = MemoryBackend()
    backend.queue("engineer-r1", _edit(tmp_path / "work", SURVEY, "v1"))
    backend.queue("reviewer", _review("done"))
    assert _run(tmp_path, backend, _task(tmp_path, "ddeb85499e56", closing=False))[0] == "done"


def test_a_closing_step_on_accepted_files_is_not_reviewed_again(tmp_path: Path) -> None:
    _delivered(tmp_path)

    status, rounds, labels = _run(tmp_path, MemoryBackend(), _task(tmp_path, "9e5992dac968", closing=True))

    # No model is called and no new task follows: the acceptance of these very
    # files stands for the final acceptance, and the record names it.
    assert labels == []
    assert status == "done"
    review = rounds[-1].review
    assert review.final_submission_certified
    (reused,) = review.interventions
    assert reused["action"] == "reused_verdict"
    assert "of ddeb85499e56" in reused["note"]


def test_a_closing_step_that_only_touches_internal_records_keeps_the_acceptance(tmp_path: Path) -> None:
    _delivered(tmp_path)
    work = tmp_path / "work"
    (work / ".argus/evidence").mkdir(parents=True)

    # Argus's own records are not the deliverable: rewriting one between the
    # acceptance and the closing step leaves the delivered files as reviewed.
    (work / ".argus/evidence/source-verification.md").write_text("line 55, corrected", encoding="utf-8")
    status, _rounds, labels = _run(tmp_path, MemoryBackend(), _task(tmp_path, "9e5992dac968", closing=True))

    assert (labels, status) == ([], "done")


def test_a_closing_step_reviews_a_changed_deliverable(tmp_path: Path) -> None:
    _delivered(tmp_path)
    (tmp_path / "work" / SURVEY).write_text("v2, four method corrections", encoding="utf-8")
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(action=hand_off("reviewer", "Ready for acceptance.")))
    backend.queue("reviewer", _review("done"))

    status, _rounds, labels = _run(tmp_path, backend, _task(tmp_path, "9e5992dac968", closing=True))

    assert labels == ["engineer-r1", "reviewer"]
    assert status == "done"


def test_an_ordinary_task_on_accepted_files_still_gets_its_review(tmp_path: Path) -> None:
    _delivered(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(action=hand_off("reviewer", "Checked a claim; nothing to change.")))
    backend.queue("reviewer", _review("done"))

    # A task that is not a closing step judges its own objective.
    status, _rounds, labels = _run(tmp_path, backend, _task(tmp_path, "5a1e0c0ffee0", closing=False))

    assert labels == ["engineer-r1", "reviewer"]
    assert status == "done"


def test_a_second_closing_step_on_unchanged_files_is_not_reviewed_or_replanned(tmp_path: Path) -> None:
    _delivered(tmp_path)
    work = tmp_path / "work"
    (work / SURVEY).write_text("v2, four method corrections", encoding="utf-8")
    first = MemoryBackend()
    first.queue("engineer-r1", CannedResponse(action=hand_off("reviewer", "Ready for acceptance.")))
    first.queue("reviewer", _review("engineer", "Fix the misattribution in source-verification.md line 55."))
    first.queue("engineer-r2", _edit(work, ".argus/evidence/source-verification.md", "corrected"))

    # 01:32: the fix is in Argus's records, so the survey is unchanged and the
    # work goes one rung up, to the Planner, which replans once.
    status, _rounds, labels = _run(tmp_path, first, _task(tmp_path, "9e5992dac968", closing=True))
    assert (labels, status) == (["engineer-r1", "reviewer", "engineer-r2"], "replan_requested")

    second = MemoryBackend()
    second.queue("engineer-r1", _edit(work, ".argus/evidence/final-certification.md", "confirmed"))
    status, rounds, labels = _run(tmp_path, second, _task(tmp_path, "67c874f26b5e", closing=True))

    # The next closing step on the same files gets no second review and no
    # second replan: the Reviewer and the Planner already held these files,
    # so the work reaches the Manager.
    assert labels == ["engineer-r1"]
    assert status == "blocked"
    assert "reviewer and the planner" in rounds[-1].review.reason


def test_the_climb_reads_as_plain_chinese() -> None:
    from argus.core.operator_messages import render_operator_update
    from argus.engineer.round_routing import climb_record

    text = render_operator_update(
        title="独立验收中文综述的完整交付", status="replan_requested",
        reason=climb_record(["reviewer"], "planner").reason, holder="planner",
    )

    assert "Nothing changed" not in text
    assert "自审阅者上次处理以来，交付的文件没有变化" in text and "交给规划者" in text
