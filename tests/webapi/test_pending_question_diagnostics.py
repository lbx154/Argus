from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.agent_cli.models import AgentRunResult
from argus.core.operator_context import OperatorContextStore
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import Manager
from argus.webapi.manager_pending_question import (
    _resolve_pending_question_with_manager,
)


def _relay_backend(tmp_path, monkeypatch) -> AgentCliBackend:
    from argus.provider_integrations import authorization_retry

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
    """The front-door runner as the pending-answer check sees it: its Manager."""

    def __init__(self, backend: AgentCliBackend, project_root) -> None:
        self.manager = Manager(project_root, runner=backend)


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
        assert not (tmp_path / "operator_context.jsonl").exists()
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
    (resumed,) = mem.backlog.history()
    assert resumed.id == item.id and resumed.status == "pending"
    assert resumed.pending_question == ""
    ledger = OperatorContextStore(tmp_path).records()
    assert [row.text for row in ledger] == [
        "Yes, authorize the requested repair."
    ]
    projection = json.loads((tmp_path / "operator_context.json").read_text())
    assert projection["consumed_once"] == [1]


def test_pending_question_backend_failure_preserves_cause_without_answer_directive(
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
    assert result["answer_preserved"] is False
    assert "login_required" in result["error"]
    assert "not been classified" in result["error"]
    assert "Retry" in result["error"]

    rows = mem.backlog.history()
    assert len(rows) == 1
    assert rows[0].pending_question == "May the repair proceed?"
    assert not (tmp_path / "operator_context.jsonl").exists()

    event = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    assert event["type"] == "life.manager.intent.failed"
    assert event["phase"] == "backend"
    assert event["cause"] == "401 Missing bearer"
    assert event["attempts"] == 2
    assert event["login_required"] is True
    assert event["answer_preserved"] is False


def test_pending_question_contract_failure_carries_reply_snippet(
    tmp_path,
    monkeypatch,
) -> None:
    from argus.manager import front_door

    reply = "IS_ANSWER=maybe\nRESOLVED=true\nDECISION=continue"
    monkeypatch.setattr(
        front_door,
        "manager_judgment",
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
    assert result["answer_preserved"] is False
    assert not (tmp_path / "operator_context.jsonl").exists()


@pytest.mark.parametrize("is_answer", [False, True])
def test_answer_directive_is_written_only_after_positive_classification(tmp_path, monkeypatch, is_answer):
    from argus.manager import front_door

    mem, item = _pending_memory(tmp_path)
    answer = "Use one of the available cards."

    def classify(*_args, **_kwargs):
        assert not (tmp_path / "operator_context.jsonl").exists()
        return json.dumps({"is_answer": is_answer, "resolved": False, "decision": "", "reply": "Please specify the card."})

    monkeypatch.setattr(front_door, "manager_judgment", classify)
    result = _resolve_pending_question_with_manager(mem, item, answer, {})

    assert result["answer_intent"] is is_answer and result["resolved"] is False
    if is_answer:
        ledger = OperatorContextStore(tmp_path).records()
        assert len(ledger) == 1 and ledger[0].text == answer
        assert ledger[0].source == "operator.pending_answer"
        assert ledger[0].scope == "mission"
        assert ledger[0].lifetime == "once"
    else:
        assert not (tmp_path / "operator_context.jsonl").exists()


def test_the_answer_check_is_one_short_stateless_turn_with_its_own_label(tmp_path) -> None:
    """Trial s-4aaf2d17: the check ran as SELF ``simple-1``, resumed the Manager's
    conversation and sent a 25,910-character prompt for a ~2.6k judgment."""
    from argus.core.models import RunnerResult
    from argus.roles.prompts.manager import build_pending_question_prompt

    calls = []

    class Backend:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            calls.append((prompt, options, run_label, resume_thread_id))
            return RunnerResult(exit_code=0, agent_messages=[
                "IS_ANSWER=false\nRESOLVED=false\nDECISION=\nREPLY=\n",
            ])

    mem, item = _pending_memory(tmp_path)
    state = {
        "manager_runner": SimpleNamespace(manager=Manager(tmp_path, runner=Backend())),
        "manager_runner_workdir": str(tmp_path),
        "last_thread_id": "manager-conversation",
    }

    result = _resolve_pending_question_with_manager(mem, item, "Also add a char_count helper.", state)

    assert result["answer_intent"] is False
    ((prompt, options, run_label, resume),) = calls
    assert run_label == "manager-pending-answer"
    assert resume is None and state["last_thread_id"] == "manager-conversation"
    assert prompt == build_pending_question_prompt(item, "Also add a char_count helper.")
    assert options.sandbox_mode == "read-only"
