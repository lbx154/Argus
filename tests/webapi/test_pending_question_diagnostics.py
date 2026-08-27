from __future__ import annotations

import json
from types import SimpleNamespace

from argus_skill.webapi.manager_pending_question import (
    _resolve_pending_question_with_manager,
)


def test_pending_question_backend_failure_preserves_answer_and_cause(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.manager import front_door

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
        pending_question="May the repair proceed?",
    )
    mem = SimpleNamespace(project_root=tmp_path)

    result = _resolve_pending_question_with_manager(
        mem,
        item,
        "Yes, authorize the requested repair.",
        {},
    )

    assert result["phase"] == "backend"
    assert result["cause"] == "401 Missing bearer"
    assert result["backend_error"] == "401 Missing bearer"
    assert result["attempts"] == 1
    assert result["answer_preserved"] is True
    assert "answer is preserved" in result["error"]
    assert "interpretation will be retried" in result["error"]
    assert "not rejected" in result["error"]

    event = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    assert event["type"] == "life.manager.intent.failed"
    assert event["phase"] == "backend"
    assert event["cause"] == "401 Missing bearer"
    assert event["answer_preserved"] is True


def test_pending_question_contract_failure_carries_reply_snippet(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.manager import front_door

    reply = "IS_ANSWER=maybe\nRESOLVED=true\nDECISION=continue"
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: reply,
    )

    result = _resolve_pending_question_with_manager(
        SimpleNamespace(project_root=tmp_path),
        SimpleNamespace(
            id="blocked-item",
            title="Blocked item",
            objective="Continue after an answer.",
            pending_question="Proceed?",
        ),
        "Yes.",
        {},
    )

    assert result["phase"] == "contract"
    assert result["contract_field"] == "pending_question_decision"
    assert result["model_reply_snippet"] == reply.replace("\n", " ")
