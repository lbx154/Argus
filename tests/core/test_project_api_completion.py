"""The core completion transaction compares, persists, and emits exactly once."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from argus.core.project_api import (
    SOURCE_INDEPENDENT_CERTIFICATION,
    SOURCE_PLANNER_VERDICT,
    SOURCE_VERTICAL_CERTIFICATE,
    CompletionSource,
    complete_project,
    evaluate_completion,
)
from argus.life.project_lifecycle import ProjectState, ProjectStatus
from argus.life.project_lifecycle_io import load_persisted
from argus.verticals._base import load_vertical_contract

_REFS = ("journal:final_certification",)


def _status(state: ProjectState = ProjectState.WRITING) -> ProjectStatus:
    return ProjectStatus(
        project_id="p",
        state=state,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(
    ("vertical", "expected"),
    [
        ("research", "certified"),
        ("math_synth", "metric"),
        ("software", "none"),
    ],
)
def test_required_strength_comes_from_vertical_contract(
    tmp_path: Path,
    vertical: str,
    expected: str,
) -> None:
    assert load_vertical_contract(
        vertical, project_root=tmp_path
    ).completion_gate == expected


def test_weaker_source_cannot_close_certified_gate() -> None:
    outcome = evaluate_completion(
        vertical="research",
        required_gate="certified",
        source=CompletionSource(kind=SOURCE_PLANNER_VERDICT, evidence_refs=_REFS),
    )
    assert not outcome.accepted
    assert "certified" in outcome.reason


def test_stronger_source_satisfies_metric_gate() -> None:
    outcome = evaluate_completion(
        vertical="math_synth",
        required_gate="metric",
        source=CompletionSource(
            kind=SOURCE_INDEPENDENT_CERTIFICATION,
            evidence_refs=_REFS,
        ),
    )
    assert outcome.accepted


def test_unknown_source_and_missing_evidence_are_refused() -> None:
    unknown = evaluate_completion(
        vertical="software",
        required_gate="none",
        source=CompletionSource(kind="looks_done_to_me", evidence_refs=_REFS),
    )
    missing = evaluate_completion(
        vertical="software",
        required_gate="none",
        source=CompletionSource(kind=SOURCE_PLANNER_VERDICT, evidence_refs=()),
    )
    assert not unknown.accepted
    assert "not a recognised source" in unknown.reason
    assert not missing.accepted
    assert "evidence" in missing.reason


def test_unknown_gate_fails_closed() -> None:
    outcome = evaluate_completion(
        vertical="external",
        required_gate="unknown",
        source=CompletionSource(
            kind=SOURCE_VERTICAL_CERTIFICATE,
            evidence_refs=_REFS,
        ),
    )
    assert not outcome.accepted


def test_refused_completion_writes_nothing(tmp_path: Path) -> None:
    events: list[dict] = []
    outcome = complete_project(
        memory_root=tmp_path,
        vertical="research",
        required_gate="certified",
        source=CompletionSource(kind=SOURCE_PLANNER_VERDICT, evidence_refs=_REFS),
        status=_status(),
        on_event=events.append,
    )
    assert not outcome.accepted
    assert load_persisted(tmp_path) == {}
    assert [event["type"] for event in events] == ["project.completion_refused"]


def test_accepted_completion_persists_done_and_announces_it(tmp_path: Path) -> None:
    events: list[dict] = []
    outcome = complete_project(
        memory_root=tmp_path,
        vertical="research",
        required_gate="certified",
        source=CompletionSource(
            kind=SOURCE_INDEPENDENT_CERTIFICATION,
            evidence_refs=_REFS,
        ),
        status=_status(),
        reason="reviewer_certified_final_result",
        on_event=events.append,
    )
    assert outcome.accepted
    persisted = load_persisted(tmp_path)
    assert persisted["state"] == ProjectState.DONE.value
    assert persisted["history"][-1]["reason"] == "reviewer_certified_final_result"
    completed = [event for event in events if event["type"] == "project.completed"]
    assert len(completed) == 1
    assert completed[0]["evidence_refs"] == list(_REFS)


def test_completing_finished_project_again_is_no_op(tmp_path: Path) -> None:
    source = CompletionSource(
        kind=SOURCE_INDEPENDENT_CERTIFICATION,
        evidence_refs=_REFS,
    )
    complete_project(
        memory_root=tmp_path,
        vertical="research",
        required_gate="certified",
        source=source,
        status=_status(),
    )
    outcome = complete_project(
        memory_root=tmp_path,
        vertical="research",
        required_gate="certified",
        source=source,
        status=_status(ProjectState.DONE),
    )
    assert outcome.accepted
    assert "already terminal" in outcome.reason
    assert len(load_persisted(tmp_path)["history"]) == 1


def test_core_completion_api_does_not_resolve_verticals() -> None:
    import inspect

    from argus.core import project_api

    source = inspect.getsource(project_api)
    assert "from ..verticals" not in source
    assert "load_vertical(" not in source
