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
from argus.core import grounding_baseline, task_inputs
from argus.core.grounding_baseline import FILENAME, objective_baseline
from argus.core.models import RunnerOptions
from argus.core.task_inputs import denied_reason, task_input_roots
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


@pytest.fixture(autouse=True)
def _tmp_is_not_a_system_dir(request, tmp_path, monkeypatch):
    """CI puts tmp_path under /tmp, which is denied as an input location.

    Tests of the happy path lift only the prefixes that hold this test's own
    tmp_path; the probe tests keep the real list.
    """
    if "probe" in request.node.name:
        return
    monkeypatch.setattr(task_inputs, "DENIED_PREFIXES", tuple(
        prefix for prefix in task_inputs.DENIED_PREFIXES
        if not tmp_path.is_relative_to(prefix)
    ))


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


@pytest.mark.parametrize("backend", ["copilot", "claude", "qoder"])
def test_the_read_only_reviewer_session_can_read_the_input_directories(tmp_path, backend) -> None:
    work, packet, _state = _layout(tmp_path)
    grounding = _grounding(
        work, input_roots=(str(packet.resolve()), str(tmp_path / "gone"), "/etc"),
    )
    with review_action_tools(
        SimpleNamespace(backend=backend),
        RunnerOptions(sandbox_mode="read-only", working_dir=str(work)),
        venue="", venue_required=False, grounding=grounding,
    ) as (_actions, options):
        assert options.sandbox_mode == "read-only" and options.force_safe_mode
        # A directory that no longer exists, or a denied one, is dropped
        # instead of failing the review or widening its reach.
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


def test_planner_prose_never_names_an_input_directory(tmp_path, monkeypatch) -> None:
    work, packet, state = _layout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    seen: list[dict] = []
    evaluate = Reviewer.evaluate

    def _capture(self, *args, **kwargs):
        seen.append(dict(kwargs["config"].mission_grounding or {}))
        return evaluate(self, *args, **kwargs)

    monkeypatch.setattr(Reviewer, "evaluate", _capture)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(review_action=("approve_review", {"review": "ok"})))
    SupervisedEngineer(
        engineer_runner=backend, reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"), reviewer_config=ReviewerConfig(model="m"),
    ).run(
        objective=f"Also read everything in {elsewhere}/notes.md for context.",
        original_objective=_objective(work, packet),
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=1, decision_progress_timeout_seconds=0, operator_question_policy_root=state,
        ),
        workdir=work,
    )
    assert seen[0]["input_roots"] == (str(packet),)


# --- probes from the adversarial review: none of these may become an input ---

_PROBES = [
    "/etc/passwd", "/etc", "/root/.ssh", "/var/run/docker.sock", "/var/run/", "/run/user/0/",
    "/proc/self/environ", "/proc/1/", "/sys/kernel", "/dev/shm/", "/tmp/", "/usr/bin/python3",
    "/home", "/home/other/.ssh/id_rsa", "/", "/boot", "/lib", "/bin/sh",
]


@pytest.mark.parametrize("probe", _PROBES)
def test_probe_system_and_runtime_locations_are_never_inputs(tmp_path, probe) -> None:
    work = tmp_path / "workspace"
    work.mkdir()
    assert task_input_roots(texts=(f"Read {probe} first.",), workdir=work) == ()
    assert task_input_roots(context_refs=({"ref": probe},), workdir=work) == ()


