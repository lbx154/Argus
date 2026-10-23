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
# How many files a step names before it says "and others".
_NAMES_SHOWN = 3

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
_CJK = re.compile(r"[㐀-鿿]")
# The Copilot CLI spills a long tool output to ``<ts>-copilot-tool-output-<id>.txt``
# and reads it back from there.
_TOOL_OUTPUT_FILE = re.compile(r"-copilot-tool-output-")
# Argus' own tools run as ``python -m argus.tools.<name>``.
_ARGUS_TOOL = re.compile(r"-m\s+argus\.tools\.")
_ABSOLUTE_PATH = re.compile(r"(?<![\w.~:-])/[^\s'\"`;|&()<>]+")
# A pattern in regular-expression syntax tells a reader nothing.
_REGEX_SYNTAX = re.compile(r"[\\^$|\[\]]|\.\*")

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


def _parts(path: str) -> list[str]:
    return [part for part in path.replace("\\", "/").split("/") if part]


# The root of a knowledge wiki: a project's own under ``.autors`` (which holds
# nothing else), the shared ones under the Argus home (``wiki/_global``,
# ``wiki/_shared_verticals/<v>``).
_WIKI_ROOTS = ((".autors",), ("wiki", "_global"), ("wiki", "_shared_verticals", "*"))
# The root of a skill library: the shared and per-project ones under the Argus
# home, and the ones Argus ships.
_SKILL_ROOTS = (
    ("state", "skills"), ("state", "projects", "*", "skills"), ("argus", "builtin_skills"),
    ("argus", "verticals", "*", "skills"), ("argus", "domains", "*", "skills"),
)
# Libraries the roles read as material: what a file in one is called, and the library.
_LIBRARIES = ((_WIKI_ROOTS, "知识页", "知识库"), (_SKILL_ROOTS, "技能", "技能库"))
# Where a role leaves its notes for whoever holds the task next.
_HANDOFF_ROOTS = (("state", "projects", "*", "handoffs"),)
# Tools that start a helper agent on part of the work.
_HELPER_TOOLS = frozenset({"task", "agent"})
# Path segments that only Argus' own plumbing uses: session stores, shared
# skill libraries and wikis, handoff checkpoints, the shipped verticals, and
# the installed Argus package itself (its built-in skills).
_PLUMBING_SEGMENTS = (
    ("state", "projects"), ("state", "skills"), ("argus", "verticals"),
    ("role-sessions",), ("copilot-home",), *_WIKI_ROOTS,
    tuple(_parts(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))),
)


def _marker_end(parts: list[str], markers: tuple[tuple[str, ...], ...]) -> int:
    """Where the first of ``markers`` found in ``parts`` ends; 0 when none is there."""
    for marker in markers:
        for start in range(len(parts) - len(marker) + 1):
            if all(want in {"*", parts[start + offset]} for offset, want in enumerate(marker)):
                return start + len(marker)
    return 0


def _phrase(verb: str, target: str) -> str:
    """``查阅 main.py``, but ``查阅项目目录``: Chinese words take no space between them."""
    return f"{verb}{'' if _CJK.match(target) else ' '}{target}"


def _short_name(path: str, workspace: str = "") -> str:
    """Return a path the operator can read: a basename, never an absolute path.

    Files of Argus' own plumbing (session stores) are named as such rather
    than spelled out. A page of a knowledge wiki or a skill of a skill
    library, the project's own or a shared one, is named as the page or
    skill it is, because roles read it as material; a role's notes for the
    next holder are named as those notes. Any other file -- including a
    repository outside the session workspace that the operator asked about
    -- is named by its file name, because reading it is the work.
    """
    text = str(path or "").strip().strip("'\"")
    parts = _parts(text)
    if not parts:
        return text
    if _TOOL_OUTPUT_FILE.search(parts[-1]):
        return "工具输出"
    for roots, item, library in _LIBRARIES:
        root = _marker_end(parts, roots)
        if root:
            # A file in the library is one of its pages; a folder in it is the library.
            name, extension = os.path.splitext(parts[-1])
            return f"{item} {name}" if len(parts) > root and extension else library
    if _marker_end(parts, _HANDOFF_ROOTS):
        return "交接记录"
    if _marker_end(parts, _PLUMBING_SEGMENTS):
        return _INTERNAL_FILE
    if workspace and os.path.normpath(text) == os.path.normpath(workspace):
        return "项目目录"
    return parts[-1]


