"""Turn one ``engineer.progress`` event into a live, operator-facing step.

The cockpit's live status line used to collapse every observable action into a
handful of euphemisms ("checking project state", "using a tool"). That told the
operator *that* something was happening but never *what*, which is exactly the
"the CLI feels frozen / I can't see what it is doing" complaint.

This module is a dumb, domain-agnostic formatter: it reports the action the
agent actually took (the command it ran, the tool it called, the files it
touched) verbatim-but-trimmed, after routing the text through
:mod:`argus.core.secret_guard`. It makes no judgment about whether the
step was useful, on-track, or complete — that stays with the agent.

``describe_progress_step`` returns ``(label, detail)``:

* ``label`` — one short scannable line for the live status row.
* ``detail`` — the longer redacted body for an expandable/secondary row; may be
  empty when the label already says everything.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

from .secret_guard import redact_secrets_text

# Kinds that are a reply block rather than an observable action. The Manager
# front door streams these as reply deltas, so they never become steps.
REPLY_KINDS = frozenset({"assistant_message", "agent_message", "message"})

_LABEL_LIMIT = 96
_DETAIL_LIMIT = 240

_SHELL_PREFIXES = (
    "/bin/bash -lc ",
    "/bin/bash -c ",
    "/bin/sh -lc ",
    "/bin/sh -c ",
    "bash -lc ",
    "bash -c ",
    "sh -lc ",
    "sh -c ",
)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return (text or "").strip()


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(1, limit - 1)].rstrip() + "…"


def strip_shell_wrapper(command: str) -> str:
    """Unwrap ``/bin/bash -lc '<cmd>'`` so the operator sees ``<cmd>``."""
    text = (command or "").strip()
    for prefix in _SHELL_PREFIXES:
        if text.startswith(prefix):
            inner = text[len(prefix):].strip()
            if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
                inner = inner[1:-1]
            return inner.strip()
    return text


def _tool_label(text: str) -> tuple[str, str]:
    """Split a ``"toolName: {json args}"`` progress text into label/detail."""
    body = (text or "").strip()
    name, separator, args = body.partition(":")
    if separator and name.strip() and " " not in name.strip():
        return name.strip(), args.strip()
    return _first_line(body), ""


_INTERNAL_FILE = "Argus 内部文件"

_READ_TOOLS = frozenset({"view", "read", "read_file", "readfile", "cat", "open", "open_file", "ls", "list_dir"})
_SEARCH_TOOLS = frozenset({
    "grep", "glob", "search", "rg", "find", "web_search", "websearch", "search_files",
    "file_search", "codebase_search",
})
_FETCH_TOOLS = frozenset({"fetch", "web_fetch", "webfetch", "browse", "open_url", "http_get", "curl"})
_EDIT_TOOLS = frozenset({
    "edit", "write", "write_file", "apply_patch", "create", "create_file", "str_replace",
    "str_replace_editor", "patch", "multiedit", "multi_edit", "edit_file", "notebookedit",
})
_PATH_KEYS = ("path", "file_path", "filePath", "file", "filename", "target_file", "notebook_path")
_QUERY_KEYS = ("query", "pattern", "q", "regex", "glob", "search", "prompt")
_URL_KEYS = ("url", "uri", "href")
_PATCH_FILE = re.compile(r"\*\*\* (?:Update|Add|Delete) File:\s*(\S+)")


def _short_name(path: str, workspace: str = "") -> str:
    """Return a path the operator can read: a basename, never an absolute path.

    Paths outside the active workspace are Argus' own plumbing (session
    stores, skill libraries); they are named as such rather than spelled out.
    """
    text = str(path or "").strip().strip("'\"")
    if not text:
        return ""
    if os.path.isabs(text) or text.startswith("~"):
        root = str(workspace or "").rstrip("/")
        if root and not (text == root or text.startswith(root + "/")):
            return _INTERNAL_FILE
    return os.path.basename(text.rstrip("/")) or text


def _parse_args(args: str) -> Any:
    try:
        return json.loads(args)
    except (TypeError, ValueError):
        return None


def _first_value(payload: Any, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _tool_verb(name: str) -> str:
    """Classify a tool name into one of: read / search / fetch / edit / ''."""
    key = name.strip().lower().replace("-", "_")
    key = key.rsplit(".", 1)[-1].rsplit("__", 1)[-1]
    if key in _READ_TOOLS:
        return "read"
    if key in _SEARCH_TOOLS or "search" in key or "grep" in key:
        return "search"
    if key in _FETCH_TOOLS or "fetch" in key:
        return "fetch"
    if key in _EDIT_TOOLS or "patch" in key or key.startswith(("edit", "write")):
        return "edit"
    return ""


def _humane_tool_label(name: str, args: str, workspace: str = "") -> str:
    """"Verb + object" for one tool call, with no raw JSON and no absolute path."""
    verb = _tool_verb(name)
    payload = _parse_args(args)
    path = _first_value(payload, _PATH_KEYS)
    if verb == "edit" and not path:
        match = _PATCH_FILE.search(args or "")
        if match:
            path = match.group(1)
    if verb == "read":
        target = _short_name(path, workspace)
        return f"查阅 {target}" if target else "查阅文件"
    if verb == "search":
        query = _first_value(payload, _QUERY_KEYS)
        if not query and payload is None and args and "{" not in args:
            query = args
        return f"搜索：{_clip(query, 48)}" if query else "搜索"
    if verb == "fetch":
        url = _first_value(payload, _URL_KEYS) or (args if payload is None else "")
        host = urlparse(url.strip()).netloc if url else ""
        return f"读取 {host}" if host else "读取网页"
    if verb == "edit":
        target = _short_name(path, workspace)
        return f"修改 {target}" if target else "修改文件"
    return f"调用 {name}"


def describe_progress_step(event: Any) -> tuple[str, str]:
    """Return ``(label, detail)`` describing one observable agent action.

    Never raises: a malformed event degrades to a generic-but-honest label
    rather than breaking the turn that produced it.
    """
    try:
        if not isinstance(event, dict):
            return "working", ""
        kind = str(event.get("kind") or "").strip()
        raw_text = redact_secrets_text(str(event.get("text") or ""))
        summary = " ".join(str(event.get("action_summary") or "").split())

        if kind == "command_execution":
            command = strip_shell_wrapper(raw_text).strip()
            head = strip_shell_wrapper(_first_line(raw_text))
            status = str(event.get("status") or "").strip().lower()
            marker = "✗ $" if status in {"failed", "error"} else "$"
            if head:
                label = f"{marker} {_clip(head, _LABEL_LIMIT - 2)}"
                # Only carry a detail when it actually adds something (a
                # multi-line or clipped command); never echo the label back.
                detail = _clip(command, _DETAIL_LIMIT)
                return label, "" if detail == _clip(head, _DETAIL_LIMIT) else detail
            return summary or "running a command", ""

        if kind in {"tool_use", "tool_call"}:
            name, args = _tool_label(raw_text)
            if name:
                workspace = str(event.get("workspace") or event.get("cwd") or "")
                label = _clip(_humane_tool_label(name, args, workspace), _LABEL_LIMIT)
                # The raw arguments stay available in the (collapsed) detail.
                return label, _clip(f"{name}: {args}" if args else "", _DETAIL_LIMIT)
            return summary or "using a tool", ""

        if kind == "file_change":
            changed = event.get("changes")
            if isinstance(changed, list) and changed:
                names = [str(item) for item in changed if str(item).strip()]
                if names:
                    head = ", ".join(names[:3])
                    extra = f" +{len(names) - 3}" if len(names) > 3 else ""
                    return _clip(f"✎ {head}{extra}", _LABEL_LIMIT), ""
            first = _first_line(raw_text)
            if first:
                return _clip(f"✎ {first}", _LABEL_LIMIT), _clip(raw_text, _DETAIL_LIMIT)
            return summary or "editing files", ""

        if kind == "reasoning":
            first = _first_line(raw_text)
            return (
                _clip(f"… {first}", _LABEL_LIMIT) if first else "reasoning about the next step"
            ), ""

        if kind == "tool_result":
            status = str(event.get("status") or "").strip().lower()
            name = " ".join(str(event.get("tool_name") or "").split())
            if name and status:
                excerpt = " ".join(str(event.get("output_excerpt") or "").split())
                return _clip(f"↳ {name} · {status}", _LABEL_LIMIT), _clip(excerpt, _DETAIL_LIMIT)
            first = _first_line(raw_text)
            return (_clip(f"↳ {first}", _LABEL_LIMIT) if first else "reading a tool result"), ""

        if kind in REPLY_KINDS:
            return summary or "writing the reply", ""

        first = _first_line(raw_text)
        if first:
            return _clip(first, _LABEL_LIMIT), ""
        return summary or (kind.replace("_", " ") if kind else "working"), ""
    except Exception:  # noqa: BLE001 — a status label must never break a turn
        return "working", ""


class ProgressDeduper:
    """Drop repeated start/complete renders of the same step.

    Backends report one tool call twice (started, then completed). Keyed by
    ``item_id``/``call_id``, a repeat with an unchanged label is redundant; a
    changed label (for example a failure marker) is an in-place update.
    """

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def is_repeat(self, event: Any, label: str) -> bool:
        if not isinstance(event, dict):
            return False
        ident = str(event.get("item_id") or event.get("call_id") or "").strip()
        if not ident:
            return False
        key = f"{event.get('kind') or ''}:{ident}"
        if self._seen.get(key) == label:
            return True
        self._seen[key] = label
        return False


class ProgressTally:
    """Running counts of what a long turn has done, for the idle notice."""

    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._dedupe = ProgressDeduper()
        self.searches = 0
        self.reads = 0
        self.edits = 0
        self.commands = 0

    def observe(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        kind = str(event.get("kind") or "")
        if kind not in {"tool_use", "tool_call", "command_execution", "file_change"}:
            return
        label, _ = describe_progress_step(event)
        if self._dedupe.is_repeat(event, label):
            return
        if kind == "command_execution":
            self.commands += 1
        elif kind == "file_change":
            self.edits += 1
        else:
            name, _ = _tool_label(redact_secrets_text(str(event.get("text") or "")))
            verb = _tool_verb(name)
            if verb == "search":
                self.searches += 1
            elif verb in {"read", "fetch"}:
                self.reads += 1
            elif verb == "edit":
                self.edits += 1

    def summary(self) -> str:
        parts = []
        if self.searches:
            parts.append(f"已搜索 {self.searches} 次")
        if self.reads:
            parts.append(f"读了 {self.reads} 页")
        if self.edits:
            parts.append(f"改了 {self.edits} 处")
        if self.commands:
            parts.append(f"跑了 {self.commands} 条命令")
        elapsed = max(0, int(self._clock() - self._started))
        parts.append(f"用时 {elapsed // 60} 分钟" if elapsed >= 60 else f"用时 {elapsed} 秒")
        return " · ".join(parts)


__all__ = [
    "REPLY_KINDS",
    "ProgressDeduper",
    "ProgressTally",
    "describe_progress_step",
    "strip_shell_wrapper",
]
