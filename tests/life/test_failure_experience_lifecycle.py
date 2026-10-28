from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from argus.life.failure_experience import (
    FailureAnnotation,
    FailureExperience,
    FailureExperienceStore,
    StaleFailureExperienceWrite,
)


def experience(name: str, **changes) -> FailureExperience:
    return replace(
        FailureExperience.new(
            mission_id=name,
            title=name,
            objective=name,
            status="failed",
            factual_outcome="one bounded observation",
            source_refs=[f"mission:{name}"],
            evidence_refs=[f"review:{name}"],
            created_at=changes.pop("created_at", None),
        ),
        **changes,
    )


def indexed(store: FailureExperienceStore) -> dict[str, tuple[int, str, str]]:
    with closing(sqlite3.connect(store.index.path)) as db:
        return {
            identity: (revision, direct, vector)
            for identity, revision, direct, vector in db.execute(
                "SELECT id, revision, direct_terms, vector FROM documents"
            )
        }


def test_revision_replaces_recall_and_vectors_and_rejects_stale_write(tmp_path: Path) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    original = store.append(experience("obsolete quartz"))

    updated = store.revise(
        original.id,
        expected_revision=1,
        evidence_refs=["review:correction"],
        title="verified sapphire",
        objective="verified sapphire",
        lessons=["Use the revised measurement."],
    )

    assert updated.revision == 2
    assert store.get(original.id) == updated
    assert "#### obsolete quartz" not in store.render_context("quartz sapphire")
    assert "verified sapphire" in store.render_context("sapphire")
    assert indexed(store)[original.id][0] == 2
    assert "quartz" not in indexed(store)[original.id][1]
    assert "review:correction" in updated.evidence_refs
    with pytest.raises(StaleFailureExperienceWrite):
        store.append(original)
    with pytest.raises(StaleFailureExperienceWrite):
        store.revise(
            original.id, expected_revision=1, evidence_refs=["review:stale"], title="stale"
        )


def test_same_source_and_annotation_retries_are_idempotent(tmp_path: Path) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    original = experience("retry")
    written = store.append(original)
    before = store.path.read_bytes()
    assert store.append(original) == written
    assert store.path.read_bytes() == before
    note = FailureAnnotation.new("A later interpretation.", evidence_refs=["review:later"])
    updated = store.annotate(written.id, note)
    before = store.path.read_bytes()
    assert store.annotate(written.id, note) == updated
    assert store.path.read_bytes() == before
    assert updated.revision == 2
    assert indexed(store)[written.id][0] == 2
    with pytest.raises(StaleFailureExperienceWrite):
        store.annotate("unknown", note)


def test_supersession_checks_replacement_and_merge_is_atomic_on_stale_input(tmp_path: Path) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    first, second = [store.append(experience(name)) for name in ("old", "new")]
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.supersede(
            first.id,
            "missing",
            expected_revision=1,
            evidence_refs=["review:new"],
            reason="replaced",
        )
    with pytest.raises(StaleFailureExperienceWrite):
        store.merge(
            first.id,
            [second.id],
            expected_revisions={first.id: 1, second.id: 2},
            evidence_refs=["review:new"],
            reason="merge",
        )
    assert store.path.read_bytes() == before
    store.supersede(
        first.id, second.id, expected_revision=1, evidence_refs=["review:new"], reason="corrected"
    )
    assert set(indexed(store)) == {second.id}
    with pytest.raises(ValueError, match="evidence"):
        store.retract(second.id, expected_revision=1, evidence_refs=[], reason="unsubstantiated")


def test_retired_source_cannot_be_replayed_with_a_fresh_timestamp(tmp_path: Path) -> None:
    from argus.life.failure_experience import experience_from_settled_mission

    fields = dict(
        mission_id="first",
        title="first",
        objective="first",
        status="failed",
        factual_outcome="bounded",
    )
    store = FailureExperienceStore(
        tmp_path / "failure_experiences.jsonl", max_active=1, max_history=0
    )
    first = experience_from_settled_mission(**fields, created_at=1000)
    store.append(first)
    store.append(experience("new", created_at=1001))
    with pytest.raises(ValueError, match="original stable"):
        experience_from_settled_mission(**fields)
    with pytest.raises(ValueError, match="identity"):
        store.append(replace(first, created_at=time.time()))
    with pytest.raises(StaleFailureExperienceWrite):
        store.append(experience_from_settled_mission(**fields, created_at=1000))
    assert first.id not in {hit.experience.id for hit in store.retrieve("first")}


def test_old_unbound_identity_cannot_reenter_after_its_tombstone_is_compacted(
    tmp_path: Path,
) -> None:
    store = FailureExperienceStore(
        tmp_path / "failure_experiences.jsonl", max_active=1, max_history=0
    )
    legacy = store.append(experience("legacy", id="old-unbound-id", created_at=1000))
    store.append(experience("new", created_at=1001))
    with pytest.raises(StaleFailureExperienceWrite):
        store.append(replace(legacy, created_at=time.time(), updated_at=0))


def test_damaged_snapshot_metadata_cannot_reset_retirement_watermark(tmp_path: Path) -> None:
    store = FailureExperienceStore(
        tmp_path / "failure_experiences.jsonl", max_active=1, max_history=0
    )
    first = store.append(experience("retracted", created_at=1000))
    store.retract(
        first.id, expected_revision=1, evidence_refs=["review:withdrawn"], reason="withdrawn"
    )
    lines = store.path.read_text().splitlines()
    header = json.loads(lines[0])
    assert header["admission_floor"] == 1000
    header["record_type"] = "damaged-snapshot"
    lines[0] = json.dumps(header)
    store.path.write_text("\n".join(lines) + "\n")
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="corrupt"):
        store.append(first)
    assert store.render_context("retracted") == ""
    assert store.path.read_bytes() == before


