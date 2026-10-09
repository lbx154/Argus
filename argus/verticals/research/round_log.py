"""What the Engineer actually did this round, as the host saw it.

The Reviewer used to learn about the round from the Engineer's account and
then re-read the tree to check it: in one control project 61 of its 105 file
reads were files the Engineer had just read, and it still never opened the
script that computed a "perplexity" by formula. The host sees every command
and file the Engineer's CLI reports, so the packet can simply say what
happened: how many commands, which ran longest (the time to the next action
bounds a command's runtime), which tests and checks ran with every run's exit
code in order, which other commands failed, and which paths lay outside the
workspace.

What the host records is narrow: what the agent CLI's output stream reported
for each command, including its exit code. That stream is not tamper-proof (a
command can write CLI-shaped lines to it), so a second, different result for a
call is shown as a conflict and a result without a matching start as
unverified. The command text and the output are the Engineer's; a command can
pipe a failure away, select tests, or run a check the Engineer just edited.
The packet therefore shows each check in full and flags those shapes, so a
Reviewer that cannot run commands reads what a check does before weighing its
result.

The calls come from host memory (:mod:`argus.core.command_record`), fed by the
agent CLI's stream rather than by any project file. Without a capture the provider reads only
the exact event log the host writes for the mission, never a file found by
walking up from the mission packet, with the same conflict handling.

Evidence, not a gate: the provider renders text into the Reviewer's
raw-evidence slot and never decides anything. This module only renders text;
:mod:`spec_checks`, the research vertical's round-evidence entry point, wraps
:func:`render_round_log` as a provider, so nothing here knows the engineer layer.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ...core.command_record import CommandCapture, CommandRun, anonymous_call_id

MAX_EVENTS_SCAN = 200_000
LONGEST_COMMANDS = 4
OUTSIDE_PATHS = 4
CHECKS_SHOWN = 6
LAST_COMMANDS = 3
FAILED_SHOWN = 3
OUTCOMES_SHOWN = 6
COMMAND_CHARS = 700
_HEAD_CHARS = 480
_TAIL_CHARS = 200

HEADER = (
    "For each command below the host recorded what the agent CLI's output stream "
    "reported, including its exit code. That record is not tamper-proof (a "
    "command can write to the same stream), and the command text and output "
    "were produced by the Engineer's commands, so read what a check runs before "
    "weighing its result."
)
HEADER_WITHOUT_RESULTS = (
    "The agent CLI reported these commands but no exit codes, so nothing below "
    "says whether a command passed; the command text and any output were "
    "produced by the Engineer's commands."
)

_ABS_PATH = re.compile(r"(?<![\w/])(/(?:data|home|mnt|srv|opt|tmp|var|app|workspace)/[^\s'\"`:;|)>]+)")
_TOOL_PREFIX = re.compile(r"^(read|write|edit|create|ls|find|grep|glob|search_experiences|apply_patch|view): ")
_READ_PREFIXES = ("read:", "view:", "read_file:")
_WRITE_PREFIXES = ("write:", "edit:", "create:", "apply_patch", "str_replace_editor:")
_JSON_PATH = re.compile(r'"(?:path|file_path|filePath|file)"\s*:\s*"([^"]+)"')

# --- What counts as a check: decided by the command a segment runs ----------
_OPERATOR_CHARS = frozenset(";&|(){}")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "nohup", "chronic", "unbuffer"}
#: Wrappers that run the command after them, with the options that take a value.
_WRAPPER_OPTIONS: dict[str, set[str]] = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"},
    "nice": {"-n"},
    "ionice": {"-c", "-n", "-p"},
    "env": {"-u", "-C", "-S"},
    "stdbuf": {"-i", "-o", "-e"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-n", "-I", "-L", "-P", "-d", "-a", "-E", "-s", "-i"},
    "time": {"-f", "-o"},
    "exec": {"-a"},
    "xvfb-run": {"-n", "-s", "-e", "-f", "-p"},
    "taskset": set(),
}
_RUNNERS = {"uv", "poetry", "pipenv", "hatch", "pdm", "rye"}
_SHELLS = {"bash", "sh", "zsh", "dash"}
_TEST_TOOLS = {
    "pytest", "py.test", "tox", "nox", "jest", "vitest", "mocha", "ctest",
    "phpunit", "rspec", "nosetests", "nose2", "behave", "bats",
}
_TEST_MODULES = {"pytest", "unittest", "doctest", "nose2", "tox", "nox"}
_SUBCOMMAND_TOOLS = {"cargo", "go", "mvn", "gradle", "gradlew", "dotnet", "swift", "mix", "zig", "deno", "bazel"}
_PACKAGE_MANAGERS = {"npm", "yarn", "pnpm", "bun"}
_MAKE_TARGETS = {"test", "tests", "check", "checks", "verify", "validate"}
_CHECK_SCRIPT = re.compile(r"(?i)(?:^|[_\-.])(?:tests?|checks?|verify|verification|validate|validation|spec)(?:[_\-.]|$)")
_EVALUATION_SCRIPT = re.compile(
    r"(?i)(?:^|[_\-.])(?:eval\w*|bench\w*|train\w*)(?:[_\-.]|$)|figure_lint|pptx_export|generate_figures"
)


def _first_line(command: str) -> str:
    """A heredoc's body is input, not commands: keep the line that starts it."""
    return command.split("\n", 1)[0] if "<<" in command else command


