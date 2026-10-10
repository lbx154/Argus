"""The task's own input directories are readable and groundable for the Reviewer.

A benchmark container mounts the task packet beside the workdir (the packet
in ``/app/packet``, the run in ``/workspace``). The read-only Reviewer must be
able to open it, and a statement quoted from an unchanged packet file must
ground an "impossible here" the same way a workspace file does.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.grounding_baseline import FILENAME, objective_baseline
from argus.core.models import RunnerOptions
from argus.core.task_inputs import task_input_roots
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import ReviewActions, review_action_tools

_STATEMENT = "During each verifier ingest command, the environment also provides the feed token."
_REVISE = {
    "review": "The live check still needs the feed token.",
    "forward_progress": False,
    "unverifiable": (
        "Live ingestion needs the feed token, which exists only during the verifier's "
        "own commands; a labelled fixture is the best evidence here."
    ),
}


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    work = tmp_path / "workspace"
    work.mkdir()
    packet = tmp_path / "app" / "packet"
    packet.mkdir(parents=True)
    (packet / "OUTPUT_SCHEMA.md").write_text(f"# Schema\n\n{_STATEMENT}\n", encoding="utf-8")
    return work, packet, tmp_path / "state"


def _objective(work: Path, packet: Path) -> str:
    return (
        f"Build a stateful `dispatch` CLI for the shift packet in `{packet}`. The executable "
        f"must be available at `{work}/dispatch` as described in `{packet}/OUTPUT_SCHEMA.md`. "
        "See https://example.invalid/docs/x for background."
    )


def _grounding(work: Path, **mission):
    from argus.reviewer._core import _review_grounding

    config = ReviewerConfig(
        model="m", working_dir=str(work), artifact_root=str(work), mission_grounding=mission,
    )
    return _review_grounding(config, task_parts=("Build the dispatch CLI.",))


def test_input_directories_come_from_the_paths_the_task_names(tmp_path) -> None:
    work, packet, _state = _layout(tmp_path)
    (tmp_path / "refs").mkdir()
    (tmp_path / "refs" / "GRADING.md").write_text("x", encoding="utf-8")

    roots = task_input_roots(
        texts=(_objective(work, packet), f"also `{packet}/OUTPUT_SCHEMA.md`."),
        context_refs=({"ref": str(tmp_path / "refs" / "GRADING.md")}, {"ref": "local.md"}),
        workdir=work,
    )

    # The packet directory (named as a directory and through a file in it) and
    # the context ref's directory. Not the workdir path, not a missing path,
    # not a URL.
    assert roots == (str(packet.resolve()), str((tmp_path / "refs").resolve()))


def test_input_directories_never_include_the_workdir_its_parents_or_broad_roots(tmp_path) -> None:
    work, packet, _state = _layout(tmp_path)
    roots = task_input_roots(
        texts=(f"Work in {work}/src under {tmp_path} and read /, {Path.home()} and {packet}.",),
        workdir=work,
    )
    assert roots == (str(packet.resolve()),)


def test_an_unchanged_packet_file_beside_the_workdir_grounds_impossible(tmp_path) -> None:
    work, packet, state = _layout(tmp_path)
    objective = _objective(work, packet)
    inputs = task_input_roots(texts=(objective,), workdir=work)
    baseline = objective_baseline(state, objective, (work,), input_roots=inputs)
    schema = packet / "OUTPUT_SCHEMA.md"
    assert str(schema.resolve()) in baseline

    grounding = _grounding(work, baseline=baseline, input_roots=inputs)
    assert str(packet.resolve()) in grounding.roots
    for source in (str(schema), "OUTPUT_SCHEMA.md"):
        actions = ReviewActions(grounding=grounding)
        actions.dispatch("revise_review", {
            **_REVISE, "impossible_because": {"quote": _STATEMENT, "source": source},
        })
        assert actions.decision.verification_obstacle_basis_source == source

    # Edited after work began, the packet copy is the objective's own work.
    schema.write_text(f"# Schema (edited)\n\n{_STATEMENT}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not as it was when work on this objective began"):
        ReviewActions(grounding=grounding).dispatch("revise_review", {
            **_REVISE, "impossible_because": {"quote": _STATEMENT, "source": str(schema)},
        })


def test_input_directories_named_after_work_began_ground_nothing(tmp_path) -> None:
    work, packet, state = _layout(tmp_path)
    objective = _objective(work, packet)
    # The objective's workspace baseline was recorded before input directories
    # were known (an earlier mission, or an earlier release): the packet may
    # already hold that mission's edits, so it is readable but not a source.
    objective_baseline(state, objective, (work,))
    inputs = task_input_roots(texts=(objective,), workdir=work)
    later = objective_baseline(state, objective, (work,), input_roots=inputs)
    assert str((packet / "OUTPUT_SCHEMA.md").resolve()) not in later
    with pytest.raises(ValueError, match="not as it was when work on this objective began"):
        ReviewActions(grounding=_grounding(work, baseline=later, input_roots=inputs)).dispatch(
            "revise_review", {
                **_REVISE,
                "impossible_because": {"quote": _STATEMENT, "source": str(packet / "OUTPUT_SCHEMA.md")},
            },
        )


def test_the_read_only_reviewer_session_can_read_the_input_directories(tmp_path) -> None:
    work, packet, _state = _layout(tmp_path)
    grounding = _grounding(work, input_roots=(str(packet.resolve()), str(tmp_path / "gone")))
    with review_action_tools(
        SimpleNamespace(backend="copilot"),
        RunnerOptions(sandbox_mode="read-only", working_dir=str(work)),
        venue="", venue_required=False, grounding=grounding,
    ) as (_actions, options):
        assert options.sandbox_mode == "read-only" and options.force_safe_mode
        # A directory that no longer exists is dropped instead of failing the review.
        assert options.add_dirs == [str(packet.resolve())]


def test_a_mission_hands_its_packet_directory_to_the_reviewer(tmp_path, monkeypatch) -> None:
    work, packet, state = _layout(tmp_path)
    objective = _objective(work, packet)
    seen: list[dict] = []
    evaluate = Reviewer.evaluate

    def _capture(self, *args, **kwargs):
        seen.append(dict(kwargs["config"].mission_grounding or {}))
        return evaluate(self, *args, **kwargs)

    monkeypatch.setattr(Reviewer, "evaluate", _capture)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="built the CLI"))
    backend.queue("reviewer", CannedResponse(review_action=("approve_review", {"review": "ok"})))
    SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    ).run(
        objective=objective,
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=1, decision_progress_timeout_seconds=0,
            operator_question_policy_root=state,
        ),
        workdir=work,
    )

    assert seen and seen[0]["input_roots"] == (str(packet.resolve()),)
    recorded = json.loads((state / FILENAME).read_text(encoding="utf-8"))["objectives"]
    assert any(str((packet / "OUTPUT_SCHEMA.md").resolve()) in row for row in recorded.values())
