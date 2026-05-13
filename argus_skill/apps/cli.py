"""argus-skill CLI — single-entry 7×24 lifetime agent.

The product has exactly one positioning: a long-running supervised
coding agent that drains a backlog forever. There is therefore exactly
one entry point — ``argus-skill`` — which:

* drops you into the unified life REPL (the cockpit), and
* by default ensures a detached daemon is alive draining the backlog
  in the background even after you log out.

Top-level flags control daemon lifecycle and read-only operator help
(``--daemon``, ``--daemon-fg``, ``--daemon-stop``, ``--status``,
``--daemon-runbook``, ``--no-daemon``). There are no other subcommands
— earlier ad-hoc ``run`` / ``list-skills`` modes were removed because
they fragmented the mental model and competed with the backlog-driven
workflow.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from ..core import paths as core_paths
from ..life.status import count_backlog_statuses, select_current_running_item
from ._inbox import count_pending_inbox_messages, format_inbox_event, queue_inbox_message
from ._target_paths import resolve_life_root


def build_parser() -> argparse.ArgumentParser:
    from .. import __version__

    parser = argparse.ArgumentParser(
        prog="argus-skill",
        description="argus-skill — 7×24 supervised lifetime coding agent",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"argus-skill {__version__}",
    )

    daemon_grp = parser.add_argument_group("7×24 daemon")
    daemon_grp.add_argument(
        "--daemon",
        action="store_true",
        help="start a detached background worker that drains the backlog forever",
    )
    daemon_grp.add_argument(
        "--daemon-fg",
        action="store_true",
        help="run the worker in the foreground (for systemd / debugging)",
    )
    daemon_grp.add_argument(
        "--daemon-stop",
        action="store_true",
        help="send SIGTERM to the current project's daemon",
    )
    daemon_grp.add_argument(
        "--status",
        action="store_true",
        help="print the current project daemon + backlog status and exit",
    )
    daemon_grp.add_argument(
        "--daemon-runbook",
        action="store_true",
        help="print the daemon-safe upgrade / restart playbook and exit",
    )
    daemon_grp.add_argument(
        "--no-daemon",
        action="store_true",
        help="skip auto-spawning the background daemon when entering the REPL",
    )
    daemon_grp.add_argument(
        "--life-dir",
        default=None,
        help="override the global argus-skill root (default: ~/.argus-skill)",
    )
    daemon_grp.add_argument(
        "--continuous",
        action="store_true",
        help="enable continuous planner mode (daemon generates new tasks "
             "when backlog is empty)",
    )
    daemon_grp.add_argument(
        "--objective",
        default="",
        help="continuous improvement objective (used with --continuous)",
    )

    cockpit_grp = parser.add_argument_group("cockpit")
    cockpit_grp.add_argument(
        "--watch",
        action="store_true",
        help="open the live read-only cockpit for the current project",
    )
    cockpit_grp.add_argument(
        "--notify",
        metavar="MSG",
        help="append a nudge message to the supervisor's inbox (the next "
             "engineer round picks it up as operator guidance)",
    )
    cockpit_grp.add_argument(
        "--follow",
        action="store_true",
        help="stream daemon events to terminal in real-time "
             "(like tail -f, Ctrl-C to stop)",
    )
    cockpit_grp.add_argument(
        "--init-identity",
        action="store_true",
        help="run the interactive identity-card wizard "
             "(never overwrites an existing card)",
    )

    skills_grp = parser.add_argument_group("skill admin")
    skills_grp.add_argument(
        "--skill-stats",
        action="store_true",
        help="print empirical skill effectiveness report (hit-rate, "
             "mean rounds with/without skill) and exit",
    )
    skills_grp.add_argument(
        "--skill-stats-json",
        action="store_true",
        help="render the skill-stats output as JSON instead of plain text",
    )
    skills_grp.add_argument(
        "--skill-cleanse",
        action="store_true",
        help="strip historic 'Memory context' boilerplate from existing skill "
             "task_history entries (idempotent migration)",
    )
    skills_grp.add_argument(
        "--skill-compact",
        action="store_true",
        help="cluster near-duplicate skills and propose archiving redundant "
             "ones; pass --apply to actually archive (otherwise dry-run)",
    )
    skills_grp.add_argument(
        "--apply",
        action="store_true",
        help="with --skill-compact / --skill-cleanse: actually mutate disk "
             "(default is dry-run)",
    )
    skills_grp.add_argument(
        "--sim-threshold",
        type=float,
        default=None,
        help="cosine-similarity threshold for --skill-compact clustering "
             "(default 0.55)",
    )
    skills_grp.add_argument(
        "--skills-dir",
        default=None,
        help="override skills directory (default: global skills root)",
    )

    return parser


def _continuous_contract_error(
    *,
    continuous: bool,
    objective: str,
    backend: str,
) -> str:
    from ..daemon.life_worker import continuous_mode_error
    return continuous_mode_error(backend, continuous, objective)


def _resolve_global_root(args: argparse.Namespace) -> Path:
    return resolve_life_root(args.life_dir)


def _resolve_project_bundle(args: argparse.Namespace):
    from ..life import MemoryBundle

    return MemoryBundle.for_cwd(Path.cwd(), global_root=_resolve_global_root(args))


def _resolve_follow_events_path(args: argparse.Namespace) -> Path:
    if args.life_dir:
        explicit = core_paths.resolve_runtime_path(args.life_dir, context="--life-dir")
        if explicit.name == "events.jsonl":
            return explicit
    bundle = _resolve_project_bundle(args)
    return bundle.project.root / "events.jsonl"


_FOLLOW_LAYER_LABELS = {
    "engineer": "L1 工程师",
    "reviewer": "L2 审查员",
    "critic": "L3 评审员",
    "planner": "L4 规划师",
}
_FOLLOW_HEARTBEAT_SECONDS = 20.0


def _follow_layer_label(layer: str | None) -> str:
    return _FOLLOW_LAYER_LABELS.get(layer or "", layer or "agent")


def _follow_layer_from_event(event: dict, current: str) -> str:
    layer = event.get("agent_layer")
    if isinstance(layer, str) and layer:
        return layer
    etype = str(event.get("type") or "")
    if etype in {"life.mission.started", "loop.start", "round.start", "round.main.completed"}:
        return "engineer"
    if etype in {"round.review.started", "round.review.completed"}:
        return "reviewer"
    if etype in {"life.iteration.critic", "life.iteration.continued"}:
        return "critic"
    if etype.startswith("life.planner."):
        return "planner"
    return current


def _clean_follow_text(text: str, *, limit: int = 220) -> str:
    import re

    text = str(text or "")
    text = re.sub(r"```[a-zA-Z0-9_-]*", " ", text)
    text = text.replace("```", " ")
    text = re.sub(r"\[([^\]]+)\]\(\(?[^)\n]+\)?\)", r"\1", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _verification_summary(text: str) -> str | None:
    lowered = text.lower()
    if "verification" not in lowered and "verbatim" not in lowered:
        return None
    parts: list[str] = []
    if "[100%]" in text or " passed" in lowered:
        parts.append("tests passed")
    if "All checks passed!" in text:
        parts.append("ruff passed")
    if "Success: no issues found" in text:
        parts.append("mypy passed")
    elif "python -m mypy" in text or "note:" in text:
        parts.append("mypy completed")
    if not parts:
        return None
    return "✅ 验证：" + " · ".join(dict.fromkeys(parts))


def _json_object_from_text(text: str) -> dict | None:
    import json

    stripped = str(text or "").strip()
    if not stripped:
        return None
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(stripped[start:end + 1])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _select_backlog_row_by_id(
    rows: Sequence[dict[str, Any]],
    item_id: str,
) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("id") or "") == item_id:
            return row
    return None


def _read_backlog_rows(backlog_path: Path) -> list[dict[str, Any]]:
    import json

    rows: list[dict[str, Any]] = []
    try:
        with backlog_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _format_follow_mission_context(
    event: dict,
    *,
    mission_context: dict[str, str] | None = None,
) -> list[str]:
    context = mission_context or {}
    item_id = str(event.get("item_id") or context.get("item_id") or "")
    title = str(event.get("title") or context.get("title") or "")
    objective = str(event.get("objective") or context.get("objective") or "")
    bits = [f"item_id={item_id or '-'}"]
    bits.append(
        f"title={_clean_follow_text(title, limit=90) if title else '-'}"
    )
    bits.append(
        f"objective={_clean_follow_text(objective, limit=120) if objective else '-'}"
    )
    return bits


def _format_follow_agent_message(layer: str, text: str) -> str:
    summary = _verification_summary(text)
    if summary:
        return summary
    data = _json_object_from_text(text)
    if data:
        if layer == "reviewer":
            status = data.get("status", "?")
            conf = data.get("confidence")
            reason = _clean_follow_text(str(data.get("reason") or ""), limit=140)
            conf_part = f" · conf={conf}" if conf is not None else ""
            return f"💭 reviewer verdict: {status}{conf_part}" + (
                f" · {reason}" if reason else ""
            )
        if layer == "critic":
            stop = bool(data.get("stop"))
            improvements = data.get("improvements") or []
            count = len(improvements) if isinstance(improvements, list) else 0
            reason = _clean_follow_text(str(data.get("reason") or ""), limit=140)
            verdict = "stop" if stop else f"continue · {count} improvement(s)"
            return f"💭 critic verdict: {verdict}" + (f" · {reason}" if reason else "")
        if layer == "planner":
            done = bool(data.get("project_done"))
            tasks = data.get("new_tasks") or []
            count = len(tasks) if isinstance(tasks, list) else 0
            reason = _clean_follow_text(str(data.get("reason") or ""), limit=140)
            verdict = "project done" if done else f"queue {count} task(s)"
            return f"💭 planner verdict: {verdict}" + (f" · {reason}" if reason else "")
    return "💭 " + _clean_follow_text(text, limit=240)


def _format_follow_command(event: dict) -> str:
    from ..life.notify import _annotate_progress_result, _parse_command

    event_for_render = dict(event)
    cmd = str(event.get("text") or "")
    parsed = _parse_command(cmd)
    excerpt = str(event.get("output_excerpt") or "")
    compact = excerpt
    if "pytest" in cmd and "[100%]" in excerpt:
        compact = "pytest passed [100%]"
    elif "ruff check" in cmd and "All checks passed!" in excerpt:
        compact = "All checks passed!"
    elif "mypy" in cmd and "Success: no issues found" in excerpt:
        compact = "mypy passed"
    elif "mypy" in cmd and "note:" in excerpt:
        compact = "mypy completed (notes omitted)"
    elif parsed.startswith(("📖", "🔍", "📁", "📂", "🔎")) and not _command_failed(event):
        compact = ""
    if compact:
        event_for_render["output_excerpt"] = compact
    else:
        event_for_render.pop("output_excerpt", None)
    return _annotate_progress_result(parsed, event_for_render)


def _format_follow_planner_task_added(event: dict) -> str:
    bits = ["added"]
    if event.get("item_id"):
        bits.append(f"item_id={event['item_id']}")
    if event.get("title"):
        bits.append(f"title={_clean_follow_text(str(event['title']), limit=90)}")
    if event.get("objective"):
        bits.append(f"objective={_clean_follow_text(str(event['objective']), limit=120)}")
    return f"📋 [{_follow_layer_label('planner')}] " + " · ".join(bits)


def _format_follow_planner_task_skipped(event: dict) -> str:
    bits = ["skipped duplicate"]
    if event.get("title"):
        bits.append(f"title={_clean_follow_text(str(event['title']), limit=90)}")
    if event.get("objective"):
        bits.append(f"objective={_clean_follow_text(str(event['objective']), limit=120)}")
    if event.get("matched_item_id"):
        bits.append(f"matched_item_id={event['matched_item_id']}")
    if event.get("matched_status"):
        bits.append(f"matched_status={event['matched_status']}")
    reason = _clean_follow_text(str(event.get("reason") or ""), limit=140)
    if reason:
        bits.append(f"reason={reason}")
    return f"⏭️ [{_follow_layer_label('planner')}] " + " · ".join(bits)


def _command_failed(event: dict) -> bool:
    status = str(event.get("status") or "").lower()
    exit_code = event.get("exit_code")
    return status == "failed" or (
        isinstance(exit_code, int) and exit_code not in (0, None)
    )


def _format_follow_event(
    event: dict,
    current_layer: str,
    *,
    mission_context: dict[str, str] | None = None,
) -> str | None:
    inbox_line = format_inbox_event(event) if isinstance(event, dict) else None
    if inbox_line is not None:
        return f"  {inbox_line}"

    etype = str(event.get("type") or "")
    layer = _follow_layer_from_event(event, current_layer)
    label = _follow_layer_label(layer)

    if etype == "engineer.progress":
        kind = str(event.get("kind") or "")
        text = str(event.get("text") or "")
        if not text:
            return None
        if kind == "agent_message":
            return f"  [{label}] {_format_follow_agent_message(layer, text)}"
        if kind == "command_execution":
            return f"  [{label}] {_format_follow_command(event)}"
        if kind == "reasoning":
            if os.environ.get("ARGUS_SKILL_SHOW_REASONING", "0").lower() not in (
                "1", "true", "yes", "on",
            ):
                return None
            return f"  [{label}] 🧠 {_clean_follow_text(text, limit=180)}"
        return f"  [{label}] ▸ {_clean_follow_text(text, limit=160)}"

    if etype == "life.mission.started":
        bits = ["started", *_format_follow_mission_context(event, mission_context=mission_context)]
        return f"\n🚀 [{_follow_layer_label('engineer')}] " + " · ".join(bits)

    if etype == "life.phase.started":
        bits = [f"进入 [{label}]"]
        if event.get("round_index"):
            bits.append(f"round={event['round_index']}")
        if event.get("iteration_cycle"):
            bits.append(
                f"iteration={event['iteration_cycle']}/{event.get('iteration_max', '?')}"
            )
        return "🔄 " + " · ".join(bits)

    if etype == "round.review.started":
        return f"🔄 进入 [{_follow_layer_label('reviewer')}] · round={event.get('round_index', '?')}"

    if etype == "round.main.completed":
        return f"✅ [{_follow_layer_label('engineer')}] completed · round={event.get('round_index', '?')}"

    if etype == "round.review.completed":
        status = event.get("status", "?")
        conf = event.get("confidence")
        reason = _clean_follow_text(str(event.get("reason") or ""), limit=140)
        conf_part = f" · conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
        return f"✅ [{_follow_layer_label('reviewer')}] completed · status={status}{conf_part}" + (
            f" · {reason}" if reason else ""
        )

    if etype == "life.iteration.critic":
        stop = bool(event.get("stop"))
        count = int(event.get("improvement_count") or 0)
        reason = _clean_follow_text(str(event.get("reason") or ""), limit=150)
        verdict = "stop" if stop else f"continue · {count} improvement(s)"
        return f"👔 [{_follow_layer_label('critic')}] {verdict}" + (
            f" · {reason}" if reason else ""
        )

    if etype == "life.iteration.continued":
        return f"🔁 [{_follow_layer_label('critic')}] queued next iteration · cycle={event.get('cycles_done', '?')}/{event.get('cycles_max', '?')}"

    if etype == "life.planner.start":
        obj = _clean_follow_text(str(event.get("objective") or ""), limit=120)
        return f"\n📋 [{_follow_layer_label('planner')}] planning" + (
            f" · {obj}" if obj else ""
        )

    if etype == "life.planner.verdict":
        if event.get("project_done"):
            return f"🏁 [{_follow_layer_label('planner')}] project done"
        return f"📋 [{_follow_layer_label('planner')}] queued {event.get('enqueued_tasks', event.get('task_count', '?'))} task(s)"

    if etype == "life.planner.task_added":
        return _format_follow_planner_task_added(event)

    if etype == "life.planner.task_skipped":
        return _format_follow_planner_task_skipped(event)

    if etype == "life.planner.error":
        return f"⚠️ [{_follow_layer_label('planner')}] planner error · {_clean_follow_text(str(event.get('error') or event.get('text') or ''), limit=160)}"

    if etype == "life.mission.completed":
        status = event.get("status", "?")
        raw_iteration = event.get("iteration")
        iter_info = raw_iteration if isinstance(raw_iteration, dict) else {}
        if iter_info.get("requeued"):
            bits = ["mission round complete", "requeued by critic", f"status={status}"]
        else:
            bits = [
                "mission complete",
                f"status={status}",
                f"success={event.get('success')}",
            ]
        bits.extend(_format_follow_mission_context(event, mission_context=mission_context))
        return "✅ " + " · ".join(bits)

    if etype == "life.mission.failed":
        return f"❌ mission failed · {_clean_follow_text(str(event.get('reason') or event.get('error') or ''), limit=160)}"

    if etype == "loop.start":
        return f"▶️ [{_follow_layer_label('engineer')}] {_clean_follow_text(str(event.get('text') or ''), limit=180)}"

    if etype == "round.start":
        return f"▶️ [{_follow_layer_label('engineer')}] {event.get('text', 'round started')}"

    if etype == "loop.done":
        return f"🏁 loop done · {_clean_follow_text(str(event.get('text') or ''), limit=160)}"

    return None


def _daemon_alive_for_events_path(events_path: Path) -> bool | None:
    pid_path = events_path.parent / "daemon.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _format_follow_heartbeat(events_path: Path, current_layer: str, idle_seconds: float) -> str:
    alive = _daemon_alive_for_events_path(events_path)
    if alive is True:
        state = "daemon alive"
    elif alive is False:
        state = "daemon not running"
    else:
        state = "daemon state unknown"
    return (
        f"  ⏳ [{_follow_layer_label(current_layer)}] waiting "
        f"{_format_short_duration(idle_seconds)} without new events · {state} · "
        "normal during LLM calls"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.skill_stats = bool(args.skill_stats or args.skill_stats_json)
    backend_default = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    continuous_error = _continuous_contract_error(
        continuous=bool(args.continuous),
        objective=str(getattr(args, "objective", "") or ""),
        backend=backend_default,
    )
    if continuous_error:
        sys.stderr.write(f"argus-skill: {continuous_error}\n")
        return 2

    # ---- mutual exclusion -----------------------------------------
    # Action-style flags pick exactly one mission; --no-daemon and
    # --life-dir are modifiers and may combine with any of them.
    action_flags = (
        bool(args.daemon)
        + bool(args.daemon_fg)
        + bool(args.daemon_stop)
        + bool(args.status)
        + bool(args.daemon_runbook)
        + bool(args.watch)
        + bool(args.follow)
        + bool(args.notify)
        + bool(args.init_identity)
        + bool(args.skill_stats)
        + bool(args.skill_cleanse)
        + bool(args.skill_compact)
    )
    if action_flags > 1:
        sys.stderr.write(
            "argus-skill: --daemon / --daemon-fg / --daemon-stop / --status / "
            "--daemon-runbook / --watch / --follow / --notify / --init-identity / "
            "--skill-stats / --skill-cleanse / --skill-compact are mutually "
            "exclusive.\n"
        )
        return 2
    if args.daemon:
        return _run_with_path_resolution_errors(
            lambda: _cmd_daemon_start(args, foreground=False)
        )
    if args.daemon_fg:
        return _run_with_path_resolution_errors(
            lambda: _cmd_daemon_start(args, foreground=True)
        )
    if args.daemon_stop:
        return _run_with_path_resolution_errors(lambda: _cmd_daemon_stop(args))
    if args.status:
        return _run_with_path_resolution_errors(lambda: _cmd_status(args))
    if args.daemon_runbook:
        return _run_with_path_resolution_errors(lambda: _cmd_daemon_runbook(args))
    if args.watch:
        return _run_with_path_resolution_errors(lambda: _cmd_watch(args))
    if args.follow:
        return _run_with_path_resolution_errors(lambda: _cmd_follow(args))
    if args.notify:
        return _run_with_path_resolution_errors(lambda: _cmd_notify(args))
    if args.init_identity:
        return _run_with_path_resolution_errors(lambda: _cmd_init_identity(args))
    if args.skill_stats:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_stats(args))
    if args.skill_cleanse:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_cleanse(args))
    if args.skill_compact:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_compact(args))

    # Default path: drop into the unified life REPL. The REPL itself
    # auto-spawns a background daemon (unless ``--no-daemon`` was given
    # or one is already alive) so the agent keeps draining the backlog
    # 24/7 even after the operator detaches.
    from ._life_repl import run_life_chat_loop

    repl_args = argparse.Namespace(
        life_dir=args.life_dir,
        color=None,
        backend=backend_default,
        scientist_model=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4"),
        engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL",
                                      "gpt-5.4-mini"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL"),
        plan_mode="auto",
        plan_model=None,
        max_rounds=500,
        check=[],
        workdir=None,
        no_daemon=bool(args.no_daemon),
        continuous=bool(args.continuous),
        objective=str(getattr(args, "objective", "") or ""),
    )
    return _run_with_path_resolution_errors(lambda: run_life_chat_loop(repl_args))


# ---------------------------------------------------------------------------
# 7×24 daemon dispatchers
# ---------------------------------------------------------------------------

def _resolve_life_dir(args: argparse.Namespace) -> Path:
    return resolve_life_root(args.life_dir)


def _build_worker_config(args: argparse.Namespace):
    from ..daemon.life_worker import LifeWorkerConfig
    bundle = _resolve_project_bundle(args)
    backend = getattr(args, "backend", None) or os.environ.get(
        "ARGUS_SKILL_LIFE_BACKEND",
        "codex",
    )
    return LifeWorkerConfig(
        life_dir=bundle.project.root,
        global_root=bundle.global_root,
        project_fingerprint=bundle.project.fingerprint,
        project_label=bundle.project.label,
        backend=backend,
        engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4"),
        scientist_model=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4"),
        per_mission_cap_usd=float(os.environ.get("ARGUS_SKILL_PER_MISSION_CAP_USD", "30.0")),
        daily_cap_usd=float(os.environ.get("ARGUS_SKILL_DAILY_CAP_USD", "180.0")),
        poll_interval=float(os.environ.get("ARGUS_SKILL_DAEMON_POLL_S", "5.0")),
        continuous=getattr(args, "continuous", False),
        continuous_objective=getattr(args, "objective", ""),
    )


def _cmd_daemon_start(args: argparse.Namespace, *, foreground: bool) -> int:
    from ..daemon.life_worker import run_foreground, spawn_detached_daemon
    backend_default = os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    continuous_error = _continuous_contract_error(
        continuous=bool(getattr(args, "continuous", False)),
        objective=str(getattr(args, "objective", "") or ""),
        backend=backend_default,
    )
    if continuous_error:
        sys.stderr.write(f"argus-skill: {continuous_error}\n")
        return 2
    cfg = _build_worker_config(args)
    if foreground:
        return run_foreground(cfg)
    return spawn_detached_daemon(cfg)


def _cmd_daemon_stop(args: argparse.Namespace) -> int:
    from ..daemon.life_worker import stop_daemon
    bundle = _resolve_project_bundle(args)
    return stop_daemon(bundle.project.root)


def _cmd_watch(args: argparse.Namespace) -> int:
    from ._watch import run_watch
    return run_watch(_resolve_project_bundle(args))


def _cmd_follow(args: argparse.Namespace) -> int:
    """Tail events.jsonl with pretty formatting — like ``tail -f``."""
    events_path = _resolve_follow_events_path(args)
    backlog_path = events_path.parent / "backlog.jsonl"

    import json as _json

    print(f"argus-skill: following {events_path}  (Ctrl-C to stop)", flush=True)
    print("━" * 60, flush=True)
    fh = None
    current_layer = "engineer"
    current_mission: dict[str, str] = {"item_id": "", "title": "", "objective": ""}
    last_event_at = time.monotonic()
    last_heartbeat_at = 0.0
    try:
        while fh is None:
            try:
                fh = events_path.open("r", encoding="utf-8")
                fh.seek(0, 2)
                pos = fh.tell()
                fh.seek(max(0, pos - 8192))
                if pos > 8192:
                    fh.readline()  # skip partial line
            except FileNotFoundError:
                print(f"argus-skill: waiting for {events_path} ...", flush=True)
                time.sleep(0.5)
            except OSError as exc:
                sys.stderr.write(f"argus-skill: cannot open {events_path}: {exc}\n")
                return 1
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.5)
                now = time.monotonic()
                idle = now - last_event_at
                if (
                    idle >= _FOLLOW_HEARTBEAT_SECONDS
                    and now - last_heartbeat_at >= _FOLLOW_HEARTBEAT_SECONDS
                ):
                    print(
                        _format_follow_heartbeat(events_path, current_layer, idle),
                        flush=True,
                    )
                    last_heartbeat_at = now
                # Check if file was rotated
                try:
                    if events_path.stat().st_ino != os.fstat(fh.fileno()).st_ino:
                        fh.close()
                        fh = events_path.open("r", encoding="utf-8")
                except OSError:
                    pass
                continue
            line = line.strip()
            if not line:
                continue
            try:
                ev = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            current_layer = _follow_layer_from_event(ev, current_layer)
            etype = str(ev.get("type") or "")
            if etype in {"life.mission.started", "life.mission.completed"}:
                item_id = str(ev.get("item_id") or current_mission.get("item_id") or "")
                title = str(ev.get("title") or current_mission.get("title") or "")
                objective = str(ev.get("objective") or current_mission.get("objective") or "")
                if item_id:
                    row = _select_backlog_row_by_id(
                        _read_backlog_rows(backlog_path), item_id
                    )
                    if row is not None:
                        title = str(row.get("title") or title)
                        objective = str(row.get("objective") or objective)
                current_mission = {
                    "item_id": item_id,
                    "title": title,
                    "objective": objective,
                }
            rendered = _format_follow_event(
                ev,
                current_layer,
                mission_context=current_mission,
            )
            if rendered:
                print(rendered, flush=True)
                last_event_at = time.monotonic()
                last_heartbeat_at = 0.0
    except KeyboardInterrupt:
        print("\nargus-skill: stopped following", flush=True)
    finally:
        if fh is not None:
            fh.close()
    return 0


def _cmd_notify(args: argparse.Namespace) -> int:
    """Append a free-form nudge to ``<life_dir>/inbox.jsonl``.

    The next engineer round picks it up via the supervisor's
    ``user_inbox`` callable and splices it into the prompt as
    operator guidance.
    """
    msg = (args.notify or "").strip()
    if not msg:
        sys.stderr.write("argus-skill: --notify requires a non-empty message\n")
        return 2
    bundle = _resolve_project_bundle(args)
    bundle.project.root.mkdir(parents=True, exist_ok=True)
    inbox = bundle.project.root / "inbox.jsonl"
    queue_inbox_message(bundle.project.root, msg, source="cli.notify")
    print(f"argus-skill: queued nudge ({len(msg)} chars) → {inbox}")
    return 0


def _cmd_init_identity(args: argparse.Namespace) -> int:
    from ._init_identity import run_init_identity
    return run_init_identity(_resolve_global_root(args))


def _resolve_skills_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "skills_dir", None):
        return core_paths.resolve_runtime_path(args.skills_dir, context="--skills-dir")
    return _resolve_global_root(args) / "skills"


def _run_with_path_resolution_errors(action) -> int:
    try:
        return action()
    except core_paths.PathResolutionError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return 2


def _cmd_skill_stats(args: argparse.Namespace) -> int:
    from ._skill_stats import run_skill_stats
    return run_skill_stats(
        _resolve_project_bundle(args).project.root,
        as_json=bool(args.skill_stats_json),
    )


def _cmd_skill_cleanse(args: argparse.Namespace) -> int:
    from ._skill_cleanse import run_cleanse
    return run_cleanse(
        _resolve_skills_dir(args),
        dry_run=not bool(args.apply),
    )


def _cmd_skill_compact(args: argparse.Namespace) -> int:
    from ..scientist.compactor import DEFAULT_SIM_THRESHOLD, run_compact
    threshold = (
        float(args.sim_threshold)
        if args.sim_threshold is not None
        else DEFAULT_SIM_THRESHOLD
    )
    return run_compact(
        _resolve_skills_dir(args),
        sim_threshold=threshold,
        dry_run=not bool(args.apply),
    )


def _cmd_status(args: argparse.Namespace) -> int:
    from ..daemon.life_worker import (
        format_budget_status,
        read_continuous_state,
        read_daemon_status,
    )
    bundle = _resolve_project_bundle(args)
    status = read_daemon_status(bundle.project.root)
    all_items = bundle.backlog.all()
    pending, running, done, failed, skipped = count_backlog_statuses(all_items)
    current_running = select_current_running_item(all_items)
    # Status should stay cheap even on a long-lived daemon.
    journal_tail = bundle.journal.tail(3)

    print(f"argus-skill — global-root: {bundle.global_root}")
    print(f"  project  : {bundle.project.root}")
    if status.alive and status.pid is not None:
        uptime = _format_short_duration(status.uptime_seconds or 0.0)
        backend = status.backend or "?"
        print(f"  daemon   : alive (pid {status.pid}, up {uptime}, backend {backend})")
    else:
        print("  daemon   : not running   (start with `argus-skill --daemon`)")
    print(f"  {format_budget_status(bundle.journal, status=status)}")
    print(f"  active   : {pending} pending · {running} running")
    if current_running is not None:
        print("  current  :")
        print(f"    id       : {getattr(current_running, 'id', '')}")
        print(
            f"    title    : "
            f"{_clean_follow_text(str(getattr(current_running, 'title', '')), limit=80)}"
        )
        print(
            f"    objective: "
            f"{_clean_follow_text(str(getattr(current_running, 'objective', '')), limit=120)}"
        )
    print(f"  inbox    : {count_pending_inbox_messages(bundle.project.root)} pending")
    history_parts = [part for part in (
        f"{done} done" if done else "",
        f"{failed} failed" if failed else "",
        f"{skipped} skipped" if skipped else "",
    ) if part]
    if history_parts:
        print(f"  history  : {' · '.join(history_parts)}")
    # Total cost from journal
    try:
        total_cost = bundle.journal.total_cost_since(0)
        print(f"  cost     : ${total_cost:.2f} total")
    except Exception:  # noqa: BLE001
        pass
    if running and not (status.alive and status.pid is not None):
        print(
            "             ↳ orphan running items will be reaped to `failed` "
            "when a worker (REPL or --daemon) next starts."
        )
    cont = read_continuous_state(bundle.project.root)
    print(f"  continuous: {'on' if cont.enabled else 'off'}")
    if cont.objective:
        print(f"    objective: {cont.objective}")
    if cont.done_reason:
        print(f"    done_reason: {cont.done_reason}")
    if cont.done_at:
        print(f"    done_at: {cont.done_at}")
    if journal_tail:
        print("  recent   :")
        for entry in journal_tail:
            print(f"    - {entry.kind}  {entry.summary}")
    survival_msg = _check_logout_survival(status)
    if survival_msg:
        print(f"  survival : {survival_msg}")
    return 0


def _cmd_daemon_runbook(args: argparse.Namespace) -> int:
    bundle = _resolve_project_bundle(args)
    from ..daemon.life_worker import read_daemon_status

    status = read_daemon_status(bundle.project.root)
    lines = [
        "argus-skill daemon-safe upgrade runbook",
        f"global   : {bundle.global_root}",
        f"project  : {bundle.project.root}",
        (
            f"daemon   : alive (pid {status.pid})"
            if status.alive and status.pid is not None
            else "daemon   : not running"
        ),
        "",
        "1. Open a second shell, tmux pane, or systemd session before touching the daemon.",
        "2. Treat the live daemon as the control plane: do not restart the process that owns your current session.",
        "3. Persist context first. Global identity/journal live under the global root; the backlog, inbox, and project memory live under the project root.",
        "4. For an ad-hoc detached worker, run `argus-skill --daemon-stop` from the external shell, wait for exit, update the code, then relaunch with `argus-skill --daemon`.",
        "5. For a systemd-managed worker, edit the unit from the maintenance shell, then run `systemctl daemon-reload && systemctl restart argus-skill.service`.",
        "6. Verify the new process with `argus-skill --status` before resuming work.",
    ]
    print("\n".join(lines))
    return 0


def _check_logout_survival(status) -> str | None:  # noqa: ANN001
    """Best-effort check whether the daemon will survive logout.

    The daemon already double-forks + setsid + ignores SIGHUP, so an
    SSH disconnect or terminal close cannot kill it. The remaining
    real-world risk on Linux is ``systemd-logind KillUserProcesses=yes``
    which kills user-owned processes (regardless of session) when the
    user has no more login sessions and ``linger`` is off. We probe
    ``loginctl show-user`` and tell the operator how to fix it.
    """
    if not (status.alive and status.pid is not None):
        return None
    if sys.platform != "linux":
        return None
    try:
        import getpass
        import subprocess
        user = getpass.getuser()
        out = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    body = (out.stdout or "").strip()
    if "Linger=yes" in body:
        return "linger=on  (daemon will survive logout / SSH disconnect)"
    if "Linger=no" in body:
        return (
            "linger=off ⚠  daemon may be killed at logout. "
            f"Run `loginctl enable-linger {getpass.getuser()}` to make 7×24 honest."
        )
    return None


def _format_short_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    if seconds < 86400:
        h, rem = divmod(int(seconds), 3600)
        m, _ = divmod(rem, 60)
        return f"{h}h {m}m"
    d, rem = divmod(int(seconds), 86400)
    h, _ = divmod(rem, 3600)
    return f"{d}d {h}h"
