from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.life.failure_experience import (
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
    initial_vector = indexed(store)[original.id][2]

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
    assert indexed(store)[original.id][2] != initial_vector
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


def test_merge_and_retract_remove_old_vectors_and_preserve_provenance(tmp_path: Path) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    first = store.append(experience("first mechanism"))
    second = store.append(experience("second mechanism"))
    merged = store.merge(
        first.id,
        [second.id],
        expected_revisions={first.id: 1, second.id: 1},
        evidence_refs=["review:joint"],
        reason="Both observations concern the same measured mechanism.",
        title="consolidated mechanism",
        objective="consolidated mechanism",
    )
    assert merged.revision == 2
    assert set(merged.source_refs) == {"mission:first mechanism", "mission:second mechanism"}
    assert store.get(second.id).superseded_by == [first.id]
    assert set(indexed(store)) == {first.id}
    assert {hit.experience.id for hit in store.retrieve("mechanism")} == {first.id}

    store.retract(
        first.id,
        expected_revision=2,
        evidence_refs=["review:contradiction"],
        reason="New controlled result contradicts the claim.",
    )
    assert store.retrieve("mechanism") == []
    assert indexed(store) == {}
    store.index.path.unlink()
    assert FailureExperienceStore(store.path).retrieve("mechanism") == []
    assert indexed(store) == {}


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


def test_capacity_compacts_payloads_history_and_tombstones_without_reviving_old_ids(
    tmp_path: Path,
) -> None:
    store = FailureExperienceStore(
        tmp_path / "failure_experiences.jsonl",
        max_active=3,
        max_history=2,
        max_active_bytes=12_000,
        max_history_bytes=4_000,
    )
    first = store.append(experience("oldest", created_at=1000))
    for index in range(20):
        store.append(experience(f"new {index}", created_at=1001 + index))
    latest = store.recent(max_entries=1)[0]
    for index in range(8):
        latest = store.revise(
            latest.id,
            expected_revision=latest.revision,
            evidence_refs=[f"review:{index}"],
            lessons=[f"new finding {index}"],
        )
    counts = store.compact()
    assert counts["active"] == 3
    assert counts["terminal"] + counts["history"] <= 2
    assert store.path.stat().st_size < 17_000
    assert store.index.path.stat().st_size < 100_000
    assert len(indexed(store)) == 3
    assert store.get(first.id) is None
    with pytest.raises(StaleFailureExperienceWrite):
        FailureExperienceStore(store.path).append(first)
    with pytest.raises(StaleFailureExperienceWrite):
        store.append(replace(first, revision=2))
    assert "oldest" not in store.render_context("oldest")


def test_retired_source_cannot_be_replayed_with_a_fresh_timestamp(tmp_path: Path) -> None:
    from argus_skill.life.failure_experience import experience_from_settled_mission

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


@pytest.mark.parametrize("field", ["record_type", "admission_floor"])
def test_damaged_snapshot_metadata_cannot_reset_retirement_watermark(
    tmp_path: Path, field: str
) -> None:
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
    header[field] = "damaged-snapshot" if field == "record_type" else 0
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
        patch.setattr("argus_skill.life.failure_experience.time.time", lambda: item.expires_at + 1)
        assert store.retrieve("temporary") == []
        assert indexed(store) == {}
        store.compact()
    assert store.get(item.id).state == "stale"
    assert FailureExperienceStore(store.path).retrieve("temporary") == []


def test_streaming_legacy_migration_preserves_old_match_and_its_later_annotation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "failure_experiences.jsonl"
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


def test_replace_failure_preserves_source_and_retry_can_commit(tmp_path: Path, monkeypatch) -> None:
    from argus_skill.life import failure_experience_storage as storage

    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    item = store.append(experience("original"))
    before = store.path.read_bytes()
    original_replace = storage.os.replace

    def broken_replace(source, destination):
        if Path(destination) == store.path:
            raise OSError("injected replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", broken_replace)
    with pytest.raises(OSError, match="injected"):
        store.revise(item.id, expected_revision=1, evidence_refs=["review:new"], title="corrected")
    assert store.path.read_bytes() == before
    assert indexed(store)[item.id][0] == 1
    assert list(tmp_path.glob("*.tmp")) == []
    monkeypatch.setattr(storage.os, "replace", original_replace)
    assert (
        store.revise(
            item.id, expected_revision=1, evidence_refs=["review:new"], title="corrected"
        ).revision
        == 2
    )


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


def test_lexical_recall_can_match_chinese_without_whitespace(tmp_path: Path) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    matching = store.append(experience("缓存失效的边界"))
    store.append(experience("更新任务状态图"))
    assert any(
        hit.experience.id == matching.id and hit.channel == "direct factual/conceptual"
        for hit in store.retrieve("缓存错误的原因")
    )


def test_two_store_instances_cannot_overwrite_the_same_revision(tmp_path: Path) -> None:
    path = tmp_path / "failure_experiences.jsonl"
    item = FailureExperienceStore(path).append(experience("shared"))

    def revise(index: int) -> str:
        try:
            FailureExperienceStore(path).revise(
                item.id,
                expected_revision=1,
                evidence_refs=[f"review:{index}"],
                title=f"revision {index}",
            )
            return "committed"
        except StaleFailureExperienceWrite:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(revise, range(2))) == ["committed", "stale"]
    assert FailureExperienceStore(path).get(item.id).revision == 2


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
            .startswith("lexical-hash-v1")
        )
    assert len(json.loads(indexed(lexical)[database.id][2])) == 256


