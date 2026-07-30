from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life import (
    FailureAnnotation,
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


def test_failure_experience_round_trip_preserves_open_narrative_and_annotations(
    tmp_path: Path,
) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    experience = _experience(
        "Carry scheduling failure",
        objective="reduce reversible-adder depth",
        narrative="The carry lifetime, rather than gate count, caused the bottleneck.",
        transfer=["Try lifetime-aware register reuse in unrelated arithmetic blocks."],
    )
    store.append(experience)
    store.annotate(
        experience.id,
        FailureAnnotation.new(
            "Later modular inversion work reused the lifetime argument.",
            relation="inspired",
        ),
    )

    (loaded,) = store.recent()

    assert loaded.research_narrative == experience.research_narrative
    assert loaded.transfer_insights == experience.transfer_insights
    assert loaded.annotations[0].relation == "inspired"
    assert "modular inversion" in loaded.annotations[0].text


def test_retrieval_mixes_direct_transfer_and_distant_analogy(
    tmp_path: Path,
) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    store.append(_experience(
        "Direct modular search",
        objective="optimize modular multiplication",
        concepts=["modular", "multiplication"],
    ))
    store.append(_experience(
        "Scheduling lesson",
        objective="compress a circuit",
        transfer=["Multiplication may benefit from delaying uncompute."],
    ))
    store.append(_experience(
        "Distant cache experiment",
        objective="reduce database cache misses",
        narrative="A phase boundary invalidated the assumed reuse.",
    ))
    store.append(_experience(
        "Newest unrelated result",
        objective="improve figure typography",
    ))

    hits = store.retrieve("new modular multiplication scheduling", max_entries=4)

    channels = {hit.channel for hit in hits}
    assert "recent" in channels
    assert "direct factual/conceptual" in channels
    assert "transfer insight" in channels
    assert "exploratory analogy" in channels
    assert len({hit.experience.id for hit in hits}) == 4


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


def test_retrieval_never_opens_lazy_artifact_references(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_failure_dir = tmp_path / "raw-failure"
    raw_failure_dir.mkdir()
    (raw_failure_dir / "huge.bin").write_bytes(b"x" * 1024)
    store = FailureExperienceStore(tmp_path / "state" / "failure_experiences.jsonl")
    store.append(_experience(
        "Failed benchmark",
        objective="reduce runtime",
        artifact_refs=[str(raw_failure_dir)],
    ))

    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path == raw_failure_dir:
            raise AssertionError("raw failure directory was traversed")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)

    rendered = store.render_context("runtime")

    assert str(raw_failure_dir) in rendered


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
