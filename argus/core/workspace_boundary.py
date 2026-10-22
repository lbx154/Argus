"""Keep an Engineer's and a Reviewer's tools out of other projects' data (SE-1).

Every project's roles run as the same OS user, so without a boundary an
Engineer working on one project can read another project's workspace and
state, and on a host where several tenants share that user (the enrolled
native trial workspace) another tenant's files. The roadmap item SE-1 asks
for a boundary at the tool layer first; process sandboxing is the long-term
answer and is not attempted here.

A :class:`WorkspaceBoundary` names two kinds of directories:

* ``own``: the role's workdir, its own project state and the directories Argus
  hands it (Skill and recall libraries, handoffs).
* ``closed``: where other projects' and tenants' data lives that Argus knows
  of: every project's state under the runtime root, the runtime's session
  stores that hold every project's transcripts, other projects' workdirs, and
  roots the operator declares in ``ARGUS_SKILL_FOREIGN_ROOTS``.

A path is refused when the deepest of these directories that contains it is
closed, so a project's own state inside the runtime's ``projects/`` stays
readable. A recursive search (a grep/glob/find tool, or ``grep -r``, ``rg``,
``find`` ...) is refused when a closed directory lies under the place it
searches. Everything else on the machine is left as it was.

Agent CLIs consult it through their own pre-tool hooks, which run this file
as a script (``python -I workspace_boundary.py --boundary FILE --format F``)
and read the hook payload on stdin. The script needs only the standard
library, so it starts quickly and never imports the package it guards.

What it does not do: file tools are checked exactly; shell commands are
checked by the paths they name, which a determined model can hide (a script
it wrote, an encoded path, a ``cd`` in an earlier call). Only an OS sandbox
closes that, which is why the roadmap keeps sandbox isolation as SE-1's long
term step.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shlex
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

#: ``os.pathsep``-separated directories of other tenants (or anything else an
#: operator wants no role to touch) on a host where tenants share an OS user.
FOREIGN_ROOTS_ENV = "ARGUS_SKILL_FOREIGN_ROOTS"

# Tool-input keys whose value names a file or directory.
_PATH_KEYS = frozenset({
    "path", "paths", "filepath", "file", "files", "notebookpath", "targetfile",
    "dir", "directory", "cwd", "workdir", "workingdirectory", "root",
})
# Tool-input keys whose value is shell text: Bash/exec commands, and the input
# a model types into a shell session it started earlier.
_COMMAND_KEYS = frozenset({"command", "commands", "cmd", "script", "input", "chars"})
# A search tool's file pattern, which can itself be an absolute glob.
_PATTERN_KEYS = frozenset({"pattern", "glob", "include"})
# Structured tools that walk the tree under their path.
_SEARCH_TOOLS = frozenset({"grep", "glob", "find", "rg", "search"})
# Shell commands that walk the tree under their arguments, always or with a
# recursive flag.
_RECURSIVE_COMMANDS = frozenset({
    "rg", "ag", "ack", "find", "fd", "fdfind", "tree", "du", "rsync", "tar", "zip", "7z",
})
_FLAG_RECURSIVE_COMMANDS = frozenset({
    "grep", "egrep", "fgrep", "zgrep", "cp", "scp", "ls", "chmod", "chown", "rm",
})
# Searches whose first argument is the pattern, not a place.
_PATTERN_FIRST = frozenset({"grep", "egrep", "fgrep", "zgrep", "rg", "ag", "ack", "fd", "fdfind"})
_COMMAND_PREFIXES = frozenset({"sudo", "nohup", "time", "env", "exec", "nice", "timeout", "xargs", "command"})
_SHELL_OPERATORS = frozenset({";", "|", "||", "&", "&&", "(", ")", "\n", ";;", "|&"})
# An absolute or home-relative path anywhere in a command, including inside
# quoted code (``python -c "open('/x/y')"``) and ``--flag=/x/y``.
_ABSOLUTE_PATH = re.compile(r"(?<![\w.~-])~?/[^\s'\"`;|&<>(){},]*")
_BRACE = re.compile(r"\{([^{}]*,[^{}]*)\}")
_MAX_TOKENS = 2000
_MAX_GLOB_MATCHES = 200
_TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024


def _key(path: str | os.PathLike[str]) -> str:
    absolute = os.path.abspath(os.path.expanduser(os.fspath(path)))
    if os.name != "nt" and absolute.startswith("//"):
        absolute = "/" + absolute.lstrip("/")  # the shell reads //x as /x
    return os.path.normcase(absolute)


def _braces(token: str, limit: int = 64) -> list[str]:
    """``a/{b,c}/d`` as the shell expands it (comma lists only)."""
    found = [token]
    while True:
        expanded: list[str] = []
        for item in found:
            match = _BRACE.search(item)
            if not match:
                expanded.append(item)
                continue
            expanded += [item[: match.start()] + choice + item[match.end():] for choice in match.group(1).split(",")]
        if expanded == found or len(expanded) > limit:
            return expanded[:limit]
        found = expanded


def _real(path: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return path


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _forms(path: str) -> list[str]:
    lexical = _key(path)
    real = _real(lexical)
    return [lexical] if real == lexical else [lexical, real]


@dataclass(frozen=True)
class WorkspaceBoundary:
    """What one role call may not touch, and what stays its own inside that."""

    own: tuple[str, ...] = ()
    closed: tuple[str, ...] = ()
    # Closed stores keyed by session id; a call keeps its own session's entry.
    session_stores: tuple[str, ...] = ()
    workdir: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "own": list(self.own), "closed": list(self.closed),
            "session_stores": list(self.session_stores), "workdir": self.workdir,
        })

    @classmethod
    def from_json(cls, text: str) -> "WorkspaceBoundary":
        data = json.loads(text)
        return cls(
            own=tuple(str(p) for p in data.get("own") or ()),
            closed=tuple(str(p) for p in data.get("closed") or ()),
            session_stores=tuple(str(p) for p in data.get("session_stores") or ()),
            workdir=str(data.get("workdir") or ""),
        )

    def for_session(self, session_id: str) -> "WorkspaceBoundary":
        name = str(session_id or "").strip()
        if not name or os.sep in name or "/" in name or name in {".", ".."}:
            return self
        own = (*self.own, *(os.path.join(store, name) for store in self.session_stores))
        return WorkspaceBoundary(own, self.closed, self.session_stores, self.workdir)

    @cached_property
    def _roots(self) -> tuple[list[str], list[str]]:
        own = [form for path in self.own for form in _forms(path)]
        closed = [form for path in (*self.closed, *self.session_stores) for form in _forms(path)]
        return own, closed

    def refusal(self, path: str, cwd: str, *, recursive: bool = False) -> str:
        """Why the role may not use ``path``, or ``""`` when it may."""
        raw = str(path or "").strip()
        if raw.startswith("@"):
            raw = raw[1:]
        if not raw or "\x00" in raw:
            return ""
        own, closed = self._roots
        candidate = raw if os.path.isabs(os.path.expanduser(raw)) else os.path.join(cwd or os.getcwd(), raw)
        for form in _forms(candidate):
            deepest_own = max((len(r) for r in own if _inside(form, r)), default=-1)
            deepest_closed = max((len(r) for r in closed if _inside(form, r)), default=-1)
            if deepest_closed > deepest_own:
                return _reason(raw, self.workdir)
            if recursive and any(_inside(r, form) and r != form for r in closed):
                return _reason(raw, self.workdir, searched=True)
        return ""

    def tool_refusal(self, tool_name: str, tool_input: object, cwd: str) -> str:
        """Why this tool call would leave the boundary, or ``""``."""
        name = str(tool_name or "").rsplit("__", 1)[-1].lower()
        if isinstance(tool_input, str):
            return self.command_refusal(tool_input, cwd)
        if not isinstance(tool_input, Mapping):
            return ""
        searched = name in _SEARCH_TOOLS
        searched_paths = 0
        for key, value in _flatten(tool_input):
            if key in _COMMAND_KEYS:
                text = " ".join(map(str, value)) if isinstance(value, list) else str(value)
                why = self.command_refusal(text, cwd)
            elif searched and key in _PATTERN_KEYS and str(value).strip()[:1] in {"/", "~"}:
                searched_paths += 1
                why = self._token_refusal(str(value).strip(), cwd)
            elif key in _PATH_KEYS:
                values = value if isinstance(value, list) else [value]
                why = ""
                for item in values:
                    searched_paths += 1
                    why = self.refusal(str(item), cwd, recursive=searched)
                    if why:
                        break
            else:
                continue
            if why:
                return why
        if searched and not searched_paths:
            return self.refusal(cwd, cwd, recursive=True)
        return ""

    def command_refusal(self, command: str, cwd: str) -> str:
        """Why a shell command names a closed path, or ``""``. Best effort."""
        text = os.path.expandvars(str(command or ""))
        for match in _ABSOLUTE_PATH.finditer(text):
            why = self._token_refusal(match.group(0), cwd)
            if why:
                return why
        try:
            lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)[:_MAX_TOKENS]
        except ValueError:
            tokens = text.split()[:_MAX_TOKENS]
        here = cwd
        for segment in _segments(tokens):
            for token in segment:
                for part in {token, token.partition("=")[2]} - {""}:
                    for variant in _braces(part):
                        why = self._token_refusal(variant, here)
                        if why:
                            return why
            words = _command_words(segment)
            if words and words[0] in {"cd", "pushd"} and len(words) > 1:
                # Later commands in the same line run from there.
                here = os.path.join(here, os.path.expanduser(words[1]))
                continue
            if not _is_recursive(words):
                continue
            arguments = [t for t in words[1:] if not t.startswith("-")]
            # A search given only its pattern searches the current directory.
            if len(arguments) <= (1 if os.path.basename(words[0]) in _PATTERN_FIRST else 0):
                arguments.append(here)
            for root in arguments:
                why = self.refusal(root, here, recursive=True)
                if why:
                    return why
        return ""

    def _token_refusal(self, token: str, cwd: str) -> str:
        why = self.refusal(token, cwd)
        if why or not any(ch in token for ch in "*?["):
            return why
        pattern = os.path.expanduser(token)
        base = pattern if os.path.isabs(pattern) else os.path.join(cwd or os.getcwd(), pattern)
        # The fixed directory before the first wildcard, then every match.
        literal = base[: min(base.find(ch) for ch in "*?[" if ch in base)]
        why = self.refusal(os.path.dirname(literal) or literal, cwd, recursive="**" in base)
        if why:
            return why
        for count, match in enumerate(glob.iglob(base, recursive=True)):
            if count >= _MAX_GLOB_MATCHES:
                break
            why = self.refusal(match, cwd)
            if why:
                return why
        return ""


def _reason(path: str, workdir: str, *, searched: bool = False) -> str:
    what = "searches a directory that holds" if searched else "is in"
    return (
        f"Argus workspace boundary: {path} {what} another project's or tenant's "
        "data, which this role may not read or change. Work in your own workspace"
        + (f" ({workdir})" if workdir else "")
        + " and the directories you were given; if you need something from "
        "elsewhere, say so in your summary."
    )


def _flatten(value: Mapping, depth: int = 0) -> Iterable[tuple[str, object]]:
    for key, item in value.items():
        name = re.sub(r"[^a-z]", "", str(key).lower())
        if isinstance(item, Mapping) and depth < 4:
            yield from _flatten(item, depth + 1)
        elif isinstance(item, list) and item and all(isinstance(x, Mapping) for x in item) and depth < 4:
            for entry in item:
                yield from _flatten(entry, depth + 1)
        elif isinstance(item, (str, list)):
            yield name, item


def _segments(tokens: list[str]) -> Iterable[list[str]]:
    segment: list[str] = []
    for token in tokens:
        if token in _SHELL_OPERATORS or set(token) <= set(";|&()"):
            if segment:
                yield segment
            segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def _command_words(segment: list[str]) -> list[str]:
    """The command and its arguments, without env assignments and wrappers."""
    words = [t for t in segment if "=" not in t.partition("/")[0] or t.startswith("-")]
    while words and os.path.basename(words[0]) in _COMMAND_PREFIXES:
        words = words[1:]
    return words


def _is_recursive(words: list[str]) -> bool:
    if not words:
        return False
    command = os.path.basename(words[0])
    if command in _RECURSIVE_COMMANDS:
        return True
    if command not in _FLAG_RECURSIVE_COMMANDS:
        return False
    for flag in words[1:]:
        if flag in {"--recursive", "--archive", "--dereference-recursive"}:
            return True
        if flag.startswith("-") and not flag.startswith("--") and (
            {"r", "R"} & set(flag[1:]) or (command == "cp" and "a" in flag[1:])
        ):
            return True
    return False


# --- computing a role's boundary (runs in the Argus process) ---------------------


def foreign_roots(env: Mapping[str, str] | None = None) -> list[str]:
    source = os.environ if env is None else env
    return [part for part in str(source.get(FOREIGN_ROOTS_ENV) or "").split(os.pathsep) if part.strip()]


def _not_a_boundary(path: str, keep: list[str]) -> bool:
    """A directory that holds the home, the runtime, Python or the source can
    not be closed without closing the machine; it is left open."""
    return any(_inside(k, path) for k in keep)


def _project_workdirs(states: Path) -> Iterable[tuple[str, str]]:
    """``(state_dir, workdir)`` of every project recorded under ``states``."""
    try:
        entries = list(states.iterdir())
    except OSError:
        return
    for entry in entries:
        for name, field in (("session.json", None), ("campaign-workdir.json", "workdir")):
            try:
                data = json.loads((entry / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            values = [data.get(field)] if field else [data.get("workdir") or data.get("cwd")]
            for value in values:
                if isinstance(value, str) and value.strip():
                    yield str(entry), value.strip()


def role_workspace_boundary(
    *,
    working_dir: str | os.PathLike[str] | None,
    own_dirs: Iterable[str | os.PathLike[str] | None] = (),
    global_root: str | os.PathLike[str] | None = None,
) -> WorkspaceBoundary:
    """The boundary for one Engineer or Reviewer call working in ``working_dir``.

    ``own_dirs`` are the call's own project state and the directories Argus
    hands the role. Other projects are read from the runtime's project records
    at call time; a project created during the call is not included.
    """
    from .paths import global_root as default_global_root
    from .paths import session_states_root, session_trash_root

    root = Path(global_root).expanduser() if global_root else default_global_root()
    workdir = _key(working_dir) if working_dir else ""
    own = list(dict.fromkeys(_key(p) for p in (working_dir, *own_dirs) if p))
    states = session_states_root(root)
    pi_sessions = os.environ.get("ARGUS_SKILL_PI_SESSION_DIR") or str(root / "pi-sessions")
    session_stores = [_key(pi_sessions), _key(root / "copilot-home" / "session-state")]
    keep = [_key(Path.home()), _key(root), _key(sys.prefix), _key(Path(__file__).resolve().parents[2])]
    closed = [_key(states), _key(session_trash_root(root))]
    own_states = {_key(p) for p in own if _inside(_key(p), _key(states))}
    for state_dir, other in _project_workdirs(states):
        other_key = _key(other)
        if any(_inside(o, _key(state_dir)) or _inside(_key(state_dir), o) for o in own_states):
            continue  # this project's own record
        if (
            other_key in own
            or (workdir and _inside(other_key, workdir))
            or _not_a_boundary(other_key, keep)
        ):
            continue
        closed.append(other_key)
    closed += [_key(p) for p in foreign_roots()]
    return WorkspaceBoundary(
        own=tuple(own),
        closed=tuple(dict.fromkeys(c for c in closed if c not in own)),
        session_stores=tuple(session_stores),
        workdir=workdir,
    )


def hook_command(boundary_file: str | os.PathLike[str], fmt: str) -> list[str]:
    """The argv an agent CLI's pre-tool hook runs for this boundary."""
    return [sys.executable, "-I", str(Path(__file__).resolve()), "--boundary", str(boundary_file), "--format", fmt]


