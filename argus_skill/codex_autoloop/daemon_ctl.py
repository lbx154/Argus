from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from .apps.daemon_app import render_plan_context, render_review_context
from .daemon_bus import (
    DEFAULT_DAEMON_STATUS_STALE_SECONDS,
    BusCommand,
    JsonlCommandBus,
    inspect_daemon_status,
)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    bus_dir = Path(args.bus_dir).resolve()
    bus_dir.mkdir(parents=True, exist_ok=True)
    command_bus = JsonlCommandBus(bus_dir / "daemon_commands.jsonl")
    status_path = bus_dir / "daemon_status.json"

    if args.subcommand == "status":
        payload = load_status_for_cli(status_path, require_live=False)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(0 if payload.get("daemon_status_state") != "stale" else 1)

    if args.subcommand == "show-plan":
        payload = load_status_for_cli(status_path, require_live=True)
        plan_path = payload.get("child_plan_report_path") or payload.get("child_plan_overview_path")
        print(read_required_text_artifact(plan_path, label="plan overview"))
        raise SystemExit(0)

    if args.subcommand == "show-main-prompt":
        payload = load_status_for_cli(status_path, require_live=True)
        prompt_path = payload.get("child_main_prompt_path")
        print(read_required_text_artifact(prompt_path, label="main prompt"))
        raise SystemExit(0)

    if args.subcommand == "show-plan-context":
        payload = load_status_for_cli(status_path, require_live=True)
        print(
            render_plan_context(
                operator_messages_path=payload.get("child_operator_messages_path") or payload.get("operator_messages_file"),
                plan_overview_path=payload.get("child_plan_report_path") or payload.get("child_plan_overview_path"),
                plan_mode=str(payload.get("default_plan_mode") or payload.get("plan_mode") or "auto"),
            )
        )
        raise SystemExit(0)

    if args.subcommand == "show-review":
        payload = load_status_for_cli(status_path, require_live=True)
        review_dir = payload.get("child_review_summaries_dir")
        if not isinstance(review_dir, str) or not review_dir.strip():
            print("No review summaries dir found in daemon status.")
            raise SystemExit(1)
        base = Path(review_dir)
        target = base / "index.md"
        if args.text:
            try:
                round_index = int(args.text)
            except ValueError:
                print("Round must be an integer.")
                raise SystemExit(1)
            target = base / f"round-{round_index:03d}.md"
        print(read_required_text_artifact(target, label="review summary"))
        raise SystemExit(0)

    if args.subcommand == "show-review-context":
        payload = load_status_for_cli(status_path, require_live=True)
        print(
            render_review_context(
                operator_messages_path=payload.get("child_operator_messages_path") or payload.get("operator_messages_file"),
                review_summaries_dir=payload.get("child_review_summaries_dir"),
                state_file=payload.get("run_state_file"),
                check_commands=list(payload.get("run_check", [])) if isinstance(payload.get("run_check"), list) else [],
            )
        )
        raise SystemExit(0)

    if args.subcommand == "help":
        publish(command_bus, "help", "", source="terminal")
        print("Sent: help")
        raise SystemExit(0)

    if args.subcommand == "run":
        publish(command_bus, "run", args.text, source="terminal")
        print("Sent: run")
        raise SystemExit(0)

    if args.subcommand == "new":
        publish(command_bus, "new", "", source="terminal")
        print("Sent: new")
        raise SystemExit(0)

    if args.subcommand == "btw":
        payload = load_status_for_cli(status_path, require_live=True)
        btw_path_raw = payload.get("btw_messages_file")
        btw_path = Path(str(btw_path_raw)) if isinstance(btw_path_raw, str) and btw_path_raw.strip() else None
        before = ""
        if btw_path is not None and btw_path.exists():
            try:
                before = btw_path.read_text(encoding="utf-8")
            except OSError:
                before = ""
        publish(command_bus, "btw", args.text, source="terminal")
        print("Sent: btw")
        if btw_path is None:
            raise SystemExit(0)
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                after = btw_path.read_text(encoding="utf-8") if btw_path.exists() else ""
            except OSError:
                after = ""
            if len(after) > len(before):
                print("")
                print(after[len(before) :].strip())
                raise SystemExit(0)
            time.sleep(1)
        print("Timed out waiting for btw answer.")
        raise SystemExit(1)

    if args.subcommand == "inject":
        publish(command_bus, "inject", args.text, source="terminal")
        print("Sent: inject")
        raise SystemExit(0)

    if args.subcommand == "mode":
        publish(command_bus, "mode", args.text, source="terminal")
        print("Sent: mode")
        raise SystemExit(0)

    if args.subcommand == "plan":
        publish(command_bus, "plan", args.text, source="terminal")
        print("Sent: plan")
        raise SystemExit(0)

    if args.subcommand == "review":
        publish(command_bus, "review", args.text, source="terminal")
        print("Sent: review")
        raise SystemExit(0)

    if args.subcommand == "show-plan":
        parser.error("show-plan should have returned before publish path.")

    if args.subcommand == "show-review":
        parser.error("show-review should have returned before publish path.")

    if args.subcommand == "stop":
        publish(command_bus, "stop", "", source="terminal")
        print("Sent: stop")
        raise SystemExit(0)

    if args.subcommand == "daemon-stop":
        publish(command_bus, "daemon-stop", "", source="terminal")
        print("Sent: daemon-stop")
        raise SystemExit(0)

    parser.error(f"Unknown command: {args.subcommand}")


