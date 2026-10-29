from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from argus.core.operator_context import (
    DirectiveRecord,
    IntakeDecision,
    OperatorContextCapacityError,
    OperatorContextStore,
    PreferenceRecord,
    StaleOperatorContextWrite,
    append_capability,
    append_revoke,
    build_operator_context_block,
    persist_intake_decision,
)


def directive(text: str, *, lifetime: str = "standing", revision: int = 1) -> DirectiveRecord:
    return DirectiveRecord(
        text, "project", "all", lifetime, "operator.test", revision, "2026-09-12T00:00:00Z"
    )


def preference(value: str, *, scope: str = "project") -> PreferenceRecord:
    return PreferenceRecord("workflow", value, scope, "all", 1)


def global_preference(project: Path, value: str, *, global_root: Path | None = None):
    return persist_intake_decision(
        project,
        value,
        IntakeDecision(
            kind="preference", scope="global", preference_kind="workflow", preference_value=value
        ),
        source="operator.test",
        global_root=global_root,
    )


def test_legacy_local_global_label_stays_local_through_compaction(tmp_path: Path) -> None:
    user = tmp_path / "user"
    first, second = user / "projects/first", user / "projects/second"
    # The raw Store is one physical revision namespace: this represents old data.
    store = OperatorContextStore(first)
    legacy = store.append(
        preference("Private legacy preference.", scope="global"), expected_revision=0
    )
    store.compact()
    assert store.project("planner").preferences == (legacy,)
    assert OperatorContextStore(second).project("planner").preferences == ()
    assert not (user / "operator_context.jsonl").exists()
    global_preference(second, "New explicitly shared preference.")
    assert store.project("planner").preferences == (legacy,)
    block, _ = build_operator_context_block("planner", first)
    assert "project; legacy global label" in block


def test_shared_root_project_records_and_capabilities_do_not_leak(tmp_path: Path) -> None:
    user = tmp_path / "user"
    project = user / "projects/first"
    shared = global_preference(project, "Shared choice.")
    store = OperatorContextStore(user)
    store.append(preference("Misplaced old project choice."), expected_revision=1)
    store.append(directive("Only this old local run may act."), expected_revision=2)
    append_capability(
        user,
        kind="service",
        available=True,
        route="service-route",
        secret_ref="vault-reference",
        scope="global",
        expected_revision=3,
    )
    projection = OperatorContextStore(project).project("engineer")
    assert projection.preferences == (shared,)
    assert projection.directives == ()
    assert projection.capabilities == ()


def test_custom_state_roots_require_explicit_shared_root_and_reject_conflicting_users(
    tmp_path: Path,
) -> None:
    custom = tmp_path / "custom-state"
    shared = tmp_path / "profile"
    global_preference(custom, "Explicitly scoped choice.", global_root=shared)
    assert OperatorContextStore(custom).project("planner").preferences == ()
    assert (
        OperatorContextStore(custom, global_root=shared).project("planner").preferences[0].value
        == "Explicitly scoped choice."
    )
    with pytest.raises(ValueError, match="different users"):
        OperatorContextStore(tmp_path / "other/projects/session", global_root=shared)


def test_compaction_preserves_revision_ack_consumption_and_mission_binding(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path, max_records=5, max_bytes=16_000)
    keep = store.append(directive("Keep the public API."), expected_revision=0)
    once = store.append(directive("One acknowledged action.", lifetime="once"), expected_revision=1)
    store.acknowledge("engineer", once.revision)
    retired = store.append(directive("Withdrawn directive."), expected_revision=2)
    append_revoke(tmp_path, retired.revision, reason="withdrawn", expected_revision=3)
    bounded = store.append(
        directive("Only mission-a.", lifetime="bounded_increment"),
        expected_revision=4,
        mission_id="mission-a",
    )
    for index in range(100):
        store.append(preference(f"Current setting {index}"), expected_revision=store.revision)
    before_revision = store.revision
    store.compact()
    assert store.ledger_path.stat().st_size <= 16_000
    assert len(store.records()) == 3

    restored = OperatorContextStore(tmp_path)
    assert restored.revision == before_revision
    assert restored.acknowledged_revision("engineer") == once.revision
    assert restored.acknowledged_revision("planner") == 0
    visible = restored.project("engineer", mission_id="mission-a", consume_once=False)
    assert {record.revision for record in visible.directives} == {keep.revision, bounded.revision}
    other = restored.project("engineer", mission_id="mission-b", consume_once=False)
    assert [record.revision for record in other.directives] == [keep.revision]
    with pytest.raises(StaleOperatorContextWrite):
        restored.append(directive("stale"), expected_revision=4)
    restored.settle_once(once.revision)
    assert restored.revision == before_revision


