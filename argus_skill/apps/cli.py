"""argus-skill CLI — single-entry 7×24 lifetime agent.

The product has exactly one positioning: a long-running supervised
coding agent that drains a backlog forever. There is therefore exactly
one entry point — ``argus-skill`` — which:

* drops you into the unified life REPL (the cockpit), and
* by default ensures a detached daemon is alive draining the backlog
  in the background even after you log out.

Top-level flags control daemon lifecycle and read-only operator help
(``--daemon``, ``--daemon-fg``, ``--daemon-stop``, ``--status``,
``--daemon-runbook``, ``--no-daemon``). There are no subcommands; the
REPL and backlog are the single workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Sequence

from ..core import paths as core_paths
from ..life.status import count_backlog_statuses, select_current_running_item
from ._inbox import count_pending_inbox_messages, format_inbox_event, queue_inbox_message
from ._target_paths import resolve_life_root


def build_parser() -> argparse.ArgumentParser:
    from .. import __version__
    from ..skills.builtins import DEFAULT_PROJECT_BUILTIN_SKILLS_DIR

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
    daemon_grp.add_argument(
        "--bounded",
        action="store_true",
        help="treat the mission as a bounded one-shot goal: hard-stop once the "
             "planner certifies project_done (default: open-ended — the agent "
             "keeps generating new work forever)",
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

    capability_grp = parser.add_argument_group("capability config")
    capability_grp.add_argument(
        "--setup",
        action="store_true",
        help="run the interactive setup wizard (API + GPU configuration)",
    )
    capability_grp.add_argument(
        "--model-api-status",
        action="store_true",
        help="print the unified model/image API capability status without secrets",
    )
    capability_grp.add_argument(
        "--init-model-api",
        action="store_true",
        help="import OPENAI_* / Codex config into the private capability vault "
             "(~/.argus-skill/capabilities/model_api.json, mode 0600)",
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
        "--export-builtin-skills",
        nargs="?",
        const=DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
        default=None,
        metavar="DIR",
        help="copy packaged built-in skill markdown into DIR for a project "
             "(default: ./argus_builtin_skills; preserves existing files)",
    )
    skills_grp.add_argument(
        "--apply",
        action="store_true",
        help="with --skill-compact / --skill-cleanse: actually mutate disk "
             "(default is dry-run); with --export-builtin-skills: replace "
             "existing copied built-in files",
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

    gates_grp = parser.add_argument_group("research-factory gates")
    gates_grp.add_argument(
        "--evidence-chain-check",
        action="store_true",
        help="run F4 evidence-chain validator on a project root and exit; "
             "prints broken chains and exits non-zero if any claim ↔ "
             "evidence ↔ bundle link is broken",
    )
    gates_grp.add_argument(
        "--anti-mediocrity-check",
        action="store_true",
        help="run F3 anti-mediocrity gates (baseline-reproduction, "
             "Δ-reward, benchmark-diversity) and exit; requires "
             "--proposed-condition and --baseline-condition to enable "
             "the comparison gates",
    )
    gates_grp.add_argument(
        "--lifecycle-status",
        action="store_true",
        help="print F5 project-lifecycle state derived from project memory "
             "(incubating/running/writing/quarantined/done/archived) and exit",
    )
    gates_grp.add_argument(
        "--lifecycle-resume",
        action="store_true",
        help="resume a quarantined project; clears persisted quarantine "
             "state in <life-dir>/lifecycle.json so the supervisor will "
             "dispatch missions again",
    )
    gates_grp.add_argument(
        "--lifecycle-archive",
        action="store_true",
        help="archive the project; supervisor will permanently refuse to "
             "dispatch missions until --lifecycle-resume is called",
    )
    gates_grp.add_argument(
        "--project-root",
        default=".",
        help="project root for --evidence-chain-check / "
             "--anti-mediocrity-check / --lifecycle-status (default cwd)",
    )
    gates_grp.add_argument(
        "--proposed-condition",
        default=None,
        help="condition name to evaluate against the baseline for "
             "--anti-mediocrity-check",
    )
    gates_grp.add_argument(
        "--baseline-condition",
        default=None,
        help="baseline condition name for --anti-mediocrity-check",
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


def _lifetime_entry_error(args: argparse.Namespace) -> str:
    """Return an actionable error if the lifetime agent is under-configured.

    The lifetime daemon / cockpit refuses to start unless the operator has
    explicitly supplied BOTH a mission objective and at least one trusted
    special prompt (machine house rules). This replaces any implicit guessing:
    the agent must be told its mission and its operating rules up front.

    The objective is satisfied by ``--objective`` (which requires
    ``--continuous``) or by a previously-persisted ``continuous.json`` for the
    current project. Returns ``""`` when both requirements are met.
    """
    from ..daemon.life_worker import read_continuous_config
    from ..life.special_prompts import describe_special_prompt_gate

    objective = str(getattr(args, "objective", "") or "").strip()
    if not objective:
        try:
            bundle = _resolve_project_bundle(args)
            _, persisted = read_continuous_config(bundle.project.root)
            objective = persisted.strip()
        except Exception:  # noqa: BLE001 — under-configured path resolution
            objective = ""
    if not objective:
        return (
            "no mission objective configured — the lifetime agent must be told "
            "what to work on. Launch with `--continuous --objective \"<goal>\"` "
            "(persisted to <life_dir>/continuous.json for later runs)."
        )

    ok, detail = describe_special_prompt_gate()
    if not ok:
        return detail
    return ""


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
    # critic layer removed,
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


def _format_bytes(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = 0.0
    sign = "+" if amount > 0 else "-" if amount < 0 else ""
    amount = abs(amount)
    units = ("B", "KiB", "MiB", "GiB")
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        body = f"{int(amount)} {unit}"
    else:
        body = f"{amount:.1f} {unit}"
    return f"{sign}{body}" if sign else body


def _telemetry_age(event: dict[str, Any], *, now: float | None = None) -> float:
    now = time.time() if now is None else now
    try:
        ts = float(event.get("ts") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    return max(0.0, now - ts) if ts > 0 else 0.0


def _format_telemetry_process_bits(event: dict[str, Any], *, limit: int = 2) -> str:
    raw_processes = event.get("processes") or []
    processes = raw_processes if isinstance(raw_processes, list) else []
    bits: list[str] = []
    for proc in processes[:limit]:
        if not isinstance(proc, dict):
            continue
        cmd = str(proc.get("cmd") or proc.get("argv0") or "").strip()
        if cmd:
            bits.append(_clean_follow_text(cmd, limit=90))
    truncated = int(event.get("processes_truncated") or 0)
    total = int(event.get("process_count") or len(processes))
    if truncated:
        bits.append(f"+{truncated} more")
    if not bits and total:
        bits.append(f"{total} descendant process(es)")
    return " · ".join(bits)


def _format_telemetry_file_bits(event: dict[str, Any], *, limit: int = 3) -> str:
    raw_files = event.get("files") or []
    files = raw_files if isinstance(raw_files, list) else []
    bits: list[str] = []
    for item in files[:limit]:
        if not isinstance(item, dict):
            continue
        path = _clean_follow_text(str(item.get("path") or "?"), limit=70)
        if item.get("new_lines") is not None:
            detail = f"+{int(item.get('new_lines') or 0)} lines"
        elif item.get("size_delta") not in (None, 0):
            detail = _format_bytes(item.get("size_delta"))
        else:
            detail = _format_bytes(item.get("size"))
        bits.append(f"{path} {detail}")
    changed = int(event.get("files_changed") or len(files))
    if changed > len(bits):
        bits.append(f"+{changed - len(bits)} files")
    return " · ".join(bits)


def _format_telemetry_inline(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    age = _telemetry_age(event)
    if age > 120:
        return f"telemetry stale ({_format_short_duration(age)} old)"
    running = bool(event.get("running"))
    run_for = _format_short_duration(float(event.get("running_seconds") or 0.0))
    state = f"telemetry {'running' if running else 'idle'}"
    bits = [state, f"updated {_format_short_duration(age)} ago"]
    if running:
        bits.append(f"mission {run_for}")
    procs = _format_telemetry_process_bits(event, limit=1)
    if procs:
        bits.append(f"proc: {procs}")
    files = _format_telemetry_file_bits(event, limit=2)
    if files:
        bits.append(f"artifacts: {files}")
    return " · ".join(bits)


def _format_telemetry_status_lines(event: dict[str, Any] | None) -> list[str]:
    if not event:
        return []
    age = _telemetry_age(event)
    running = bool(event.get("running"))
    run_for = _format_short_duration(float(event.get("running_seconds") or 0.0))
    seq = event.get("seq", "?")
    if running:
        state = f"running · mission {run_for} · updated {_format_short_duration(age)} ago · seq {seq}"
    else:
        state = f"idle · last mission {run_for} · updated {_format_short_duration(age)} ago · seq {seq}"
    lines = [f"    state    : {state}"]
    if event.get("item_id"):
        lines.append(f"    item     : {event.get('item_id')}")
    procs = _format_telemetry_process_bits(event, limit=3)
    if procs:
        lines.append(f"    proc     : {procs}")
    files = _format_telemetry_file_bits(event, limit=4)
    if files:
        lines.append(f"    artifacts: {files}")
    scan_bits = [
        f"{int(event.get('scanned_files') or 0)} files",
        f"{int(event.get('scan_ms') or 0)} ms",
    ]
    if event.get("scan_truncated"):
        scan_bits.append("truncated")
    lines.append(f"    scan     : {' · '.join(scan_bits)}")
    return lines


def _read_recent_jsonl_events(
    path: Path,
    *,
    limit: int = 80,
    max_bytes: int = 256 * 1024,
) -> list[dict[str, Any]]:
    """Read a bounded JSONL tail without scanning the whole event log."""
    if limit <= 0:
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            start = max(0, size - max(1, int(max_bytes)))
            fh.seek(start)
            raw = fh.read()
    except OSError:
        return []
    if start:
        _, sep, raw = raw.partition(b"\n")
        if not sep:
            return []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            rows.append(event)
    return list(rows)


def _read_recent_project_events(life_dir: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    events = _read_recent_jsonl_events(life_dir / "events.jsonl", limit=limit)
    if events:
        return events
    return _read_recent_jsonl_events(life_dir / "events.jsonl.1", limit=limit)


def _activity_layer_from_event(event: dict[str, Any]) -> str | None:
    layer = event.get("agent_layer")
    if isinstance(layer, str) and layer:
        return layer
    etype = str(event.get("type") or "")
    if etype.startswith("life.planner."):
        return "planner"
    if etype in {"life.iteration.critic", "life.iteration.continued"}:
        return "critic"
    if etype in {"round.review.started", "round.review.completed"}:
        return "reviewer"
    if etype in {
        "life.mission.started",
        "loop.start",
        "round.start",
        "round.main.completed",
        "loop.done",
    }:
        return "engineer"
    return None


def _latest_activity_event(events: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        etype = str(event.get("type") or "")
        if etype == "life.telemetry":
            continue
        if etype.startswith("life.") or etype in {
            "engineer.progress",
            "loop.start",
            "round.start",
            "round.main.completed",
            "round.review.started",
            "round.review.completed",
            "match.info",
        }:
            return event
    return None


def _looks_like_agent_process(proc: dict[str, Any]) -> bool:
    cmd = str(proc.get("cmd") or proc.get("argv0") or "").lower()
    return "codex exec" in cmd or "@openai/codex" in cmd


def _format_activity_process_bits(processes: Sequence[dict[str, Any]], *, limit: int = 3) -> str:
    bits: list[str] = []
    for proc in processes[:limit]:
        cmd = str(proc.get("cmd") or proc.get("argv0") or "").strip()
        if cmd:
            bits.append(_clean_follow_text(cmd, limit=90))
    if len(processes) > len(bits):
        bits.append(f"+{len(processes) - len(bits)} more")
    return " · ".join(bits)


def _format_activity_event(event: dict[str, Any]) -> str:
    etype = str(event.get("type") or "event")
    kind = str(event.get("kind") or "")
    actor = str(event.get("actor") or "")
    status = str(event.get("status") or "")
    label = kind or etype
    if actor:
        label = f"{actor} {label}"
    if status:
        label = f"{label} {status}"
    text = (
        event.get("text")
        or event.get("title")
        or event.get("reason")
        or event.get("objective")
        or event.get("error")
        or ""
    )
    if text:
        return _clean_follow_text(f"{label} · {text}", limit=160)
    return _clean_follow_text(label, limit=160)


def _format_daemon_activity_status_lines(
    status: Any,
    *,
    life_dir: Path,
    telemetry_event: dict[str, Any] | None,
) -> list[str]:
    """Expose planner/critic subprocess activity that mission telemetry cannot see."""
    if not (getattr(status, "alive", False) and getattr(status, "pid", None) is not None):
        return []
    if telemetry_event and bool(telemetry_event.get("running")):
        return []

    recent_events = _read_recent_project_events(life_dir)
    latest_event = _latest_activity_event(recent_events)
    try:
        from ..life.telemetry import collect_descendant_processes

        proc_snapshot = collect_descendant_processes(int(status.pid), limit=12)
    except Exception:  # noqa: BLE001
        proc_snapshot = {"processes": [], "process_count": 0, "processes_truncated": 0}
    raw_processes = proc_snapshot.get("processes") or []
    processes = raw_processes if isinstance(raw_processes, list) else []
    agent_processes = [
        proc for proc in processes
        if isinstance(proc, dict) and _looks_like_agent_process(proc)
    ]
    if not agent_processes:
        return []

    layer = None
    for event in reversed(recent_events):
        layer = _activity_layer_from_event(event)
        if layer:
            break
    layer = layer or "agent"

    state_bits = [
        f"{layer} active",
        f"{len(agent_processes)} agent process(es)",
    ]
    if latest_event is not None:
        state_bits.append(
            f"last event {_format_short_duration(_telemetry_age(latest_event))} ago"
        )
    if proc_snapshot.get("processes_truncated"):
        state_bits.append(f"+{int(proc_snapshot.get('processes_truncated') or 0)} hidden")

    lines = [f"    state    : {' · '.join(state_bits)}"]
    procs = _format_activity_process_bits(agent_processes)
    if procs:
        lines.append(f"    proc     : {procs}")
    if latest_event is not None:
        lines.append(f"    last     : {_format_activity_event(latest_event)}")
    return lines


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
    skip_category = str(event.get("skip_category") or "")
    if skip_category == "recent_no_progress_failure":
        bits = ["quarantined recent no-progress failure"]
    else:
        bits = ["skipped duplicate"]
    if event.get("title"):
        bits.append(f"title={_clean_follow_text(str(event['title']), limit=90)}")
    if event.get("objective"):
        bits.append(f"objective={_clean_follow_text(str(event['objective']), limit=120)}")
    if event.get("matched_item_id"):
        bits.append(f"matched_item_id={event['matched_item_id']}")
    if event.get("matched_title"):
        bits.append(f"matched_title={_clean_follow_text(str(event['matched_title']), limit=90)}")
    if event.get("matched_status"):
        bits.append(f"matched_status={event['matched_status']}")
    if event.get("matched_stop_reason"):
        bits.append(
            f"matched_stop_reason={_clean_follow_text(str(event['matched_stop_reason']), limit=120)}"
        )
    if event.get("skip_category"):
        bits.append(f"skip_category={event['skip_category']}")
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

    if etype == "life.telemetry":
        inline = _format_telemetry_inline(event)
        return f"  📡 {inline}" if inline else None

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
    telemetry = ""
    try:
        from ..life.telemetry import read_latest_telemetry
        telemetry = _format_telemetry_inline(read_latest_telemetry(events_path.parent))
    except Exception:  # noqa: BLE001
        telemetry = ""
    tail = telemetry or "normal during LLM calls"
    return (
        f"  ⏳ [{_follow_layer_label(current_layer)}] waiting "
        f"{_format_short_duration(idle_seconds)} without new events · {state} · "
        f"{tail}"
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
        + bool(args.setup)
        + bool(args.model_api_status)
        + bool(args.init_model_api)
        + bool(args.skill_stats)
        + bool(args.skill_cleanse)
        + bool(args.skill_compact)
        + bool(args.export_builtin_skills is not None)
        + bool(args.evidence_chain_check)
        + bool(args.anti_mediocrity_check)
        + bool(args.lifecycle_status)
        + bool(args.lifecycle_resume)
        + bool(args.lifecycle_archive)
    )
    if action_flags > 1:
        sys.stderr.write(
            "argus-skill: --daemon / --daemon-fg / --daemon-stop / --status / "
            "--daemon-runbook / --watch / --follow / --notify / --init-identity / "
            "--model-api-status / --init-model-api / --skill-stats / "
            "--skill-cleanse / --skill-compact / --export-builtin-skills / "
            "--evidence-chain-check / --anti-mediocrity-check / --lifecycle-status "
            "are mutually exclusive.\n"
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
    if args.setup:
        from ..tools.setup import run_setup
        return run_setup()
    if args.model_api_status:
        return _run_with_path_resolution_errors(lambda: _cmd_model_api_status(args))
    if args.init_model_api:
        return _run_with_path_resolution_errors(lambda: _cmd_init_model_api(args))
    if args.skill_stats:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_stats(args))
    if args.skill_cleanse:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_cleanse(args))
    if args.skill_compact:
        return _run_with_path_resolution_errors(lambda: _cmd_skill_compact(args))
    if args.export_builtin_skills is not None:
        return _run_with_path_resolution_errors(
            lambda: _cmd_export_builtin_skills(args)
        )
    if args.evidence_chain_check:
        return _run_with_path_resolution_errors(
            lambda: _cmd_evidence_chain_check(args)
        )
    if args.anti_mediocrity_check:
        return _run_with_path_resolution_errors(
            lambda: _cmd_anti_mediocrity_check(args)
        )
    if args.lifecycle_status:
        return _run_with_path_resolution_errors(
            lambda: _cmd_lifecycle_status(args)
        )
    if args.lifecycle_resume:
        return _run_with_path_resolution_errors(
            lambda: _cmd_lifecycle_transition(args, action="resume")
        )
    if args.lifecycle_archive:
        return _run_with_path_resolution_errors(
            lambda: _cmd_lifecycle_transition(args, action="archive")
        )

    # Default path: drop into the unified life REPL. The REPL itself
    # auto-spawns a background daemon (unless ``--no-daemon`` was given
    # or one is already alive) so the agent keeps draining the backlog
    # 24/7 even after the operator detaches.
    entry_error = _lifetime_entry_error(args)
    if entry_error:
        sys.stderr.write(f"argus-skill: {entry_error}\n")
        return 2

    from ._life_repl import run_life_chat_loop

    from ..tools.capability_vault import resolve_route_model

    repl_args = argparse.Namespace(
        life_dir=args.life_dir,
        color=None,
        backend=backend_default,
        scientist_model=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL")
        or resolve_route_model("scientist"),
        engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL")
        or resolve_route_model("engineer"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL"),
        scientist_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_SCIENTIST_REASONING_EFFORT", "high"
        ),
        engineer_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high"
        ),
        reviewer_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high"
        ),
        plan_mode="auto",
        plan_model=None,
        max_rounds=500,
        check=[],
        workdir=None,
        no_daemon=bool(args.no_daemon),
        continuous=bool(args.continuous),
        objective=str(getattr(args, "objective", "") or ""),
        bounded=bool(getattr(args, "bounded", False)),
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
    from ..tools.capability_vault import resolve_route_model

    return LifeWorkerConfig(
        life_dir=bundle.project.root,
        global_root=bundle.global_root,
        project_workdir=Path.cwd(),
        project_fingerprint=bundle.project.fingerprint,
        project_label=bundle.project.label,
        backend=backend,
        engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL")
        or resolve_route_model("engineer"),
        reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL")
        or resolve_route_model("reviewer"),
        scientist_model=os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL")
        or resolve_route_model("scientist"),
        engineer_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high"
        ),
        reviewer_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high"
        ),
        scientist_reasoning_effort=os.environ.get(
            "ARGUS_SKILL_SCIENTIST_REASONING_EFFORT", "high"
        ),
        per_mission_cap_usd=float(os.environ.get("ARGUS_SKILL_PER_MISSION_CAP_USD", "30.0")),
        daily_cap_usd=float(os.environ.get("ARGUS_SKILL_DAILY_CAP_USD", "180.0")),
        planner_task_iteration_max_cycles=int(os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_MAX_CYCLES", "6")),
        planner_task_iteration_budget_usd=float(os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_BUDGET_USD", "30.0")),
        poll_interval=float(os.environ.get("ARGUS_SKILL_DAEMON_POLL_S", "5.0")),
        continuous=getattr(args, "continuous", False),
        continuous_objective=getattr(args, "objective", ""),
        continuous_open_ended=not bool(getattr(args, "bounded", False)),
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
    entry_error = _lifetime_entry_error(args)
    if entry_error:
        sys.stderr.write(f"argus-skill: {entry_error}\n")
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


def _model_api_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    env["ARGUS_SKILL_CAPABILITY_VAULT"] = str(
        _resolve_global_root(args) / "capabilities" / "model_api.json"
    )
    return env


def _cmd_model_api_status(args: argparse.Namespace) -> int:
    import json

    from ..tools.capability_vault import status_payload

    print(json.dumps(status_payload(_model_api_env(args)), indent=2, sort_keys=True))
    return 0


def _cmd_init_model_api(args: argparse.Namespace) -> int:
    from ..tools.capability_vault import bootstrap_model_api_vault

    path = bootstrap_model_api_vault(_model_api_env(args))
    print(f"argus-skill: model API capability saved at {path} (0600, secret not printed)")
    return 0


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


def _cmd_export_builtin_skills(args: argparse.Namespace) -> int:
    from ..skills.builtins import (
        DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
        builtin_skill_source_path,
        seed_builtin_skills,
    )

    raw_target = args.export_builtin_skills or DEFAULT_PROJECT_BUILTIN_SKILLS_DIR
    target = core_paths.resolve_runtime_path(
        raw_target,
        context="--export-builtin-skills",
    )
    if not target.is_absolute():
        target = Path.cwd() / target
    result = seed_builtin_skills(target, overwrite=bool(args.apply))
    written = sum(1 for changed in result.values() if changed)
    skipped = len(result) - written
    source_path = builtin_skill_source_path()
    source = (
        str(source_path)
        if source_path.exists()
        else "package resource argus_skill.builtin_skills"
    )
    action = "created/replaced" if args.apply else "created"
    print(f"argus-skill: exported built-in skills to {target}")
    print(f"  source : {source}")
    print(
        f"  files  : {written} {action}, {skipped} preserved, "
        f"{len(result)} total"
    )
    if skipped and not args.apply:
        print("  hint   : pass --apply to replace existing copied built-in skill files")
    return 0


def _cmd_evidence_chain_check(args: argparse.Namespace) -> int:
    """Run F4 evidence-chain validator. Exits non-zero on broken chain."""
    from ..skills.evidence_chain import main as _evidence_chain_main

    return _evidence_chain_main(["--project-root", str(args.project_root)])


def _cmd_anti_mediocrity_check(args: argparse.Namespace) -> int:
    """Run F3 anti-mediocrity gates. Exits non-zero on any gate failure."""
    from ..skills.anti_mediocrity import main as _anti_mediocrity_main

    argv = ["--project-root", str(args.project_root)]
    if args.proposed_condition:
        argv += ["--proposed-condition", str(args.proposed_condition)]
    if args.baseline_condition:
        argv += ["--baseline-condition", str(args.baseline_condition)]
    return _anti_mediocrity_main(argv)


def _cmd_lifecycle_status(args: argparse.Namespace) -> int:
    """Print the F5 ProjectStatus inferred from current project memory
    plus any persisted quarantine / done / archived state.

    Reads observable signals (evidence bundles, paper/main.tex|pdf,
    project mtime) and overlays the persisted state from
    ``<life-dir>/lifecycle.json`` so quarantine survives daemon
    restarts.
    """
    from ..life.project_lifecycle import (
        advisory_time_signals,
        decide_next_state,
        infer_observable_status,
        is_token_allocatable,
    )
    from ..life.project_lifecycle_io import (
        LifecycleIOError,
        apply_persisted_to_status,
        load_history,
        load_persisted,
    )

    root = Path(args.project_root).resolve()
    if not root.exists():
        sys.stderr.write(f"argus-skill: project root not found: {root}\n")
        return 2

    status = infer_observable_status(root, project_id=root.name)
    try:
        persisted = load_persisted(root)
    except LifecycleIOError as exc:
        sys.stderr.write(
            f"argus-skill: lifecycle sidecar at {root}/lifecycle.json is "
            f"malformed: {exc}\n"
        )
        persisted = {}
    overlaid = apply_persisted_to_status(status, persisted)
    event = decide_next_state(overlaid)
    history = load_history(root)
    signals = advisory_time_signals(overlaid)

    print(f"argus-skill — project lifecycle (F5)")
    print(f"  root              : {root}")
    print(f"  observed_state    : {status.state.value}")
    print(
        f"  effective_state   : {overlaid.state.value}"
        + ("  (persisted)" if persisted.get("state") else "")
    )
    print(f"  has_draft         : {overlaid.has_draft}")
    print(f"  has_submission    : {overlaid.has_submission_artifact}")
    print(
        f"  last_evidence_at  : "
        f"{overlaid.last_evidence_at.isoformat() if overlaid.last_evidence_at else '(none)'}"
    )
    print(f"  token_allocatable : {is_token_allocatable(overlaid)}")
    if event is None:
        print("  next_action       : no transition warranted at this tick")
    else:
        print(
            f"  next_action       : transition "
            f"{event.from_state.value} → {event.to_state.value} "
            f"({event.reason})"
        )

    # Advisory time signals are facts the AGENT reads to decide whether
    # to pivot / push / give up. The harness does not act on them.
    if signals:
        print(f"  advisory signals  : {len(signals)}  (agent reads, harness does not act)")
        for sig in signals:
            print(f"    - [{sig.kind}] {sig.message}")

    if history:
        print(f"  history ({len(history)} event(s), most recent first):")
        for ev in reversed(history[-5:]):
            print(
                f"    - {ev.at.isoformat()}  "
                f"{ev.from_state.value} → {ev.to_state.value}  ({ev.reason})"
            )
    return 0


def _cmd_lifecycle_transition(
    args: argparse.Namespace, *, action: str
) -> int:
    """Handle ``--lifecycle-resume`` and ``--lifecycle-archive``."""
    from datetime import datetime, timezone

    from ..life.project_lifecycle import (
        archive as _lifecycle_archive,
        infer_observable_status,
        resume as _lifecycle_resume,
    )
    from ..life.project_lifecycle_io import (
        LifecycleIOError,
        append_event,
        apply_persisted_to_status,
        load_persisted,
    )

    root = Path(args.project_root).resolve()
    if not root.exists():
        sys.stderr.write(f"argus-skill: project root not found: {root}\n")
        return 2

    status = infer_observable_status(root, project_id=root.name)
    try:
        persisted = load_persisted(root)
    except LifecycleIOError as exc:
        sys.stderr.write(
            f"argus-skill: lifecycle sidecar malformed: {exc}\n"
        )
        return 2
    status = apply_persisted_to_status(status, persisted)

    now = datetime.now(timezone.utc)
    try:
        if action == "resume":
            new_status, event = _lifecycle_resume(status, now=now)
        elif action == "archive":
            new_status, event = _lifecycle_archive(status, now=now)
        else:
            raise ValueError(f"unknown lifecycle action {action!r}")
    except ValueError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return 1

    try:
        append_event(root, new_status=new_status, event=event)
    except OSError as exc:
        sys.stderr.write(f"argus-skill: cannot persist transition: {exc}\n")
        return 1

    print(
        f"argus-skill: lifecycle transition "
        f"{event.from_state.value} → {event.to_state.value} "
        f"({event.reason})"
    )
    print(f"  root  : {root}")
    print(f"  state : {new_status.state.value}")
    return 0


def _resolve_research_workdir(bundle: Any) -> Path:
    """Find where the actual research project lives (paper/ benchmarks/
    research/ etc.) for surfaces like --status that need to inspect
    research artifacts, not the life-dir state.

    Resolution order (matches supervisor._project_workdir):

    1. ``ARGUS_SKILL_WORKDIR`` env var (operator override)
    2. ``<bundle.project.root>/code/`` if it exists (the
       ``new_auto_research_project`` layout seeds code under code/)
    3. ``bundle.project.root`` (life dir; may not have research/ but
       at worst the gates render empty findings, never crash)
    """
    env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
    if env_workdir:
        return Path(env_workdir).expanduser()
    project_root = Path(bundle.project.root)
    code = project_root / "code"
    if code.is_dir():
        return code
    return project_root


def _read_current_stage(workdir: Path) -> str | None:
    """Best-effort read of ``research/PIPELINE_STATE.json``. None if
    the project hasn't reached stage tracking yet."""
    state_path = workdir / "research" / "PIPELINE_STATE.json"
    if not state_path.exists():
        return None
    try:
        import json as _json
        data = _json.loads(state_path.read_text(encoding="utf-8"))
        stage = data.get("current_stage")
        return str(stage) if stage else None
    except (OSError, ValueError):
        return None


