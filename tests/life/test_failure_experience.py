from __future__ import annotations

import json
from pathlib import Path

from argus.life import (
    FailureExperience,
    FailureExperienceStore,
)


def _experience(
    title: str,
    *,
    objective: str,
    narrative: str = "",
    concepts: list[str] | None = None,
    transfer: list[str] | None = None,
    artifact_refs: list[str] | None = None,
) -> FailureExperience:
    return FailureExperience.new(
        mission_id=title.lower().replace(" ", "-"),
        title=title,
        objective=objective,
        status="no_progress",
        factual_outcome="bounded search found no qualifying candidate",
        research_narrative=narrative,
        transfer_insights=transfer,
        claim_boundaries=["finite search only; no impossibility claim"],
        concepts=concepts,
        artifact_refs=artifact_refs,
    )


def test_facets_are_advisory_and_context_denies_hard_blocking(
    tmp_path: Path,
) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    store.append(_experience(
        "Timed out approach",
        objective="search one circuit family",
        concepts=["rejected", "timeout", "mechanism-a"],
    ))

    rendered = store.render_context("retry with mechanism-b")

    assert "not rules" in rendered
    assert "does not prove impossibility" in rendered
    assert "block a changed approach" in rendered


def test_reads_are_byte_bounded_and_corrupt_rows_fail_soft(tmp_path: Path) -> None:
    path = tmp_path / "failure_experiences.jsonl"
    old = _experience("Old", objective="old")
    recent = _experience("Recent", objective="recent")
    path.write_text(
        json.dumps({"record_type": "experience", **old.to_jsonable()})
        + "\n"
        + (" " * 4096)
        + "\nnot-json\n"
        + json.dumps({
            "record_type": "experience",
            "created_at": "not-a-number",
            "title": "Malformed",
        })
        + "\n"
        + json.dumps({"record_type": "experience", **recent.to_jsonable()})
        + "\n",
        encoding="utf-8",
    )
    store = FailureExperienceStore(path)

    loaded = store.recent(max_bytes=2_048)

    assert [item.title for item in loaded] == ["Recent"]
