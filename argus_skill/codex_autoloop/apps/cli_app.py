from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

from ..adapters.control_channels import FeishuControlChannel, LocalBusControlChannel, TelegramControlChannel
from ..adapters.event_sinks import (
    CompositeEventSink,
    DashboardEventSink,
    FeishuEventSink,
    TelegramEventSink,
    TerminalEventSink,
)
from ..attachment_policy import (
    format_attachment_confirmation_message,
    requires_attachment_confirmation,
)
from ..btw_agent import BtwAgent, BtwConfig
from ..copilot_proxy import build_codex_runner, config_from_args, format_proxy_summary
from ..core.engine import LoopConfig, LoopEngine
from ..core.state_store import LoopStateStore
from ..dashboard import DashboardServer, DashboardStore
from ..feishu_adapter import FeishuConfig, FeishuNotifier
from ..planner import Planner
from ..reviewer import Reviewer
from ..runner_backend import backend_supports_copilot_proxy
from ..telegram_notifier import TelegramConfig, TelegramNotifier, resolve_chat_id
from .shell_utils import (
    control_help_text,
    format_control_status,
    format_mode_menu,
    looks_like_bot_token,
    parse_telegram_events,
    resolve_btw_messages_file,
    resolve_final_report_file,
    resolve_plan_overview_file,
    resolve_pptx_report_file,
    resolve_review_summaries_dir,
    resolve_operator_messages_file,
)