def _render_lifecycle_status_lines(workdir: Path) -> list[str]:
    """Render the F5 lifecycle block for --status / cockpit.

    Pure projection of observable + persisted state through the F5
    state machine. Returns the lines to print (no I/O of its own).
    Fail-soft: any error returns an empty list — --status must not
    crash on a missing or corrupt lifecycle sidecar.
    """
    try:
        from ..life.project_lifecycle import (
            advisory_time_signals,
            infer_observable_status,
            is_token_allocatable,
        )
        from ..life.project_lifecycle_io import (
            LifecycleIOError,
            apply_persisted_to_status,
            load_persisted,
        )
    except Exception:  # noqa: BLE001
        return []

    # ``infer_observable_status`` tolerates a non-existent workdir
    # (returns an INCUBATING status using "now" as created_at), so we
    # do NOT early-return when the dir is missing — that's the normal
    # state for a freshly-bound project that hasn't started yet.

    try:
        status = infer_observable_status(workdir, project_id=workdir.name)
        try:
            persisted = load_persisted(workdir)
        except LifecycleIOError:
            persisted = {}
        overlaid = apply_persisted_to_status(status, persisted)
        signals = advisory_time_signals(overlaid)
    except Exception:  # noqa: BLE001
        return []

    lines: list[str] = []
    lines.append("  lifecycle:")
    state_label = overlaid.state.value
    if persisted.get("state"):
        state_label += "  (persisted)"
    lines.append(f"    state         : {state_label}")
    lines.append(
        f"    allocatable   : {is_token_allocatable(overlaid)}"
    )
    if signals:
        lines.append(
            f"    advisory      : {len(signals)} signal(s) "
            f"(agent reads, harness does not act)"
        )
        for sig in signals:
            lines.append(f"      - [{sig.kind}] {sig.message}")
    return lines


