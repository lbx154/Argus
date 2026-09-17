"""What the Engineer actually did this round, from the host's own log.

The Reviewer used to learn about the round from the Engineer's account and
then re-read the tree to check it: in one control project 61 of its 105 file
reads were files the Engineer had just read, and it still never opened the
script that computed a "perplexity" by formula. The host already records
every command and file the Engineer touched (``engineer.progress`` events),
so the packet can simply say what happened: how many commands, which ran
longest (the time to the next action bounds a command's runtime), which
tests and evaluations ran, and which paths lay outside the workspace. The
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
TEST_OR_EVAL = re.compile(r"pytest|\beval|benchmark|figure_lint|pptx_export|generate_figures|train", re.IGNORECASE)
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
    """Engineer tool events after ``since``, each with the time until the next one."""
    rows = [
        e
        for e in events
        if e.get("type") == "engineer.progress"
        and str(e.get("agent_layer") or "engineer") == "engineer"
        and e.get("kind") in ("command_execution", "tool_use")
        and float(e.get("ts") or 0) >= since
    ]
    rows.sort(key=lambda e: float(e.get("ts") or 0))
    out: list[dict[str, Any]] = []
    for index, e in enumerate(rows):
        ts = float(e.get("ts") or 0)
        nxt = float(rows[index + 1].get("ts") or 0) if index + 1 < len(rows) else None
        text = str(e.get("text") or "").strip()
        out.append(
            {
                "ts": ts,
                "kind": e.get("kind"),
                "tool": str(e.get("tool_name") or ""),
                "text": text,
                "gap_s": (nxt - ts) if nxt is not None else None,
            }
        )
    return out


def summarize_actions(actions: list[dict[str, Any]], workdir: Path) -> list[str]:
    """Lines for the Reviewer: counts, longest commands, tests/evals, paths outside the workspace."""
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
    if tests:
        seen: dict[str, int] = {}
        for c in tests:
            seen[_one_line(c["text"], 90)] = seen.get(_one_line(c["text"], 90), 0) + 1
        shown = ", ".join(f"`{k}`" + (f" ×{v}" if v > 1 else "") for k, v in list(seen.items())[:5])
        lines.append(f"- tests or evaluations invoked: {shown}")
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
