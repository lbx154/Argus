"""Tests for the do-not-run / status-only triage safe-fail.

Incident (2026-07-11): a Chinese "请只做状态检查，不要运行任务" message was
dispatched to the team. Root cause was the empty-model pricing block making the
Manager's classify call raise; ``manager_triage`` then hit its
"triage failure -> treat as a task" fallback and the message was enqueued as a
real mission (Engineer -> "bootstrap empty project root").

Defense-in-depth fix: on a triage FAILURE (the classify call errored), if the
operator explicitly forbade running (status-only / do-not-run), safe-fail to a
chat notice instead of dispatching. A SUCCESSFUL classify decision is never
overridden, so genuine work is never silently dropped (the codebase's
"never drop work to a bad classify" contract is preserved).
"""

from __future__ import annotations

from typing import Any

from argus_skill.manager.front_door import (
    looks_like_do_not_run_request,
    manager_triage,
)

# --- pure detector ----------------------------------------------------------

def test_detector_true_for_incident_phrases() -> None:
    assert looks_like_do_not_run_request(
        "请只做状态检查，不要启动 daemon，不要运行任务，不要使用 GPU"
    )
    assert looks_like_do_not_run_request("请回复状态正常四个字。不要运行任务。")
    assert looks_like_do_not_run_request("只做状态检查")


def test_detector_true_for_english_do_not_run() -> None:
    assert looks_like_do_not_run_request("status check only, do not run tasks")
    assert looks_like_do_not_run_request("Please DON'T RUN anything, just status")
    assert looks_like_do_not_run_request("do not start the daemon")


def test_detector_false_for_real_tasks() -> None:
    assert not looks_like_do_not_run_request("optimize the throughput of this project")
    assert not looks_like_do_not_run_request("帮我优化这个 kernel 的性能")
    assert not looks_like_do_not_run_request("写一篇关于符号回归的论文")
    assert not looks_like_do_not_run_request("")
    assert not looks_like_do_not_run_request("run the benchmark and report results")


# --- triage failure path -----------------------------------------------------

class _RaisingRunner:
    """A Manager runner whose classify call always errors (e.g. the provider
    call was blocked by the cost gate — the real incident)."""

    last_thread_id = None

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        raise RuntimeError("classify blocked before start")


class _TaskRunner:
    """A runner that SUCCESSFULLY classifies the input as a task (not chat)."""

    last_thread_id = "t"

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        return False


class _PreProviderRefusalRunner:
    last_thread_id = None

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        raise RuntimeError(
            "refused before start: unresolved provider cost blocks new calls"
        )


class _HandledWithoutReplyRunner:
    last_thread_id = None

    def chat_reply_if_conversational(self, **kwargs: Any) -> bool:
        return True


def _triage(runner: Any, body: str) -> str | None:
    return manager_triage(
        object(),
        body,
        {},
        ensure_runner=lambda _chat_state, _mem: runner,
    )


def test_triage_failure_safe_fails_for_do_not_run_input() -> None:
    reply = _triage(_RaisingRunner(), "请只做状态检查，不要运行任务")
    # A non-None reply means "handled as chat, do NOT enqueue".
    assert reply is not None


def test_triage_failure_still_dispatches_real_work() -> None:
    reply = _triage(
        _RaisingRunner(),
        "optimize the training throughput of this project",
    )
    # None means "route to the TEAM backlog" — real work is never dropped.
    assert reply is None


def test_pre_provider_refusal_never_dispatches_unclassified_input() -> None:
    reply = _triage(_PreProviderRefusalRunner(), "你好")

    assert reply is not None
    assert "你好" in reply


def test_handled_empty_self_reply_is_explicit_and_never_dispatched() -> None:
    reply = _triage(_HandledWithoutReplyRunner(), "这个进程还活着吗")

    assert reply is not None
    assert reply != "(no reply)"
    assert "活着" in reply or "运行正常" in reply


def test_successful_task_classify_is_not_overridden() -> None:
    # Even a do-not-run-looking body, when the classify SUCCEEDS and decides
    # "task", is left as a task. The detector only guards the FAILURE path.
    reply = _triage(_TaskRunner(), "只做状态检查 and then run it")
    assert reply is None