def _unwrap(tokens: list[str]) -> list[str]:
    """Drop what runs a command rather than being it: env, wrappers, keywords."""
    tokens = list(tokens)
    while tokens:
        token = tokens[0]
        name = Path(token).name
        if _ENV_ASSIGNMENT.match(token) or token in _KEYWORDS:
            tokens.pop(0)
            continue
        if name == "command":
            if tokens[1:2] and tokens[1] in {"-v", "-V"}:
                return []  # a lookup, not a run
            tokens.pop(0)
            continue
        if name in _WRAPPER_OPTIONS:
            takes_value = _WRAPPER_OPTIONS[name]
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                option = tokens.pop(0)
                if option in takes_value and tokens:
                    tokens.pop(0)
            if name in {"timeout", "taskset"} and tokens:
                tokens.pop(0)  # the duration or CPU mask
            continue
        if name in _RUNNERS and tokens[1:2] == ["run"]:
            tokens = tokens[2:]
            continue
        break
    return tokens


def command_segments(command: str, _depth: int = 0) -> list[list[str]]:
    """The simple commands in a command line, each unwrapped, `sh -c` bodies opened."""
    text = _first_line(command or "")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|(){}")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = text.split()
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and set(token) <= _OPERATOR_CHARS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    found: list[list[str]] = []
    for segment in segments:
        segment = _unwrap(segment)
        if not segment:
            continue
        if Path(segment[0]).name in _SHELLS and "-c" in segment and _depth < 3:
            body = segment.index("-c") + 1
            if body < len(segment):
                found.extend(command_segments(segment[body], _depth + 1))
                continue
        found.append(segment)
    return found


def _script_name_matches(path: str, pattern: re.Pattern[str]) -> bool:
    name = Path(path.strip("'\"")).name
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return bool(stem) and bool(pattern.search(stem))


def _script_is_check(path: str) -> bool:
    name = Path(path.strip("'\"")).name
    return _script_name_matches(path, _CHECK_SCRIPT) or name.startswith(("test", "check"))


def _program(tokens: list[str]) -> tuple[str, str]:
    """``(kind, name)`` of what a simple command runs: a module, a script or a tool."""
    head = Path(tokens[0]).name
    rest = tokens[1:]
    if head.startswith("python") or head in {"pypy", "pypy3"}:
        args = [token for token in rest if token not in {"-B", "-u", "-O", "-I", "-E", "-s", "-q"}]
        if args[:1] == ["-m"] and len(args) > 1:
            return "module", args[1]
        if args and args[0].endswith(".py"):
            return "script", args[0]
        return "inline", ""
    if head in _SHELLS:
        script = next((token for token in rest if not token.startswith("-")), "")
        return ("script", script) if script else ("inline", "")
    if "/" in tokens[0]:
        return "script", tokens[0]
    return "tool", head


