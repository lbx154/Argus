"""The cockpit must read the stage from the root that records one.

Testbed run 15 (``s-f0dbba19``) ran the math vertical, ``scope -> solve ->
review``, and finished at ``review`` with all three stages marked done. For the
entire run the API served ``research`` — stage one of the eight-stage default
pipeline, belonging to a vertical that project never ran.

Two files, one name. The session state root holds the real thing::

    {"current_stage": "review", "vertical": "math", "workflow_mode": "staged",
     "stages": {"scope": {"status": "done"}, ...}}

The execution workdir holds the adopted objective and nothing else::

    {"math_goal": "...", "math_objective_mode": "targeted",
     "math_objective_source": "transcribed_from_request"}

``current_stage_for_session`` asked the workdir first and accepted it because
the file was *there*. ``current_stage`` then did what it is supposed to do for
a project that has not started — returned the first stage of the vertical it
could resolve, which with no ``vertical`` key is the default ``research``. Two
correct behaviours composing into a wrong answer.

It is not cosmetic: ``snapshot_mission_view`` overwrites the event-sourced
stage with this value, so the served view contradicted the harness's own
persisted ``mission-view.json``, and both cockpits render the served one. The
Engineer noticed mid-run and printed both roots side by side; a gate-repair
script it wrote then aborted with ``unexpected stage before completion:
research``.

The general fallback in ``current_stage`` is deliberately left alone. A fresh
project really is at stage one, and that is the only thing the fallback is for.
What changes is which root gets asked.
"""

from __future__ import annotations

import json

from argus.webapi.project_state import current_stage_for_session

WORKDIR_ONLY_OBJECTIVE = {
    "math_goal": "characterise the universal moduli",
    "math_objective_mode": "targeted",
    "math_objective_source": "transcribed_from_request",
}
STATE_ROOT_PIPELINE = {
    "current_stage": "review",
    "vertical": "math",
    "workflow_mode": "staged",
    "stages": {
        "scope": {"status": "done"},
        "solve": {"status": "done"},
        "review": {"status": "done"},
    },
}


def _write(root, payload) -> None:
    (root / ".argus").mkdir(parents=True, exist_ok=True)
    (root / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_run_15s_stage_is_read_from_the_root_that_records_it(tmp_path) -> None:
    state_root = tmp_path / "s-f0dbba19"
    workdir = tmp_path / "argus-testbed-univ24-r15"
    _write(state_root, STATE_ROOT_PIPELINE)
    _write(workdir, WORKDIR_ONLY_OBJECTIVE)

    stage = current_stage_for_session({"workdir": str(workdir)}, state_root)

    assert stage == "review"


def test_the_wrong_verticals_first_stage_is_not_served(tmp_path) -> None:
    """``research`` is not merely stale here — it is another pipeline entirely."""
    state_root = tmp_path / "state"
    workdir = tmp_path / "work"
    _write(state_root, STATE_ROOT_PIPELINE)
    _write(workdir, WORKDIR_ONLY_OBJECTIVE)

    assert current_stage_for_session({"workdir": str(workdir)}, state_root) != "research"


def test_a_workdir_that_does_record_a_stage_is_still_honoured(tmp_path) -> None:
    """Sessions that execute in their own project root must not regress."""
    workdir = tmp_path / "single-root"
    _write(workdir, {"current_stage": "solve", "vertical": "math"})

    stage = current_stage_for_session({"workdir": str(workdir)}, tmp_path / "empty")

    assert stage == "solve"


def test_cwd_is_read_when_there_is_no_workdir(tmp_path) -> None:
    cwd = tmp_path / "cwd"
    _write(cwd, {"current_stage": "solve", "vertical": "math"})

    assert current_stage_for_session({"cwd": str(cwd)}, tmp_path / "empty") == "solve"


def test_a_fresh_project_still_reports_its_first_stage(tmp_path) -> None:
    """The stage-one fallback is correct for a project that has not started.

    Only the choice of root changes; a state file with no stage recorded
    anywhere still answers, because that is a real project at its beginning.
    """
    workdir = tmp_path / "fresh"
    _write(workdir, WORKDIR_ONLY_OBJECTIVE)

    assert current_stage_for_session({"workdir": str(workdir)}, tmp_path / "none")


def test_no_pipeline_state_anywhere_is_empty(tmp_path) -> None:
    assert current_stage_for_session({"workdir": str(tmp_path / "nothing")}, tmp_path) == ""


def test_an_unreadable_state_file_does_not_win_the_authoritative_pass(tmp_path) -> None:
    """Corrupt JSON records no stage, so it must not be treated as recording one."""
    state_root = tmp_path / "state"
    workdir = tmp_path / "work"
    (state_root / ".argus").mkdir(parents=True)
    (state_root / ".argus" / "PIPELINE_STATE.json").write_text("{not json", encoding="utf-8")
    _write(workdir, {"current_stage": "solve", "vertical": "math"})

    assert current_stage_for_session({"workdir": str(workdir)}, state_root) == "solve"
