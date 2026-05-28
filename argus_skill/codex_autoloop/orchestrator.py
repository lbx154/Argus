from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .checks import all_checks_passed, run_checks
from .codex_runner import CodexRunner, InactivitySnapshot, RunnerOptions
from .failure_modes import (
    build_progress_signature,
    build_quota_exhaustion_stop_reason,
    looks_like_invalid_encrypted_content,
    looks_like_quota_exhaustion,
)
from .models import ReviewDecision, RoundSummary
from .reviewer import Reviewer, ReviewerConfig
from .stall_subagent import analyze_stall

LoopEventCallback = Callable[[dict[str, Any]], None]


@dataclass
class AutoLoopConfig:
    objective: str
    max_rounds: int = 500
    max_no_progress_rounds: int = 3
    check_commands: list[str] | None = None
    check_timeout_seconds: int = 1200
    main_model: str | None = None
    main_reasoning_effort: str | None = None
    reviewer_model: str | None = None
    reviewer_reasoning_effort: str | None = None
    planner_model: str | None = None
    planner_reasoning_effort: str | None = None
    planner_mode: str = "off"
    main_extra_args: list[str] | None = None
    reviewer_extra_args: list[str] | None = None
    planner_extra_args: list[str] | None = None
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False
    state_file: str | None = None
    plan_report_file: str | None = None
    plan_todo_file: str | None = None
    initial_session_id: str | None = None
    loop_event_callback: LoopEventCallback | None = None
    stall_soft_idle_seconds: int = 1200
    stall_hard_idle_seconds: int = 10800
    plan_update_interval_seconds: int = 1800
    planner_enabled: bool = False
    external_interrupt_reason_provider: Callable[[], str | None] | None = None
    pending_instruction_consumer: Callable[[], str | None] | None = None
    stop_requested_checker: Callable[[], bool] | None = None
    operator_messages_provider: Callable[[], list[str]] | None = None


@dataclass
class AutoLoopResult:
    success: bool
    session_id: str | None
    rounds: list[RoundSummary]
    stop_reason: str
    plan: Any | None = None


