from __future__ import annotations

import json
import os
from types import SimpleNamespace

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.agent_cli.models import AgentRunResult
from argus_skill.core.models import RunnerOptions
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.manager._session_ops import _ManagerSession
from argus_skill.webapi.manager_pending_question import (
    _resolve_pending_question_with_manager,
)


def _relay_backend(tmp_path, monkeypatch) -> AgentCliBackend:
    from argus_skill.provider_integrations import authorization_retry

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model_provider = "copilot_relay"\n'
        '[model_providers.copilot_relay]\n'
        'base_url = "http://127.0.0.1:41419/v1"\n'
        'env_key = "COPILOT_RELAY_TOKEN"\n',
        encoding="utf-8",
    )
    relay_dir = tmp_path / ".config" / "copilot-codex-relay"
    relay_dir.mkdir(parents=True)
    (relay_dir / "env").write_text(
        "COPILOT_RELAY_TOKEN=fresh-relay-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        authorization_retry.Path,
        "home",
        classmethod(lambda cls: tmp_path),
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("COPILOT_RELAY_TOKEN", "rejected-relay-token")
    return AgentCliBackend(backend="codex")


def _raw_result(*, message: str = "", error: str = "") -> AgentRunResult:
    return AgentRunResult(
        command=["codex", "exec"],
        exit_code=1 if error else 0,
        agent_messages=[message] if message else [],
        stderr_lines=[error] if error else [],
        turn_completed=not error,
        turn_failed=bool(error),
        fatal_error=error or None,
    )


class _PendingManagerRunner:
    def __init__(self, backend: AgentCliBackend, project_root) -> None:
        self.backend = _ManagerSession(backend, project_root)

    def chat_reply_if_conversational(self, *, objective, sink, **_kwargs) -> bool:
        result = self.backend.run_exec(
            prompt=objective,
            options=RunnerOptions(skip_git_repo_check=True),
            run_label="manager-pending-answer",
        )
        sink.handle_event({
            "type": "round.main.completed",
            "last_message": result.last_agent_message,
        })
        return True


def _pending_memory(tmp_path):
    life = LifeMemory.open(tmp_path)
    item = BacklogItem.new(
        item_id="blocked-item",
        title="Blocked item",
        objective="Continue after operator authorization.",
    )
    item.pending_question = "May the repair proceed?"
    life.backlog.add(item)
    return SimpleNamespace(project_root=tmp_path, backlog=life.backlog), item


def test_pending_question_401_replay_settles_answer_after_persistence(
    tmp_path,
    monkeypatch,
) -> None:
    backend = _relay_backend(tmp_path, monkeypatch)
    calls: list[str] = []

    def run_exec(**_kwargs) -> AgentRunResult:
        calls.append(os.environ["COPILOT_RELAY_TOKEN"])
        if len(calls) == 1:
            return _raw_result(error="401 Missing bearer")
        return _raw_result(
            message=json.dumps({
                "is_answer": True,
                "resolved": True,
                "decision": "Proceed under the operator's standing authorization.",
                "reply": "Proceeding.",
            })
        )

    monkeypatch.setattr(backend._runner, "run_exec", run_exec)
    mem, item = _pending_memory(tmp_path)
    result = _resolve_pending_question_with_manager(
        mem,
        item,
        "Yes, authorize the requested repair.",
        {
            "manager_runner": _PendingManagerRunner(backend, tmp_path),
            "manager_runner_workdir": str(tmp_path),
        },
    )

    assert result["resolved"] is True
    assert calls == ["rejected-relay-token", "fresh-relay-token"]
    rows = mem.backlog.history()
    blocked = next(row for row in rows if row.id == item.id)
    assert blocked.pending_question == ""
    assert len([row for row in rows if row.id != item.id]) == 1
    ledger = [
        json.loads(line)
        for line in (tmp_path / "operator_context.jsonl").read_text().splitlines()
    ]
    assert [row["text"] for row in ledger] == [
        "Yes, authorize the requested repair."
    ]
    projection = json.loads((tmp_path / "operator_context.json").read_text())
    assert projection["consumed_once"] == [1]


def test_pending_question_backend_failure_preserves_answer_and_cause(
    tmp_path,
    monkeypatch,
) -> None:
    backend = _relay_backend(tmp_path, monkeypatch)
    calls = 0

    def run_exec(**_kwargs) -> AgentRunResult:
        nonlocal calls
        calls += 1
        return _raw_result(error="401 Missing bearer")

    monkeypatch.setattr(backend._runner, "run_exec", run_exec)
    mem, item = _pending_memory(tmp_path)

    result = _resolve_pending_question_with_manager(
        mem,
        item,
        "Yes, authorize the requested repair.",
        {
            "manager_runner": _PendingManagerRunner(backend, tmp_path),
            "manager_runner_workdir": str(tmp_path),
        },
    )

    assert calls == 2
    assert result["phase"] == "backend"
    assert result["cause"] == "401 Missing bearer"
    assert result["backend_error"] == "401 Missing bearer"
    assert result["attempts"] == 2
    assert result["login_required"] is True
    assert result["answer_preserved"] is True
    assert "login_required" in result["error"]
    assert "answer is preserved" in result["error"]
    assert "interpretation will be retried" in result["error"]
    assert "not rejected" in result["error"]

    rows = mem.backlog.history()
    assert len(rows) == 1
    assert rows[0].pending_question == "May the repair proceed?"
    ledger = [
        json.loads(line)
        for line in (tmp_path / "operator_context.jsonl").read_text().splitlines()
    ]
    assert [row["text"] for row in ledger] == [
        "Yes, authorize the requested repair."
    ]
    projection = json.loads((tmp_path / "operator_context.json").read_text())
    assert projection["consumed_once"] == []

    event = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    assert event["type"] == "life.manager.intent.failed"
    assert event["phase"] == "backend"
    assert event["cause"] == "401 Missing bearer"
    assert event["attempts"] == 2
    assert event["login_required"] is True
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
