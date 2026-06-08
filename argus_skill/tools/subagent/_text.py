"""Codex CLI stdout parsing + binary discovery (pure helpers)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def _find_codex() -> str:
    codex = shutil.which("codex")
    if codex:
        return codex
    for candidate in ["/usr/local/bin/codex", "/usr/bin/codex"]:
        if os.path.isfile(candidate):
            return candidate
    return "codex"

def _tail_file(path: Path, max_chars: int = 3000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:] if len(text) > max_chars else text
    except (OSError, FileNotFoundError):
        return ""

def _codex_agent_messages(stdout: str) -> list[str]:
    """Extract all assistant messages from ``codex exec --json`` output.

    Codex emits JSONL (one event per line); each assistant reply arrives as
    ``{"type": "item.completed", "item": {"type": "agent_message",
    "text": ...}}``. This mirrors the canonical parser in
    ``argus_skill.codex_autoloop.codex_runner`` so the subagent supervisor and
    reporter read the real schema instead of a stale ``messages`` shape.
    """
    out: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text", "")
                if isinstance(text, str) and text:
                    out.append(text)
    return out

def _codex_last_agent_message(stdout: str) -> str:
    """Return the final assistant message (empty string if none)."""
    messages = _codex_agent_messages(stdout)
    return messages[-1] if messages else ""

def _strip_code_fence(text: str) -> str:
    """Drop a leading/trailing markdown code fence if the model wrapped JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()

def _codex_thread_id(stdout: str) -> str | None:
    """Extract the codex thread/session id from a ``codex exec --json`` stream."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
        sid = event.get("session_id") or event.get("sessionId")
        if isinstance(sid, str) and sid.strip():
            return sid
    return None