def _segment_is_check(tokens: list[str]) -> bool:
    head = Path(tokens[0]).name
    rest = tokens[1:]
    kind, name = _program(tokens)
    if kind == "module":
        return name.split(".")[0] in _TEST_MODULES
    if kind == "script":
        return _script_is_check(name)
    if kind == "inline":
        return False
    if head in _TEST_TOOLS:
        return True
    if head in _PACKAGE_MANAGERS:
        if rest[:1] in (["test"], ["t"]):
            return True
        return len(rest) > 1 and rest[0] == "run" and _script_is_check(rest[1])
    if head in _SUBCOMMAND_TOOLS:
        return bool(rest) and rest[0] in {"test", "check", "verify"}
    if head == "make":
        return any(token in _MAKE_TARGETS for token in rest if not token.startswith("-"))
    if head == "node":
        return "--test" in rest
    return False


def is_check(command: str) -> bool:
    """Whether a command runs a test runner or the project's own check script."""
    return any(_segment_is_check(segment) for segment in command_segments(command))


def is_evaluation(command: str) -> bool:
    """Whether a command runs an evaluation, benchmark or training script."""
    for segment in command_segments(command):
        kind, name = _program(segment)
        if kind == "module":
            name = name.rsplit(".", 1)[-1]
        if kind in {"module", "script"} and _script_name_matches(name, _EVALUATION_SCRIPT):
            return True
    return False


# --- Shapes that change what an exit code means -----------------------------
_PIPE = re.compile(r"(?<!\|)\|(?![|&])")
_OR_TRUE = re.compile(r"\|\|\s*(?:true\b|:(?![\w:])|exit\s+0\b)")
_OR_ANY = re.compile(r"\|\|")
_CHAIN = re.compile(r";")
_SELECTION = re.compile(
    r"(?:^|[\s'\"=])(?:-k|--deselect|--ignore(?:-glob)?|--lf|--last-failed|--sw|--stepwise|"
    r"--maxfail|--testNamePattern|--filter|--grep)(?:[\s=]|$)"
    r"|\bpy(?:\.)?test\b.*\s-m\s"
    r"|\S::\w"
)
_ADDOPTS = re.compile(r"\b(?:PYTEST_ADDOPTS|PYTEST_PLUGINS)=")
_REDIRECT = re.compile(r"(?<![<>&\d])(?:\d|&)?>{1,2}\s*(?!&)([^\s;|&]+)")
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_SHELL_C = re.compile(r"\b(?:bash|sh|zsh|dash)\s+(?:-[A-Za-z]+\s+)*?-c\s+(?:'([^']*)'|\"((?:[^\"\\]|\\.)*)\")")


def _open_shell_bodies(command: str) -> str:
    """Put a `bash -c '…'` body back into the command line it runs in."""
    for _ in range(3):
        opened = _SHELL_C.sub(
            lambda m: "( " + (m.group(1) if m.group(1) is not None else re.sub(r"\\(.)", r"\1", m.group(2))) + " )",
            command,
        )
        if opened == command:
            break
        command = opened
    return command


def _shape(command: str) -> str:
    """The command's shell shape: `sh -c` bodies opened, quoted text and heredoc bodies removed."""
    return _QUOTED.sub("''", _first_line(_open_shell_bodies(command)))


