from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..adapters.control_channels import LocalBusControlChannel, TelegramControlChannel
from ..attachment_policy import format_attachment_confirmation_message, requires_attachment_confirmation
from ..btw_agent import BtwAgent, BtwConfig
from ..copilot_proxy import build_codex_runner, config_from_args, format_proxy_summary
from ..daemon_bus import BusCommand, JsonlCommandBus, read_status, write_status
from ..model_catalog import get_preset
from ..objective_rewrite import format_objective_rewrite_failure_message, format_objective_rewrite_message, rewrite_run_objective
from ..runner_backend import DEFAULT_RUNNER_BACKEND, backend_supports_copilot_proxy
from ..telegram_notifier import TelegramConfig, TelegramNotifier, resolve_chat_id
from ..token_lock import TokenLock, acquire_token_lock
from .shell_utils import format_mode_menu


def build_active_run_run_conflict_message() -> str:
    return (
        "[daemon] an active run already exists, so this /run was handled as /inject and forwarded to the current task.\n"
        "[CN] 当前已有 run 在执行，所以这条 `/run` 已按 `/inject` 转发给当前任务。"
        "如果你想开启新的 run，请先发送 `/stop`，等收到已结束/已停止的确认后，再重新发送 `/run`。\n"
        "[EN] An active run already exists, so this /run was handled as /inject and forwarded to the current task. "
        "If you want a brand-new run, send `/stop` first, wait until you receive confirmation that the current run finished/stopped, and then send `/run` again."
    )


def _split_autoloop_command(command: str) -> list[str]:
    if not command:
        raise ValueError("ArgusBot command cannot be empty")
    if os.name == "nt":
        parts = shlex.split(command, posix=False)
        return [_strip_wrapping_quotes(item) for item in parts if item]
    return shlex.split(command)


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


class TelegramDaemonApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.chat_id = ""
        self.run_cwd = Path(args.run_cd).resolve()
        self.logs_dir = Path(args.logs_dir).resolve()
        self.bus_dir = Path(args.bus_dir).resolve()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.bus_dir.mkdir(parents=True, exist_ok=True)
        self.events_log = self.logs_dir / "daemon-events.jsonl"
        self.status_path = self.bus_dir / "daemon_status.json"
        self.next_run_new_session_flag_path = self.bus_dir / "next_run_new_session.flag"
        preset = get_preset(args.run_model_preset) if getattr(args, "run_model_preset", None) else None
        run_copilot_proxy = config_from_args(args, prefix="run_")
        if run_copilot_proxy.enabled and backend_supports_copilot_proxy(getattr(args, "run_runner_backend", DEFAULT_RUNNER_BACKEND)):
            print(f"[daemon] Copilot proxy mode: {format_proxy_summary(run_copilot_proxy)}", file=sys.stderr)
        self.daemon_runner = build_codex_runner(
            backend=getattr(args, "run_runner_backend", DEFAULT_RUNNER_BACKEND),
            runner_bin=getattr(args, "run_runner_bin", None),
            config=run_copilot_proxy,
        )

        self.token_lock: TokenLock | None = None
        self.notifier: TelegramNotifier | None = None
        self.control_channels: list[object] = []
        self.child: subprocess.Popen[str] | None = None
        self.child_objective: str | None = None
        self.child_log_path: Path | None = None
        self.child_operator_messages_path: Path | None = None
        self.child_main_prompt_path: Path | None = None
        self.child_plan_overview_path: Path | None = None
        self.child_review_summaries_dir: Path | None = None
        self.child_started_at: dt.datetime | None = None
        self.child_control_bus: JsonlCommandBus | None = None
        self.pending_attachment_batches: dict[str, list[Any]] = {}
        self.pending_pptx_run_objective: str | None = None
        self.pending_pptx_run_source: str | None = None
        self._clock_timer: threading.Timer | None = None
        self.btw_agent = BtwAgent(
            runner=self.daemon_runner,
            config=BtwConfig(
                working_dir=str(self.run_cwd),
                model=(
                    getattr(args, "run_plan_model", None)
                    or (preset.plan_model if preset is not None else None)
                    or getattr(args, "run_reviewer_model", None)
                    or (preset.reviewer_model if preset is not None else None)
                    or getattr(args, "run_main_model", None)
                    or (preset.main_model if preset is not None else None)
                ),
                reasoning_effort=(
                    getattr(args, "run_plan_reasoning_effort", None)
                    or (preset.plan_reasoning_effort if preset is not None else None)
                    or getattr(args, "run_reviewer_reasoning_effort", None)
                    or (preset.reviewer_reasoning_effort if preset is not None else None)
                    or getattr(args, "run_main_reasoning_effort", None)
                    or (preset.main_reasoning_effort if preset is not None else None)
                ),
                messages_file=str(self.logs_dir / "btw_messages.md"),
            ),
        )
        self._stopping = False

    def run(self) -> None:
        self.chat_id = self._resolve_chat_id()
        self.token_lock = self._acquire_token_lock()
        self.notifier = TelegramNotifier(
            TelegramConfig(
                bot_token=self.args.telegram_bot_token,
                chat_id=self.chat_id,
                events=set(),
                typing_enabled=False,
            ),
            on_error=lambda msg: print(f"[daemon] {msg}", file=sys.stderr),
        )
        self.control_channels = self._build_control_channels()
        for channel in self.control_channels:
            channel.start(self._on_command)

        self.notifier.send_message(
            "[daemon] online\n"
            "Send /run <objective> to start a new run.\n"
            "Commands: /status /new /mode /btw /confirm-send /cancel-send /plan /review /show-main-prompt /show-plan /show-plan-context /show-review /show-review-context /stop /daemon-stop /help"
        )
        self._log_event(
            "daemon.started",
            run_cwd=str(self.run_cwd),
            logs_dir=str(self.logs_dir),
            bus_dir=str(self.bus_dir),
            token_hash=(self.token_lock.token_hash if self.token_lock else None),
        )
        self._write_status()

        try:
            while not self._stopping:
                time.sleep(1)
                self._check_child()
                self._write_status()
        except KeyboardInterrupt:
            print("Daemon interrupted.", file=sys.stderr)
            self._log_event("daemon.interrupted")
        finally:
            self._shutdown()

    def _resolve_chat_id(self) -> str:
        chat_id = (self.args.telegram_chat_id or "").strip()
        if chat_id.lower() not in {"", "auto", "none", "null"}:
            return chat_id
        print("Resolving chat_id from updates. Send /start or a message to your bot now...", file=sys.stderr)
        resolved = resolve_chat_id(
            bot_token=self.args.telegram_bot_token,
            timeout_seconds=self.args.telegram_chat_id_resolve_timeout_seconds,
            poll_interval_seconds=2,
            on_error=lambda msg: print(f"[daemon] {msg}", file=sys.stderr),
        )
        if not resolved:
            raise ValueError("Unable to resolve chat_id automatically.")
        print(f"Resolved chat_id={resolved}", file=sys.stderr)
        return resolved

    def _acquire_token_lock(self) -> TokenLock:
        return acquire_token_lock(
            token=self.args.telegram_bot_token,
            owner_info={
                "pid": os.getpid(),
                "chat_id": self.chat_id,
                "run_cwd": str(self.run_cwd),
                "bus_dir": str(self.bus_dir),
                "started_at": dt.datetime.utcnow().isoformat() + "Z",
            },
            lock_dir=self.args.token_lock_dir,
        )

    def _build_control_channels(self) -> list[object]:
        return [
            LocalBusControlChannel(
                path=str(self.bus_dir / "daemon_commands.jsonl"),
                source="terminal",
                on_error=lambda msg: print(f"[daemon] {msg}", file=sys.stderr),
            ),
            TelegramControlChannel(
                bot_token=self.args.telegram_bot_token,
                chat_id=self.chat_id,
                on_error=lambda msg: print(f"[daemon] {msg}", file=sys.stderr),
                poll_interval_seconds=self.args.poll_interval_seconds,
                long_poll_timeout_seconds=self.args.long_poll_timeout_seconds,
                plain_text_as_inject=True,
                whisper_enabled=self.args.telegram_control_whisper,
                whisper_api_key=self.args.telegram_control_whisper_api_key,
                whisper_model=self.args.telegram_control_whisper_model,
                whisper_base_url=self.args.telegram_control_whisper_base_url,
                whisper_timeout_seconds=self.args.telegram_control_whisper_timeout_seconds,
            ),
        ]

    def _on_command(self, command) -> None:
        self._log_event("command.received", source=command.source, kind=command.kind, text=command.text[:700])

        # Handle pending PPTX confirmation reply
        if self.pending_pptx_run_objective is not None:
            reply = command.text.strip().lower()
            if reply in ("y", "yes", "n", "no"):
                objective = self.pending_pptx_run_objective
                pptx_enabled = reply in ("y", "yes")
                self.pending_pptx_run_objective = None
                self.pending_pptx_run_source = None
                self._start_child(objective, pptx_report=pptx_enabled)
                return
            self._send_reply(command.source, "[daemon] PPTX confirmation cancelled. Send /run again to start.")
            self.pending_pptx_run_objective = None
            self.pending_pptx_run_source = None
            # fall through to handle the command normally

        if command.kind == "help":
            self._send_reply(command.source, help_text())
            return
        if command.kind == "new":
            write_force_new_session_next_run(self.next_run_new_session_flag_path, True)
            self._log_event("daemon.new_session_requested", source=command.source)
            self._send_reply(
                command.source,
                "[daemon] next /run will start the main agent in a fresh session. "
                "If a run is active now, this applies after it finishes or after you stop it.",
            )
            self._write_status()
            return
        if command.kind == "mode-menu":
            self._send_reply(command.source, format_mode_menu(getattr(self.args, "run_plan_mode", "auto")))
            return
        if command.kind == "mode-invalid":
            self._send_reply(command.source, "[daemon] invalid selection. Reply with 1, 2, or 3.")
            return
        if command.kind == "attachments-confirm":
            pending = self.pending_attachment_batches.pop(command.source, None)
            if not pending:
                self._send_reply(command.source, "[btw] no pending attachment batch.")
                return
            self._send_reply(command.source, f"[btw] confirmed. Sending {len(pending)} attachments now.")
            if self.notifier is not None:
                for item in pending:
                    self.notifier.send_local_file(item.path, caption=item.reason)
            return
        if command.kind == "attachments-cancel":
            pending = self.pending_attachment_batches.pop(command.source, None)
            if not pending:
                self._send_reply(command.source, "[btw] no pending attachment batch.")
                return
            self._send_reply(command.source, f"[btw] cancelled. Skipped {len(pending)} attachments.")
            return
        if command.kind == "mode":
            updated_mode = _normalize_plan_mode(command.text)
            if updated_mode is None:
                self._send_reply(command.source, "[daemon] invalid mode. Use: off, auto, or record.")
                return
            self.args.run_plan_mode = updated_mode
            self._log_event("daemon.mode.updated", source=command.source, plan_mode=updated_mode)
            if self._child_running():
                if self._forward_to_child("mode", updated_mode, command.source):
                    self._send_reply(
                        command.source,
                        f"[daemon] plan mode updated to {updated_mode} for future runs and forwarded to active run.",
                    )
                else:
                    self._send_reply(
                        command.source,
                        f"[daemon] plan mode updated to {updated_mode} for future runs, but active child bus is unavailable.",
                    )
            else:
                self._send_reply(command.source, f"[daemon] default plan mode updated to {updated_mode}.")
            self._write_status()
            return
        if command.kind == "status":
            self._send_reply(
                command.source,
                format_status(
                    child=self.child,
                    child_objective=self.child_objective,
                    child_operator_messages_path=self.child_operator_messages_path,
                    child_main_prompt_path=self.child_main_prompt_path,
                    child_log_path=self.child_log_path,
                    child_plan_overview_path=self.child_plan_overview_path,
                    child_review_summaries_dir=self.child_review_summaries_dir,
                    child_started_at=self.child_started_at,
                    default_plan_mode=getattr(self.args, "run_plan_mode", "auto"),
                    btw_status=self.btw_agent.status_snapshot(),
                    force_new_session_next_run=read_force_new_session_next_run(self.next_run_new_session_flag_path),
                    last_session_id=resolve_saved_session_id(self.args.run_state_file),
                ),
            )
            return
        if command.kind == "show-plan":
            doc = _read_text_file(self.child_plan_overview_path)
            self._send_reply(command.source, doc or "[daemon] no plan overview markdown available.")
            return
        if command.kind == "show-main-prompt":
            doc = _read_text_file(self.child_main_prompt_path)
            self._send_reply(command.source, doc or "[daemon] no main prompt markdown available.")
            return
        if command.kind == "show-plan-context":
            self._send_reply(
                command.source,
                render_plan_context(
                    operator_messages_path=self.child_operator_messages_path,
                    plan_overview_path=self.child_plan_overview_path,
                    plan_mode=getattr(self.args, "run_plan_mode", "auto"),
                ),
            )
            return
        if command.kind == "show-review":
            target = self.child_review_summaries_dir / "index.md" if self.child_review_summaries_dir else None
            if command.text.strip():
                if self.child_review_summaries_dir is None:
                    self._send_reply(command.source, "[daemon] no reviewer summary markdown available.")
                    return
                try:
                    round_index = int(command.text.strip())
                except ValueError:
                    self._send_reply(command.source, "[daemon] invalid round number for show-review.")
                    return
                target = self.child_review_summaries_dir / f"round-{round_index:03d}.md"
            doc = _read_text_file(target)
            self._send_reply(command.source, doc or "[daemon] no reviewer summary markdown available.")
            return
        if command.kind == "show-review-context":
            self._send_reply(
                command.source,
                render_review_context(
                    operator_messages_path=self.child_operator_messages_path,
                    review_summaries_dir=self.child_review_summaries_dir,
                    state_file=getattr(self.args, "run_state_file", None),
                    check_commands=getattr(self.args, "run_check", []),
                ),
            )
            return
        if command.kind in {"run", "inject"}:
            objective = command.text.strip()
            if not objective:
                self._send_reply(command.source, "[daemon] missing objective. Use /run <objective>.")
                return
            if self._child_running():
                if self._forward_to_child("inject", objective, command.source):
                    if command.kind == "run":
                        self._send_reply(command.source, build_active_run_run_conflict_message())
                    else:
                        self._send_reply(
                            command.source,
                            "[daemon] inject forwarded to active run. "
                            "Child loop will interrupt and apply your new instruction.",
                        )
                else:
                    self._send_reply(command.source, "[daemon] active run exists but child control bus unavailable.")
                return
            # Ask about PPTX report before launching
            self.pending_pptx_run_objective = objective
            self.pending_pptx_run_source = command.source
            self._send_reply(command.source, "Generate a PPTX run report at the end? Reply Y or N")
            return
        if command.kind in {"plan", "review"}:
            if not self._child_running():
                self._send_reply(command.source, "[daemon] no active run for targeted plan/review command.")
                return
            if self._forward_to_child(command.kind, command.text.strip(), command.source):
                self._send_reply(command.source, f"[daemon] {command.kind} forwarded to active run.")
            else:
                self._send_reply(command.source, "[daemon] active run exists but child control bus unavailable.")
            return
        if command.kind == "btw":
            question = command.text.strip()
            if not question:
                self._send_reply(command.source, "[btw] missing question.")
                return

            def on_busy() -> None:
                self._send_reply(command.source, "[btw] side-agent is busy. Wait for the current answer to finish.")

            def on_complete(result) -> None:
                self._send_reply(command.source, result.answer)
                if command.source == "telegram" and result.attachments:
                    if requires_attachment_confirmation(source=command.source, attachment_count=len(result.attachments)):
                        self.pending_attachment_batches[command.source] = list(result.attachments)
                        self._send_reply(
                            command.source,
                            format_attachment_confirmation_message(attachment_count=len(result.attachments)),
                        )
                    elif self.notifier is not None:
                        for item in result.attachments:
                            self.notifier.send_local_file(item.path, caption=item.reason)
                elif result.attachments:
                    self._send_reply(
                        command.source,
                        "[btw] attachments:\n" + "\n".join(f"- {item.path}" for item in result.attachments),
                    )
                self._write_status()

            started = self.btw_agent.start_async(question=question, on_complete=on_complete, on_busy=on_busy)
            if started:
                self._send_reply(command.source, "[btw] side-agent started. It will reply when ready.")
                self._write_status()
            return
        if command.kind == "clock":
            m = re.fullmatch(r"(\d+)h(\d+)min", command.text.strip())
            if not m:
                self._send_reply(
                    command.source,
                    "[clock] invalid format.\n"
                    "Usage: /clock <time> — e.g. `/clock 2h30min`\n"
                    "Format must be `XhXmin` where X is a non-negative integer.\n"
                    "Examples: `/clock 1h0min`, `/clock 0h30min`, `/clock 2h15min`",
                )
                return
            hours = int(m.group(1))
            minutes = int(m.group(2))
            total_seconds = hours * 3600 + minutes * 60
            if total_seconds <= 0:
                self._send_reply(command.source, "[clock] time must be greater than zero. Example: `/clock 0h5min`")
                return
            if self._clock_timer is not None:
                self._clock_timer.cancel()
                self._clock_timer = None
                self._send_reply(command.source, "[clock] previous timer cancelled.")
            src = command.source

            def fire_clock(_h: int = hours, _m: int = minutes, _src: str = src) -> None:
                self._clock_timer = None
                if not self._child_running():
                    self._send_reply(_src, f"[clock] \u23f0 timer expired after {_h}h {_m}min \u2014 no active run.")
                    return
                objective_text = str(self.child_objective or "unknown")
                self._forward_to_child("stop", "", _src)
                assert self.child is not None
                self.child.terminate()
                report = (
                    f"## \u23f0 Clock timeout \u2014 run stopped after {_h}h {_m}min\n\n"
                    f"**Objective**: {objective_text[:500]}\n\n"
                    f"**Elapsed**: {_h}h {_m}min\n\n"
                    "The active run was automatically stopped by `/clock`."
                )
                self._send_reply(_src, report)

            t = threading.Timer(float(total_seconds), fire_clock)
            t.daemon = True
            t.start()
            self._clock_timer = t
            time_desc = f"{hours}h {minutes}min" if hours > 0 else f"{minutes}min"
            self._send_reply(command.source, f"[clock] \u23f0 timer set: run will be stopped in {time_desc} if still active.")
            return
        if command.kind == "stop":
            if self._clock_timer is not None:
                self._clock_timer.cancel()
                self._clock_timer = None
            if not self._child_running():
                self._send_reply(command.source, "[daemon] no active run.")
                return
            if self._forward_to_child("stop", "", command.source):
                self._send_reply(command.source, "[daemon] stop forwarded to active run.")
            else:
                assert self.child is not None
                self.child.terminate()
                self._send_reply(command.source, "[daemon] stop signal sent to active run.")
            return
        if command.kind == "daemon-stop":
            self._send_reply(command.source, "[daemon] stopping daemon.")
            self._stopping = True

    def _start_child(self, objective: str, *, pptx_report: bool = True) -> None:
        assert self.notifier is not None
        timestamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        log_path = self.logs_dir / f"run-{timestamp}.log"
        control_path = self.bus_dir / f"child-control-{timestamp}.jsonl"
        messages_path = self.logs_dir / f"run-{timestamp}-operator_messages.md"
        main_prompt_path = self.logs_dir / f"run-{timestamp}-main_prompt.md"
        plan_overview_path = self.logs_dir / f"run-{timestamp}-plan_overview.md"
        review_summaries_dir = self.logs_dir / f"run-{timestamp}-review"
        force_new_session = consume_force_new_session_next_run(self.next_run_new_session_flag_path)
        resume_session_id = (
            None
            if force_new_session
            else resolve_saved_session_id(self.args.run_state_file) if self.args.run_resume_last_session else None
        )
        self.child_control_bus = JsonlCommandBus(control_path)
        cmd = build_child_command(
            args=self.args,
            objective=objective,
            chat_id=self.chat_id,
            control_file=str(control_path),
            operator_messages_file=str(messages_path),
            main_prompt_file=str(main_prompt_path),
            plan_overview_file=str(plan_overview_path),
            review_summaries_dir=str(review_summaries_dir),
            resume_session_id=resume_session_id,
            force_new_session=force_new_session,
            pptx_report=pptx_report,
        )
        log_file = log_path.open("w", encoding="utf-8")
        self.child = subprocess.Popen(cmd, stdout=log_file, stderr=log_file, text=True, cwd=self.run_cwd)
        self.child_objective = objective
        self.child_log_path = log_path
        self.child_operator_messages_path = messages_path
        self.child_main_prompt_path = main_prompt_path
        self.child_plan_overview_path = plan_overview_path
        self.child_review_summaries_dir = review_summaries_dir
        self.child_started_at = dt.datetime.utcnow()
        self.notifier.send_message(
            "[daemon] launched run\n"
            f"pid={self.child.pid}\n"
            f"objective={objective[:700]}\n"
            f"log={log_path}"
        )
        self._log_event(
            "child.launched",
            pid=self.child.pid,
            objective=objective[:700],
            log_path=str(log_path),
            control_path=str(control_path),
            operator_messages_file=str(messages_path),
            main_prompt_file=str(main_prompt_path),
            plan_overview_file=str(plan_overview_path),
            review_summaries_dir=str(review_summaries_dir),
            resume_session_id=resume_session_id,
            force_new_session=force_new_session,
        )
        self._write_status()

    def _maybe_rewrite_run_objective(self, objective: str, *, source: str) -> str:
        if not getattr(self.args, "run_objective_rewrite", False):
            return objective
        preset = get_preset(self.args.run_model_preset) if getattr(self.args, "run_model_preset", None) else None
        result = rewrite_run_objective(
            runner=self.daemon_runner,
            objective=objective,
            working_dir=str(self.run_cwd),
            project_name=self.run_cwd.name,
            model=(preset.main_model if preset is not None else getattr(self.args, "run_main_model", None)),
            reasoning_effort=(
                preset.main_reasoning_effort if preset is not None else getattr(self.args, "run_main_reasoning_effort", None)
            ),
        )
        if result.failure_reason:
            self._send_reply(source, format_objective_rewrite_failure_message(result))
            self._log_event(
                "run.objective_rewrite.failed",
                source=source,
                objective=objective[:700],
                reason=result.failure_reason[:700],
            )
            return objective
        self._send_reply(source, format_objective_rewrite_message(result))
        self._log_event(
            "run.objective_rewrite.applied" if result.applied else "run.objective_rewrite.kept",
            source=source,
            original_objective=result.original_objective[:700],
            rewritten_objective=result.rewritten_objective[:700],
        )
        return result.rewritten_objective

    def _send_reply(self, source: str, message: str) -> None:
        if source == "telegram":
            assert self.notifier is not None
            self.notifier.send_message(message)
        else:
            print(message, file=sys.stdout)
        self._log_event("reply.sent", source=source, message=message[:700])

    def _forward_to_child(self, kind: str, text: str, source: str) -> bool:
        if not self._child_running() or self.child_control_bus is None:
            return False
        self.child_control_bus.publish(BusCommand(kind=kind, text=text, source=source, ts=time.time()))
        self._log_event("child.command.forwarded", source=source, kind=kind, text=text[:700])
        return True

    def _child_running(self) -> bool:
        return self.child is not None and self.child.poll() is None

    def _check_child(self) -> None:
        if self.child is None:
            return
        rc = self.child.poll()
        if rc is None:
            return
        assert self.notifier is not None
        self.notifier.send_message(
            "[daemon] run finished\n"
            f"exit_code={rc}\n"
            f"objective={str(self.child_objective or '')[:700]}\n"
            f"log={self.child_log_path}"
        )
        self._log_event(
            "child.finished",
            exit_code=rc,
            objective=str(self.child_objective or "")[:700],
            log_path=str(self.child_log_path) if self.child_log_path else None,
        )
        if self._clock_timer is not None:
            self._clock_timer.cancel()
            self._clock_timer = None
        self.child = None
        self.child_control_bus = None

    def _write_status(self) -> None:
        running = self._child_running()
        write_status(
            self.status_path,
            {
                "updated_at": dt.datetime.utcnow().isoformat() + "Z",
                "daemon_pid": os.getpid(),
                "daemon_running": not self._stopping,
                "running": running,
                "default_plan_mode": getattr(self.args, "run_plan_mode", "auto"),
                "force_new_session_next_run": read_force_new_session_next_run(self.next_run_new_session_flag_path),
                "run_check": list(getattr(self.args, "run_check", [])),
                "child_pid": self.child.pid if running and self.child else None,
                "child_objective": self.child_objective,
                "child_operator_messages_path": (
                    str(self.child_operator_messages_path) if self.child_operator_messages_path else None
                ),
                "child_main_prompt_path": str(self.child_main_prompt_path) if self.child_main_prompt_path else None,
                "child_log_path": str(self.child_log_path) if self.child_log_path else None,
                "child_plan_overview_path": str(self.child_plan_overview_path) if self.child_plan_overview_path else None,
                "child_review_summaries_dir": (
                    str(self.child_review_summaries_dir) if self.child_review_summaries_dir else None
                ),
                "child_started_at": self.child_started_at.isoformat() + "Z" if self.child_started_at else None,
                "btw_busy": self.btw_agent.status_snapshot().busy,
                "btw_session_id": self.btw_agent.status_snapshot().session_id,
                "btw_messages_file": self.btw_agent.status_snapshot().messages_file,
                "last_session_id": resolve_saved_session_id(self.args.run_state_file),
                "run_state_file": getattr(self.args, "run_state_file", None),
                "run_cwd": str(self.run_cwd),
                "logs_dir": str(self.logs_dir),
                "bus_dir": str(self.bus_dir),
                "events_log": str(self.events_log),
            },
        )

    def _log_event(self, event_type: str, **kwargs) -> None:
        payload = {
            "ts": dt.datetime.utcnow().isoformat() + "Z",
            "type": event_type,
            **kwargs,
        }
        with self.events_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def _shutdown(self) -> None:
        for channel in reversed(self.control_channels):
            channel.stop()
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
        write_status(
            self.status_path,
            {
                "updated_at": dt.datetime.utcnow().isoformat() + "Z",
                "daemon_pid": os.getpid(),
                "daemon_running": False,
                "running": False,
            },
        )
        self._log_event("daemon.stopped")
        if self.token_lock is not None:
            self.token_lock.release()
        if self.notifier is not None:
            self.notifier.close()


