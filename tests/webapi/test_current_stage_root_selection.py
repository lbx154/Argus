"""The cockpit must read the stage from the root that records one.

Testbed run 15 (``s-f0dbba19``) ran the math vertical, ``scope -> solve ->
review``, and finished at ``review`` with all three stages marked done. For the
entire run the API served ``research`` — stage one of the eight-stage default
pipeline, belonging to a vertical that project never ran.

Two files, one name. The session state root holds the real thing::

    {"current_stage": "review", "vertical": "targeted_lab", "workflow_mode": "staged",
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

import pytest

from argus.webapi.project_state import current_stage_for_session

pytestmark = pytest.mark.usefixtures("targeted_vertical")


def _write(root, payload) -> None:
    (root / ".argus").mkdir(parents=True, exist_ok=True)
    (root / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_an_unreadable_state_file_does_not_win_the_authoritative_pass(tmp_path) -> None:
    """Corrupt JSON records no stage, so it must not be treated as recording one."""
    state_root = tmp_path / "state"
    workdir = tmp_path / "work"
    (state_root / ".argus").mkdir(parents=True)
    (state_root / ".argus" / "PIPELINE_STATE.json").write_text("{not json", encoding="utf-8")
    _write(workdir, {"current_stage": "solve", "vertical": "targeted_lab"})

    assert current_stage_for_session({"workdir": str(workdir)}, state_root) == "solve"
