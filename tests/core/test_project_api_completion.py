"""The core completion transaction compares, persists, and emits exactly once."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from argus.core.project_api import (
    SOURCE_INDEPENDENT_CERTIFICATION,
    SOURCE_PLANNER_VERDICT,
    CompletionSource,
    complete_project,
    evaluate_completion,
)
from argus.life.project_lifecycle import ProjectState, ProjectStatus
from argus.life.project_lifecycle_io import load_persisted

_REFS = ("journal:final_certification",)


def _status(state: ProjectState = ProjectState.WRITING) -> ProjectStatus:
    return ProjectStatus(
        project_id="p",
        state=state,
        created_at=datetime.now(timezone.utc),
    )


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
