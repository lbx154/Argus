from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from argus.core.operator_context import (
    DirectiveRecord,
    OperatorContextStore,
    StaleOperatorContextWrite,
    append_directive,
    build_operator_context_block,
    import_deterministic_credential,
)


def _directive(text: str, *, scope: str = "project", lifetime: str = "standing"):
    return DirectiveRecord(
        text=text,
        scope=scope,
        applies_to_roles="all",
        lifetime=lifetime,
        source="test",
        revision=1,
        created_at="2026-01-01T00:00:00Z",
    )


def test_role_acknowledgement_survives_projection_and_new_input(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    first = store.append(_directive("first"), expected_revision=0)
    assert store.acknowledged_revision("planner") == 0
    store.project("planner", consume_once=False)
    assert store.acknowledged_revision("planner") == 0
    store.acknowledge("planner", first.revision)
    second = store.append(_directive("second"), expected_revision=first.revision)
    store.project("engineer")
    store = OperatorContextStore(tmp_path)
    assert store.acknowledged_revision("planner") == first.revision < second.revision
    assert store.acknowledged_revision("engineer") == 0
    store.acknowledge("planner", 0)
    assert store.acknowledged_revision("planner") == first.revision
    with pytest.raises(ValueError, match="outside"):
        store.acknowledge("planner", second.revision + 1)


def test_identical_standing_retry_is_idempotent(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    first = append_directive(
        tmp_path,
        "Keep the public API stable.",
        applies_to_roles=("engineer", "reviewer"),
        expected_revision=0,
    )
    original_ledger = store.ledger_path.read_bytes()

    for _ in range(20):
        repeated = append_directive(
            tmp_path,
            "Keep the public API stable.",
            applies_to_roles=("reviewer", "engineer"),
            expected_revision=store.revision,
        )
        assert repeated == first

    assert store.revision == 1
    assert store.ledger_path.read_bytes() == original_ledger
    with pytest.raises(StaleOperatorContextWrite, match="expected 0, current 1"):
        append_directive(tmp_path, first.text, expected_revision=0)


def test_standing_reassertion_after_update_keeps_new_revision(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    append_directive(tmp_path, "Use route A.", expected_revision=0)
    append_directive(tmp_path, "Use route B.", expected_revision=1)
    latest = append_directive(tmp_path, "Use route A.", expected_revision=2)

    projection = store.project("engineer")

    assert latest.revision == 3
    assert [record.revision for record in store.records()] == [1, 2, 3]
    assert [(record.text, record.revision) for record in projection.directives] == [
        ("Use route A.", 3),
        ("Use route B.", 2),
    ]


def _idle_instruction(text: str) -> DirectiveRecord:
    return _directive(text, scope="mission", lifetime="bounded_increment")


def _visible(root: Path, role: str = "planner") -> list[int]:
    projection = OperatorContextStore(root).project(role, consume_once=False)
    return [record.revision for record in projection.directives]


def test_idle_bounded_instruction_settles_only_when_planner_declares_it(
    tmp_path: Path,
) -> None:
    store = OperatorContextStore(tmp_path)
    standing = store.append(_directive("keep this"), expected_revision=0)
    first = store.append(_idle_instruction("evaluate the parser"), expected_revision=1)
    second = store.append(_idle_instruction("profile the loader"), expected_revision=2)

    # A checkpoint that names nothing (a wait, or work for something else)
    # leaves every idle instruction in place.
    assert store.acknowledge("planner", second.revision) == ()
    assert _visible(tmp_path) == [second.revision, first.revision, standing.revision]

    assert store.acknowledge("planner", second.revision, settle=[first.revision]) == (
        first.revision,
    )
    for role in ("planner", "manager"):
        assert _visible(tmp_path, role) == [second.revision, standing.revision]


def test_legacy_idle_subagent_report_no_longer_rides_along(tmp_path: Path) -> None:
    # Reports are ephemeral host input since 2026-09-30; an idle one persisted
    # by older code is revoked once, for every role. A report bound to a running
    # mission, an ordinary idle instruction, and a report the operator pasted
    # (with an instruction after it) once reports were ephemeral are unaffected.
    store = OperatorContextStore(tmp_path)
    report = "## Subagent Report: job-1\n**Event**: COMPLETED\n\n- **Description**: background task"
    legacy = store.append(_idle_instruction(report), expected_revision=0)
    bound = store.append(
        _idle_instruction(report), expected_revision=1, mission_id="mission-a"
    )
    instruction = store.append(_idle_instruction("evaluate the parser"), expected_revision=2)
    pasted = store.append(
        replace(
            _idle_instruction(report + "\n\nRerun it with seed 3."),
            created_at="2026-10-01T00:00:00Z",
        ),
        expected_revision=3,
    )

    for role in ("planner", "manager"):
        assert _visible(tmp_path, role) == [pasted.revision, instruction.revision]
    from argus.core.operator_context import build_settleable_planner_context_block

    _text, _revision, offered = build_settleable_planner_context_block(tmp_path)
    assert offered == frozenset({instruction.revision, pasted.revision})
    assert [
        record.revision
        for record in store.project("engineer", mission_id="mission-a").directives
    ] == [bound.revision]
    assert legacy.revision not in _visible(tmp_path)
    assert legacy.revision in OperatorContextStore(tmp_path)._load().state["revoked"]


def test_settlement_ignores_instructions_that_are_not_idle_planner_input(
    tmp_path: Path,
) -> None:
    store = OperatorContextStore(tmp_path)
    standing = store.append(_directive("keep this"), expected_revision=0)
    bound = store.append(
        _idle_instruction("only mission-a"), expected_revision=1, mission_id="mission-a"
    )
    engineer_only = append_directive(
        tmp_path, "engineer only", scope="mission", lifetime="bounded_increment",
        applies_to_roles=("engineer",), expected_revision=2,
    )
    later = store.append(_idle_instruction("arrived later"), expected_revision=3)

    settled = store.acknowledge(
        "planner",
        engineer_only.revision,
        settle=[standing.revision, bound.revision, engineer_only.revision, later.revision, 99],
    )

    assert settled == ()
    assert _visible(tmp_path) == [later.revision, standing.revision]
    assert [record.revision for record in store.project("engineer").directives] == [
        later.revision, engineer_only.revision, standing.revision,
    ]
    in_mission = store.project("engineer", mission_id="mission-a")
    assert bound.revision in [record.revision for record in in_mission.directives]


def test_only_planner_settles_idle_bounded_instructions(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    idle = store.append(_idle_instruction("evaluate the parser"), expected_revision=0)

    with pytest.raises(ValueError, match="only Planner"):
        store.acknowledge("engineer", idle.revision, settle=[idle.revision])
    assert _visible(tmp_path) == [idle.revision]


def test_compaction_drops_settled_idle_instruction_and_keeps_the_rest(
    tmp_path: Path,
) -> None:
    store = OperatorContextStore(tmp_path)
    keep = store.append(_directive("keep this"), expected_revision=0)
    settled = store.append(_idle_instruction("evaluate the parser"), expected_revision=1)
    pending = store.append(_idle_instruction("profile the loader"), expected_revision=2)
    store.acknowledge("planner", pending.revision, settle=[settled.revision])

    store.compact()
    (tmp_path / "operator_context.json").unlink()

    restored = OperatorContextStore(tmp_path)
    assert {record.revision for record in restored.records()} == {keep.revision, pending.revision}
    assert _visible(tmp_path) == [pending.revision, keep.revision]


def test_corrupt_settlement_state_is_rejected_on_load(tmp_path: Path) -> None:
    from argus.core.operator_context_storage import read_document, write_checkpoint

    store = OperatorContextStore(tmp_path)
    idle = store.append(_idle_instruction("evaluate the parser"), expected_revision=0)
    store.compact()
    doc = read_document(store.ledger_path, legacy_state={})
    doc.state["settled_idle_bounded"] = [idle.revision + 5]
    write_checkpoint(store.ledger_path, doc)

    with pytest.raises(ValueError, match="invalid settled"):
        read_document(store.ledger_path, legacy_state={})


def test_read_adapter_serves_legacy_steering(tmp_path: Path) -> None:
    (tmp_path / "STEERING.jsonl").write_text(
        json.dumps({
            "id": "legacy-1",
            "kind": "directive",
            "source": "operator.inbox",
            "text": "preserve the public API",
            "timestamp": "2026-01-01T00:00:00Z",
            "version": 1,
        })
        + "\n",
        encoding="utf-8",
    )

    projection = OperatorContextStore(tmp_path).project("reviewer")

    assert [record.text for record in projection.directives] == [
        "preserve the public API"
    ]
    assert not (tmp_path / "operator_context.jsonl").exists()
    append_directive(
        tmp_path,
        "new directive",
        expected_revision=projection.revision,
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "operator_context.jsonl").read_text().splitlines()
    ]
    assert [row["revision"] for row in rows] == [1, 2]


def test_credential_import_keeps_raw_key_out_of_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.tools import capability_vault

    vault = tmp_path / "capabilities" / "model_api.json"
    monkeypatch.setattr(
        capability_vault,
        "bootstrap_model_api_vault",
        lambda _environment: vault,
    )
    raw_key = "sk-abcdefghijklmnopqrstuvwxyz123456"

    safe_text, capability = import_deterministic_credential(
        tmp_path,
        f"OPENAI_API_KEY={raw_key}",
        global_root=tmp_path,
    )
    block, _revision = build_operator_context_block("engineer", tmp_path)
    repeated_safe_text, repeated_capability = import_deterministic_credential(
        tmp_path, safe_text, global_root=tmp_path
    )

    assert capability is not None
    assert raw_key not in safe_text
    assert raw_key not in (tmp_path / "operator_context.jsonl").read_text()
    assert raw_key not in block
    assert "handle=text" in block
    assert repeated_safe_text == safe_text
    assert repeated_capability is None


def test_input_for_other_roles_is_not_unhandled_planner_input(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    store.acknowledge("planner", 0)
    manager_only = append_directive(
        tmp_path, "manager only preference", applies_to_roles=("manager",), expected_revision=0,
    )

    assert not store.has_unhandled_input("planner")
    assert store.has_unhandled_input("manager")
    # Input for every role still counts.
    append_directive(tmp_path, "everyone", expected_revision=manager_only.revision)
    assert store.has_unhandled_input("planner")
