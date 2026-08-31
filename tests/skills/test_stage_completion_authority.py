"""Regression test: a completion primitive must enforce the check it is named for.

Testbed run 13 (``s-d9ea298f``) is the case. Its Engineer was blocked on
``staged_goal_gate_incomplete`` at ``scope``, stage 1 of math's ``scope ->
solve -> review``. It imported the framework and called
``complete_final_stage`` against the project state root. The function completed
``scope`` — running only ``scope``'s validator — stamped a valid contract
fingerprint, and marked ``solve`` and ``review`` ``skipped``.

Nothing was hand-forged. The resulting ``PIPELINE_STATE.json`` passes
``_vertical_completion_record``'s structural audit exactly, because that audit
checks an early completion is *internally consistent* and this one was minted
by the real primitive. ``vertical_completion_certificate_status`` answered
``{"ok": True}``. The math had in fact been done and certified, but nothing in
this path required that; the same three calls close any project at stage 1.

The word "final" lived only in ``final_stage_completion_decision``, in the
Manager layer, which a caller reaches this primitive without passing through.

Two changes, because a write-side lock does nothing about state already on disk:

* ``complete_final_stage`` refuses off the final stage unless the caller passes
  ``allow_early_completion``, which the Manager supplies for ``direct``
  workflow mode — the one arrangement where stopping early is a real outcome;
* ``vertical_completion_certificate_status`` re-checks the same condition at
  read time, so a record like run 13's is rejected wherever it is read.

This is a lock, not a signature. ``completed_by`` is free text (run 13 wrote
``"manager-repair"``, a string in no source file) and the fingerprint is
recomputable by anyone who can read the framework. A caller determined to pass
the argument still can. What is closed is the accident-shaped path: reaching
for a function whose name promised a check it did not perform.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_machine import complete_final_stage
from argus_skill.skills.vertical_select import (
    persist_vertical,
    vertical_completion_certificate_status,
)

MATH_STAGES = ("scope", "solve", "review")


def _project(tmp_path: Path, *, stage: str, mode: str = "staged") -> Path:
    root = tmp_path / "project"
    (root / ".argus").mkdir(parents=True)
    (root / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps(
            {
                "current_stage": stage,
                "workflow_mode": mode,
                "stages": {name: {"status": "pending"} for name in MATH_STAGES},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    persist_vertical(root, "math")
    return root


def test_completing_a_non_final_stage_is_refused(tmp_path: Path) -> None:
    """Run 13's exact call."""
    root = _project(tmp_path, stage="scope")

    with pytest.raises(ValueError) as excinfo:
        complete_final_stage(root, reason="objective satisfied", completed_by="manager-repair")

    message = str(excinfo.value)
    assert "not the final stage" in message
    assert "'review'" in message


def test_the_refusal_names_what_is_still_ahead(tmp_path: Path) -> None:
    """A blocked caller that is not told what remains invents bookkeeping.

    Run 13's Planner answered an unexplained refusal by queueing a mission to
    "record the missing route/ledger state or equivalent gate metadata".
    """
    root = _project(tmp_path, stage="scope")

    with pytest.raises(ValueError) as excinfo:
        complete_final_stage(root, reason="done", completed_by="manager")

    message = str(excinfo.value)
    assert "solve" in message
    assert "review" in message


def test_the_refusal_happens_before_any_write(tmp_path: Path) -> None:
    """A refused completion must leave the pipeline exactly as it was."""
    root = _project(tmp_path, stage="scope")
    state = root / ".argus" / "PIPELINE_STATE.json"
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        complete_final_stage(root, reason="done", completed_by="manager")

    assert state.read_text(encoding="utf-8") == before


def test_early_completion_is_still_available_to_a_caller_with_standing(
    tmp_path: Path,
) -> None:
    """``direct`` workflow mode is a real path and must not be broken.

    The explicit argument and persisted direct workflow jointly carry standing.
    Direct work does not require a staged artifact bundle.
    """
    root = _project(tmp_path, stage="scope", mode="direct")

    complete_final_stage(
        root,
        reason="direct mode stops here",
        completed_by="manager",
        allow_early_completion=True,
    )

    assert vertical_completion_certificate_status(root, "math")["ok"] is True


