"""One transaction marks a Project done, and it refuses what the vertical did not ask for.

Before `core/project_api.py`, "is this project finished?" had four answers that
shared a name and were never reconciled: the lifecycle sidecar, the versioned
final-stage certificate, the Planner's verdict and the daemon's done_reason.
The write side is now single.

Two properties matter more than the happy path:

* the strength required is read from the *vertical's own* declaration, so the
  harness never decides that some work counts as finished;
* wiring the supervisor through the API did not change *when* anything
  completes. Twenty of twenty-three verticals declare a gate that never wrote
  the lifecycle sidecar, and reaching DONE stops token allocation — quietly
  broadening completion would park live daemons mid-mission.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from argus_skill.core.project_api import (
    SOURCE_PLANNER_VERDICT,
    SOURCE_REVIEWER_FULL_PAPER,
    SOURCE_VERTICAL_CERTIFICATE,
    CompletionSource,
    complete_project,
    evaluate_completion,
    required_completion_gate,
)
from argus_skill.life.project_lifecycle import ProjectState, ProjectStatus
from argus_skill.life.project_lifecycle_io import load_persisted

_REFS = ("journal:full_paper_gate_success",)


def _status(state: ProjectState = ProjectState.WRITING) -> ProjectStatus:
    return ProjectStatus(
        project_id="p",
        state=state,
        created_at=datetime.now(timezone.utc),
    )


# -- the vertical declares, the harness compares -----------------------------


@pytest.mark.parametrize(
    ("vertical", "expected"),
    [
        ("research", "full_paper"),
        ("kernelbench", "metric"),
        ("software", "none"),
    ],
)
def test_the_required_strength_comes_from_the_vertical(
    tmp_path: Path,
    vertical: str,
    expected: str,
) -> None:
    """Not a table in the harness — the vertical module states its own gate."""
    assert required_completion_gate(tmp_path, vertical) == expected


def test_a_weaker_source_cannot_close_a_stronger_gate(tmp_path: Path) -> None:
    outcome = evaluate_completion(
        project_root=tmp_path,
        vertical="research",
        source=CompletionSource(kind=SOURCE_PLANNER_VERDICT, evidence_refs=_REFS),
    )

    assert not outcome.accepted
    assert "full_paper" in outcome.reason


def test_a_stronger_source_satisfies_a_weaker_gate(tmp_path: Path) -> None:
    """Certifying a full paper also satisfies a vertical that asked for less.

    The ordering is mechanical: it ranks how strong a claim is, not how good
    the work is.
    """
    outcome = evaluate_completion(
        project_root=tmp_path,
        vertical="kernelbench",
        source=CompletionSource(kind=SOURCE_REVIEWER_FULL_PAPER, evidence_refs=_REFS),
    )

    assert outcome.accepted


def test_an_unknown_source_is_refused(tmp_path: Path) -> None:
    outcome = evaluate_completion(
        project_root=tmp_path,
        vertical="software",
        source=CompletionSource(kind="looks_done_to_me", evidence_refs=_REFS),
    )

    assert not outcome.accepted
    assert "not a recognised source" in outcome.reason


def test_a_completion_claim_must_name_its_evidence(tmp_path: Path) -> None:
    outcome = evaluate_completion(
        project_root=tmp_path,
        vertical="software",
        source=CompletionSource(kind=SOURCE_PLANNER_VERDICT, evidence_refs=()),
    )

    assert not outcome.accepted
    assert "evidence" in outcome.reason


def test_an_unreadable_vertical_demands_the_strongest_evidence(
    tmp_path: Path,
) -> None:
    """Fail closed: a requirement we cannot read is read strictly."""
    outcome = evaluate_completion(
        project_root=tmp_path,
        vertical="no-such-vertical-anywhere",
        source=CompletionSource(kind=SOURCE_VERTICAL_CERTIFICATE, evidence_refs=_REFS),
    )

    assert not outcome.accepted


# -- the write side ----------------------------------------------------------


def test_a_refused_completion_writes_nothing(tmp_path: Path) -> None:
    """The check runs before the write, so there is nothing to unwind."""
    events: list[dict] = []

    outcome = complete_project(
        memory_root=tmp_path,
        project_root=tmp_path,
        vertical="research",
        source=CompletionSource(kind=SOURCE_PLANNER_VERDICT, evidence_refs=_REFS),
        status=_status(),
        on_event=events.append,
    )

    assert not outcome.accepted
    assert load_persisted(tmp_path) == {}
    assert [event["type"] for event in events] == ["project.completion_refused"]


def test_an_accepted_completion_persists_done_and_announces_it(
    tmp_path: Path,
) -> None:
    events: list[dict] = []

    outcome = complete_project(
        memory_root=tmp_path,
        project_root=tmp_path,
        vertical="research",
        source=CompletionSource(kind=SOURCE_REVIEWER_FULL_PAPER, evidence_refs=_REFS),
        status=_status(),
        reason="reviewer_certified_full_paper",
        on_event=events.append,
    )

    assert outcome.accepted
    persisted = load_persisted(tmp_path)
    assert persisted["state"] == ProjectState.DONE.value
    completed = [event for event in events if event["type"] == "project.completed"]
    assert len(completed) == 1
    assert completed[0]["evidence_refs"] == list(_REFS)


def test_the_callers_reason_is_what_gets_persisted(tmp_path: Path) -> None:
    """Wiring must not silently rewrite the recorded history of a project."""
    complete_project(
        memory_root=tmp_path,
        project_root=tmp_path,
        vertical="research",
        source=CompletionSource(kind=SOURCE_REVIEWER_FULL_PAPER, evidence_refs=_REFS),
        status=_status(),
        reason="reviewer_certified_full_paper",
    )

    history = load_persisted(tmp_path)["history"]
    assert history[-1]["reason"] == "reviewer_certified_full_paper"


def test_completing_a_finished_project_again_is_a_no_op(tmp_path: Path) -> None:
    events: list[dict] = []
    source = CompletionSource(kind=SOURCE_REVIEWER_FULL_PAPER, evidence_refs=_REFS)

    complete_project(
        memory_root=tmp_path,
        project_root=tmp_path,
        vertical="research",
        source=source,
        status=_status(),
        on_event=events.append,
    )
    outcome = complete_project(
        memory_root=tmp_path,
        project_root=tmp_path,
        vertical="research",
        source=source,
        status=_status(ProjectState.DONE),
        on_event=events.append,
    )

    assert outcome.accepted
    assert "already terminal" in outcome.reason
    assert len(load_persisted(tmp_path)["history"]) == 1


# -- the regression this wiring must not cause -------------------------------


def test_wiring_did_not_broaden_which_verticals_can_reach_done() -> None:
    """Reaching DONE stops token allocation, so broadening it parks daemons.

    The supervisor still guards the completion call with
    `_effective_full_paper_gate`, which is False for every vertical that does
    not declare the paper gate. If that guard were dropped so the API decided
    on its own, twenty verticals would start writing DONE on their next tick.
    """
    import inspect

    from argus_skill.life.supervisor import _lifecycle

    source = inspect.getsource(_lifecycle.LifecycleMixin)
    call = source.index("complete_project(")
    guard = source.rindex("_effective_full_paper_gate", 0, call)
    between = source[guard:call]
    assert "if " not in between.split("\n", 1)[0]
    assert "complete_project" not in source[:guard]


def test_a_vertical_that_cannot_be_resolved_does_not_disable_the_block_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Regression guard for a fail-open introduced by wiring completion in.

    `_maybe_block_on_lifecycle` runs inside one `except Exception` that logs and
    allows dispatch. Resolving the vertical for the completion call is new work
    on that path; if it raised, the *block* check below it would be skipped and
    a project that should be held would be dispatched instead.
    """
    from argus_skill.life.supervisor import _lifecycle as lifecycle_mod

    def _boom(_root):
        raise RuntimeError("vertical state unreadable")

    monkeypatch.setattr(
        "argus_skill.skills.vertical_select.resolve_vertical",
        _boom,
    )

    resolved = lifecycle_mod.resolved_vertical_or_default(tmp_path)

    assert isinstance(resolved, str) and resolved