def publish(bus: JsonlCommandBus, kind: str, text: str, source: str) -> None:
    bus.publish(BusCommand(kind=kind, text=text, source=source, ts=time.time()))


def load_status_for_cli(status_path: Path, *, require_live: bool) -> dict[str, Any]:
    inspection = inspect_daemon_status(
        status_path,
        stale_after_seconds=DEFAULT_DAEMON_STATUS_STALE_SECONDS,
    )
    if inspection.payload is None:
        raise SystemExit(_print_error("No daemon status found."))
    if require_live and not inspection.is_live:
        raise SystemExit(_print_error(inspection.reason or "Daemon is not running."))
    payload = dict(inspection.payload)
    payload["daemon_status_live"] = inspection.is_live
    if inspection.is_live:
        payload["daemon_status_state"] = "live"
    elif inspection.payload.get("daemon_running") is True:
        payload["daemon_status_state"] = "stale"
    else:
        payload["daemon_status_state"] = "offline"
    if inspection.reason:
        payload["daemon_status_warning"] = inspection.reason
    if payload.get("daemon_running") is True and not inspection.is_live:
        payload["daemon_running"] = False
        payload["running"] = False
    return payload


def read_required_text_artifact(path_value: str | Path | None, *, label: str) -> str:
    if isinstance(path_value, Path):
        path = path_value
    elif isinstance(path_value, str) and path_value.strip():
        path = Path(path_value)
    else:
        raise SystemExit(_print_error(f"No {label} path found in daemon status."))
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(_print_error(f"Unable to read {label}: {exc}"))


def _print_error(message: str) -> int:
    print(message)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="argusbot-daemon-ctl",
        description="Send terminal commands to the ArgusBot Telegram daemon.",
    )
    parser.add_argument(
        "--bus-dir",
        default=".argusbot/bus",
        help="Daemon bus directory.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    run = sub.add_parser("run", help="Start a run objective.")
    run.add_argument("text", help="Objective text.")
    sub.add_parser("new", help="Force the next run to start in a fresh main session.")
    btw = sub.add_parser("btw", help="Ask the read-only BTW side-agent a project question.")
    btw.add_argument("text", help="Question text.")
    inject = sub.add_parser("inject", help="Inject instruction to active run.")
    inject.add_argument("text", help="Instruction text.")
    mode = sub.add_parser("mode", help="Hot-switch plan mode for daemon default and active child.")
    mode.add_argument("text", help="Mode value: off, auto, or record.")
    plan = sub.add_parser("plan", help="Send direction to the plan agent only.")
    plan.add_argument("text", help="Plan direction text.")
    review = sub.add_parser("review", help="Send audit criteria to the reviewer only.")
    review.add_argument("text", help="Review criteria text.")
    sub.add_parser("show-main-prompt", help="Print the latest main prompt markdown from daemon status.")
    sub.add_parser("show-plan", help="Print the current plan overview markdown from daemon status.")
    sub.add_parser("show-plan-context", help="Ask daemon to print current plan directions and inputs.")
    show_review = sub.add_parser("show-review", help="Print review summaries markdown.")
    show_review.add_argument("text", nargs="?", default="", help="Optional round number.")
    sub.add_parser("show-review-context", help="Ask daemon to print current review direction, checks, and criteria.")
    sub.add_parser("stop", help="Stop active run.")
    sub.add_parser("status", help="Read daemon status file.")
    sub.add_parser("help", help="Ask daemon to print help.")
    sub.add_parser("daemon-stop", help="Stop daemon process.")
    return parser


if __name__ == "__main__":
    main()
