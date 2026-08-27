from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.operator_context import (
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


def test_pending_answer_survives_failed_interpretation_for_engineer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.manager import front_door
    from argus_skill.webapi.manager_pending_question import (
        _resolve_pending_question_with_manager,
    )

    monkeypatch.setattr(
        front_door,
        "manager_triage",
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

    result = _resolve_pending_question_with_manager(
        SimpleNamespace(project_root=tmp_path),
        item,
        answer,
        {},
    )
    block, revision = build_operator_context_block("engineer", tmp_path)

    assert result["answer_preserved"] is True
    assert revision == 1
    assert answer in block
    ledger = (tmp_path / "operator_context.jsonl").read_text(encoding="utf-8")
    assert ledger.index(answer) >= 0


def test_credential_import_keeps_raw_key_out_of_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.tools import capability_vault

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