def _render_stage_budget_lines(bundle: Any, *, current_stage: str | None) -> list[str]:
    """Render per-stage budget snapshot for --status. Facts-only; the
    reviewer / planner agent decides whether to act on advisories.
    Fail-soft: any error returns []."""
    try:
        from ..life.stage_budget import compute_snapshot
    except Exception:  # noqa: BLE001
        return []
    try:
        from ..daemon.life_worker import resolve_effective_budget
        eff = resolve_effective_budget()
        total_budget = float(getattr(eff, "daily_cap_usd", 180.0) or 180.0)
    except Exception:  # noqa: BLE001
        total_budget = 180.0
    try:
        entries = list(bundle.journal.all())
    except Exception:  # noqa: BLE001
        return []

    snap = compute_snapshot(
        journal_entries=entries,
        total_budget_usd=total_budget,
        current_stage=current_stage,
    )
    if snap.total_spent_usd <= 0.0 and not snap.spent_by_stage:
        return []

    lines = ["  stage_budget:"]
    for stage, amount in sorted(snap.spent_by_stage.items(), key=lambda kv: -kv[1]):
        fraction = (amount / total_budget * 100.0) if total_budget else 0.0
        lines.append(
            f"    {stage:14s} ${amount:7.2f}  ({fraction:5.1f}% of ${total_budget:.0f})"
        )
    if snap.advisory_signals:
        lines.append(
            f"    advisory      : {len(snap.advisory_signals)} signal(s) "
            f"(stage > 30% of total — agent reads, harness does not act)"
        )
        for sig in snap.advisory_signals:
            lines.append(f"      - [{sig.stage}] {sig.message}")
    return lines