def command_flags(command: str, edited: Iterable[str] = ()) -> list[str]:
    """What about a command's shape a Reviewer should weigh with its exit code."""
    opened = _open_shell_bodies(_first_line(command))
    shape = _shape(command)
    flags: list[str] = []
    if _OR_TRUE.search(shape):
        flags.append("`|| true` hides a failure")
    elif _OR_ANY.search(shape):
        flags.append("`||` runs a fallback after a failure")
    if _PIPE.search(shape):
        flags.append("piped: the exit code is the last stage's")
    if _CHAIN.search(shape):
        flags.append("`;` chain: the exit code is the last command's")
    check = is_check(command)
    if check and (_SELECTION.search(opened) or _ADDOPTS.search(opened)):
        flags.append("selects or deselects tests")
    if _REDIRECT.search(shape):
        flags.append("redirects output")
    names = {Path(path).name for path in edited}
    touched = sorted(name for name in names if len(name) >= 3 and name in command)
    if touched:
        flags.append("uses a file edited this round: " + ", ".join(touched[:3]))
    elif check and any(_script_is_check(name) or "/test" in path or path.startswith("test") for path in edited for name in [Path(path).name]):
        flags.append("tests or checks were edited this round")
    return flags


def _edited_paths(runs: Iterable[CommandRun]) -> dict[str, list[str]]:
    """Paths each call wrote this round, by call id."""
    edited: dict[str, list[str]] = {}
    for run in runs:
        paths: list[str] = []
        if run.kind == "tool" and run.text.startswith(_WRITE_PREFIXES):
            paths.extend(_JSON_PATH.findall(run.text))
        elif run.kind == "command":
            shape = _shape(run.text)
            for tokens in command_segments(run.text):
                if Path(tokens[0]).name == "sed" and any(token.startswith("-i") for token in tokens):
                    paths.append(tokens[-1])
                if Path(tokens[0]).name == "tee" and len(tokens) > 1:
                    paths.append(tokens[-1])
            paths.extend(target.strip("'\"") for target in _REDIRECT.findall(shape) if target != "/dev/null")
        if paths:
            edited[run.call_id] = [path for path in paths if path]
    return edited


def _edited_by_others(edited: dict[str, list[str]], run: CommandRun) -> list[str]:
    return [path for call_id, paths in edited.items() if call_id != run.call_id for path in paths]


# --- Rendering ---------------------------------------------------------------
def _flat(text: str) -> str:
    return " ".join(text.split())


def show_command(text: str) -> str:
    """The whole command, or its head and tail when it is long."""
    flat = _flat(text)
    if len(flat) <= COMMAND_CHARS:
        return flat
    return flat[:_HEAD_CHARS].rstrip() + " … " + flat[-_TAIL_CHARS:].lstrip()


def _one_line(text: str, limit: int = 110) -> str:
    flat = _flat(text)
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60.0:.1f} min"
    return f"{seconds / 3600.0:.1f} h"


def _result_word(exit_code: int | None) -> str:
    return "failed (no exit code)" if exit_code is None else f"exit {exit_code}"


def _outcome(run: CommandRun) -> str:
    if not run.finished:
        return "no result recorded"
    if run.conflicting:
        return " / ".join(_result_word(result.exit_code) for result in run.results) + " (conflicting results recorded)"
    return _result_word(run.exit_code)


def _outcomes(runs: list[CommandRun]) -> str:
    words = [_outcome(run) for run in runs]
    if len(words) > OUTCOMES_SHOWN:
        words = [words[0], f"… {len(words) - OUTCOMES_SHOWN + 1} more …", *words[-(OUTCOMES_SHOWN - 2):]]
    return ", then ".join(words)


def _is_failure(run: CommandRun) -> bool:
    return run.finished and (run.failed or any(r.exit_code not in (None, 0) for r in run.results))


def _command_lines(runs: list[CommandRun], edited: dict[str, list[str]], indent: str = "  ") -> list[str]:
    latest = runs[-1]
    times = f" ×{len(runs)}" if len(runs) > 1 else ""
    flags = command_flags(latest.text, _edited_by_others(edited, latest))
    if any(run.conflicting for run in runs):
        flags.insert(0, "conflicting results recorded for one call")
    unverified = next((run.unverified for run in reversed(runs) if run.unverified), "")
    if unverified:
        flags.insert(0, f"unverified: {unverified}")
    note = f" (watch: {'; '.join(flags)})" if flags else ""
    lines = [f"{indent}- `{show_command(latest.text)}`{times}: {_outcomes(runs)}{note}"]
    if latest.conflicting:
        for number, result in enumerate(latest.results, 1):
            if result.output:
                lines.append(
                    f"{indent}  output reported with result {number} "
                    f"({_result_word(result.exit_code)}): {result.output}"
                )
    elif latest.output:
        lines.append(f"{indent}  output of the latest run: {latest.output}")
    if not _is_failure(latest):
        failing = next((run for run in reversed(runs[:-1]) if _is_failure(run) and run.output), None)
        if failing is not None:
            lines.append(f"{indent}  output of the last failing run: {failing.output}")
    return lines