# --- the hook ---------------------------------------------------------------------


def _codex_call_arguments(payload: Mapping) -> object:
    """Codex's hook payload drops ``exec_command``'s ``workdir``; the session
    transcript it names holds the call exactly as the model made it."""
    path, call_id = payload.get("transcript_path"), payload.get("tool_use_id")
    if not path or not call_id:
        return None
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - _TRANSCRIPT_TAIL_BYTES))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if call_id not in line:
            continue
        try:
            record = json.loads(line).get("payload") or {}
        except ValueError:
            continue
        if record.get("call_id") == call_id:
            raw = record.get("arguments", record.get("input"))
            try:
                return json.loads(raw) if isinstance(raw, str) and raw.lstrip().startswith("{") else raw
            except ValueError:
                return raw
    return None


def check_payload(boundary: WorkspaceBoundary, payload: Mapping, fmt: str) -> str:
    """Why the tool call in a pre-tool hook ``payload`` is refused, or ``""``."""
    boundary = boundary.for_session(str(payload.get("session_id") or ""))
    cwd = str(payload.get("cwd") or os.getcwd())
    name = str(payload.get("tool_name") or payload.get("toolName") or "")
    tool_input = payload.get("tool_input", payload.get("toolArgs"))
    if isinstance(tool_input, str) and fmt == "copilot":
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            pass
    why = boundary.tool_refusal(name, tool_input, cwd)
    if not why and fmt == "codex":
        original = _codex_call_arguments(payload)
        if original is not None:
            why = boundary.tool_refusal(name, original, cwd)
    return why


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--format", choices=("claude", "codex", "copilot", "pi"), default="claude")
    parser.add_argument("--sha256", default="", help="digest the boundary file must still have")
    args = parser.parse_args(argv)
    try:
        with open(args.boundary, "rb") as handle:
            raw = handle.read()
        if args.sha256 and hashlib.sha256(raw).hexdigest() != args.sha256:
            raise ValueError("the boundary file changed during the call")
        boundary = WorkspaceBoundary.from_json(raw.decode("utf-8"))
        payload = json.loads(sys.stdin.read() or "{}")
        why = check_payload(boundary, payload if isinstance(payload, Mapping) else {}, args.format)
    except Exception as exc:  # noqa: BLE001 - an unreadable boundary refuses, never allows
        print(f"Argus workspace boundary could not be checked: {exc}", file=sys.stderr)
        return 2
    if not why:
        return 0
    if args.format == "copilot":
        # Copilot forwards the reason only from this form; a bare exit 2
        # reaches the model as "hook exited with code 2".
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why,
        }}))
        return 0
    print(why, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