def plain_command(command: str, workspace: str = "") -> str:
    """A command line as the operator follows it.

    A path into Argus' plumbing (a backend session's scratch files, a session
    store) is cut to its last name; a path in the project ``workspace`` reads
    from the project root; any other path stays as it is.
    """
    root = _parts(workspace)

    def short(match: re.Match[str]) -> str:
        parts = _parts(match.group())
        if _marker_end(parts, _PLUMBING_SEGMENTS):
            return f"…/{parts[-1]}"
        if root and parts[:len(root)] == root:
            return "/".join(parts[len(root):]) or "."
        return match.group()

    return _ABSOLUTE_PATH.sub(short, command)


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


def _is_glob_tool(name: str) -> bool:
    key = name.strip().lower().rsplit(".", 1)[-1].rsplit("__", 1)[-1]
    return key in {"glob", "find", "find_files", "list_files", "file_search"}


_GLOB_WILDCARD = re.compile(r"\[[^\]]*\]|[*?]")
_BRACES = re.compile(r"([^{}]*)\{([^{}]*)\}([^{}]*)")


def _brace_names(pattern: str) -> str:
    """``{pyproject.toml,setup.py,*.cfg}`` -> ``pyproject.toml、setup.py 等文件``.

    The first few alternatives that name a file are said; with none, or with
    braces in braces, it is plain "files".
    """
    match = _BRACES.fullmatch(pattern)
    leaves = [f"{match[1]}{alternative}{match[3]}".rsplit("/", 1)[-1]
              for alternative in match[2].split(",")] if match else []
    names = list(dict.fromkeys(leaf for leaf in leaves if leaf and not _GLOB_WILDCARD.search(leaf)))
    if not names:
        return "文件"
    shown = "、".join(names[:_NAMES_SHOWN])
    return shown if len(names) == len(set(leaves)) <= _NAMES_SHOWN else f"{shown} 等文件"


def _readable_glob(pattern: str) -> str:
    """``**/*.py`` -> ``.py 文件``; ``src/**/*`` -> ``src 里的所有文件``.

    A glob is read as "which files": the directory part becomes the folder
    the search ran in (last segment only) and the file part becomes the kind
    of file, so no slash reaches the status line. A pattern with ``{a,b}``
    alternatives names the files among them; any other wildcard name is said
    by the words it keeps: ``**/*SKILL*`` -> ``名字含 SKILL 的文件``.
    """
    text = pattern.strip().strip("'\"").replace("\\", "/")
    if "{" in text:
        return _clip(_brace_names(text), 60)
    parts = [part for part in text.split("/") if part and part not in {"**", "."}]
    leaf = parts[-1] if parts else "*"
    folders = [part for part in parts[:-1] if "*" not in part and "?" not in part]
    if leaf in {"*", "*.*"}:
        what = "所有文件"
    elif re.fullmatch(r"\*\.[\w.]+", leaf):
        what = f"{leaf[1:]} 文件"
    elif _GLOB_WILDCARD.search(leaf):
        named = "、".join(piece for piece in _GLOB_WILDCARD.split(leaf) if re.search(r"\w", piece))
        what = f"名字含 {named} 的文件" if named else "文件"
    else:
        what = _clip(leaf, 40)
    return _clip(_phrase(f"{folders[-1]} 里的", what) if folders else what, 60)


def _no_paths(text: str) -> str:
    """Keep a search phrase but reduce any absolute path in it to its last name."""
    return " ".join(
        _short_name(token) if token.startswith(("/", "~")) else token
        for token in (text or "").split()
    )