class AutoLoopOrchestrator:
    def __init__(
        self,
        runner: CodexRunner,
        reviewer: Reviewer,
        config: AutoLoopConfig,
        planner: Any | None = None,
    ) -> None:
        self.runner = runner
        self.reviewer = reviewer
        self.config = config
        self.planner = planner

    def run(self) -> AutoLoopResult:
        rounds: list[RoundSummary] = []
        session_id = self.config.initial_session_id
        no_progress_rounds = 0
        previous_progress_signature = ""
        next_main_prompt = self._initial_main_prompt(self.config.objective)
        self._emit(
            {
                "type": "loop.started",
                "objective": self.config.objective,
                "max_rounds": self.config.max_rounds,
                "session_id": session_id,
            }
        )

        for round_index in range(1, self.config.max_rounds + 1):
            if self._is_stop_requested():
                result = AutoLoopResult(
                    success=False,
                    session_id=session_id,
                    rounds=rounds,
                    stop_reason="Stopped by operator command.",
                )
                self._emit({"type": "loop.completed", "success": result.success, "stop_reason": result.stop_reason})
                return result

            def inactivity_callback(snapshot: InactivitySnapshot) -> str:
                return self._handle_inactivity(round_index=round_index, snapshot=snapshot)

            self._emit(
                {
                    "type": "round.started",
                    "round_index": round_index,
                    "session_id": session_id,
                }
            )
            resume_session_id = session_id
            main_result = self.runner.run_exec(
                prompt=next_main_prompt,
                resume_thread_id=resume_session_id,
                options=RunnerOptions(
                    model=self.config.main_model,
                    reasoning_effort=self.config.main_reasoning_effort,
                    dangerous_yolo=self.config.dangerous_yolo,
                    full_auto=self.config.full_auto,
                    skip_git_repo_check=self.config.skip_git_repo_check,
                    extra_args=self.config.main_extra_args,
                    watchdog_soft_idle_seconds=self.config.stall_soft_idle_seconds,
                    watchdog_hard_idle_seconds=self.config.stall_hard_idle_seconds,
                    inactivity_callback=inactivity_callback,
                    external_interrupt_reason_provider=self.config.external_interrupt_reason_provider,
                ),
                run_label="main",
            )
            session_id = main_result.thread_id or session_id
            interrupted = (
                main_result.fatal_error is not None
                and main_result.fatal_error.startswith("External interrupt:")
            )
            invalid_encrypted_content = looks_like_invalid_encrypted_content(main_result.fatal_error)
            quota_exhausted = looks_like_quota_exhaustion(main_result.fatal_error)
            self._emit(
                {
                    "type": "round.main.completed",
                    "round_index": round_index,
                    "session_id": session_id,
                    "exit_code": main_result.exit_code,
                    "turn_completed": main_result.turn_completed,
                    "turn_failed": False if interrupted else main_result.turn_failed,
                    "interrupted": interrupted,
                    "fatal_error": main_result.fatal_error,
                    "last_message": main_result.last_agent_message,
                }
            )
            if interrupted:
                injected_instruction = self._consume_pending_instruction()
                review_reason = main_result.fatal_error
                next_action = "Continue with prior objective after external interruption."
                if injected_instruction:
                    review_reason = (
                        f"{main_result.fatal_error}. New operator instruction injected and will be applied."
                    )
                    next_action = "Apply injected operator instruction in next round."
                    self._emit(
                        {
                            "type": "round.control.injected",
                            "round_index": round_index,
                            "instruction": injected_instruction,
                        }
                    )
                review = ReviewDecision(
                    status="continue",
                    confidence=1.0,
                    reason=review_reason,
                    next_action=next_action,
                )
                round_summary = RoundSummary(
                    round_index=round_index,
                    thread_id=session_id,
                    main_exit_code=main_result.exit_code,
                    main_turn_completed=main_result.turn_completed,
                    main_turn_failed=False,
                    checks=[],
                    review=review,
                    main_last_message=main_result.last_agent_message,
                )
                rounds.append(round_summary)
                self._persist_state(rounds=rounds, session_id=session_id, current_review=review)

                if self._is_stop_requested():
                    result = AutoLoopResult(
                        success=False,
                        session_id=session_id,
                        rounds=rounds,
                        stop_reason="Stopped by operator command.",
                    )
                    self._emit(
                        {"type": "loop.completed", "success": result.success, "stop_reason": result.stop_reason}
                    )
                    return result

                if injected_instruction:
                    next_main_prompt = self._build_operator_override_prompt(
                        objective=self.config.objective,
                        instruction=injected_instruction,
                    )
                else:
                    next_main_prompt = self._build_continue_prompt(
                        objective=self.config.objective,
                        review=review,
                        checks_ok=False,
                    )
                continue

            if invalid_encrypted_content:
                if resume_session_id:
                    self._emit(
                        {
                            "type": "round.session.reset",
                            "round_index": round_index,
                            "previous_session_id": resume_session_id,
                            "reason": main_result.fatal_error,
                        }
                    )
                    session_id = None
                    review = ReviewDecision(
                        status="continue",
                        confidence=1.0,
                        reason=(
                            "Main agent failed with invalid_encrypted_content while resuming session. "
                            "Resetting to a fresh session for the next round."
                        ),
                        next_action="Retry objective from a fresh session immediately.",
                    )
                    round_summary = RoundSummary(
                        round_index=round_index,
                        thread_id=session_id,
                        main_exit_code=main_result.exit_code,
                        main_turn_completed=main_result.turn_completed,
                        main_turn_failed=main_result.turn_failed,
                        checks=[],
                        review=review,
                        main_last_message=main_result.last_agent_message,
                    )
                    rounds.append(round_summary)
                    self._persist_state(rounds=rounds, session_id=session_id, current_review=review)
                    next_main_prompt = self._build_fresh_session_retry_prompt(
                        objective=self.config.objective,
                        fatal_error=main_result.fatal_error or "",
                    )
                    continue
                result = AutoLoopResult(
                    success=False,
                    session_id=session_id,
                    rounds=rounds,
                    stop_reason=(
                        "Main agent failed with invalid_encrypted_content in a fresh session; "
                        "cannot recover automatically."
                    ),
                )
                self._emit({"type": "loop.completed", "success": result.success, "stop_reason": result.stop_reason})
                return result

            if quota_exhausted:
                review = ReviewDecision(
                    status="blocked",
                    confidence=1.0,
                    reason="Main agent hit a non-recoverable Codex quota or billing limit.",
                    next_action="Wait for quota reset or increase available quota, then rerun the objective.",
                )
                self._emit(
                    {
                        "type": "round.review.completed",
                        "round_index": round_index,
                        "status": review.status,
                        "confidence": review.confidence,
                        "reason": review.reason,
                        "next_action": review.next_action,
                    }
                )
                round_summary = RoundSummary(
                    round_index=round_index,
                    thread_id=session_id,
                    main_exit_code=main_result.exit_code,
                    main_turn_completed=main_result.turn_completed,
                    main_turn_failed=main_result.turn_failed,
                    checks=[],
                    review=review,
                    main_last_message=main_result.last_agent_message,
                )
                rounds.append(round_summary)
                self._persist_state(rounds=rounds, session_id=session_id, current_review=review)
                result = AutoLoopResult(
                    success=False,
                    session_id=session_id,
                    rounds=rounds,
                    stop_reason=build_quota_exhaustion_stop_reason(main_result.fatal_error),
                )
                self._emit({"type": "loop.completed", "success": result.success, "stop_reason": result.stop_reason})
                return result

            checks = run_checks(self.config.check_commands or [], self.config.check_timeout_seconds)
            self._emit(
                {
                    "type": "round.checks.completed",
                    "round_index": round_index,
                    "checks": [
                        {
                            "command": item.command,
                            "exit_code": item.exit_code,
                            "passed": item.passed,
                        }
                        for item in checks
                    ],
                }
            )
            review = self.reviewer.evaluate(
                objective=self.config.objective,
                operator_messages=self._get_operator_messages(),
                round_index=round_index,
                session_id=session_id,
                main_exit_code=main_result.exit_code,
                main_turn_completed=main_result.turn_completed,
                main_turn_failed=main_result.turn_failed,
                main_agent_message_count=len(main_result.agent_messages),
                main_summary=main_result.last_agent_message,
                main_error=main_result.fatal_error,
                checks=checks,
                config=ReviewerConfig(
                    model=self.config.reviewer_model,
                    reasoning_effort=self.config.reviewer_reasoning_effort,
                    extra_args=self.config.reviewer_extra_args,
                    skip_git_repo_check=self.config.skip_git_repo_check,
                    full_auto=self.config.full_auto,
                    dangerous_yolo=self.config.dangerous_yolo,
                ),
            )
            self._emit(
                {
                    "type": "round.review.completed",
                    "round_index": round_index,
                    "status": review.status,
                    "confidence": review.confidence,
                    "reason": review.reason,
                    "next_action": review.next_action,
                    "round_summary_markdown": review.round_summary_markdown,
                    "completion_summary_markdown": review.completion_summary_markdown,
                    "raw_output": main_result.last_agent_message,
                }
            )

            round_summary = RoundSummary(
                round_index=round_index,
                thread_id=session_id,
                main_exit_code=main_result.exit_code,
                main_turn_completed=main_result.turn_completed,
                main_turn_failed=main_result.turn_failed,
                checks=checks,
                review=review,
                main_last_message=main_result.last_agent_message,
            )
            rounds.append(round_summary)
            self._persist_state(rounds=rounds, session_id=session_id, current_review=review)

            checks_ok = all_checks_passed(checks)
            if review.status == "done" and checks_ok:
                result = AutoLoopResult(
                    success=True,
                    session_id=session_id,
                    rounds=rounds,
                    stop_reason="Reviewer marked done and acceptance checks passed.",
                )
                self._emit({"type": "loop.completed", "success": result.success, "stop_reason": result.stop_reason})
                return result

            if review.status == "blocked":
                result = AutoLoopResult(
                    success=False,
                    session_id=session_id,
                    rounds=rounds,
                    stop_reason=f"Reviewer blocked: {review.reason}",
                )
                self._emit({"type": "loop.completed", "success": result.success, "stop_reason": result.stop_reason})
                return result

            current_progress_signature = build_progress_signature(main_result=main_result)
            if current_progress_signature and current_progress_signature == previous_progress_signature:
                no_progress_rounds += 1
            else:
                no_progress_rounds = 0
                previous_progress_signature = current_progress_signature

            if no_progress_rounds >= self.config.max_no_progress_rounds:
                result = AutoLoopResult(
                    success=False,
                    session_id=session_id,
                    rounds=rounds,
                    stop_reason=(
                        "Stopped due to repeated no-progress rounds. "
                        "Reviewer kept requesting continuation without new output."
                    ),
                )
                self._emit({"type": "loop.completed", "success": result.success, "stop_reason": result.stop_reason})
                return result

            next_main_prompt = self._build_continue_prompt(
                objective=self.config.objective,
                review=review,
                checks_ok=checks_ok,
            )

        result = AutoLoopResult(
            success=False,
            session_id=session_id,
            rounds=rounds,
            stop_reason=f"Reached max rounds ({self.config.max_rounds}).",
        )
        self._emit({"type": "loop.completed", "success": result.success, "stop_reason": result.stop_reason})
        return result

    def _persist_state(
        self,
        *,
        rounds: list[RoundSummary],
        session_id: str | None,
        current_review: ReviewDecision,
    ) -> None:
        if not self.config.state_file:
            return
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "objective": self.config.objective,
            "session_id": session_id,
            "round_count": len(rounds),
            "latest_review_status": current_review.status,
            "rounds": [self._serialize_round(item) for item in rounds],
        }
        path = Path(self.config.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _serialize_round(round_summary: RoundSummary) -> dict:
        data = asdict(round_summary)
        data["checks"] = [asdict(item) for item in round_summary.checks]
        data["review"] = asdict(round_summary.review)
        return data

    @staticmethod
    def _initial_main_prompt(objective: str) -> str:
        if AutoLoopOrchestrator._request_style(objective) == "response":
            return (
                "You are the primary agent.\n"
                "The user may be greeting you, asking a question, or requesting analysis instead of code edits.\n"
                "Reply directly in the user's language.\n"
                "Inspect the repository, logs, or local context yourself if that helps answer.\n"
                "Do not force code changes unless they are actually needed to solve the request.\n"
                "Do not refuse unless the request is genuinely disallowed.\n"
                "If the user is simply greeting you or checking whether you are still here, reply briefly and ask what they want next.\n\n"
                f"User request:\n{objective}\n"
            )
        return (
            "You are the primary implementation agent.\n"
            "Complete the objective end-to-end by executing required edits and commands directly.\n"
            "Do not stop after a partial plan.\n"
            "If one path fails, try alternatives before declaring a blocker.\n"
            "Do not ask the user to perform next steps.\n\n"
            f"Objective:\n{objective}\n\n"
            "At the end, output a concise execution summary:\n"
            "- DONE:\n- REMAINING:\n- BLOCKERS:\n"
        )

    @staticmethod
    def _build_continue_prompt(*, objective: str, review: ReviewDecision, checks_ok: bool) -> str:
        if AutoLoopOrchestrator._request_style(objective) == "response":
            return (
                "Continue the same user request in this session.\n"
                f"User request:\n{objective}\n\n"
                f"Reviewer reason:\n{review.reason}\n\n"
                f"Reviewer next action:\n{review.next_action}\n\n"
                "Respond directly.\n"
                "Inspect the repository or logs yourself if needed.\n"
                "Do not force code edits unless they are actually needed.\n"
                "For greetings or short questions, answer naturally without DONE/REMAINING/BLOCKERS."
            )
        check_instruction = (
            "Acceptance checks passed in previous round."
            if checks_ok
            else "Acceptance checks failed in previous round; fix them first."
        )
        return (
            "Continue the same objective in this session.\n"
            f"Objective:\n{objective}\n\n"
            f"Reviewer reason:\n{review.reason}\n\n"
            f"Reviewer next action:\n{review.next_action}\n\n"
            f"{check_instruction}\n"
            "Execute concrete work now. Do not stop at guidance-only output.\n"
            "End your response with updated DONE/REMAINING/BLOCKERS."
        )

    def _emit(self, event: dict[str, Any]) -> None:
        callback = self.config.loop_event_callback
        if callback is None:
            return
        callback(event)

    def _handle_inactivity(self, *, round_index: int, snapshot: InactivitySnapshot) -> str:
        decision = analyze_stall(snapshot)
        self._emit(
            {
                "type": "round.watchdog.checked",
                "round_index": round_index,
                "idle_seconds": int(snapshot.idle_seconds),
                "should_restart": decision.should_restart,
                "reason": decision.reason,
                "matched_pattern": decision.matched_pattern,
            }
        )
        if decision.should_restart:
            self._emit(
                {
                    "type": "round.watchdog.restart_requested",
                    "round_index": round_index,
                    "idle_seconds": int(snapshot.idle_seconds),
                    "reason": decision.reason,
                }
            )
            return "restart"
        return "continue"

    def _consume_pending_instruction(self) -> str | None:
        consumer = self.config.pending_instruction_consumer
        if consumer is None:
            return None
        return consumer()

    def _is_stop_requested(self) -> bool:
        checker = self.config.stop_requested_checker
        if checker is None:
            return False
        return checker()

    def _get_operator_messages(self) -> list[str]:
        provider = self.config.operator_messages_provider
        if provider is None:
            return []
        return provider()

    @staticmethod
    def _build_operator_override_prompt(*, objective: str, instruction: str) -> str:
        if AutoLoopOrchestrator._request_style(instruction) == "response":
            return (
                "Operator override received from control channel.\n"
                "Treat it as a direct user request and answer it in the user's language.\n"
                "Inspect local repository context if useful.\n"
                "Do not force code edits unless they are actually needed.\n\n"
                f"Original objective:\n{objective}\n\n"
                f"New operator instruction:\n{instruction}\n"
            )
        return (
            "Operator override received from control channel.\n"
            "Immediately switch to the following instruction while preserving repository safety.\n\n"
            f"Original objective:\n{objective}\n\n"
            f"New operator instruction:\n{instruction}\n\n"
            "Execute concrete work now and continue until completion gates are met.\n"
            "End with DONE/REMAINING/BLOCKERS."
        )

    @staticmethod
    def _build_fresh_session_retry_prompt(*, objective: str, fatal_error: str) -> str:
        if AutoLoopOrchestrator._request_style(objective) == "response":
            return (
                "Previous resumed session failed with invalid_encrypted_content.\n"
                "Start a fresh session and continue responding directly in the user's language.\n"
                f"User request:\n{objective}\n\n"
                f"Prior fatal error:\n{fatal_error}\n"
            )
        return (
            "Previous resumed session failed with invalid_encrypted_content.\n"
            "Start a fresh session and continue the same objective immediately.\n\n"
            f"Objective:\n{objective}\n\n"
            f"Prior fatal error:\n{fatal_error}\n\n"
            "Execute concrete work now and end with DONE/REMAINING/BLOCKERS."
        )

    @staticmethod
    def _request_style(text: str) -> str:
        normalized = " ".join((text or "").strip().lower().split())
        if not normalized:
            return "implementation"

        implementation_markers = (
            "fix",
            "implement",
            "add ",
            "write ",
            "edit ",
            "modify",
            "refactor",
            "commit",
            "push",
            "train",
            "experiment",
            "run ",
            "rerun",
            "修改",
            "修复",
            "实现",
            "新增",
            "添加",
            "重构",
            "提交",
            "推送",
            "训练",
            "实验",
            "继续改",
            "继续修",
            "直接改",
            "帮我改",
            "帮我修",
        )
        if any(marker in normalized for marker in implementation_markers):
            return "implementation"

        greeting_phrases = {
            "hi",
            "hello",
            "hey",
            "ping",
            "你好",
            "您好",
            "在吗",
            "还在吗",
            "还活着吗",
            "兄弟在吗",
        }
        if normalized in greeting_phrases:
            return "response"

        response_markers = (
            "?",
            "？",
            "why",
            "what",
            "how",
            "explain",
            "analyze",
            "analysis",
            "question",
            "为什么",
            "为啥",
            "怎么",
            "啥原因",
            "是什么",
            "是不是",
            "分析",
            "解释",
            "看看问题",
            "bug问题",
            "错误在哪",
            "问题在哪",
            "问题所在",
        )
        if any(marker in normalized for marker in response_markers):
            return "response"

        return "implementation"
