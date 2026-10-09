"""What the Engineer actually did this round, from the host's own log.

The Reviewer used to learn about the round from the Engineer's account and
then re-read the tree to check it: in one control project 61 of its 105 file
reads were files the Engineer had just read, and it still never opened the
script that computed a "perplexity" by formula. The host already records
every command and file the Engineer touched (``engineer.progress`` events),
so the packet can simply say what happened: how many commands, which ran
longest (the time to the next action bounds a command's runtime), which
tests and checks ran and what the host recorded as their result (exit code
and last output lines), which commands failed, and which paths lay outside
the workspace. A Reviewer whose tools cannot run commands weighs those
recorded results instead of the Engineer's account of them. The
Reviewer then decides where to look instead of looking everywhere.

Evidence, not a gate: the provider renders text into the Reviewer's
raw-evidence slot and never decides anything. This module only renders text;
:mod:`spec_checks`, the research vertical's round-evidence entry point, wraps
:func:`render_round_log` as a provider, so nothing here knows the engineer layer.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

EVENTS_NAME = "events.jsonl"
LIFE_DIR_ASCENT = 4
MAX_EVENTS_SCAN = 200_000
LONGEST_COMMANDS = 4
OUTSIDE_PATHS = 4
CHECKS_SHOWN = 6
LAST_COMMANDS = 3
FAILED_SHOWN = 3
RESULT_EXCERPT = 160
TEST_OR_EVAL = re.compile(
    r"pytest|unittest|(?<![\w/.])tests?/|\beval|benchmark|check|verif|validat|"
    r"figure_lint|pptx_export|generate_figures|train",
    re.IGNORECASE,
)
_ABS_PATH = re.compile(r"(?<![\w/])(/(?:data|home|mnt|srv|opt|tmp|var)/[^\s'\"`:;|)>]+)")
_TOOL_PREFIX = re.compile(r"^(read|write|edit|ls|find|grep|glob|search_experiences|apply_patch|view): ")


def find_events_file(life_dir: Path, workdir: Path) -> Path | None:
    """The project's events log: beside the mission packet or up to four levels above it."""
    candidates: list[Path] = []
    current = Path(life_dir)
    for _ in range(LIFE_DIR_ASCENT + 1):
        candidates.append(current / EVENTS_NAME)
        if current.parent == current:
            break
        current = current.parent
    candidates.append(Path(workdir) / ".argus" / "life" / EVENTS_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _iter_events(path: Path):
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            count += 1
            if count > MAX_EVENTS_SCAN:
                return
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def round_window_start(events: list[dict[str, Any]], round_index: int) -> float | None:
    """When this Engineer round started: the last matching ``round.start``, else the last mission start."""
    starts = [
        float(e.get("ts") or 0)
        for e in events
        if e.get("type") == "round.start" and str(e.get("round_index") or e.get("round") or "") == str(round_index)
    ]
    if not starts:
        starts = [float(e.get("ts") or 0) for e in events if e.get("type") == "round.start"]
    if not starts:
        starts = [float(e.get("ts") or 0) for e in events if e.get("type") == "life.mission.started"]
    return max(starts) if starts else None


def engineer_actions(events: list[dict[str, Any]], since: float) -> list[dict[str, Any]]:
    """Engineer tool events after ``since``, each with the time until the next one.

    A command carries the result the host recorded for it, when there is one:
    its exit code and the last lines of its output. Some CLIs report a command
    once, finished; others report its start and later its result, joined here
    by call id so a result never counts as a second command.
    """
    rows = [
        e
        for e in events
        if e.get("type") == "engineer.progress"
        and str(e.get("agent_layer") or "engineer") == "engineer"
        and e.get("kind") in ("command_execution", "tool_use", "tool_result")
        and float(e.get("ts") or 0) >= since
    ]
    rows.sort(key=lambda e: float(e.get("ts") or 0))
    out: list[dict[str, Any]] = []
    by_call: dict[str, dict[str, Any]] = {}
    for e in rows:
        kind = e.get("kind")
        call_id = str(e.get("call_id") or "")
        status = str(e.get("status") or "").lower()
        text = str(e.get("text") or "").strip()
        finished = status not in ("", "running")
        started = by_call.get(call_id) if call_id else None
        if kind == "tool_result" or (finished and started is not None):
            if started is not None:
                _record_result(started, e)
            continue
        if kind == "command_execution" and status == "failed" and not call_id:
            # Older logs name a failed command again without its call id.
            pending = next((a for a in reversed(out) if a["kind"] == kind), None)
            if pending is not None and pending["text"] == text and "exit_code" not in pending:
                _record_result(pending, e)
                continue
        action = {
            "ts": float(e.get("ts") or 0),
            "kind": kind,
            "tool": str(e.get("tool_name") or ""),
            "text": text,
        }
        if finished:
            _record_result(action, e)
        if call_id:
            by_call[call_id] = action
        out.append(action)
    for index, action in enumerate(out):
        nxt = out[index + 1]["ts"] if index + 1 < len(out) else None
        action["gap_s"] = (nxt - action["ts"]) if nxt is not None else None
    return out


def _record_result(action: dict[str, Any], event: dict[str, Any]) -> None:
    exit_code = event.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        action["exit_code"] = exit_code
    elif str(event.get("status") or "").lower() == "failed":
        action["exit_code"] = None
    excerpt = str(event.get("output_excerpt") or "").strip()
    if excerpt:
        action["excerpt"] = excerpt


def _result(action: dict[str, Any]) -> str:
    if "exit_code" not in action:
        return "no result recorded"
    code = action["exit_code"]
    head = "failed" if code is None else f"exit {code}"
    excerpt = action.get("excerpt")
    return f"{head}: {_one_line(excerpt, RESULT_EXCERPT)}" if excerpt else head


def summarize_actions(actions: list[dict[str, Any]], workdir: Path) -> list[str]:
    """Lines for the Reviewer: counts, longest commands, checks and last commands with results, failures, outside paths."""
    if not actions:
        return []
    commands = [a for a in actions if a["kind"] == "command_execution" and not _TOOL_PREFIX.match(a["text"])]
    reads = [a for a in actions if a["kind"] == "tool_use" and a["text"].startswith(("read:", "view:"))]
    writes = [a for a in actions if a["kind"] == "tool_use" and a["text"].startswith(("write:", "edit:", "apply_patch"))]
    span = (actions[-1]["ts"] - actions[0]["ts"]) / 60.0
    lines = [
        f"{len(commands)} shell commands, {len(reads)} file reads, {len(writes)} writes over {span:.1f} min "
        f"(the host's log of the Engineer's actions this round, not the Engineer's account)."
    ]
    timed = [c for c in commands if c["gap_s"] is not None]
    for c in sorted(timed, key=lambda c: -c["gap_s"])[:LONGEST_COMMANDS]:
        head = _one_line(c["text"])
        lines.append(f"- ran ≤{_duration(c['gap_s'])} (time to the next action): `{head}`")
    tests = [c for c in commands if TEST_OR_EVAL.search(c["text"])]
    listed: set[int] = set()
    latest: dict[str, dict[str, Any]] = {}
    if tests:
        # The latest run of each distinct command, newest last.
        counts: dict[str, int] = {}
        for c in tests:
            key = _one_line(c["text"], 90)
            counts[key] = counts.get(key, 0) + 1
            latest.pop(key, None)
            latest[key] = c
        lines.append("- tests and checks run, with the result the host recorded for the latest run:")
        for key, c in list(latest.items())[-CHECKS_SHOWN:]:
            times = f" ×{counts[key]}" if counts[key] > 1 else ""
            lines.append(f"  - `{key}`{times}: {_result(c)}")
            listed.add(id(c))
    last = [
        c for c in commands[-LAST_COMMANDS:]
        if id(c) not in listed and _one_line(c["text"], 90) not in latest
    ]
    if last:
        lines.append("- the round's last commands, with the result the host recorded:")
        lines.extend(f"  - `{_one_line(c['text'], 90)}`: {_result(c)}" for c in last)
        listed.update(id(c) for c in last)
    checks = {id(c) for c in tests}
    failed = [
        c for c in commands
        if id(c) not in checks and id(c) not in listed
        and "exit_code" in c and c["exit_code"] != 0
    ]
    if failed:
        shown_failed = ", ".join(
            f"`{_one_line(c['text'], 70)}` "
            f"({'failed' if c['exit_code'] is None else 'exit ' + str(c['exit_code'])})"
            for c in failed[-FAILED_SHOWN:]
        )
        lines.append(f"- other commands that failed: {shown_failed}")
    outside = _outside_paths(actions, workdir)
    if outside:
        lines.append(
            "- paths outside the workspace touched: "
            + "; ".join(f"{p} ({n})" for p, n in outside[:OUTSIDE_PATHS])
        )
    return lines


def _outside_paths(actions: list[dict[str, Any]], workdir: Path) -> list[tuple[str, int]]:
    root = os.path.realpath(str(workdir))
    counts: dict[str, int] = {}
    for a in actions:
        for match in _ABS_PATH.finditer(a["text"]):
            raw = match.group(1).rstrip(".,")
            try:
                real = os.path.realpath(raw)
            except (OSError, ValueError):
                real = raw
            if real == root or real.startswith(root + os.sep):
                continue
            parts = Path(raw).parts
            key = str(Path(*parts[:5])) if len(parts) > 5 else raw
            counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _one_line(text: str, limit: int = 110) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60.0:.1f} min"
    return f"{seconds / 3600.0:.1f} h"


def render_round_log(workdir: Path, life_dir: Path, round_index: int, *, now: float | None = None) -> str:
    """The packet text, or '' when there is no log to read."""
    events_path = find_events_file(Path(life_dir), Path(workdir))
    if events_path is None:
        return ""
    events = list(_iter_events(events_path))
    since = round_window_start(events, round_index)
    if since is None:
        return ""
    actions = engineer_actions(events, since)
    lines = summarize_actions(actions, Path(workdir))
    if not lines:
        return ""
    started = time.strftime("%H:%M", time.localtime(since))
    return "\n".join([f"Engineer's actions this round (host log since {started}):", *lines])


__all__ = [
    "engineer_actions",
    "find_events_file",
    "render_round_log",
    "round_window_start",
    "summarize_actions",
]