def run_cli(args: Namespace) -> tuple[dict[str, Any], int]:
    objective = " ".join(args.objective).strip()
    dashboard_server: DashboardServer | None = None
    event_sink: CompositeEventSink | None = None
    control_channels: list[object] = []
    copilot_proxy = config_from_args(args)

    operator_messages_file = resolve_operator_messages_file(
        explicit_path=args.operator_messages_file,
        control_file=args.control_file,
        state_file=args.state_file,
    )
    plan_overview_file = resolve_plan_overview_file(
        explicit_path=args.plan_overview_file,
        operator_messages_file=operator_messages_file,
        control_file=args.control_file,
        state_file=args.state_file,
    )
    review_summaries_dir = resolve_review_summaries_dir(
        explicit_path=args.review_summaries_dir,
        operator_messages_file=operator_messages_file,
        control_file=args.control_file,
        state_file=args.state_file,
    )
    final_report_file = resolve_final_report_file(
        explicit_path=args.final_report_file,
        review_summaries_dir=review_summaries_dir,
        operator_messages_file=operator_messages_file,
        control_file=args.control_file,
        state_file=args.state_file,
    )
    if getattr(args, "pptx_report", None) is False:
        pptx_report_file: str | None = None
    else:
        pptx_report_file = resolve_pptx_report_file(
            explicit_path=getattr(args, "pptx_report_file", None),
            operator_messages_file=operator_messages_file,
            control_file=args.control_file,
            state_file=args.state_file,
        )
    btw_messages_file = resolve_btw_messages_file(
        explicit_path=None,
        operator_messages_file=operator_messages_file,
        control_file=args.control_file,
        state_file=args.state_file,
    )
    state_store = LoopStateStore(
        objective=objective,
        state_file=args.state_file,
        operator_messages_file=operator_messages_file,
        plan_overview_file=plan_overview_file,
        review_summaries_dir=review_summaries_dir,
        final_report_file=final_report_file,
        pptx_report_file=pptx_report_file,
        main_prompt_file=args.main_prompt_file,
        check_commands=args.check or [],
        plan_mode=args.plan_mode,
    )
    state_store.record_message(text=objective, source="operator", kind="initial-objective")

    sinks = [TerminalEventSink(live_terminal=args.live_terminal, verbose_events=args.verbose_events)]

    if args.dashboard:
        dashboard_store = DashboardStore(objective=objective)
        dashboard_server = DashboardServer(
            store=dashboard_store,
            host=args.dashboard_host,
            port=args.dashboard_port,
        )
        dashboard_server.start()
        print(
            f"Dashboard running at http://{args.dashboard_host}:{args.dashboard_port}",
            file=sys.stderr,
        )
        sinks.append(DashboardEventSink(dashboard_store))

    if copilot_proxy.enabled and backend_supports_copilot_proxy(args.runner_backend):
        print(f"Copilot proxy mode: {format_proxy_summary(copilot_proxy)}", file=sys.stderr)

    telegram_notifier: TelegramNotifier | None = None
    raw_chat_id = (args.telegram_chat_id or "").strip()
    telegram_requested = bool(args.telegram_bot_token) or raw_chat_id.lower() not in {"", "auto"}
    if telegram_requested:
        if not args.telegram_bot_token:
            raise ValueError("--telegram-bot-token is required when Telegram notifications are enabled.")
        if not looks_like_bot_token(args.telegram_bot_token):
            print(
                "Warning: telegram bot token format looks wrong. "
                "Expected something like '123456:ABCDEF...'.",
                file=sys.stderr,
            )
        telegram_chat_id = raw_chat_id
        if telegram_chat_id.lower() in {"", "null", "none", "auto"}:
            print(
                "Resolving Telegram chat_id from recent updates. "
                "If needed, send /start to your bot now.",
                file=sys.stderr,
            )
            resolved_chat_id = resolve_chat_id(
                bot_token=args.telegram_bot_token,
                timeout_seconds=args.telegram_chat_id_resolve_timeout_seconds,
                poll_interval_seconds=2,
                on_error=lambda msg: print(f"[telegram] {msg}", file=sys.stderr),
            )
            if not resolved_chat_id:
                raise ValueError(
                    "Unable to resolve Telegram chat_id. "
                    "Send /start to bot and try again, or pass --telegram-chat-id explicitly."
                )
            telegram_chat_id = resolved_chat_id
            print(f"Resolved Telegram chat_id={telegram_chat_id}", file=sys.stderr)

        telegram_notifier = TelegramNotifier(
            TelegramConfig(
                bot_token=args.telegram_bot_token,
                chat_id=telegram_chat_id,
                events=parse_telegram_events(args.telegram_events),
                timeout_seconds=args.telegram_timeout_seconds,
                typing_enabled=(not args.telegram_no_typing),
                typing_interval_seconds=args.telegram_typing_interval_seconds,
            ),
            on_error=lambda msg: print(f"[telegram] {msg}", file=sys.stderr),
        )
        print("Telegram notifications enabled.", file=sys.stderr)
        sinks.append(
            TelegramEventSink(
                notifier=telegram_notifier,
                live_updates=args.telegram_live_updates,
                live_interval_seconds=args.telegram_live_interval_seconds,
                on_error=lambda msg: print(f"[telegram] {msg}", file=sys.stderr),
            )
        )

        if args.telegram_control:
            control_channels.append(
                TelegramControlChannel(
                    bot_token=args.telegram_bot_token,
                    chat_id=telegram_chat_id,
                    on_error=lambda msg: print(f"[telegram-control] {msg}", file=sys.stderr),
                    poll_interval_seconds=args.telegram_control_poll_interval_seconds,
                    long_poll_timeout_seconds=args.telegram_control_long_poll_timeout_seconds,
                    plain_text_as_inject=args.telegram_control_plain_text_inject,
                    whisper_enabled=args.telegram_control_whisper,
                    whisper_api_key=args.telegram_control_whisper_api_key,
                    whisper_model=args.telegram_control_whisper_model,
                    whisper_base_url=args.telegram_control_whisper_base_url,
                    whisper_timeout_seconds=args.telegram_control_whisper_timeout_seconds,
                )
            )
            print("Telegram control channel enabled.", file=sys.stderr)

    feishu_notifier: FeishuNotifier | None = None
    feishu_values = [
        str(args.feishu_app_id or "").strip(),
        str(args.feishu_app_secret or "").strip(),
        str(args.feishu_chat_id or "").strip(),
    ]
    feishu_requested = all(feishu_values)
    if any(feishu_values) and not feishu_requested:
        raise ValueError(
            "--feishu-app-id, --feishu-app-secret, and --feishu-chat-id are all required when Feishu is enabled."
        )
    if feishu_requested:
        feishu_notifier = FeishuNotifier(
            FeishuConfig(
                app_id=feishu_values[0],
                app_secret=feishu_values[1],
                chat_id=feishu_values[2],
                receive_id_type=args.feishu_receive_id_type,
                events=parse_telegram_events(args.feishu_events),
                timeout_seconds=args.feishu_timeout_seconds,
            ),
            on_error=lambda msg: print(f"[feishu] {msg}", file=sys.stderr),
        )
        print("Feishu notifications enabled.", file=sys.stderr)
        sinks.append(
            FeishuEventSink(
                notifier=feishu_notifier,
                live_updates=args.feishu_live_updates,
                live_interval_seconds=args.feishu_live_interval_seconds,
                on_error=lambda msg: print(f"[feishu] {msg}", file=sys.stderr),
            )
        )
        if args.feishu_control:
            control_channels.append(
                FeishuControlChannel(
                    app_id=feishu_values[0],
                    app_secret=feishu_values[1],
                    chat_id=feishu_values[2],
                    on_error=lambda msg: print(f"[feishu-control] {msg}", file=sys.stderr),
                    poll_interval_seconds=args.feishu_control_poll_interval_seconds,
                    plain_text_kind=("inject" if args.feishu_control_plain_text_inject else "run"),
                )
            )
            print("Feishu control channel enabled.", file=sys.stderr)

    if args.control_file:
        control_channels.append(
            LocalBusControlChannel(
                path=args.control_file,
                source="terminal",
                on_error=lambda msg: print(f"[control] {msg}", file=sys.stderr),
                poll_interval_seconds=args.control_poll_interval_seconds,
            )
        )
        print(f"Local control channel enabled: {args.control_file}", file=sys.stderr)

    def reply_to_source(source: str, message: str) -> None:
        if source == "telegram" and telegram_notifier is not None:
            telegram_notifier.send_message(message)
            return
        if source == "feishu" and feishu_notifier is not None:
            feishu_notifier.send_message(message)
            return
        print(message, file=sys.stderr)

    btw_agent = BtwAgent(
        runner=build_codex_runner(
            backend=args.runner_backend,
            runner_bin=args.runner_bin,
            config=copilot_proxy,
        ),
        config=BtwConfig(
            working_dir=str(Path.cwd()),
            model=args.plan_model or args.reviewer_model or args.main_model,
            reasoning_effort=args.plan_reasoning_effort or args.reviewer_reasoning_effort or args.main_reasoning_effort,
            messages_file=btw_messages_file,
        ),
    )
    pending_attachment_batches: dict[str, list[Any]] = {}

    def send_attachment_batch(source: str, attachments: list[Any]) -> None:
        if source == "telegram" and telegram_notifier is not None:
            for item in attachments:
                telegram_notifier.send_local_file(item.path, caption=item.reason)
            return
        if source == "feishu" and feishu_notifier is not None:
            for item in attachments:
                feishu_notifier.send_local_file(item.path, caption=item.reason)
            return
        attachment_lines = ["[btw] attachments:"] + [f"- {item.path}" for item in attachments]
        reply_to_source(source, "\n".join(attachment_lines))

    def start_btw(question: str, source: str) -> None:
        normalized = question.strip()
        if not normalized:
            reply_to_source(source, "[btw] missing question.")
            return

        def on_busy() -> None:
            reply_to_source(source, "[btw] side-agent is busy. Wait for the current answer to finish.")

        def on_complete(result) -> None:
            reply_to_source(source, result.answer)
            if not result.attachments:
                return
            if requires_attachment_confirmation(source=source, attachment_count=len(result.attachments)):
                pending_attachment_batches[source] = list(result.attachments)
                reply_to_source(
                    source,
                    format_attachment_confirmation_message(attachment_count=len(result.attachments)),
                )
                return
            send_attachment_batch(source, list(result.attachments))

        started = btw_agent.start_async(question=normalized, on_complete=on_complete, on_busy=on_busy)
        if started:
            reply_to_source(source, "[btw] side-agent started. It will reply when ready.")

    def on_control_command(command) -> None:
        if command.kind == "inject":
            state_store.request_inject(command.text, source=command.source)
            if command.source in {"telegram", "feishu"}:
                reply_to_source(
                    command.source,
                    "[autoloop] control ack\n"
                    "Action: inject\n"
                    "Main agent will be interrupted and resumed with your new instruction.",
                )
            else:
                print("[control] local inject received.", file=sys.stderr)
            return
        if command.kind == "mode":
            updated_mode = state_store.request_plan_mode(command.text, source=command.source)
            if updated_mode is None:
                reply_to_source(command.source, "[autoloop] invalid mode. Use: off, auto, or record.")
            else:
                reply_to_source(
                    command.source,
                    f"[autoloop] control ack\nAction: mode\nplan_mode={updated_mode}",
                )
            return
        if command.kind == "mode-menu":
            reply_to_source(command.source, format_mode_menu(state_store.current_plan_mode()))
            return
        if command.kind == "mode-invalid":
            reply_to_source(command.source, "[autoloop] invalid selection. Reply with 1, 2, or 3.")
            return
        if command.kind == "attachments-confirm":
            pending = pending_attachment_batches.pop(command.source, None)
            if not pending:
                reply_to_source(command.source, "[btw] no pending attachment batch.")
                return
            reply_to_source(command.source, f"[btw] confirmed. Sending {len(pending)} attachments now.")
            send_attachment_batch(command.source, pending)
            return
        if command.kind == "attachments-cancel":
            pending = pending_attachment_batches.pop(command.source, None)
            if not pending:
                reply_to_source(command.source, "[btw] no pending attachment batch.")
                return
            reply_to_source(command.source, f"[btw] cancelled. Skipped {len(pending)} attachments.")
            return
        if command.kind == "btw":
            start_btw(command.text, command.source)
            return
        if command.kind == "plan":
            state_store.request_plan_direction(command.text, source=command.source)
            reply_to_source(
                command.source,
                "[autoloop] control ack\n"
                "Action: plan\n"
                "Plan-only direction recorded and will be applied by the plan agent.",
            )
            return
        if command.kind == "review":
            state_store.request_review_criteria(command.text, source=command.source)
            reply_to_source(
                command.source,
                "[autoloop] control ack\n"
                "Action: review\n"
                "Reviewer-only audit criteria recorded for the next review pass.",
            )
            return
        if command.kind == "stop":
            state_store.request_stop(source=command.source)
            if command.source in {"telegram", "feishu"}:
                reply_to_source(
                    command.source,
                    "[autoloop] control ack\n"
                    "Action: stop\n"
                    "Current run will be interrupted and loop will stop.",
                )
            else:
                print("[control] local stop received.", file=sys.stderr)
            return
        if command.kind == "status":
            reply_to_source(command.source, format_control_status(state_store.runtime_snapshot()))
            return
        if command.kind == "help":
            reply_to_source(command.source, control_help_text())
            return
        if command.kind == "show-plan":
            doc = state_store.read_plan_overview_markdown()
            reply_to_source(command.source, doc or "[autoloop] no plan overview markdown available yet.")
            return
        if command.kind == "show-main-prompt":
            doc = state_store.read_main_prompt_markdown()
            reply_to_source(command.source, doc or "[autoloop] no main prompt markdown available yet.")
            return
        if command.kind == "show-review":
            round_index = None
            raw = command.text.strip()
            if raw:
                try:
                    round_index = int(raw)
                except ValueError:
                    reply_to_source(command.source, "[autoloop] invalid round number for /show-review.")
                    return
            doc = state_store.read_review_summaries_markdown(round_index=round_index)
            reply_to_source(
                command.source,
                doc or "[autoloop] no reviewer summary markdown available for that request.",
            )
            return
        if command.kind == "show-plan-context":
            reply_to_source(command.source, state_store.render_plan_context_markdown())
            return
        if command.kind == "show-review-context":
            reply_to_source(command.source, state_store.render_review_context_markdown())
            return
        if command.kind == "run":
            reply_to_source(
                command.source,
                "[autoloop] /run is available in daemon mode.\n"
                "Current loop is already active; use /inject, /status, or /stop.",
            )
            return
        if command.kind == "new":
            reply_to_source(
                command.source,
                "[autoloop] /new is available in daemon mode.\n"
                "Use it there to force the next /run to start in a fresh main session.",
            )
            return
            
    for channel in control_channels:
        channel.start(on_control_command)

    event_sink = CompositeEventSink(sinks)
    runner = build_codex_runner(
        backend=args.runner_backend,
        runner_bin=args.runner_bin,
        config=copilot_proxy,
        event_callback=event_sink.handle_stream_line,
    )
    reviewer = Reviewer(runner=runner)
    planner = Planner(runner=runner) if args.plan_mode != "off" else None
    engine = LoopEngine(
        runner=runner,
        reviewer=reviewer,
        planner=planner,
        state_store=state_store,
        event_sink=event_sink,
        config=LoopConfig(
            objective=objective,
            max_rounds=args.max_rounds,
            max_no_progress_rounds=args.max_no_progress_rounds,
            check_commands=args.check or [],
            check_timeout_seconds=args.check_timeout_seconds,
            main_model=args.main_model,
            main_reasoning_effort=args.main_reasoning_effort,
            reviewer_model=args.reviewer_model,
            reviewer_reasoning_effort=args.reviewer_reasoning_effort,
            plan_mode=args.plan_mode,
            plan_model=args.plan_model,
            plan_reasoning_effort=args.plan_reasoning_effort,
            main_extra_args=args.main_extra_arg or [],
            reviewer_extra_args=args.reviewer_extra_arg or [],
            plan_extra_args=args.plan_extra_arg or [],
            skip_git_repo_check=args.skip_git_repo_check,
            full_auto=args.full_auto,
            dangerous_yolo=args.yolo,
            stall_soft_idle_seconds=args.stall_soft_idle_seconds,
            stall_hard_idle_seconds=args.stall_hard_idle_seconds,
            initial_session_id=args.session_id,
            main_add_dirs=args.add_dir,
            main_plugin_dirs=args.plugin_dir,
            main_file_specs=args.file_specs,
            main_worktree_name=args.worktree_name,
            allow_follow_up_phase=args.follow_up_phase,
        ),
    )

    try:
        result = engine.run()
        payload = {
            "success": result.success,
            "session_id": result.session_id,
            "stop_reason": result.stop_reason,
            "plan_mode": args.plan_mode,
            "main_prompt_file": state_store.main_prompt_path(),
            "plan_overview_file": state_store.plan_overview_path(),
            "review_summaries_dir": state_store.review_summaries_dir(),
            "final_report_file": state_store.final_report_path(),
            "final_report_ready": state_store.has_final_report(),
            "pptx_report_file": state_store.pptx_report_path(),
            "pptx_report_ready": state_store.has_pptx_report(),
            "rounds": [
                {
                    "round": item.round_index,
                    "thread_id": item.thread_id,
                    "main_exit_code": item.main_exit_code,
                    "main_turn_completed": item.main_turn_completed,
                    "main_turn_failed": item.main_turn_failed,
                    "review_status": item.review.status,
                    "review_confidence": item.review.confidence,
                    "review_reason": item.review.reason,
                    "check_count": len(item.checks),
                    "checks_passed": all(check.passed for check in item.checks),
                    "plan_next_explore": item.plan.next_explore if item.plan is not None else None,
                }
                for item in result.rounds
            ],
        }
        return payload, 0 if result.success else 2
    finally:
        for channel in reversed(control_channels):
            channel.stop()
        if event_sink is not None:
            event_sink.close()
        if dashboard_server is not None:
            dashboard_server.stop()