def test_expiry_is_filtered_before_index_query_and_physically_compacted(tmp_path: Path) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    item = store.append(experience("temporary", expires_at=time.time() + 3600))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("argus.life.failure_experience.time.time", lambda: item.expires_at + 1)
        assert store.retrieve("temporary") == []
        assert indexed(store) == {}
        store.compact()
    assert store.get(item.id).state == "stale"
    assert FailureExperienceStore(store.path).retrieve("temporary") == []


def test_upgraded_append_log_preserves_old_match_and_its_later_annotation(
    tmp_path: Path,
) -> None:
    from argus.apps.state_upgrade import upgrade_state

    path = tmp_path / "projects" / "s-0a1b2c3d" / "failure_experiences.jsonl"
    path.parent.mkdir(parents=True)
    records = [
        experience("rare zircon" if index == 0 else f"unrelated {index}", created_at=1000 + index)
        for index in range(100)
    ]
    annotation = FailureAnnotation.new(
        "Later evidence clarifies the zircon result.", evidence_refs=["review:later"]
    )
    lines = [
        json.dumps({"record_type": "experience", **record.to_jsonable()}) for record in records
    ]
    lines.insert(5, "malformed legacy row")
    lines.append(
        json.dumps(
            {
                "record_type": "annotation",
                "experience_id": records[0].id,
                "annotation": annotation.__dict__,
            }
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    upgrade_state(tmp_path)

    store = FailureExperienceStore(path)
    hits = store.retrieve("zircon")
    recalled = next(hit.experience for hit in hits if hit.experience.id == records[0].id)
    assert recalled.annotations == [annotation]
    assert json.loads(path.read_text().splitlines()[0])["schema_version"] == 2
    assert len(indexed(store)) == 100
    store.retract(
        recalled.id, expected_revision=1, evidence_refs=["review:invalidated"], reason="invalidated"
    )
    store.index.path.unlink()
    assert records[0].id not in {
        hit.experience.id for hit in FailureExperienceStore(path).retrieve("zircon")
    }


def test_index_failure_cannot_serve_stale_revision_and_recovers(
    tmp_path: Path, monkeypatch
) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    item = store.append(experience("obsolete finding"))

    def unavailable(*_args, **_kwargs):
        raise OSError("injected index outage")

    with monkeypatch.context() as patch:
        patch.setattr(store.index, "sync", unavailable)
        patch.setattr(store.index, "rebuild", unavailable)
        store.revise(
            item.id,
            expected_revision=1,
            evidence_refs=["review:fixed"],
            title="current finding",
            objective="current finding",
        )
        assert indexed(store)[item.id][0] == 1
        rendered = store.render_context("finding")
        assert "current finding" in rendered
        assert "#### obsolete finding" not in rendered
    assert store.retrieve("finding")[0].experience.revision == 2
    assert indexed(store)[item.id][0] == 2


def test_corrupt_or_partially_missing_index_is_rebuilt_from_current_source(tmp_path: Path) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    item = store.append(experience("retained"))
    store.index.path.write_bytes(b"not a sqlite database")
    assert store.retrieve("retained")[0].experience.id == item.id
    with closing(sqlite3.connect(store.index.path)) as db, db:
        db.execute("DELETE FROM documents")
    assert store.retrieve("retained")[0].experience.id == item.id
    assert set(indexed(store)) == {item.id}
    store.path.unlink()
    assert store.retrieve("retained") == []
    assert indexed(store) == {}


def test_corrupt_canonical_snapshot_fails_closed_without_replaying_history(tmp_path: Path) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    item = store.append(experience("retired evidence"))
    store.retract(
        item.id, expected_revision=1, evidence_refs=["review:retract"], reason="retracted"
    )
    source = store.path.read_bytes()
    store.path.write_bytes(source.rsplit(b"\n", 2)[0] + b"\n")
    broken = store.path.read_bytes()
    with pytest.raises(ValueError, match="corrupt"):
        store.retrieve("retired evidence")
    assert store.render_context("retired evidence") == ""
    with pytest.raises(ValueError, match="corrupt"):
        store.append(experience("new"))
    assert store.path.read_bytes() == broken


def test_explicit_embedding_adapter_reindexes_when_its_version_changes(tmp_path: Path) -> None:
    class Embedding:
        dimensions = 2
        identifier = "test-semantic-fixture-v1"

        def embed(self, text: str):
            return [1.0, 0.0] if "database" in text or "cache" in text else [0.0, 1.0]

    path = tmp_path / "failure_experiences.jsonl"
    store = FailureExperienceStore(path, embedder=Embedding())
    database = store.append(experience("database result"))
    store.append(experience("new unrelated result"))
    hit = next(hit for hit in store.retrieve("cache") if hit.experience.id == database.id)
    assert hit.channel == "embedding similarity (advisory)"
    lexical = FailureExperienceStore(path)
    lexical.retrieve("cache")
    with closing(sqlite3.connect(lexical.index.path)) as db:
        assert (
            db.execute("SELECT value FROM metadata WHERE key='encoder'")
            .fetchone()[0]
            .startswith("lexical-v2")
        )
    assert len(json.loads(indexed(lexical)[database.id][2])) == 1


@pytest.mark.parametrize("budget", [1, 20, 600, 6000])
def test_recall_keeps_the_existing_prompt_character_budget(tmp_path: Path, budget: int) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    store.append(experience("bounded", research_narrative="long context " * 200))
    assert len(store.render_context("bounded", max_chars=budget)) <= budget
