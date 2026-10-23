from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.operator_context import (
    DirectiveRecord,
    OperatorContextStore,
    StaleOperatorContextWrite,
    append_directive,
    append_preference,
    append_revoke,
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


def test_stale_write_is_rejected(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    store.append(_directive("first"), expected_revision=0)

    with pytest.raises(StaleOperatorContextWrite, match="expected 0, current 1"):
        store.append(_directive("stale"), expected_revision=0)


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


def test_acknowledgement_settles_only_handled_role_once_input(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    first = append_directive(
        tmp_path, "first instruction", lifetime="once", expected_revision=0,
    )
    second = append_directive(
        tmp_path, "arrived during planning", lifetime="once",
        expected_revision=first.revision,
    )
    store.acknowledge("planner", first.revision)
    projection = OperatorContextStore(tmp_path).project("planner", consume_once=False)
    assert [record.revision for record in projection.directives] == [second.revision]
    assert store.acknowledged_revision("planner") == first.revision


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


def test_projection_deduplicates_existing_standing_history_without_rewriting(
    tmp_path: Path,
) -> None:
    store = OperatorContextStore(tmp_path)
    first = _directive("Publish a verified portfolio report.", scope="global")
    records = [
        first,
        replace(_directive("Preserve the public API."), revision=2),
        replace(first, revision=3, source="scheduled.reminder"),
    ]
    store.ledger_path.write_text(
        "".join(json.dumps(asdict(record)) + "\n" for record in records),
        encoding="utf-8",
    )
    original_ledger = store.ledger_path.read_bytes()

    projection = store.project("engineer")
    block, revision = build_operator_context_block("engineer", tmp_path)

    assert [record.revision for record in projection.directives] == [2, 3]
    assert block.count(first.text) == 1
    assert revision == 3
    assert store.records() == records
    assert store.ledger_path.read_bytes() == original_ledger

    # Tombstones still address exact revisions: withdrawing the latest copy
    # exposes the older active one until that revision is withdrawn as well.
    append_revoke(tmp_path, 3, reason="withdraw reminder", expected_revision=3)
    assert [record.revision for record in store.project("engineer").directives] == [2, 1]
    append_revoke(tmp_path, 1, reason="withdraw directive", expected_revision=4)
    assert [record.revision for record in store.project("engineer").directives] == [2]
    renewed = append_directive(
        tmp_path, first.text, scope="global", expected_revision=5
    )
    assert renewed.revision == 6
    assert [record.revision for record in store.project("engineer").directives] == [2, 6]
    assert store.ledger_path.read_bytes().startswith(original_ledger)


@pytest.mark.parametrize(
    ("first_options", "second_options"),
    [
        ({"scope": "global"}, {"scope": "project"}),
        ({"scope": "project"}, {"scope": "mission"}),
        ({"applies_to_roles": "all"}, {"applies_to_roles": ("engineer",)}),
        (
            {"applies_to_roles": ("engineer", "planner")},
            {"applies_to_roles": ("engineer", "reviewer")},
        ),
    ],
)
def test_standing_identity_preserves_scope_and_role_boundaries(
    tmp_path: Path, first_options: dict, second_options: dict
) -> None:
    append_directive(tmp_path, "Respect this boundary.", expected_revision=0, **first_options)
    second = append_directive(
        tmp_path, "Respect this boundary.", expected_revision=1, **second_options
    )

    assert second.revision == 2
    assert len(OperatorContextStore(tmp_path).project("engineer").directives) == 2


def test_standing_source_updates_retain_audit_and_manager_classification(
    tmp_path: Path,
) -> None:
    append_directive(tmp_path, "Use judgment.", source="operator", expected_revision=0)
    second = append_directive(
        tmp_path, "Use judgment.", source="scheduler", expected_revision=1
    )
    assert second.revision == 2
    assert len(OperatorContextStore(tmp_path).project("manager").directives) == 1

    append_directive(
        tmp_path,
        "Use judgment.",
        source="operator.answer.standing_sounding",
        expected_revision=2,
    )
    block, revision = build_operator_context_block("manager", tmp_path)

    assert revision == 3
    assert block.count("Use judgment.") == 2
    assert "standing-sounding answer: classify its durable scope" in block
    assert [record.source for record in OperatorContextStore(tmp_path).records()] == [
        "operator", "scheduler", "operator.answer.standing_sounding"
    ]


@pytest.mark.parametrize("lifetime", ["once", "bounded_increment"])
def test_identical_actions_remain_independent_of_standing_dedup(
    tmp_path: Path, lifetime: str
) -> None:
    store = OperatorContextStore(tmp_path)
    for revision in range(2):
        append_directive(
            tmp_path,
            "Run the measurement.",
            scope="mission",
            lifetime=lifetime,
            mission_id="mission-a",
            expected_revision=revision,
        )
    append_directive(
        tmp_path, "Run the measurement.", scope="mission", expected_revision=2
    )

    first = store.project("engineer", mission_id="mission-a")
    assert [record.revision for record in first.directives] == [3, 2, 1]
    assert len(store.records()) == 3
    if lifetime == "once":
        assert [record.revision for record in store.project("engineer").directives] == [3]
    else:
        other_mission = store.project("engineer", mission_id="mission-b")
        assert [record.revision for record in other_mission.directives] == [3]


def test_revoke_tombstones_target_revision(tmp_path: Path) -> None:
    first = append_directive(tmp_path, "retire me", expected_revision=0)
    append_directive(tmp_path, "keep me", expected_revision=1)
    append_revoke(
        tmp_path,
        first.revision,
        reason="operator withdrew it",
        expected_revision=2,
    )

    projection = OperatorContextStore(tmp_path).project("engineer")

    assert [record.text for record in projection.directives] == ["keep me"]


def test_once_directive_is_consumed_by_first_projection(tmp_path: Path) -> None:
    append_directive(
        tmp_path,
        "use this answer once",
        lifetime="once",
        scope="mission",
        expected_revision=0,
    )

    first = OperatorContextStore(tmp_path).project("engineer")
    second = OperatorContextStore(tmp_path).project("engineer")

    assert [record.text for record in first.directives] == ["use this answer once"]
    assert second.directives == ()


def test_bounded_directive_expires_with_its_mission(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    store.append(
        _directive("only this increment", scope="mission", lifetime="bounded_increment"),
        expected_revision=0,
        mission_id="mission-a",
    )

    assert store.project("engineer", mission_id="mission-a").directives
    assert store.project("engineer", mission_id="mission-b").directives == ()


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


def test_planner_block_offers_only_idle_instructions_for_settlement(tmp_path: Path) -> None:
    from argus.core.operator_context import build_settleable_planner_context_block

    store = OperatorContextStore(tmp_path)
    standing = store.append(_directive("keep this"), expected_revision=0)
    idle = store.append(_idle_instruction("evaluate the parser"), expected_revision=1)

    block, revision, offered = build_settleable_planner_context_block(tmp_path)
    assert revision == idle.revision
    assert offered == frozenset({idle.revision})
    assert f"[mission; revision {idle.revision}]: evaluate the parser" in block
    assert "[project]: keep this" in block
    assert f"revision {standing.revision}]" not in block
    assert "`settle_instructions`" in block

    for role in ("planner", "engineer"):
        plain, _ = build_operator_context_block(role, tmp_path, consume_once=False)
        assert "settle_instructions" not in plain
        assert f"revision {idle.revision}]" not in plain

    store.acknowledge("planner", idle.revision, settle=[idle.revision])
    block, _, offered = build_settleable_planner_context_block(tmp_path)
    assert offered == frozenset()
    assert "evaluate the parser" not in block
    assert "settle_instructions" not in block


def test_repeated_settlement_reports_only_new_revisions(tmp_path: Path) -> None:
    store = OperatorContextStore(tmp_path)
    idle = store.append(_idle_instruction("evaluate the parser"), expected_revision=0)

    assert store.acknowledge("planner", idle.revision, settle=[idle.revision]) == (idle.revision,)
    assert store.acknowledge("planner", idle.revision, settle=[idle.revision]) == ()


def test_projection_precedence_and_live_turn(tmp_path: Path) -> None:
    append_preference(
        tmp_path,
        kind="workflow",
        value="global choice",
        scope="global",
        expected_revision=0,
    )
    append_preference(
        tmp_path,
        kind="workflow",
        value="project choice",
        scope="project",
        expected_revision=1,
    )
    append_directive(
        tmp_path,
        "mission constraint",
        scope="mission",
        expected_revision=2,
    )

    projection = OperatorContextStore(tmp_path).project("planner")
    block, revision = build_operator_context_block(
        "planner", tmp_path, live_turn="live correction", consume_once=False
    )

    assert [record.value for record in projection.preferences] == ["project choice"]
    assert block.index("live correction") < block.index("mission constraint")
    assert revision == 3


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


def test_unclassified_pending_message_does_not_become_an_engineer_directive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.core.transcript import append_turn, read_turns
    from argus.manager import front_door
    from argus.webapi.manager_pending_question import (
        _resolve_pending_question_with_manager,
    )

    monkeypatch.setattr(
        front_door,
        "manager_judgment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("401 Missing bearer")
        ),
    )
    item = SimpleNamespace(
        id="blocked-item",
        title="Blocked item",
        objective="Continue after operator authorization.",
        pending_question="May the technical route proceed?",
    )
    answer = "Always decide reversible infrastructure choices without asking me."
    append_turn(tmp_path, "operator", answer)

    result = _resolve_pending_question_with_manager(
        SimpleNamespace(project_root=tmp_path),
        item,
        answer,
        {},
    )
    block, revision = build_operator_context_block("engineer", tmp_path)

    assert result["answer_preserved"] is False
    assert revision == 0
    assert answer not in block
    assert not (tmp_path / "operator_context.jsonl").exists()
    assert read_turns(tmp_path)[0]["text"] == answer


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