def test_settlement_is_idempotent_and_new_revisions_reach_memory_and_planner(
    tmp_path: Path,
) -> None:
    from argus_skill.life.memory import BacklogItem, MemoryBundle
    from argus_skill.life.supervisor._mission_execution_helpers import _MissionRunState
    from argus_skill.life.supervisor._mission_execution_settlement import (
        MissionExecutionSettlementMixin,
    )
    from argus_skill.life.supervisor._planner_rendering import PlannerRenderingMixin

    memory = MemoryBundle.for_cwd(global_root=tmp_path, fingerprint="s-memory")
    item = BacklogItem.new(title="bounded experiment", objective="test the measured mechanism")
    state = _MissionRunState(
        item=item, usage_attempt_id="attempt-1", status="failed", stop_reason="old interpretation"
    )
    host = SimpleNamespace(memory=memory)
    MissionExecutionSettlementMixin._capture_failure_experience(host, state)
    MissionExecutionSettlementMixin._capture_failure_experience(host, state)
    store = memory.failure_experiences
    (captured,) = store.recent()
    assert captured.revision == 1
    assert "attempt-1" in captured.source_refs[0]
    updated = store.revise(
        captured.id,
        expected_revision=1,
        evidence_refs=["review:controlled-retry"],
        factual_outcome="corrected measured interpretation",
    )

    class Planner(PlannerRenderingMixin):
        def _render_campaign_tally(self):
            return ""

    planner = Planner()
    planner.memory = memory
    planner.config = SimpleNamespace(continuous_objective=item.objective)
    for context in (
        memory.render_prelude(objective=item.objective),
        planner._render_journal_entries_for_planner([]),
    ):
        assert "corrected measured interpretation" in context
        assert "old interpretation" not in context
    store.retract(
        updated.id, expected_revision=2, evidence_refs=["review:withdrawn"], reason="withdrawn"
    )
    assert "corrected measured interpretation" not in memory.render_prelude(
        objective=item.objective
    )
    assert "corrected measured interpretation" not in planner._render_journal_entries_for_planner(
        []
    )
    state.usage_attempt_id = "attempt-2"
    MissionExecutionSettlementMixin._capture_failure_experience(host, state)
    assert len(store.recent()) == 1
    assert store.recent()[0].id != captured.id


@pytest.mark.parametrize("budget", [1, 20, 600, 6000])
def test_recall_keeps_the_existing_prompt_character_budget(tmp_path: Path, budget: int) -> None:
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    store.append(experience("bounded", research_narrative="long context " * 200))
    assert len(store.render_context("bounded", max_chars=budget)) <= budget