def run_telegram_daemon(args: argparse.Namespace) -> None:
    TelegramDaemonApp(args).run()


def build_child_command(
    *,
    args: argparse.Namespace,
    objective: str,
    chat_id: str,
    control_file: str,
    operator_messages_file: str,
    main_prompt_file: str,
    plan_overview_file: str,
    review_summaries_dir: str,
    resume_session_id: str | None,
    force_new_session: bool = False,
    pptx_report: bool = True,
) -> list[str]:
    preset = get_preset(args.run_model_preset) if args.run_model_preset else None
    main_model = preset.main_model if preset is not None else args.run_main_model
    main_reasoning_effort = preset.main_reasoning_effort if preset is not None else args.run_main_reasoning_effort
    reviewer_model = preset.reviewer_model if preset is not None else args.run_reviewer_model
    reviewer_reasoning_effort = (
        preset.reviewer_reasoning_effort if preset is not None else args.run_reviewer_reasoning_effort
    )
    plan_model = preset.plan_model if preset is not None else getattr(args, "run_plan_model", None)
    plan_reasoning_effort = (
        preset.plan_reasoning_effort
        if preset is not None
        else getattr(args, "run_plan_reasoning_effort", None)
    )
    plan_mode = getattr(args, "run_plan_mode", "auto")
    cmd = [
        *_split_autoloop_command(args.codex_autoloop_bin),
        "--max-rounds",
        str(args.run_max_rounds),
        "--telegram-bot-token",
        args.telegram_bot_token,
        "--telegram-chat-id",
        chat_id,
        "--telegram-events",
        args.run_telegram_events,
        "--telegram-live-interval-seconds",
        str(args.run_telegram_live_interval_seconds),
        "--control-file",
        control_file,
        "--operator-messages-file",
        operator_messages_file,
        "--main-prompt-file",
        main_prompt_file,
        "--plan-overview-file",
        plan_overview_file,
        "--review-summaries-dir",
        review_summaries_dir,
        "--plan-mode",
        plan_mode,
        "--telegram-control-whisper-model",
        args.telegram_control_whisper_model,
        "--telegram-control-whisper-base-url",
        args.telegram_control_whisper_base_url,
        "--telegram-control-whisper-timeout-seconds",
        str(args.telegram_control_whisper_timeout_seconds),
    ]
    if main_model:
        cmd.extend(["--main-model", main_model])
    if main_reasoning_effort:
        cmd.extend(["--main-reasoning-effort", main_reasoning_effort])
    if reviewer_model:
        cmd.extend(["--reviewer-model", reviewer_model])
    if reviewer_reasoning_effort:
        cmd.extend(["--reviewer-reasoning-effort", reviewer_reasoning_effort])
    if plan_model:
        cmd.extend(["--plan-model", plan_model])
    if plan_reasoning_effort:
        cmd.extend(["--plan-reasoning-effort", plan_reasoning_effort])
    if args.telegram_control_whisper:
        cmd.append("--telegram-control-whisper")
    else:
        cmd.append("--no-telegram-control-whisper")
    cmd.append("--no-telegram-control")
    if args.run_telegram_live_updates:
        cmd.append("--telegram-live-updates")
    else:
        cmd.append("--no-telegram-live-updates")
    if args.telegram_control_whisper_api_key:
        cmd.extend(["--telegram-control-whisper-api-key", args.telegram_control_whisper_api_key])
    if getattr(args, "run_copilot_proxy", False):
        cmd.append("--copilot-proxy")
    else:
        cmd.append("--no-copilot-proxy")
    cmd.extend(["--runner-backend", getattr(args, "run_runner_backend", DEFAULT_RUNNER_BACKEND)])
    run_runner_bin = str(getattr(args, "run_runner_bin", "") or "").strip()
    if run_runner_bin:
        cmd.extend(["--runner-bin", run_runner_bin])
    run_copilot_proxy_dir = str(getattr(args, "run_copilot_proxy_dir", "") or "").strip()
    if run_copilot_proxy_dir:
        cmd.extend(["--copilot-proxy-dir", run_copilot_proxy_dir])
    cmd.extend(["--copilot-proxy-port", str(int(getattr(args, "run_copilot_proxy_port", 18080)))])
    if resume_session_id:
        cmd.extend(["--session-id", resume_session_id])
    if args.run_skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    if args.run_full_auto:
        cmd.append("--full-auto")
    if args.run_yolo:
        cmd.append("--yolo")
    for check in args.run_check:
        cmd.extend(["--check", check])
    if args.run_stall_soft_idle_seconds > 0:
        cmd.extend(["--stall-soft-idle-seconds", str(args.run_stall_soft_idle_seconds)])
    if args.run_stall_hard_idle_seconds > 0:
        cmd.extend(["--stall-hard-idle-seconds", str(args.run_stall_hard_idle_seconds)])
    if args.run_state_file:
        cmd.extend(["--state-file", args.run_state_file])
    if args.run_no_dashboard:
        cmd.append("--no-dashboard")
    if pptx_report:
        cmd.append("--pptx-report")
    else:
        cmd.append("--no-pptx-report")
    for add_dir in args.run_add_dir:
        cmd.extend(["--add-dir", add_dir])
    for plugin_dir in args.run_plugin_dir:
        cmd.extend(["--plugin-dir", plugin_dir])
    for file_spec in args.run_file_specs:
        cmd.extend(["--file", file_spec])
    if args.run_worktree_name:
        cmd.extend(["--worktree", args.run_worktree_name])
    if getattr(args, "run_plan_mode", "fully-plan") == "fully-plan":
        cmd.append("--follow-up-phase")
    else:
        cmd.append("--no-follow-up-phase")
    cmd.append(objective)
    return cmd


