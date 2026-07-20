"""Detect long jobs launched outside Argus durable subagent ownership."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_LONG_COMMAND = re.compile(
    r"run_on_free_gpu\.sh|\btorchrun\b|\bdeepspeed\b|"
    r"\baccelerate\s+launch\b|--scale\s+full\b|"
    r"while\s+:\s*;\s*do|sleep\s+(?:[2-9]\d\d|\d{4,})\b",
    re.IGNORECASE,
)
_MANAGED_COMMAND = re.compile(
    r"argus_skill\.tools\.subagent\s+submit|"
    r"argus_skill\.tools\.gpu_lease\s+run\b[^\n]*--detach",
    re.IGNORECASE,
)


def classify_long_job_command(command: str) -> str:
    text = str(command or "")
    if _MANAGED_COMMAND.search(text):
        return "managed"
    if _LONG_COMMAND.search(text):
        return "unmanaged_long_job"
    return "ordinary"


def find_unmanaged_long_jobs(
    events_path: str | Path,
    *,
    call_id: str = "",
    since: float = 0.0,
) -> list[dict[str, Any]]:
    path = Path(events_path)
    if not path.is_file():
        return []
    findings: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            outer = json.loads(line)
        except json.JSONDecodeError:
            continue
        if outer.get("type") != "agent.io.stream":
            continue
        if call_id and str(outer.get("call_id") or "") != call_id:
            continue
        try:
            if float(outer.get("ts") or 0.0) < float(since or 0.0):
                continue
        except (TypeError, ValueError):
            continue
        try:
            event = json.loads(str(outer.get("line") or ""))
        except json.JSONDecodeError:
            continue
        if event.get("type") != "tool.execution_start":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        arguments = data.get("arguments")
        if isinstance(arguments, dict):
            command = str(arguments.get("command") or "")
            if not command and data.get("toolName") == "read_bash":
                delay = arguments.get("delay") or arguments.get("initial_wait")
                command = f"read_bash delay={delay}"
        else:
            command = str(arguments or "")
        if data.get("toolName") == "read_bash":
            delay_match = re.search(r"(?:delay|initial_wait)[^0-9]*(\d+)", command)
            if delay_match and int(delay_match.group(1)) >= 120:
                classification = "unmanaged_busy_wait"
            else:
                classification = "ordinary"
        else:
            classification = classify_long_job_command(command)
        if classification.startswith("unmanaged"):
            findings.append({
                "tool": str(data.get("toolName") or ""),
                "classification": classification,
                "command": command[:1000],
                "timestamp": event.get("timestamp"),
            })
    return findings[-20:]


__all__ = ["classify_long_job_command", "find_unmanaged_long_jobs"]
