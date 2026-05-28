from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .btw_skills import BtwAttachment, resolve_btw_skill_result
from .codex_runner import CodexRunner, RunnerOptions


@dataclass
class BtwConfig:
    working_dir: str
    model: str | None = None
    reasoning_effort: str | None = None
    messages_file: str | None = None
    codex_extra_args: list[str] | None = None


@dataclass
class BtwResult:
    answer: str
    session_id: str | None
    messages_file: str | None
    attachments: list[BtwAttachment]


@dataclass
class BtwStatus:
    busy: bool
    session_id: str | None
    last_question: str | None
    last_answer: str | None
    updated_at: str | None
    messages_file: str | None


CompletionCallback = Callable[[BtwResult], None]
BusyCallback = Callable[[], None]


class BtwAgent:
    def __init__(self, runner: CodexRunner, config: BtwConfig) -> None:
        self.runner = runner
        self.config = config
        self._lock = threading.Lock()
        self._busy = False
        self._session_id: str | None = None
        self._last_question: str | None = None
        self._last_answer: str | None = None
        self._updated_at: str | None = None

    def status_snapshot(self) -> BtwStatus:
        with self._lock:
            return BtwStatus(
                busy=self._busy,
                session_id=self._session_id,
                last_question=self._last_question,
                last_answer=self._last_answer,
                updated_at=self._updated_at,
                messages_file=self.config.messages_file,
            )

    def start_async(
        self,
        *,
        question: str,
        on_complete: CompletionCallback,
        on_busy: BusyCallback | None = None,
    ) -> bool:
        normalized = question.strip()
        if not normalized:
            return False
        with self._lock:
            if self._busy:
                if on_busy is not None:
                    on_busy()
                return False
            self._busy = True
            self._last_question = normalized
            self._updated_at = _now()
        thread = threading.Thread(
            target=self._run_question,
            args=(normalized, on_complete),
            daemon=True,
        )
        thread.start()
        return True

    def _run_question(self, question: str, on_complete: CompletionCallback) -> None:
        answer = ""
        next_session_id: str | None = None
        attachments: list[BtwAttachment] = []
        try:
            skill_result = resolve_btw_skill_result(working_dir=self.config.working_dir, question=question)
            if skill_result.is_file_request:
                attachments = skill_result.attachments
                if attachments:
                    answer = (
                        "[btw] found matching project files:\n"
                        + "\n".join(skill_result.summary_lines)
                    )
                else:
                    answer = "[btw] this looks like a file/image request, but I did not find a good local match."
                return
            with self._lock:
                resume_session_id = self._session_id
            result = self.runner.run_exec(
                prompt=(
                    _followup_prompt(question)
                    if resume_session_id
                    else _initial_prompt(question, working_dir=self.config.working_dir)
                ),
                resume_thread_id=resume_session_id,
                options=RunnerOptions(
                    model=self.config.model,
                    reasoning_effort=self.config.reasoning_effort,
                    skip_git_repo_check=True,
                    extra_args=self.config.codex_extra_args,
                    working_dir=self.config.working_dir,
                ),
                run_label="btw",
            )
            next_session_id = result.thread_id or resume_session_id
            answer = (result.last_agent_message or "").strip()
            if not answer:
                if result.fatal_error:
                    answer = f"[btw] question failed: {result.fatal_error}"
                else:
                    answer = "[btw] no answer produced."
        except Exception as exc:  # pragma: no cover - defensive
            answer = f"[btw] question failed: {exc}"
        finally:
            with self._lock:
                self._busy = False
                if next_session_id:
                    self._session_id = next_session_id
                self._last_answer = answer
                self._updated_at = _now()
                messages_file = self.config.messages_file
            self._append_messages(question=question, answer=answer)
            on_complete(
                BtwResult(
                    answer=answer,
                    session_id=self._session_id,
                    messages_file=messages_file,
                    attachments=attachments,
                )
            )

    def _append_messages(self, *, question: str, answer: str) -> None:
        if not self.config.messages_file:
            return
        path = Path(self.config.messages_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# BTW Messages\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            ts = _now()
            f.write(f"## {ts}\n\n")
            f.write(f"- **Question**: {question}\n\n")
            f.write("### Answer\n\n")
            f.write(answer.strip() + "\n\n")


def _initial_prompt(question: str, *, working_dir: str) -> str:
    return (
        "You are the BTW side-agent for this project.\n"
        "Your job is to answer quick questions about the current repository without disturbing the main implementation loop.\n\n"
        "Rules:\n"
        "1) Read-only only. Do not modify files, do not run formatting, do not patch code, and do not change git state.\n"
        "2) You may inspect repository files and run read-only commands if needed.\n"
        "3) If the question requires code changes, say that /btw is read-only and suggest using /run or /inject.\n"
        "4) Keep answers concise and factual.\n"
        "5) Your memory for this thread should be limited to the current project and previous /btw turns only.\n\n"
        f"Project working directory:\n{working_dir}\n\n"
        f"Question:\n{question}\n"
    )


def _followup_prompt(question: str) -> str:
    return (
        "Continue the BTW side-agent conversation for this repository.\n"
        "Remain read-only and do not modify code.\n\n"
        f"Question:\n{question}\n"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