def format_status(
    *,
    child: subprocess.Popen[str] | None,
    child_objective: str | None,
    child_operator_messages_path: Path | None,
    child_main_prompt_path: Path | None,
    child_log_path: Path | None,
    child_plan_overview_path: Path | None,
    child_review_summaries_dir: Path | None,
    child_started_at: dt.datetime | None,
    default_plan_mode: str,
    btw_status,
    force_new_session_next_run: bool = False,
    last_session_id: str | None = None,
) -> str:
    if child is None or child.poll() is not None:
        base = "[daemon] status=idle"
        if last_session_id:
            base += f"\nlast_session_id={last_session_id}"
        base += f"\ndefault_plan_mode={default_plan_mode}"
        base += f"\nforce_new_session_next_run={force_new_session_next_run}"
        base += f"\nbtw_busy={btw_status.busy}"
        base += f"\nbtw_session_id={btw_status.session_id}"
        if child_main_prompt_path:
            base += f"\nmain_prompt={child_main_prompt_path}"
        if child_plan_overview_path:
            base += f"\nplan_overview={child_plan_overview_path}"
        if child_operator_messages_path:
            base += f"\noperator_messages={child_operator_messages_path}"
        if child_review_summaries_dir:
            base += f"\nreview_summaries_dir={child_review_summaries_dir}"
        return base
    elapsed = "unknown"
    if child_started_at is not None:
        elapsed_seconds = int((dt.datetime.utcnow() - child_started_at).total_seconds())
        elapsed = f"{elapsed_seconds}s"
    return (
        "[daemon] status=running\n"
        f"pid={child.pid}\n"
        f"elapsed={elapsed}\n"
        f"default_plan_mode={default_plan_mode}\n"
        f"force_new_session_next_run={force_new_session_next_run}\n"
        f"btw_busy={btw_status.busy}\n"
        f"btw_session_id={btw_status.session_id}\n"
        f"last_session_id={last_session_id}\n"
        f"objective={str(child_objective or '')[:700]}\n"
        f"operator_messages={child_operator_messages_path}\n"
        f"main_prompt={child_main_prompt_path}\n"
        f"log={child_log_path}\n"
        f"plan_overview={child_plan_overview_path}\n"
        f"review_summaries_dir={child_review_summaries_dir}"
    )


