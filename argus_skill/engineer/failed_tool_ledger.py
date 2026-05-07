"""Mission-scoped registry of failed tool / command beats.

The codex stream-json protocol surfaces each command_execution and
file_change beat with a ``status`` field (``completed``/``failed``) and,
for command_execution, an ``exit_code``. Without aggregating these
across rounds, the agent has no shared "I have already tried this and
it failed" memory: we observed the same ``apply_patch``
sandbox-mismatch error firing in 4 consecutive rounds with no
investigation of the root cause.

This module provides a small per-mission ledger that:

* records every failed beat (tool name, last error text), keyed by a
  caller-defined tool identity so caller can choose granularity
  (e.g. group all ``apply_patch`` failures into one bucket regardless
  of target file);
* exposes ``repeated_failures`` so the engineer prompt can interrupt
  the agent's blind-retry loop and force a root-cause investigation.

Kept dependency-free so it can be imported anywhere — the integration
points (stream_progress callback adapter and engineer/runner prompt
builder) live in their own modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


_DEFAULT_NUDGE_THRESHOLD = 2
_DEFAULT_MAX_ERR_LEN = 600


@dataclass
class FailureRecord:
    """One recorded failure beat."""

    tool: str
    error: str  # truncated to _DEFAULT_MAX_ERR_LEN
    detail: str = ""  # optional context (e.g. command line, file path)


@dataclass
class FailedToolLedger:
    """Per-mission tally of failed tool/command beats."""

    nudge_threshold: int = _DEFAULT_NUDGE_THRESHOLD
    _records: dict[str, list[FailureRecord]] = field(default_factory=dict)
    # Tools the agent has been nudged about already this mission. Used so
    # we don't keep re-injecting the same advisory on every subsequent
    # round once the agent has been told once and presumably acted on it.
    _nudged: set[str] = field(default_factory=set)

    def record(self, tool: str, error: str, *, detail: str = "") -> None:
        """Record a failure. ``tool`` is the bucket key (e.g. ``apply_patch``,
        ``shell:git``, ``shell:pytest``). ``error`` is the truncated
        diagnostic text the agent should see. ``detail`` is optional
        context the prompt builder may include alongside the error.
        """
        if not tool:
            return
        err = (error or "").strip()
        if len(err) > _DEFAULT_MAX_ERR_LEN:
            err = err[: _DEFAULT_MAX_ERR_LEN - 1].rstrip() + "…"
        bucket = self._records.setdefault(tool, [])
        bucket.append(FailureRecord(tool=tool, error=err, detail=detail or ""))

    def count(self, tool: str) -> int:
        return len(self._records.get(tool, ()))

    def repeated_failures(self) -> dict[str, list[FailureRecord]]:
        """Tools whose failure count has reached :attr:`nudge_threshold`."""
        return {
            tool: list(records)
            for tool, records in self._records.items()
            if len(records) >= self.nudge_threshold
        }

    def pending_nudges(self) -> dict[str, list[FailureRecord]]:
        """Tools that have hit the threshold and have NOT yet been
        surfaced to the agent. After the engineer prompt builder calls
        :meth:`mark_nudged` for a tool, it stops being returned here.

        Mission-scoped: there is no "expire" — once a tool is nudged
        once per mission it stays marked, and a fresh mission gets a
        fresh ledger. This avoids the prompt growing unboundedly while
        still acting as a hard interrupt on the *first* repeated
        failure of each tool kind.
        """
        return {
            tool: records
            for tool, records in self.repeated_failures().items()
            if tool not in self._nudged
        }

    def mark_nudged(self, tools: Iterable[str]) -> None:
        for tool in tools:
            if tool:
                self._nudged.add(tool)

    def clear(self) -> None:
        self._records.clear()
        self._nudged.clear()

    # -- prompt-builder helper -------------------------------------------

    def render_advisory(self) -> str:
        """Return a markdown block to splice into the engineer prompt,
        or '' when there's nothing to nudge about. Side-effect: marks
        each surfaced tool as nudged.
        """
        pending = self.pending_nudges()
        if not pending:
            return ""
        parts: list[str] = [
            "## ⚠ Repeated tool failures — investigate the root cause before retrying",
            "",
            "The following tools/commands have failed multiple times in this "
            "mission. Do NOT keep blindly retrying. Either:",
            "  (a) explicitly investigate WHY (read the error, check "
            "permissions/path/cwd, run a probe), or",
            "  (b) switch to an alternative approach.",
            "",
        ]
        for tool, records in pending.items():
            n = len(records)
            last = records[-1]
            parts.append(f"- **{tool}** failed {n}× this mission.")
            if last.detail:
                parts.append(f"    last attempt: `{last.detail}`")
            parts.append(f"    last error:")
            parts.append("    ```")
            for line in last.error.splitlines()[:8] or [""]:
                parts.append(f"    {line}")
            parts.append("    ```")
        parts.append("")
        parts.append(
            "Continuing to call the same tool with the same arguments and "
            "expecting a different result is a bug. Spend ONE round on "
            "diagnosis (e.g. `ls -la`, `stat`, `id`, `pwd`, `which`, env "
            "checks) before resuming the original work."
        )
        self.mark_nudged(pending.keys())
        return "\n".join(parts)