def _humane_tool_label(name: str, args: str, workspace: str = "") -> str:
    """"Verb + object" for one tool call, with no raw JSON and no absolute path."""
    verb = _tool_verb(name)
    payload = _parse_args(args)
    path = _first_value(payload, _PATH_KEYS)
    if verb == "edit" and not path:
        match = _PATCH_FILE.search(args or "")
        if match:
            path = match.group(1)
    if verb in {"read", "edit"} and not path and payload is None and args and "{" not in args:
        # A bare ``read: notes.md`` names its file directly.
        path = args.split()[0]
    if verb == "read":
        target = _short_name(path, workspace)
        return _phrase("查阅", target) if target else "查阅文件"
    if verb == "search":
        query = _first_value(payload, _QUERY_KEYS)
        if not query and payload is None and args and "{" not in args:
            query = args
        globbed = _first_value(payload, ("glob",)) and not _first_value(payload, ("pattern", "query", "q", "regex"))
        if query and (_is_glob_tool(name) or globbed):
            return _phrase("查找", _readable_glob(query))
        if _REGEX_SYNTAX.search(query):
            return "搜索文件内容"
        return f"搜索：{_clip(_no_paths(query), 48)}" if query else "搜索"
    if verb == "fetch":
        url = _first_value(payload, _URL_KEYS) or (args if payload is None else "")
        host = urlparse(url.strip()).netloc if url else ""
        return f"读取 {host}" if host else "读取网页"
    if verb == "edit":
        target = _short_name(path, workspace)
        return _phrase("修改", target) if target else "修改文件"
    if name.strip().lower() in _HELPER_TOOLS:
        # A helper the role starts: say what it was given, not the tool's name.
        what = _first_value(payload, ("description",))
        return f"交给子助手：{_clip(what, 48)}" if what else "交给一个子助手"
    # A role hands the work on through Argus' own action, not a tool of the work.
    return "交出工作" if name.endswith("hand_off") else f"调用 {name}"


def describe_progress_step(event: Any, workspace: str = "") -> tuple[str, str]:
    """Return ``(label, detail)`` describing one observable agent action.

    ``workspace`` is the project root the agent works in; files outside it
    are Argus' own plumbing and are named as such. Pass it from the session
    meta, because progress events do not carry it themselves.

    Never raises: a malformed event degrades to a generic-but-honest label
    rather than breaking the turn that produced it.
    """
    try:
        if not isinstance(event, dict):
            return "working", ""
        kind = str(event.get("kind") or "").strip()
        raw_text = redact_secrets_text(str(event.get("text") or ""))
        summary = " ".join(str(event.get("action_summary") or "").split())
        workspace = str(event.get("workspace") or event.get("cwd") or workspace or "")

        if kind == "command_execution":
            command = strip_shell_wrapper(raw_text).strip()
            head = strip_shell_wrapper(_first_line(raw_text))
            status = str(event.get("status") or "").strip().lower()
            marker = "✗ $" if status in {"failed", "error"} else "$"
            if _ARGUS_TOOL.search(head):
                # Argus' own tool: the operator is told one ran, not shown its command line.
                return "运行 Argus 内部工具", _clip(command, _DETAIL_LIMIT)
            if head:
                label = f"{marker} {_clip(plain_command(head, workspace), _LABEL_LIMIT - 2)}"
                # Only carry a detail when it actually adds something (a
                # multi-line or clipped command); never echo the label back.
                detail = _clip(command, _DETAIL_LIMIT)
                return label, "" if detail == _clip(head, _DETAIL_LIMIT) else detail
            return summary or "running a command", ""

        if kind in {"tool_use", "tool_call"}:
            name, args = _tool_label(raw_text)
            if name:
                label = _clip(_humane_tool_label(name, args, workspace), _LABEL_LIMIT)
                # The raw arguments stay available in the (collapsed) detail.
                return label, _clip(f"{name}: {args}" if args else "", _DETAIL_LIMIT)
            return summary or "using a tool", ""

        if kind == "file_change":
            changed = event.get("changes")
            if isinstance(changed, list) and changed:
                names = [str(item) for item in changed if str(item).strip()]
                if names:
                    head = ", ".join(names[:_NAMES_SHOWN])
                    extra = f" +{len(names) - _NAMES_SHOWN}" if len(names) > _NAMES_SHOWN else ""
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
            # A result names paths the way a command line does.
            if name and status:
                excerpt = plain_command(" ".join(str(event.get("output_excerpt") or "").split()), workspace)
                return _clip(f"↳ {name} · {status}", _LABEL_LIMIT), _clip(excerpt, _DETAIL_LIMIT)
            first = plain_command(_first_line(raw_text), workspace)
            return (_clip(f"↳ {first}", _LABEL_LIMIT) if first else "reading a tool result"), ""

        if kind in REPLY_KINDS:
            return summary or "writing the reply", ""

        first = _first_line(raw_text)
        if first:
            return _clip(first, _LABEL_LIMIT), ""
        return summary or (kind.replace("_", " ") if kind else "working"), ""
    except Exception:  # noqa: BLE001 — a status label must never break a turn
        return "working", ""