def _grouped(commands: list[CommandRun], keep) -> dict[str, list[CommandRun]]:
    """Runs of the same full command together, the most recently run group last."""
    groups: dict[str, list[CommandRun]] = {}
    for run in commands:
        if keep(run.text):
            key = _flat(run.text)
            groups.setdefault(key, []).append(run)
            groups[key] = groups.pop(key)
    return groups


def summarize_runs(runs: list[CommandRun], workdir: Path) -> list[str]:
    """Lines for the Reviewer: counts, longest commands, checks, last commands, failures, outside paths."""
    if not runs:
        return []
    runs = sorted(runs, key=lambda run: run.started_at)
    commands = [run for run in runs if run.kind == "command" and not _TOOL_PREFIX.match(run.text)]
    tools = [run for run in runs if run.kind == "tool"]
    reads = [run for run in tools if run.text.startswith(_READ_PREFIXES)]
    writes = [run for run in tools if run.text.startswith(_WRITE_PREFIXES)]
    span = (runs[-1].started_at - runs[0].started_at) / 60.0
    lines = [f"{len(commands)} shell commands, {len(reads)} file reads, {len(writes)} writes over {span:.1f} min."]
    gaps = {
        id(run): runs[index + 1].started_at - run.started_at
        for index, run in enumerate(runs[:-1])
    }
    timed = [run for run in commands if id(run) in gaps]
    for run in sorted(timed, key=lambda run: -gaps[id(run)])[:LONGEST_COMMANDS]:
        lines.append(f"- ran ≤{_duration(gaps[id(run)])} (time to the next action): `{_one_line(run.text)}`")
    edited = _edited_paths(runs)
    groups = _grouped(commands, is_check)
    if groups:
        lines.append("- tests and checks, with every run's result in order:")
        for group in list(groups.values())[-CHECKS_SHOWN:]:
            lines.extend(_command_lines(group, edited, indent="  "))
    listed = {id(run) for group in groups.values() for run in group}
    evaluations = _grouped([run for run in commands if id(run) not in listed], is_evaluation)
    if evaluations:
        lines.append("- evaluations, benchmarks and training runs invoked, with every run's result in order:")
        for group in list(evaluations.values())[-CHECKS_SHOWN:]:
            lines.extend(_command_lines(group, edited, indent="  "))
        listed.update(id(run) for group in evaluations.values() for run in group)
    last = [run for run in commands[-LAST_COMMANDS:] if id(run) not in listed]
    if last:
        lines.append("- the round's last commands:")
        for run in last:
            lines.extend(_command_lines([run], edited, indent="  "))
        listed.update(id(run) for run in last)
    failed = [run for run in commands if id(run) not in listed and _is_failure(run)]
    if failed:
        shown = ", ".join(f"`{_one_line(run.text, 90)}` ({_outcome(run)})" for run in failed[-FAILED_SHOWN:])
        lines.append(f"- other commands that failed: {shown}")
    outside = _outside_paths(runs, workdir)
    if outside:
        lines.append(
            "- paths outside the workspace touched: "
            + "; ".join(f"{path} ({count})" for path, count in outside[:OUTSIDE_PATHS])
        )
    return lines


def _outside_paths(runs: list[CommandRun], workdir: Path) -> list[tuple[str, int]]:
    root = os.path.realpath(str(workdir))
    counts: dict[str, int] = {}
    for run in runs:
        for match in _ABS_PATH.finditer(run.text):
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
    return sorted(counts.items(), key=lambda item: -item[1])