def resolve_saved_session_id(state_file: str | None) -> str | None:
    if not state_file:
        return None
    payload = read_status(state_file)
    if payload is None:
        return None
    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return None


def help_text() -> str:
    return (
        "[daemon] commands\n"
        "/run <objective> - start a new ArgusBot run\n"
        "/new - force the next /run to start in a fresh main session\n"
        "/inject <instruction> - inject instruction to active run (or run if idle)\n"
        "/mode - show a mode selection menu\n"
        "/mode <off|auto|record> - hot-switch daemon default mode and active child mode\n"
        "/btw <question> - ask the side-agent a read-only question without disturbing the main run\n"
        "/confirm-send - confirm and continue sending a pending large attachment batch\n"
        "/cancel-send - cancel a pending large attachment batch\n"
        "/plan <session-goal> - confirm the current session-level goal for planning and forward it to the active plan agent\n"
        "/review <criteria> - send audit criteria to the active reviewer only\n"
        "/show-main-prompt - print the latest main prompt markdown\n"
        "/show-plan - print the latest plan overview markdown\n"
        "/show-plan-context - print current plan directions and inputs\n"
        "/show-review [round] - print reviewer summary markdown\n"
        "/show-review-context - print current reviewer direction, checks, and criteria\n"
        "/status - daemon + child status\n"
        "/stop - stop active run\n"
        "/daemon-stop - stop daemon process\n"
        "/help - show this help\n"
        "[CN] 默认不会自动续跑；如要使用 auto planning / auto follow-up，请先用 /plan 确认当前 session 总目标。\n"
        "[EN] Auto follow-up is disabled by default. Use /plan first to confirm the current session-level goal before auto planning/follow-up.\n"
        "Plain text message is treated as /run when idle.\n"
        "Voice/audio message will be transcribed by Whisper when enabled."
    )