_LEGACY_TOOL_LABEL = re.compile(r"^⚙\s*([\w.-]+)\s*(?:·\s*(.*))?$", re.S)
_RAW_TOOL_TEXT = re.compile(r"^[A-Za-z_][\w.-]{0,40}:\s")


def plain_step_label(label: str, tool: str = "", detail: str = "", workspace: str = "") -> str:
    """Say a stored tool step plainly, whatever shape it was saved in.

    Turn steps saved before plain labels existed read ``⚙ view · {json}``
    with the full arguments in ``detail``; those are relabelled from the tool
    and its arguments. A raw ``name: {json}`` text is relabelled the same way.
    A label that is already plain is returned unchanged, never wrapped again.
    """
    text = str(label or "").strip()
    legacy = _LEGACY_TOOL_LABEL.match(text)
    if legacy:
        name = str(tool or "").strip() or legacy.group(1)
        args = str(detail or "").strip() or legacy.group(2) or ""
        # ``detail`` of a newer step already carries the ``name: `` prefix.
        raw = args if _RAW_TOOL_TEXT.match(args) else f"{name}: {args}"
        return describe_progress_step({"kind": "tool_use", "text": raw}, workspace)[0] or text
    if _RAW_TOOL_TEXT.match(text):
        return describe_progress_step({"kind": "tool_use", "text": text}, workspace)[0] or text
    return text


_TERMINAL_FAILURES = frozenset({"failed", "error", "errored", "cancelled", "canceled"})


class ProgressDeduper:
    """Tell a new tool call apart from a later report about the same call.

    Backends report one call more than once: started, then completed or
    failed. Identity is the call itself -- its ``call_id`` when the runner
    sends one, otherwise the still-open call with the same kind, actor and
    raw text. (``item_id`` is not a call identity: some runners stamp every
    call of a mission with the same one.)

    ``classify`` returns:

    * ``"new"`` -- a call not seen before, even if it reads like an earlier
      one (two reads of the same file are two things that happened);
    * ``"update"`` -- a known call whose outcome is news worth showing
      (it failed, or its label changed);
    * ``"repeat"`` -- a known call reported again with nothing new, such as
      a plain completion echo of a step already on screen.
    """

    def __init__(self) -> None:
        self._by_call: dict[str, tuple[str, str]] = {}
        self._open: dict[tuple[str, str, str], tuple[str, str]] = {}

    @staticmethod
    def _status(event: dict) -> str:
        return str(event.get("status") or "").strip().lower()

    def classify(self, event: Any, label: str) -> str:
        if not isinstance(event, dict):
            return "new"
        status = self._status(event)
        call_id = str(event.get("call_id") or "").strip()
        kind = str(event.get("kind") or "")
        if call_id:
            key = f"{kind}:{call_id}"
            previous = self._by_call.get(key)
            self._by_call[key] = (label, status)
            if previous is None:
                return "new"
        else:
            text_key = (kind, str(event.get("actor") or event.get("agent_layer") or ""),
                        str(event.get("text") or ""))
            starting = status in {"", "running", "started", "in_progress", "pending"}
            previous = None if starting else self._open.pop(text_key, None)
            if previous is None:
                if starting:
                    self._open[text_key] = (label, status)
                return "new"
        before_label, before_status = previous
        if label != before_label:
            return "update"
        if status in _TERMINAL_FAILURES and before_status not in _TERMINAL_FAILURES:
            return "update"
        return "repeat"

    def is_repeat(self, event: Any, label: str) -> bool:
        return self.classify(event, label) == "repeat"

    def phase_kind(self, event: Any, label: str) -> str:
        """The phase kind to stream for ``event``, or ``""`` to stay silent.

        A new call streams under its own kind. A later report about a call
        already on screen (it failed) streams as ``tool_result`` so the open
        step changes in place: a failed read is one failed step, not a
        "completed" step followed by a second, failed copy of itself.
        """
        verdict = self.classify(event, label)
        if verdict == "repeat":
            return ""
        kind = str(event.get("kind") or "") if isinstance(event, dict) else ""
        return "tool_result" if verdict == "update" else kind


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
        if self._dedupe.classify(event, label) != "new":
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
    "plain_command",
    "plain_step_label",
    "strip_shell_wrapper",
]