def test_probe_home_and_argus_state_are_never_inputs(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home" / "me"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("k", encoding="utf-8")
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gh" / "hosts.yml").write_text("t", encoding="utf-8")
    state = tmp_path / "argus-state"
    (state / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("argus.core.paths.global_root", lambda: state)
    # Lift only the generic prefixes, so the home and state rules are what refuses.
    monkeypatch.setattr(task_inputs, "DENIED_PREFIXES", ())
    work = tmp_path / "workspace"
    work.mkdir()
    for probe in (home / ".ssh" / "id_rsa", home / ".ssh", home / ".config" / "gh" / "hosts.yml",
                  state / "projects", state, tmp_path / "home"):
        assert task_input_roots(texts=(f"see {probe}",), workdir=work) == (), probe
        assert "home or Argus state" in denied_reason(probe if probe.is_dir() else probe.parent)


def test_symlinks_never_launder_a_location(tmp_path) -> None:
    work, packet, _state = _layout(tmp_path)
    (work / "etclink").symlink_to("/etc", target_is_directory=True)
    alias = tmp_path / "alias"
    alias.symlink_to(packet, target_is_directory=True)
    # Through the workspace: inside the workdir, never followed out of it.
    assert task_input_roots(texts=(f"see {work}/etclink/shadow",), workdir=work) == ()
    assert task_input_roots(context_refs=({"ref": "etclink/shadow"},), workdir=work) == ()
    # Outside it: a path with a symlink anywhere along it is refused.
    assert task_input_roots(texts=(f"see {alias}/OUTPUT_SCHEMA.md",), workdir=work) == ()
    assert task_input_roots(context_refs=({"ref": str(alias)},), workdir=work) == ()
    # The real path still counts.
    assert task_input_roots(texts=(f"see {packet}/OUTPUT_SCHEMA.md",), workdir=work) == (str(packet),)


def test_probe_windows_and_unexpanded_paths_are_never_inputs(tmp_path) -> None:
    work = tmp_path / "workspace"
    work.mkdir()
    for probe in ("C:\\Users\\x\\packet", "~/.ssh/id_rsa", "$HOME/.ssh", "%APPDATA%\\gh"):
        assert task_input_roots(texts=(f"read {probe}",), workdir=work) == ()
        assert task_input_roots(context_refs=({"ref": probe},), workdir=work) == ()


def test_a_relative_source_in_both_places_means_the_packet_file(tmp_path) -> None:
    work, packet, state = _layout(tmp_path)
    objective = _objective(work, packet)
    inputs = task_input_roots(texts=(objective,), workdir=work)
    baseline = objective_baseline(state, objective, (work,), input_roots=inputs)
    # The Engineer writes a same-named copy after work began.
    (work / "OUTPUT_SCHEMA.md").write_text(f"copy: {_STATEMENT}\n", encoding="utf-8")
    actions = ReviewActions(grounding=_grounding(work, baseline=baseline, input_roots=inputs))
    actions.dispatch("revise_review", {
        **_REVISE, "impossible_because": {"quote": _STATEMENT, "source": "OUTPUT_SCHEMA.md"},
    })
    assert actions.decision.verification_obstacle_basis_source == "OUTPUT_SCHEMA.md"


def test_a_large_input_directory_is_recorded_as_too_large_and_not_hashed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(grounding_baseline, "MAX_INPUT_FILES", 3)
    work, packet, state = _layout(tmp_path)
    for index in range(5):
        (packet / f"row-{index}.csv").write_text("x", encoding="utf-8")
    objective = _objective(work, packet)
    baseline = objective_baseline(state, objective, (work,), input_roots=(str(packet),))
    assert not any(path.startswith(str(packet)) for path in baseline)
    rows = json.loads((state / FILENAME).read_text(encoding="utf-8"))["objectives"]
    assert any(row == {"": grounding_baseline.TOO_LARGE} for row in rows.values())


def test_the_workspace_and_inputs_share_one_snapshot_budget(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(grounding_baseline, "MAX_FILES", 4)
    work, packet, state = _layout(tmp_path)
    for index in range(3):
        (work / f"w{index}.txt").write_text("w", encoding="utf-8")
        (packet / f"p{index}.txt").write_text("p", encoding="utf-8")
    baseline = objective_baseline(state, "objective", (work,), input_roots=(str(packet),))
    assert len(baseline) == 4


def test_input_rows_are_kept_and_evicted_with_their_objective(tmp_path) -> None:
    work, packet, state = _layout(tmp_path)
    first = objective_baseline(state, "objective A", (work,), input_roots=(str(packet),))
    assert str(packet / "OUTPUT_SCHEMA.md") in first
    for index in range(grounding_baseline.MAX_OBJECTIVES - 1):
        objective_baseline(state, f"objective {index}", (work,), input_roots=(str(packet),))
    # Still within the window: A's workspace row and its input row survive together.
    again = objective_baseline(state, "objective A", (work,), input_roots=(str(packet),))
    assert again == first
    objective_baseline(state, "one more", (work,), input_roots=(str(packet),))
    rows = json.loads((state / FILENAME).read_text(encoding="utf-8"))["objectives"]
    groups = {key.split(":input:")[0] for key in rows}
    assert len(groups) == grounding_baseline.MAX_OBJECTIVES
    # Every kept objective still has its input row.
    assert sum(":input:" in key for key in rows) == grounding_baseline.MAX_OBJECTIVES