def _normalize_plan_mode(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"off", "auto", "record"}:
        return normalized
    return None


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


def read_force_new_session_next_run(path: str | Path) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    try:
        return p.read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def write_force_new_session_next_run(path: str | Path, value: bool) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if value:
        p.write_text("1", encoding="utf-8")
    else:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            return


def consume_force_new_session_next_run(path: str | Path) -> bool:
    value = read_force_new_session_next_run(path)
    if value:
        write_force_new_session_next_run(path, False)
    return value


def render_plan_context(
    *,
    operator_messages_path: str | Path | None,
    plan_overview_path: str | Path | None,
    plan_mode: str,
) -> str:
    plan_messages, broadcast_messages, _ = _extract_operator_messages(operator_messages_path)
    plan_overview = _read_text_file(plan_overview_path)
    lines = [
        "# Plan Context",
        "",
        f"- Plan mode: `{plan_mode}`",
        "",
        "## Plan-Only Directions",
        "",
    ]
    if plan_messages:
        lines.extend(plan_messages)
    else:
        lines.append("- none")
    lines.extend(["", "## Broadcast Inputs", ""])
    if broadcast_messages:
        lines.extend(broadcast_messages)
    else:
        lines.append("- none")
    if plan_overview:
        lines.extend(["", "## Current Plan Overview", "", plan_overview.strip()])
    return "\n".join(lines).strip() + "\n"


