from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.operator_context import (
    DirectiveRecord,
    IntakeDecision,
    OperatorContextCapacityError,
    OperatorContextStore,
    PreferenceRecord,
    StaleOperatorContextWrite,
    append_capability,
    append_preference,
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


def test_global_preference_crosses_only_its_user_and_project_override_can_be_revoked(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user-a"
    first, second = user / "projects/first", user / "projects/second"
    unrelated = tmp_path / "user-b/projects/third"
    shared = global_preference(first, "Reuse verified local tools.", global_root=user)
    assert shared.revision == 1
    assert OperatorContextStore(first).revision == 0
    assert (user / "operator_context.jsonl").exists()
    assert not (first / "operator_context.jsonl").exists()
    for project in (first, second):
        projection = OperatorContextStore(project).project("planner")
        assert [record.value for record in projection.preferences] == [
            "Reuse verified local tools."
        ]
        assert projection.global_revision == 1
        assert projection.preference_sources == ("global",)
    assert OperatorContextStore(unrelated).project("planner").preferences == ()

    local = append_preference(
        second, kind="workflow", value="Use this project's pinned tool.", expected_revision=0
    )
    assert OperatorContextStore(second).project("planner").preferences == (local,)
    append_revoke(second, local.revision, reason="remove project override", expected_revision=1)
    assert OperatorContextStore(second).project("planner").preferences == (shared,)
    append_revoke(
        first,
        shared.revision,
        reason="withdraw shared preference",
        expected_revision=1,
        scope="global",
        global_root=user,
    )
    assert OperatorContextStore(first).project("planner").preferences == ()
    assert OperatorContextStore(second).project("planner").preferences == ()


def test_real_front_door_intake_writes_to_the_same_shared_store_later_roles_read(
    tmp_path: Path,
) -> None:
    from argus.life.memory import MemoryBundle
    from argus.manager.config_intent import _front_door_classify

    memory = MemoryBundle.for_cwd(global_root=tmp_path, fingerprint="s-writer")

    class Manager:
        def classify_front_door(self, _text, *, intake_sink, **_kwargs):
            intake_sink(
                {
                    "kind": "preference",
                    "scope": "global",
                    "preference_kind": "workflow",
                    "preference_value": "Use evidence before repeating an experiment.",
                }
            )
            return None, None, "simple"

    result = _front_door_classify(
        memory,
        "Remember this preference across my projects.",
        {},
        ensure_runner=lambda *_args: SimpleNamespace(manager=Manager()),
    )
    assert result == (None, None, "simple")
    assert OperatorContextStore(memory.project_root).revision == 0
    another = tmp_path / "projects/s-reader"
    prompt, _ = build_operator_context_block("planner", another)
    assert "Use evidence before repeating an experiment." in prompt
    assert "global preference ledger" in prompt


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


def test_replacing_then_revoking_a_preference_never_resurrects_its_previous_value(
    tmp_path: Path,
) -> None:
    store = OperatorContextStore(tmp_path, max_records=3)
    first = store.append(preference("obsolete setting"), expected_revision=0)
    latest = store.append(preference("current setting"), expected_revision=1)
    append_revoke(tmp_path, latest.revision, reason="withdraw setting", expected_revision=2)
    assert store.project("planner").preferences == ()
    store.compact()
    (tmp_path / "operator_context.json").unlink()
    assert OperatorContextStore(tmp_path).project("planner").preferences == ()
    assert all(record.revision != first.revision for record in store.records())


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
    (tmp_path / "operator_context.json").unlink()

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


def test_valid_new_tail_continues_checkpoint_revision_without_replaying_history(
    tmp_path: Path,
) -> None:
    store = OperatorContextStore(tmp_path)
    store.append(directive("Consumed.", lifetime="once"), expected_revision=0)
    store.project("engineer")
    store.compact()
    with store.ledger_path.open("a") as handle:
        handle.write(json.dumps(asdict(directive("New tail input.", revision=2))) + "\n")
    restored = OperatorContextStore(tmp_path)
    assert restored.revision == 2
    assert [record.text for record in restored.project("engineer").directives] == [
        "New tail input."
    ]


def test_consumption_survives_cache_write_failure_and_stale_cache_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    from argus.core import operator_context

    store = OperatorContextStore(tmp_path)
    item = store.append(directive("Run exactly once.", lifetime="once"), expected_revision=0)
    stale_cache = (tmp_path / "operator_context.json").read_bytes()

    def fail_cache(*_args, **_kwargs):
        raise OSError("cache unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(operator_context, "_write_cache", fail_cache)
        store.settle_once(item.revision)
    (tmp_path / "operator_context.json").write_bytes(stale_cache)
    assert OperatorContextStore(tmp_path).project("engineer").directives == ()


def test_legacy_once_without_consumption_evidence_is_not_replayed(tmp_path: Path) -> None:
    path = tmp_path / "operator_context.jsonl"
    path.write_text(json.dumps(asdict(directive("Old one-shot.", lifetime="once"))) + "\n")
    store = OperatorContextStore(tmp_path)
    assert store.project("engineer", consume_once=False).directives == ()
    assert json.loads(path.read_text().splitlines()[0])["format"] == "operator-context-v2"
    (tmp_path / "operator_context.json").unlink()
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


def test_compaction_failure_leaves_the_previous_canonical_ledger_intact(
    tmp_path: Path, monkeypatch
) -> None:
    from argus.core import operator_context_storage as storage

    store = OperatorContextStore(tmp_path)
    store.append(directive("Still authoritative."), expected_revision=0)
    source = store.ledger_path.read_bytes()
    original_replace = storage.os.replace

    def fail_replace(source, destination):
        if Path(destination) == store.ledger_path:
            raise OSError("checkpoint replace failed")
        return original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_replace)
        with pytest.raises(OSError, match="checkpoint"):
            store.compact()
    assert store.ledger_path.read_bytes() == source
    assert (
        OperatorContextStore(tmp_path).project("engineer").directives[0].text
        == "Still authoritative."
    )


def test_active_authority_is_not_evicted_when_capacity_is_full(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path, max_records=2)
    for index in range(2):
        store.append(directive(f"Active constraint {index}."), expected_revision=index)
    source = store.ledger_path.read_bytes()
    with pytest.raises(OperatorContextCapacityError):
        store.append(directive("Exceeds capacity."), expected_revision=2)
    assert store.ledger_path.read_bytes() == source
    assert len(store.project("engineer").directives) == 2


@pytest.mark.parametrize("delete_cache", [False, True])
def test_missing_canonical_source_cannot_revive_withdrawn_legacy_steering(
    tmp_path: Path,
    delete_cache: bool,
) -> None:
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
    if delete_cache:
        (tmp_path / "operator_context.json").unlink()
    with pytest.raises(ValueError, match="source is missing"):
        OperatorContextStore(tmp_path).project("engineer")
    assert not store.ledger_path.exists()


def test_missing_canonical_source_cannot_reset_revision_or_consume_new_input_as_old(
    tmp_path: Path,
) -> None:
    store = OperatorContextStore(tmp_path)
    store.append(directive("Consumed original.", lifetime="once"), expected_revision=0)
    store.project("engineer")
    store.ledger_path.unlink()
    with pytest.raises(ValueError, match="source is missing"):
        OperatorContextStore(tmp_path).append(
            directive("A new action.", lifetime="once"), expected_revision=0
        )
    with pytest.raises(ValueError, match="source is missing"):
        _ = OperatorContextStore(tmp_path).revision


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


def test_real_planner_and_engineer_prompts_read_project_and_fresh_shared_preferences(
    tmp_path: Path,
) -> None:
    from argus.life.memory import BacklogItem, MemoryBundle
    from argus.life.supervisor._mission_execution_runtime import MissionExecutionRuntimeMixin
    from argus.roles.prompts.planner import build_bounded_single_task_prompt

    memory = MemoryBundle.for_cwd(global_root=tmp_path, fingerprint="s-scope")
    global_preference(memory.project_root, "Initial shared preference.")
    store = OperatorContextStore(memory.project_root)
    local = store.append(directive("Project-only instruction."), expected_revision=0)
    host = SimpleNamespace(
        memory=memory,
        config=SimpleNamespace(runtime_context=""),
        _render_backlog_item_metadata=lambda _item: "",
    )
    item = BacklogItem.new(title="task", objective="Carry out the request.")
    before = MissionExecutionRuntimeMixin._build_mission_prelude(host, item)
    assert "Initial shared preference." in before
    assert "Project-only instruction." in before
    global_preference(memory.project_root, "Updated shared preference.")
    OperatorContextStore(tmp_path).compact()
    for prompt in (
        MissionExecutionRuntimeMixin._build_mission_prelude(host, item),
        build_bounded_single_task_prompt(item.objective, state_root=memory.project_root),
    ):
        assert "Updated shared preference." in prompt
        assert "Initial shared preference." not in prompt
        assert "Project-only instruction." in prompt
    assert store.revision == local.revision


@pytest.mark.parametrize("existing_operator_ledger", [False, True])
def test_current_manager_supervision_reaches_engineer_without_becoming_operator_authority(
    tmp_path: Path,
    existing_operator_ledger: bool,
) -> None:
    from argus.manager.directive import set_active_manager_directive

    store = OperatorContextStore(tmp_path)
    if existing_operator_ledger:
        store.append(directive("Keep the operator's acceptance."), expected_revision=0)
    revision = store.revision
    set_active_manager_directive(
        tmp_path,
        "Inspect the failing boundary before another experiment.",
        source="manager.supervision:observation-1",
        scope_objective="",
    )
    block, _ = build_operator_context_block("engineer", tmp_path)
    assert "Manager direction (project advisory)" in block
    assert "Inspect the failing boundary before another experiment." in block
    assert "not a new operator instruction or authorization" in block
    assert store.revision == revision
    assert not any("failing boundary" in getattr(record, "text", "") for record in store.records())
    assert not (tmp_path / "STEERING.jsonl").exists()
