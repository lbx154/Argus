"""Triage failure behavior after removing keyword-based status/no-run detectors."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from argus.manager.front_door import manager_triage


class _RaisingRunner:
    last_thread_id = None

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        raise RuntimeError("classify blocked before start")


class _TaskRunner:
    last_thread_id = "t"

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        return False


class _PreProviderRefusalRunner:
    last_thread_id = None

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        raise RuntimeError(
            "refused before start: global daily budget exhausted"
        )


class _HandledWithoutReplyRunner:
    last_thread_id = None

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        return True


class _TimedOutWithoutReplyRunner:
    last_thread_id = None
    last_chat_outcome = SimpleNamespace(
        stop_reason=(
            "External interrupt: Manager turn wall-clock limit reached after 300s"
        )
    )

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        return True


def _triage(runner: Any, body: str) -> str | None:
    return manager_triage(
        object(),
        body,
        {},
        ensure_runner=lambda _chat_state, _mem: runner,
    )


def test_triage_failure_reports_status_check_failure_without_dispatch() -> None:
    reply = _triage(_RaisingRunner(), "请只做状态检查，不要运行任务")
    assert reply and "classify blocked before start" in reply and "未追加新任务" in reply


def test_triage_failure_does_not_dispatch_unclassified_work() -> None:
    reply = _triage(_RaisingRunner(), "optimize the training throughput of this project")
    assert reply and "classify blocked before start" in reply and "No new task was queued" in reply


def test_pre_provider_refusal_never_dispatches_unclassified_input() -> None:
    reply = _triage(_PreProviderRefusalRunner(), "你好")

    assert reply is not None
    assert "你好" in reply


def test_handled_empty_self_reply_is_explicit_and_never_dispatched() -> None:
    reply = _triage(_HandledWithoutReplyRunner(), "这个进程还活着吗")

    assert reply is not None
    assert reply != "(no reply)"
    assert "reply unavailable" in reply and "未追加新任务" in reply
    assert "运行正常" not in reply


def test_failed_empty_self_reply_surfaces_the_actual_stop_reason() -> None:
    reply = _triage(_TimedOutWithoutReplyRunner(), "调研这家公司")

    assert reply is not None
    assert "wall-clock limit reached after 300s" in reply
    assert "completed without an assistant message" not in reply


def test_successful_task_classify_is_not_overridden() -> None:
    assert _triage(_TaskRunner(), "只做状态检查 and then run it") is None
