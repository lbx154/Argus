from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..models import PlanDecision, PlanMode, ReviewDecision, RoundSummary


@dataclass
class OperatorMessage:
    ts: str
    source: str
    kind: str
    audience: str
    text: str


class LoopStateStore:
    def __init__(
        self,
        *,
        objective: str = "",
        state_file: str | None = None,
        operator_messages_file: str | None = None,
        plan_overview_file: str | None = None,
        review_summaries_dir: str | None = None,
        final_report_file: str | None = None,
        pptx_report_file: str | None = None,
        main_prompt_file: str | None = None,
        check_commands: list[str] | None = None,
        plan_mode: PlanMode = "off",
    ) -> None:
        self._lock = threading.Lock()
        self._objective = objective
        self._state_file = state_file
        self._operator_messages_file = operator_messages_file
        self._plan_overview_file = plan_overview_file
        self._review_summaries_dir = review_summaries_dir
        self._final_report_file = final_report_file
        self._pptx_report_file = pptx_report_file
        self._main_prompt_file = main_prompt_file
        self._check_commands = list(check_commands or [])
        self._plan_mode = plan_mode
        self._interrupt_reason: str | None = None
        self._pending_instruction: str | None = None
        self._stop_requested = False
        self._messages: list[OperatorMessage] = []
        self._rounds: list[RoundSummary] = []
        self._session_id: str | None = None
        self._latest_review: ReviewDecision | None = None
        self._latest_plan: PlanDecision | None = None
        self._runtime: dict[str, object | None] = {
            "status": "idle",
            "round": 0,
            "session_id": None,
            "updated_at": None,
            "success": None,
            "stop_reason": None,
            "plan_mode": plan_mode,
            "plan_overview_file": plan_overview_file,
            "review_summaries_dir": review_summaries_dir,
            "final_report_file": final_report_file,
            "final_report_ready": False,
            "pptx_report_file": pptx_report_file,
            "pptx_report_ready": False,
            "main_prompt_file": main_prompt_file,
            "latest_plan_next_explore": None,
        }

    def record_message(
        self,
        *,
        text: str,
        source: str = "operator",
        kind: str = "message",
        audience: str = "broadcast",
    ) -> None:
        normalized = text.strip()
        if not normalized:
            return
        with self._lock:
            self._messages.append(
                OperatorMessage(
                    ts=self._now(),
                    source=source,
                    kind=kind,
                    audience=audience,
                    text=normalized,
                )
            )
            self._write_messages_doc_locked()

    def request_inject(self, instruction: str, source: str = "operator") -> None:
        text = instruction.strip()
        if not text:
            return
        with self._lock:
            self._pending_instruction = text
            self._interrupt_reason = f"{source} requested instruction update"
            self._messages.append(
                OperatorMessage(
                    ts=self._now(),
                    source=source,
                    kind="inject",
                    audience="broadcast",
                    text=text,
                )
            )
            self._write_messages_doc_locked()

    def request_stop(self, source: str = "operator") -> None:
        with self._lock:
            self._stop_requested = True
            self._interrupt_reason = f"{source} requested stop"
            self._messages.append(
                OperatorMessage(
                    ts=self._now(),
                    source=source,
                    kind="stop",
                    audience="broadcast",
                    text="stop requested",
                )
            )
            self._write_messages_doc_locked()

    def request_plan_direction(self, direction: str, source: str = "operator") -> None:
        self.record_message(text=direction, source=source, kind="plan-direction", audience="plan")

    def request_review_criteria(self, criteria: str, source: str = "operator") -> None:
        self.record_message(text=criteria, source=source, kind="review-criteria", audience="review")

    def current_plan_mode(self) -> PlanMode:
        with self._lock:
            return self._plan_mode

    def request_plan_mode(self, mode: str, source: str = "operator") -> PlanMode | None:
        normalized = self._normalize_plan_mode(mode)
        if normalized is None:
            return None
        with self._lock:
            self._plan_mode = normalized
            self._runtime["plan_mode"] = normalized
            self._messages.append(
                OperatorMessage(
                    ts=self._now(),
                    source=source,
                    kind="plan-mode",
                    audience="system",
                    text=normalized,
                )
            )
            self._write_messages_doc_locked()
            if self._latest_plan is not None:
                self._write_plan_overview_locked(round_index=len(self._rounds))
            self._write_state_locked()
            return normalized

    def consume_interrupt_reason(self) -> str | None:
        with self._lock:
            reason = self._interrupt_reason
            self._interrupt_reason = None
            return reason

    def consume_pending_instruction(self) -> str | None:
        with self._lock:
            instruction = self._pending_instruction
            self._pending_instruction = None
            return instruction

    def is_stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    def list_messages(self) -> list[str]:
        with self._lock:
            return [self._format_message(item) for item in self._messages]

    def list_messages_for_role(self, role: str) -> list[str]:
        allowed_audiences = {
            "main": {"broadcast"},
            "review": {"broadcast", "review"},
            "plan": {"broadcast", "plan"},
            "all": {"broadcast", "plan", "review"},
        }.get(role, {"broadcast"})
        with self._lock:
            return [self._format_message(item) for item in self._messages if item.audience in allowed_audiences]

    def runtime_snapshot(self) -> dict[str, object | None]:
        with self._lock:
            return dict(self._runtime)

    def record_plan(self, plan_decision: PlanDecision, *, round_index: int, session_id: str | None) -> None:
        with self._lock:
            self._latest_plan = plan_decision
            self._runtime["latest_plan_next_explore"] = plan_decision.next_explore
            self._session_id = session_id
            self._write_plan_overview_locked(round_index=round_index)
            self._write_state_locked()

    def handle_event(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        with self._lock:
            self._runtime["updated_at"] = event.get("ts") or self._now()
            if event_type == "loop.started":
                self._runtime["status"] = "running"
                self._runtime["session_id"] = event.get("session_id")
                self._runtime["round"] = 0
                self._runtime["success"] = None
                self._runtime["stop_reason"] = None
                self._runtime["plan_mode"] = event.get("plan_mode", self._plan_mode)
            elif event_type == "round.started":
                self._runtime["round"] = event.get("round_index", self._runtime["round"])
                self._runtime["session_id"] = event.get("session_id", self._runtime["session_id"])
            elif event_type == "round.main.completed":
                self._runtime["session_id"] = event.get("session_id", self._runtime["session_id"])
            elif event_type == "loop.completed":
                self._runtime["status"] = "completed"
                self._runtime["success"] = event.get("success")
                self._runtime["stop_reason"] = event.get("stop_reason")
            elif event_type == "plan.completed":
                self._runtime["latest_plan_next_explore"] = event.get("next_explore")

    @staticmethod
    def _normalize_plan_mode(mode: str | None) -> PlanMode | None:
        if not isinstance(mode, str):
            return None
        normalized = mode.strip().lower()
        if normalized in {"off", "auto", "record"}:
            return normalized
        return None

    def record_round(
        self,
        round_summary: RoundSummary,
        *,
        session_id: str | None,
        current_review: ReviewDecision,
        current_plan: PlanDecision | None = None,
    ) -> None:
        with self._lock:
            self._rounds.append(round_summary)
            self._session_id = session_id
            self._latest_review = current_review
            if current_plan is not None:
                self._latest_plan = current_plan
                self._runtime["latest_plan_next_explore"] = current_plan.next_explore
            self._write_review_summaries_locked(round_summary)
            if current_plan is not None:
                self._write_plan_overview_locked(round_index=round_summary.round_index)
            self._write_state_locked()

    def record_completion(self, *, success: bool, stop_reason: str, session_id: str | None) -> None:
        with self._lock:
            self._session_id = session_id
            self._runtime["status"] = "completed"
            self._runtime["success"] = success
            self._runtime["stop_reason"] = stop_reason
            self._runtime["updated_at"] = self._now()
            self._write_state_locked()

    @staticmethod
    def _serialize_round(round_summary: RoundSummary) -> dict[str, object]:
        data = asdict(round_summary)
        data["checks"] = [asdict(item) for item in round_summary.checks]
        data["review"] = asdict(round_summary.review)
        if round_summary.plan is not None:
            data["plan"] = asdict(round_summary.plan)
        return data

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _format_message(message: OperatorMessage) -> str:
        return f"[{message.ts}] [{message.source}] [{message.kind}] [{message.audience}] {message.text}"

    def read_plan_overview_markdown(self) -> str | None:
        return self._read_text_file(self._plan_overview_file)

    def read_review_summaries_markdown(self, round_index: int | None = None) -> str | None:
        if not self._review_summaries_dir:
            return None
        base = Path(self._review_summaries_dir)
        if round_index is None:
            return self._read_text_file(base / "index.md")
        return self._read_text_file(base / f"round-{round_index:03d}.md")

    def read_main_prompt_markdown(self) -> str | None:
        return self._read_text_file(self._main_prompt_file)

    def plan_overview_path(self) -> str | None:
        return self._plan_overview_file

    def review_summaries_dir(self) -> str | None:
        return self._review_summaries_dir

    def main_prompt_path(self) -> str | None:
        return self._main_prompt_file

    def final_report_path(self) -> str | None:
        return self._final_report_file

    def has_final_report(self) -> bool:
        with self._lock:
            return bool(self._runtime.get("final_report_ready"))

    def read_final_report_markdown(self) -> str | None:
        return self._read_text_file(self._final_report_file)

    def record_final_report(self, path: str) -> None:
        if not path:
            return
        with self._lock:
            self._final_report_file = path
            self._runtime["final_report_file"] = path
            self._runtime["final_report_ready"] = True
            self._write_state_locked()

    def pptx_report_path(self) -> str | None:
        return self._pptx_report_file

    def has_pptx_report(self) -> bool:
        with self._lock:
            return bool(self._runtime.get("pptx_report_ready"))

    def record_pptx_report(self, path: str) -> None:
        if not path:
            return
        with self._lock:
            self._pptx_report_file = path
            self._runtime["pptx_report_file"] = path
            self._runtime["pptx_report_ready"] = True
            self._write_state_locked()

    def latest_plan_overview(self) -> str:
        if self._latest_plan is not None and self._latest_plan.overview_markdown.strip():
            return self._latest_plan.overview_markdown
        path_text = self.read_plan_overview_markdown()
        return path_text or ""

    def record_main_prompt(self, *, round_index: int, phase: str, prompt: str) -> None:
        if not self._main_prompt_file:
            return
        with self._lock:
            path = Path(self._main_prompt_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "# Main Prompt",
                "",
                f"- Updated At: `{self._now()}`",
                f"- Round: `{round_index}`",
                f"- Phase: `{phase}`",
                f"- Session ID: `{self._session_id or self._runtime.get('session_id') or '-'}`",
                "",
                "## Prompt",
                "",
                prompt.strip(),
                "",
            ]
            path.write_text("\n".join(lines), encoding="utf-8")

    def render_plan_context_markdown(self) -> str:
        with self._lock:
            plan_messages = [item for item in self._messages if item.audience == "plan"]
            broadcast_messages = [item for item in self._messages if item.audience == "broadcast"]
            lines = [
                "# Plan Context",
                "",
                f"- Plan mode: `{self._plan_mode}`",
                f"- Latest next explore: `{self._latest_plan.next_explore if self._latest_plan else '-'}`",
                f"- Follow-up required: `{self._latest_plan.follow_up_required if self._latest_plan else '-'}`",
                "",
                "## Plan-Only Directions",
                "",
            ]
            if plan_messages:
                for item in plan_messages:
                    lines.append(f"- `{item.ts}` `{item.source}` `{item.kind}`: {item.text}")
            else:
                lines.append("- none")
            lines.extend(["", "## Broadcast Inputs", ""])
            if broadcast_messages:
                for item in broadcast_messages:
                    lines.append(f"- `{item.ts}` `{item.source}` `{item.kind}`: {item.text}")
            else:
                lines.append("- none")
            return "\n".join(lines).strip() + "\n"

    def render_review_context_markdown(self) -> str:
        with self._lock:
            review_messages = [item for item in self._messages if item.audience == "review"]
            broadcast_messages = [item for item in self._messages if item.audience == "broadcast"]
            lines = [
                "# Review Context",
                "",
                f"- Latest review status: `{self._latest_review.status if self._latest_review else '-'}`",
                f"- Latest review reason: `{self._latest_review.reason if self._latest_review else '-'}`",
                f"- Latest review next action: `{self._latest_review.next_action if self._latest_review else '-'}`",
                "",
                "## Acceptance Checks",
                "",
            ]
            if self._check_commands:
                for item in self._check_commands:
                    lines.append(f"- `{item}`")
            else:
                lines.append("- none configured")
            lines.extend(["", "## Review-Only Criteria", ""])
            if review_messages:
                for item in review_messages:
                    lines.append(f"- `{item.ts}` `{item.source}` `{item.kind}`: {item.text}")
            else:
                lines.append("- none")
            lines.extend(["", "## Broadcast Inputs", ""])
            if broadcast_messages:
                for item in broadcast_messages:
                    lines.append(f"- `{item.ts}` `{item.source}` `{item.kind}`: {item.text}")
            else:
                lines.append("- none")
            if self._latest_review and self._latest_review.completion_summary_markdown.strip():
                lines.extend(
                    [
                        "",
                        "## Latest Review Completion Summary",
                        "",
                        self._latest_review.completion_summary_markdown.strip(),
                    ]
                )
            return "\n".join(lines).strip() + "\n"

    def _write_state_locked(self) -> None:
        if not self._state_file:
            return
        payload = {
            "updated_at": self._now(),
            "objective": self._objective,
            "session_id": self._session_id,
            "round_count": len(self._rounds),
            "latest_review_status": self._latest_review.status if self._latest_review else None,
            "status": self._runtime["status"],
            "success": self._runtime["success"],
            "stop_reason": self._runtime["stop_reason"],
            "plan_mode": self._plan_mode,
            "main_prompt_file": self._main_prompt_file,
            "plan_overview_file": self._plan_overview_file,
            "review_summaries_dir": self._review_summaries_dir,
            "final_report_file": self._final_report_file,
            "final_report_ready": bool(self._runtime.get("final_report_ready")),
            "pptx_report_file": self._pptx_report_file,
            "pptx_report_ready": bool(self._runtime.get("pptx_report_ready")),
            "latest_plan": asdict(self._latest_plan) if self._latest_plan else None,
            "rounds": [self._serialize_round(item) for item in self._rounds],
        }
        path = Path(self._state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_messages_doc_locked(self) -> None:
        if not self._operator_messages_file:
            return
        path = Path(self._operator_messages_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Operator Messages",
            "",
            "Messages entered by user/operator channels (Telegram/terminal/initial objective).",
            "",
        ]
        for item in self._messages:
            lines.append(f"- `{item.ts}` `{item.source}` `{item.kind}` `{item.audience}`: {item.text}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_plan_overview_locked(self, *, round_index: int) -> None:
        if not self._plan_overview_file or self._latest_plan is None:
            return
        path = Path(self._plan_overview_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        runtime = [
            ("Plan Mode", self._plan_mode),
            ("Round", str(round_index)),
            ("Session ID", self._session_id or "-"),
            ("Latest Review Status", self._latest_review.status if self._latest_review else "-"),
            ("Follow-up Required", str(self._latest_plan.follow_up_required)),
            ("Latest Plan Next Explore", self._latest_plan.next_explore),
            ("Updated At", self._now()),
        ]
        lines = [
            "# Plan Overview",
            "",
            self._latest_plan.overview_markdown.strip(),
            "",
            "## Runtime Data",
            "",
            "| Key | Value |",
            "| --- | --- |",
        ]
        for key, value in runtime:
            safe = str(value).replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {key} | {safe} |")
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    def _write_review_summaries_locked(self, round_summary: RoundSummary) -> None:
        if not self._review_summaries_dir:
            return
        base = Path(self._review_summaries_dir)
        base.mkdir(parents=True, exist_ok=True)
        round_path = base / f"round-{round_summary.round_index:03d}.md"
        header = [
            f"# Review Round {round_summary.round_index}",
            "",
            f"- Session ID: `{round_summary.thread_id or '-'}`",
            f"- Main Exit Code: `{round_summary.main_exit_code}`",
            f"- Review Status: `{round_summary.review.status}`",
            "",
        ]
        body = round_summary.review.round_summary_markdown.strip() or "# Review Summary\n\n- No summary provided."
        round_path.write_text("\n".join(header) + body + "\n", encoding="utf-8")

        if round_summary.review.completion_summary_markdown.strip():
            completion_path = base / "completion.md"
            completion_lines = [
                "# Review Completion Summary",
                "",
                f"- Round: `{round_summary.round_index}`",
                f"- Session ID: `{round_summary.thread_id or '-'}`",
                "",
                round_summary.review.completion_summary_markdown.strip(),
                "",
            ]
            completion_path.write_text("\n".join(completion_lines), encoding="utf-8")

        index_lines = [
            "# Review Summaries",
            "",
            f"- Latest Updated At: `{self._now()}`",
            "",
        ]
        for item in self._rounds:
            index_lines.append(
                f"- Round {item.round_index}: `round-{item.round_index:03d}.md` status=`{item.review.status}`"
            )
        completion_path = base / "completion.md"
        if completion_path.exists():
            index_lines.extend(["", "- Final completion summary: `completion.md`"])
        (base / "index.md").write_text("\n".join(index_lines).strip() + "\n", encoding="utf-8")

    @staticmethod
    def _read_text_file(path: str | Path | None) -> str | None:
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None