def render_review_context(
    *,
    operator_messages_path: str | Path | None,
    review_summaries_dir: str | Path | None,
    state_file: str | Path | None,
    check_commands: list[str],
) -> str:
    _, broadcast_messages, review_messages = _extract_operator_messages(operator_messages_path)
    completion_summary = _read_text_file(Path(review_summaries_dir) / "completion.md" if review_summaries_dir else None)
    latest_review = _read_latest_review_from_state_file(state_file)
    lines = [
        "# Review Context",
        "",
    ]
    if latest_review:
        lines.extend(
            [
                f"- Latest review status: `{latest_review.get('review_status', '-')}`",
                f"- Latest review reason: `{latest_review.get('review_reason', '-')}`",
                f"- Latest review next action: `{latest_review.get('review_next_action', '-')}`",
                "",
            ]
        )
    lines.extend(
        [
        "## Acceptance Checks",
        "",
    ]
    )
    if check_commands:
        for item in check_commands:
            lines.append(f"- `{item}`")
    else:
        lines.append("- none configured")
    lines.extend(["", "## Review-Only Criteria", ""])
    if review_messages:
        lines.extend(review_messages)
    else:
        lines.append("- none")
    lines.extend(["", "## Broadcast Inputs", ""])
    if broadcast_messages:
        lines.extend(broadcast_messages)
    else:
        lines.append("- none")
    if completion_summary:
        lines.extend(["", "## Latest Review Completion Summary", "", completion_summary.strip()])
    return "\n".join(lines).strip() + "\n"


def _extract_operator_messages(path: str | Path | None) -> tuple[list[str], list[str], list[str]]:
    raw = _read_text_file(path)
    if not raw:
        return [], [], []
    plan_messages: list[str] = []
    broadcast_messages: list[str] = []
    review_messages: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        if "`plan`:" in stripped or "`plan` " in stripped:
            plan_messages.append(stripped)
        elif "`review`:" in stripped or "`review` " in stripped:
            review_messages.append(stripped)
        elif "`broadcast`:" in stripped or "`broadcast` " in stripped:
            broadcast_messages.append(stripped)
    return plan_messages, broadcast_messages, review_messages


def _read_latest_review_from_state_file(path: str | Path | None) -> dict[str, str] | None:
    raw = _read_text_file(path)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    rounds = payload.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        return None
    last = rounds[-1]
    if not isinstance(last, dict):
        return None
    review = last.get("review")
    if not isinstance(review, dict):
        return None
    return {
        "review_status": str(review.get("status", "-")),
        "review_reason": str(review.get("reason", "-")),
        "review_next_action": str(review.get("next_action", "-")),
    }