# --- Fallback: the host's own event log, read at its exact path --------------
def _iter_events(path: Path) -> Iterator[dict[str, Any]]:
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
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event


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


def runs_from_events(events: list[dict[str, Any]], since: float) -> list[CommandRun]:
    """The Engineer's calls since ``since`` from ``engineer.progress`` rows.

    The first result recorded for a call is final; a later row for the same
    call id cannot change it.
    """
    record = CommandCapture(label="events")
    rows = sorted(
        (
            e for e in events
            if e.get("type") == "engineer.progress"
            and str(e.get("agent_layer") or "engineer") == "engineer"
            and e.get("kind") in ("command_execution", "tool_use", "tool_result")
            and float(e.get("ts") or 0) >= since
        ),
        key=lambda e: float(e.get("ts") or 0),
    )
    last_command: dict[str, Any] = {}
    for e in rows:
        kind = e.get("kind")
        ts = float(e.get("ts") or 0)
        call_id = str(e.get("call_id") or "")
        status = str(e.get("status") or "").lower()
        text = str(e.get("text") or "").strip()
        exit_code = e.get("exit_code")
        exit_code = exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None
        output = str(e.get("output_excerpt") or "")
        finished = status not in ("", "running")
        if kind == "tool_result":
            # A result whose start never appeared is kept and marked unverified.
            if call_id:
                record.finish(call_id, exit_code=exit_code, failed=status == "failed", output=output, ts=ts)
            continue
        if kind == "tool_use":
            record.start(call_id or anonymous_call_id(), "tool", text, tool=str(e.get("tool_name") or ""), ts=ts)
            continue
        if finished and call_id:
            record.finish(call_id, exit_code=exit_code, failed=status == "failed", output=output,
                          text=text, ts=ts)
            continue
        if finished and not call_id and last_command.get("text") == text and not last_command.get("done"):
            # Older logs name a failed command again without its call id.
            record.finish(last_command["id"], exit_code=exit_code, failed=status == "failed", output=output)
            last_command["done"] = True
            continue
        run_id = call_id or anonymous_call_id()
        record.start(run_id, "command", text, tool=str(e.get("tool_name") or ""), ts=ts)
        last_command = {"id": run_id, "text": text, "done": False}
        if finished:
            record.finish(run_id, exit_code=exit_code, failed=status == "failed", output=output)
            last_command["done"] = True
    return record.runs()


def render_round_log(
    workdir: Path,
    round_index: int,
    *,
    runs: Iterable[CommandRun] = (),
    events_path: Path | None = None,
    dropped: int = 0,
) -> str:
    """The packet text, or '' when there is nothing to show.

    ``runs`` (host memory) wins; otherwise only ``events_path`` is read, the
    exact log the host writes for this mission. ``dropped`` counts earlier
    calls the record no longer holds.
    """
    runs = list(runs)
    if not runs and events_path is not None and Path(events_path).is_file():
        events = list(_iter_events(Path(events_path)))
        since = round_window_start(events, round_index)
        if since is None:
            return ""
        runs = runs_from_events(events, since)
    if not runs:
        return ""
    lines = summarize_runs(runs, Path(workdir))
    if not lines:
        return ""
    if dropped:
        lines.insert(1, f"- {dropped} earlier commands not shown; the record keeps the newest.")
    started = time.strftime("%H:%M", time.localtime(min(run.started_at for run in runs)))
    if any(run.finished for run in runs):
        header = f"Engineer's commands this round (host record since {started}). {HEADER}"
    else:
        header = f"Engineer's commands this round (since {started}). {HEADER_WITHOUT_RESULTS}"
    return "\n".join([header, *lines])


__all__ = [
    "HEADER",
    "HEADER_WITHOUT_RESULTS",
    "command_flags",
    "command_segments",
    "is_check",
    "is_evaluation",
    "render_round_log",
    "round_window_start",
    "runs_from_events",
    "show_command",
    "summarize_runs",
]
