"""Codex CLI stdout parsing + binary discovery (pure helpers)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

#: System-wide install locations probed after PATH, for the case where a
#: service-managed or ``setsid``-detached process was handed a trimmed PATH.
_CODEX_SYSTEM_PATHS: tuple[str, ...] = ("/usr/local/bin/codex", "/usr/bin/codex")

#: Operator escape hatch: absolute path to the agent CLI binary. The same knob
#: the supervisor's own runner resolution honors (``core.role_config``), so the
#: fix named in the error below is the fix that actually works.
_RUNNER_BIN_ENV = "ARGUS_SKILL_RUNNER_BIN"


def _find_codex() -> str:
    """Resolve an executable path to the codex CLI.

    Raises:
        FileNotFoundError: when every probe came up empty. The probes *prove*
            codex is absent, so handing back the bare name ``"codex"`` would
            only defer the same failure to ``subprocess`` — which reports
            ``FileNotFoundError: 'codex'`` and discards the trail, leaving the
            operator unable to tell "not installed" from "PATH not exported to
            this detached worker" from "installed somewhere else". The message
            therefore carries the probed locations in order, the PATH in force,
            and the override knob. This mirrors
            ``agent_cli.runner_backend._resolve_explicit_candidate``, which
            likewise reports absence instead of guessing.
    """
    probed: list[str] = []

    configured = str(os.environ.get(_RUNNER_BIN_ENV, "") or "").strip()
    if configured:
        expanded = str(Path(configured).expanduser())
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        probed.append(f"{_RUNNER_BIN_ENV}={configured!r} (not an executable file)")

    codex = shutil.which("codex")
    if codex:
        return codex
    probed.append("PATH lookup for 'codex' (shutil.which)")

    for candidate in _CODEX_SYSTEM_PATHS:
        if os.path.isfile(candidate):
            return candidate
        probed.append(candidate)

    raise FileNotFoundError(
        "codex CLI not found. Probed, in order: "
        + "; ".join(probed)
        + f". PATH={os.environ.get('PATH', '') or '(empty)'}. "
        "Install the codex CLI, export a PATH containing it to this process "
        "(a setsid-detached worker does not inherit an interactive shell's "
        f"PATH), or set {_RUNNER_BIN_ENV} to the CLI's absolute path."
    )

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
    ``argus_skill.agent_cli.agent_cli_runner`` so the subagent supervisor and
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