def test_early_completion_flag_cannot_bypass_staged_workflow(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, stage="scope", mode="staged")

    with pytest.raises(ValueError, match="direct workflow"):
        complete_final_stage(
            root,
            reason="staged work is not done",
            completed_by="manager",
            allow_early_completion=True,
        )


def test_the_final_stage_never_needed_the_flag(tmp_path: Path) -> None:
    """Ordinary completion must be untouched by the new gate."""
    from argus_skill.skills.stage_machine import StageCompletionError

    root = _project(tmp_path, stage="review")

    try:
        complete_final_stage(root, reason="certified", completed_by="manager")
    except StageCompletionError:
        pass
    except ValueError as exc:  # pragma: no cover - the regression itself
        assert "not the final stage" not in str(exc)


def _forge(root: Path, *, mode: str) -> None:
    """Write run 13's resulting state directly.

    Reproduced as data rather than by calling the primitive, because the
    primitive now refuses — and the read side has to reject records written
    before it did.
    """
    from argus_skill.skills.stage_machine import completion_contract_fingerprint
    from argus_skill.verticals._base import (
        load_vertical,
        vertical_completion_contract_version,
    )

    version = vertical_completion_contract_version(load_vertical("math", project_root=root))
    fingerprint = completion_contract_fingerprint(root, "scope", version=version)
    state = root / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["workflow_mode"] = mode
    payload["stages"] = {
        "scope": {
            "status": "done",
            "completion_contract_version": version,
            "completion_contract_sha256": fingerprint,
        },
        "solve": {"status": "skipped", "skipped_by": "manager-repair"},
        "review": {"status": "skipped", "skipped_by": "manager-repair"},
    }
    payload["stage_history"] = [
        {
            "by": "manager-repair",
            "direction": "complete",
            "from_stage": "scope",
            "to_stage": "scope",
            "reason": "objective satisfied",
            "skipped_stages": ["solve", "review"],
        }
    ]
    state.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_a_record_like_run_13s_is_rejected_at_read_time(tmp_path: Path) -> None:
    """The state that answered ``{"ok": True}`` for run 13."""
    root = _project(tmp_path, stage="scope")
    _forge(root, mode="staged")

    status = vertical_completion_certificate_status(root, "math")

    assert status["ok"] is False
    assert "final stage" in status["reason"]
    assert status["final_stage"] == "review"


def test_the_read_side_rejection_names_the_uncertified_stages(tmp_path: Path) -> None:
    root = _project(tmp_path, stage="scope")
    _forge(root, mode="staged")

    reason = vertical_completion_certificate_status(root, "math")["reason"]

    assert "solve" in reason
    assert "review" in reason


def test_a_valid_contract_fingerprint_does_not_rescue_it(tmp_path: Path) -> None:
    """The fingerprint is recomputable; it proves freshness, not authority.

    ``_forge`` stamps the *genuine* expected value, so this asserts the new
    check is not reachable-around by getting the hash right.
    """
    root = _project(tmp_path, stage="scope")
    _forge(root, mode="staged")

    status = vertical_completion_certificate_status(root, "math")

    assert status["ok"] is False
    assert "differs from the live one" not in status["reason"], (
        "rejected for the wrong reason; the fingerprint here is correct"
    )


def test_direct_mode_still_accepts_its_own_early_completion(tmp_path: Path) -> None:
    """The read side must not break the path the write side still allows."""
    root = _project(tmp_path, stage="scope")
    _forge(root, mode="direct")

    status = vertical_completion_certificate_status(root, "math")

    assert status["ok"] is True, status


def test_an_unreadable_workflow_mode_fails_closed(tmp_path: Path) -> None:
    root = _project(tmp_path, stage="scope")
    _forge(root, mode="")

    assert vertical_completion_certificate_status(root, "math")["ok"] is False