def test_discarded_old_tail_cannot_reenter_the_closed_checkpoint_prefix(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    item = store.append(directive("Consumed old input.", lifetime="once"), expected_revision=0)
    store.project("engineer")
    store.compact()
    with store.ledger_path.open("a") as handle:
        handle.write(json.dumps(asdict(item)) + "\n")
    with pytest.raises(ValueError, match="revision gap"):
        OperatorContextStore(tmp_path).project("engineer")


def test_legacy_once_without_consumption_evidence_is_not_replayed(tmp_path: Path) -> None:
    path = tmp_path / "operator_context.jsonl"
    path.write_text(json.dumps(asdict(directive("Old one-shot.", lifetime="once"))) + "\n")
    store = OperatorContextStore(tmp_path)
    assert store.project("engineer", consume_once=False).directives == ()
    assert json.loads(path.read_text().splitlines()[0])["format"] == "operator-context-v2"
    assert OperatorContextStore(tmp_path).project("engineer").directives == ()


def test_large_legacy_preference_history_streams_into_a_bounded_checkpoint(tmp_path: Path) -> None:
    from dataclasses import replace

    path = tmp_path / "operator_context.jsonl"
    rows = [
        asdict(replace(preference(f"setting {index}"), revision=index + 1)) for index in range(600)
    ]
    rows.append(asdict(directive("Previously consumed late input.", lifetime="once", revision=601)))
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "operator_context.json").write_text(
        json.dumps(
            {
                "revision": 601,
                "consumed_once": [601],
                "acknowledged_revisions": {"engineer": 601},
            }
        )
    )
    store = OperatorContextStore(tmp_path)
    assert store.project("engineer").directives == ()
    store.compact()
    assert store.revision == 601
    assert store.acknowledged_revision("engineer") == 601
    assert len(store.records()) == 1
    assert store.project("planner").preferences[0].value == "setting 599"
    assert path.stat().st_size < 4096
    (tmp_path / "operator_context.json").unlink()
    assert OperatorContextStore(tmp_path).project("engineer").directives == ()


def test_active_authority_is_not_evicted_when_capacity_is_full(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path, max_records=2)
    for index in range(2):
        store.append(directive(f"Active constraint {index}."), expected_revision=index)
    source = store.ledger_path.read_bytes()
    with pytest.raises(OperatorContextCapacityError):
        store.append(directive("Exceeds capacity."), expected_revision=2)
    assert store.ledger_path.read_bytes() == source
    assert len(store.project("engineer").directives) == 2


def test_missing_canonical_source_cannot_revive_withdrawn_legacy_steering(tmp_path: Path) -> None:
    (tmp_path / "STEERING.jsonl").write_text(
        json.dumps(
            {
                "id": "legacy-1",
                "kind": "directive",
                "source": "operator.inbox",
                "text": "Previously withdrawn legacy action.",
                "version": 1,
                "timestamp": "2026-01-01T00:00:00Z",
            }
        )
        + "\n"
    )
    store = OperatorContextStore(tmp_path)
    assert store.revision == 1
    append_revoke(tmp_path, 1, reason="withdraw", expected_revision=1)
    store.compact()
    store.ledger_path.unlink()
    with pytest.raises(ValueError, match="source is missing"):
        OperatorContextStore(tmp_path).project("engineer")
    assert not store.ledger_path.exists()


def test_project_symlink_cannot_change_the_inferred_user_namespace(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    first = tmp_path / "user-a/projects/first"
    second = tmp_path / "user-b/projects/second"
    first.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    global_preference(first, "Private user-a preference.")
    opened_before_alias = OperatorContextStore(second)
    second.symlink_to(first, target_is_directory=True)
    with pytest.raises(ValueError, match="cannot alias"):
        OperatorContextStore(second).project("planner")
    with pytest.raises(ValueError, match="cannot alias"):
        opened_before_alias.project("planner")
