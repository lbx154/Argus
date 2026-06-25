"""Missing-tool detector for the self-evolve loop (Signal A · trajectory).

Scans a finished mission's event stream / output text for patterns that
indicate the agent **tried to do something but lacked the tool** —
the structural signal that argus should mint a new skill for next time.

Pure pattern detector. **No quality judgment.** Per edit-principle skill 04, this
module identifies factual occurrences; the agent (via the mint-skill
prompt) decides whether to actually mint, and the held-out validator
decides whether the resulting skill works.

Hooks: ``argus_skill/life/supervisor.py`` calls :func:`scan_mission`
after ``_run_one`` returns; for each :class:`MissingToolSignal` it
gets back, it dedups against in-flight ``mint-skill: <name>``
backlog items and enqueues a new ``BacklogItem`` when needed.

Patterns recognised:

* Shell ``command not found`` (exit_code 127 or output text)
* Python ``ModuleNotFoundError`` / ``ImportError: No module named``
* ``pip: command not found`` / similar package-manager missing tool
* Generic missing-file / missing-binary at command-resolution time
* Engineer self-report: "I don't have a tool" / "no skill exists"
  / "I would need a function for X" / "我没有这个工具"

Out of scope (these are different signals, not "missing tool"):

* Wrong-arguments failures (the tool exists, the call was malformed)
* Timeouts / network errors (transient, not a capability gap)
* Logic errors in the script the agent wrote (that's L1 polish, not
  L2 mint)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class MissingToolSignal:
    """A factual observation that some tool was missing in a trajectory.

    ``tool_name`` is the candidate handle the mint-skill mission will
    use as a slug; e.g. ``"pdf-extract"`` or ``"pdfplumber"``. The
    detector picks the most specific name available (Python module
    name beats binary name beats generic phrase).
    """

    tool_name: str
    kind: str  # one of: "shell_command", "python_module", "self_report"
    context: str  # short human-readable description for the agent
    evidence: tuple[str, ...] = field(default_factory=tuple)  # raw lines

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "kind": self.kind,
            "context": self.context,
            "evidence": list(self.evidence),
        }


# Patterns. We err on the side of false positives — the mint-skill
# mission can grep existing skills first and silently no-op if the tool
# already exists. Missing a real signal is more costly than minting a
# duplicate that gets immediately deduped.

_COMMAND_NOT_FOUND_RE = re.compile(
    r"(?:^|[:\s])(\S+):?\s*command not found",
    flags=re.IGNORECASE | re.MULTILINE,
)
_BASH_NO_SUCH_FILE_BINARY_RE = re.compile(
    r"/bin/(?:ba)?sh:\s*line\s*\d+:\s*(\S+):\s*No such file or directory",
)
_PYTHON_MODULE_NOT_FOUND_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s*No module named\s*['\"]([^'\"]+)['\"]",
)
_PYTHON_IMPORT_NAME_RE = re.compile(
    r"ImportError:\s*cannot import name\s*['\"]([^'\"]+)['\"]",
)
_SELF_REPORT_PATTERNS = (
    re.compile(r"I (?:do not|don'?t) have (?:a|the) (?:tool|skill)\s*(?:to|for)\s+([^.\n]+)", re.IGNORECASE),
    re.compile(r"no (?:tool|skill) (?:exists?|available) (?:to|for|that)\s+([^.\n]+)", re.IGNORECASE),
    re.compile(r"I would need (?:a|the) (?:tool|function|skill)\s+(?:to|for)\s+([^.\n]+)", re.IGNORECASE),
    re.compile(r"我没有(?:对应的)?(?:工具|技能).{0,30}", re.IGNORECASE),
)

# Tool-name slug normalisation.
_SLUG_CLEANUP_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_LEADING_DASH_RE = re.compile(r"^-+|-+$")


def _slugify(name: str) -> str:
    s = _SLUG_CLEANUP_RE.sub("-", name.strip()).lower()
    s = _LEADING_DASH_RE.sub("", s)
    return s[:60] or "unknown"


# ---------------------------------------------------------------------------
# Per-event extractors
# ---------------------------------------------------------------------------


def _scan_text_for_command_not_found(
    text: str, *, evidence_label: str
) -> list[MissingToolSignal]:
    out: list[MissingToolSignal] = []
    seen: set[str] = set()
    for m in _COMMAND_NOT_FOUND_RE.finditer(text):
        cmd = m.group(1).strip()
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        out.append(
            MissingToolSignal(
                tool_name=_slugify(cmd),
                kind="shell_command",
                context=f"shell command {cmd!r} not found",
                evidence=(evidence_label, m.group(0).strip()),
            )
        )
    for m in _BASH_NO_SUCH_FILE_BINARY_RE.finditer(text):
        cmd = m.group(1).strip()
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        out.append(
            MissingToolSignal(
                tool_name=_slugify(cmd),
                kind="shell_command",
                context=f"shell binary {cmd!r} not on PATH",
                evidence=(evidence_label, m.group(0).strip()),
            )
        )
    return out


def _scan_text_for_python_imports(
    text: str, *, evidence_label: str
) -> list[MissingToolSignal]:
    out: list[MissingToolSignal] = []
    seen: set[str] = set()
    for m in _PYTHON_MODULE_NOT_FOUND_RE.finditer(text):
        mod = m.group(1).strip().split(".", 1)[0]  # top-level package
        if not mod or mod in seen:
            continue
        seen.add(mod)
        out.append(
            MissingToolSignal(
                tool_name=_slugify(mod),
                kind="python_module",
                context=f"python package {mod!r} not installed",
                evidence=(evidence_label, m.group(0).strip()),
            )
        )
    for m in _PYTHON_IMPORT_NAME_RE.finditer(text):
        name = m.group(1).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            MissingToolSignal(
                tool_name=_slugify(name),
                kind="python_module",
                context=f"python name {name!r} not importable from referenced module",
                evidence=(evidence_label, m.group(0).strip()),
            )
        )
    return out


def _scan_text_for_self_report(
    text: str, *, evidence_label: str
) -> list[MissingToolSignal]:
    out: list[MissingToolSignal] = []
    seen: set[str] = set()
    for pat in _SELF_REPORT_PATTERNS:
        for m in pat.finditer(text):
            target = m.group(1).strip() if m.groups() else m.group(0).strip()
            target = target[:80]
            if not target or target in seen:
                continue
            seen.add(target)
            out.append(
                MissingToolSignal(
                    tool_name=_slugify(target),
                    kind="self_report",
                    context=f"agent self-report: {target!r}",
                    evidence=(evidence_label, m.group(0).strip()),
                )
            )
    return out


def scan_text(text: str, *, evidence_label: str = "text") -> list[MissingToolSignal]:
    """Run all detectors on a single text blob. Returns deduped signals.

    ``text`` can be the concatenation of a mission's agent_messages,
    fatal_error, check output_tails, or events.jsonl line excerpts.
    """
    signals: list[MissingToolSignal] = []
    signals.extend(_scan_text_for_command_not_found(text, evidence_label=evidence_label))
    signals.extend(_scan_text_for_python_imports(text, evidence_label=evidence_label))
    signals.extend(_scan_text_for_self_report(text, evidence_label=evidence_label))
    # Cross-detector dedup (same slug from different patterns).
    seen: set[str] = set()
    deduped: list[MissingToolSignal] = []
    for s in signals:
        if s.tool_name in seen:
            continue
        seen.add(s.tool_name)
        deduped.append(s)
    return deduped


# ---------------------------------------------------------------------------
# Mission-level entry points
# ---------------------------------------------------------------------------


def scan_mission(
    *,
    agent_messages: Iterable[str] = (),
    check_output_tails: Iterable[str] = (),
    fatal_error: str | None = None,
    events: Iterable[dict[str, Any]] = (),
) -> list[MissingToolSignal]:
    """Scan a finished mission for missing-tool signals.

    Caller (supervisor) collects whatever it has handy:

    * ``agent_messages`` — engineer's stdout / message stream
    * ``check_output_tails`` — CheckResult.output_tail entries from
      stage_check / shell checks
    * ``fatal_error`` — the mission's terminal error if any
    * ``events`` — raw events.jsonl rows for this mission window
      (used to pick up command_execution events with exit_code 127)

    Returns deduped signals across all sources. The detector is pure;
    no I/O of its own. Supervisor decides what to do with each signal.
    """
    bag: list[MissingToolSignal] = []
    for i, msg in enumerate(agent_messages):
        if msg:
            bag.extend(scan_text(msg, evidence_label=f"agent_messages[{i}]"))
    for i, tail in enumerate(check_output_tails):
        if tail:
            bag.extend(scan_text(tail, evidence_label=f"check_output_tails[{i}]"))
    if fatal_error:
        bag.extend(scan_text(fatal_error, evidence_label="fatal_error"))
    for i, event in enumerate(events):
        excerpt = event.get("output_excerpt") or ""
        exit_code = event.get("exit_code")
        text = event.get("text") or ""
        # exit_code 127 is bash's "command not found"; the excerpt
        # usually carries the actual error too, but we surface a
        # synthetic signal from just the command + exit code as well.
        if exit_code == 127 and text:
            bag.append(
                MissingToolSignal(
                    tool_name=_slugify(text.split()[0]) if text.split() else "unknown",
                    kind="shell_command",
                    context=f"command exited 127 (not found): {text[:120]!r}",
                    evidence=(f"events[{i}].exit_code=127", text[:200]),
                )
            )
        if excerpt:
            bag.extend(scan_text(excerpt, evidence_label=f"events[{i}].output_excerpt"))

    seen: set[str] = set()
    deduped: list[MissingToolSignal] = []
    for s in bag:
        if s.tool_name in seen:
            continue
        seen.add(s.tool_name)
        deduped.append(s)
    return deduped


def scan_events_jsonl(path: Path) -> list[MissingToolSignal]:
    """Convenience: scan a complete events.jsonl file. Used by the CLI
    and for offline analysis of past missions."""
    events: list[dict] = []
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return scan_mission(events=events)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--events",
        type=Path,
        required=True,
        help="path to events.jsonl",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    signals = scan_events_jsonl(args.events)
    if args.json:
        print(json.dumps([s.to_dict() for s in signals], indent=2))
    else:
        if not signals:
            print(f"missing_tool_detector: no signals in {args.events}")
            return 0
        print(f"missing_tool_detector: {len(signals)} signal(s) in {args.events}")
        for s in signals:
            print(f"  [{s.kind}] {s.tool_name}: {s.context}")
            for ev in s.evidence:
                print(f"      ← {ev[:120]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