def _render_gate_snapshot_lines(workdir: Path, stage: str | None) -> list[str]:
    """Render the structural/advisory gate snapshot for --status.
    Runs the F3/F4 gates against the current pipeline stage and shows
    each result with its kind. Structural failures are marked ❌;
    advisory findings are marked 📋 (never failure). Fail-soft.
    """
    if not stage:
        return []
    try:
        from ..skills.automated_gates import (
            gates_for_stage,
            run_stage_gates,
        )
    except Exception:  # noqa: BLE001
        return []

    if not gates_for_stage(stage):
        return [f"  gates @ {stage}: (no gates configured at this stage)"]

    try:
        results = run_stage_gates(
            workdir,
            stage=stage,
            proposed_condition=os.environ.get("ARGUS_SKILL_PROPOSED_CONDITION") or None,
            baseline_condition=os.environ.get("ARGUS_SKILL_BASELINE_CONDITION") or None,
        )
    except Exception:  # noqa: BLE001
        return [f"  gates @ {stage}: (snapshot failed; rerun stage_check)"]

    lines = [f"  gates @ {stage}:"]
    for gate in results:
        if gate.kind == "advisory":
            mark = "📋"
        elif gate.passed:
            mark = "✅"
        else:
            mark = "❌"
        lines.append(
            f"    {mark} {gate.name} ({gate.kind}) — {gate.summary}"
        )
    return lines


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
    try:
        from ..life.telemetry import read_latest_telemetry
        telemetry_event = read_latest_telemetry(bundle.project.root)
        telemetry_lines = _format_telemetry_status_lines(telemetry_event)
    except Exception:  # noqa: BLE001
        telemetry_event = None
        telemetry_lines = []
    if telemetry_lines:
        print("  telemetry:")
        for line in telemetry_lines:
            print(line)
    activity_lines = _format_daemon_activity_status_lines(
        status,
        life_dir=bundle.project.root,
        telemetry_event=telemetry_event,
    )
    if activity_lines:
        print("  activity :")
        for line in activity_lines:
            print(line)
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

    # Lifecycle (F5) + gate snapshot (F3 advisory / F4 structural).
    # Both are projections of observable state — surfacing facts the
    # agent already acts on; the harness makes no decision here.
    research_workdir = _resolve_research_workdir(bundle)
    lifecycle_lines = _render_lifecycle_status_lines(research_workdir)
    for line in lifecycle_lines:
        print(line)
    current_stage = _read_current_stage(research_workdir)
    gate_lines = _render_gate_snapshot_lines(research_workdir, current_stage)
    for line in gate_lines:
        print(line)

    # Per-stage budget snapshot (Opt #2). Surfaces facts only: how
    # much each stage has spent + an advisory when any stage has
    # eaten >30% of total budget. Reviewer / planner agent decides
    # what to do; harness does not auto-quarantine on spend.
    budget_lines = _render_stage_budget_lines(bundle, current_stage=current_stage)
    for line in budget_lines:
        print(line)

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
