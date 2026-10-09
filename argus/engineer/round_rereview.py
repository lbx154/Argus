"""Round >= 2 Reviewer context: its own findings, the change set, the deliverable.

Trial logs showed later reviews re-running a large share of the checks that
the same thread had already run (122 of 290 round >= 2 tool calls repeated an
earlier call verbatim). Each extra turn of a resumed review re-reads the whole
thread. A Reviewer can only safely reuse an earlier result when it knows which
inputs changed since then, so this module hands it exactly that:

* its own previous findings — compact when the provider thread that already
  holds them resumes, a full carry-over when the review starts fresh;
* the change set since that review — workspace files modified since it
  finished, and the commands the Engineer's execution log recorded this round;
* the full deliverable root, with explicit freedom to widen scope.

Everything here is host-observed fact or the Reviewer's own earlier words.
Nothing decides a verdict: the block frames the default focus and leaves the
judgment, its scope, and its checks to the Reviewer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..core.model_visible_text import sanitize_model_visible_text
from ..core.models import RoundRecord
from ..core.secret_guard import known_secret_values, redact_secrets_text

log = logging.getLogger(__name__)

_FINDINGS_REASON_LIMIT = 1600
_FINDINGS_ACTION_LIMIT = 1000
_COMPACT_ACTION_LIMIT = 500
_EVIDENCE_ITEMS = 4
_EVIDENCE_ITEM_LIMIT = 300
_EARLIER_VERDICTS = 2
_EARLIER_VERDICT_LIMIT = 240
_CHANGED_PATHS_SHOWN = 25
_COMMANDS_SHOWN = 12
_COMMAND_LIMIT = 200
# File timestamps come from a coarse kernel clock and can trail a wall-clock
# reading taken just before the write by a few milliseconds.
_MTIME_SLACK_SECONDS = 1.0
# Long campaigns grow the per-project event log to many megabytes; the current
# round's commands are always near its end.
_LOG_TAIL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class RereviewContext:
    """Two renderings of one round >= 2 block.

    ``full`` goes to a Reviewer that starts a fresh provider thread;
    ``resumed`` to one continuing the thread that already holds its findings.
    """

    full: str
    resumed: str


def _one_line(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clean(text: str) -> str:
    return sanitize_model_visible_text(
        redact_secrets_text(text, known_values=known_secret_values())
    )


def _reviewer_rounds(rounds: Sequence[RoundRecord]) -> list[RoundRecord]:
    """Rounds the independent Reviewer actually judged.

    Engineer self-reviews, provider waits, and backend failures are not this
    Reviewer's findings and never stand in for them.
    """
    return [
        record for record in rounds
        if not record.review.backend_unavailable
        and str(record.review.review_source or "reviewer") == "reviewer"
    ]


def _frontier_items(review_frontier: object, key: str) -> list[str]:
    if not isinstance(review_frontier, dict):
        return []
    items = review_frontier.get(key)
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return []
    return [
        _one_line(item, _EVIDENCE_ITEM_LIMIT)
        for item in items[:_EVIDENCE_ITEMS]
        if str(item or "").strip()
    ]


def engineer_commands_since(
    engineer_log_path: str | Path | None,
    since_ts: float | None,
    *,
    limit: int = _COMMANDS_SHOWN,
) -> list[str]:
    """Commands the Engineer's execution log recorded at or after ``since_ts``.

    The log is the host's record of what ran, not a claim that it passed.
    Missing or unreadable logs simply yield nothing.
    """
    if not engineer_log_path or since_ts is None:
        return []
    path = Path(engineer_log_path).expanduser()
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _LOG_TAIL_BYTES:
                handle.seek(size - _LOG_TAIL_BYTES)
                handle.readline()  # drop the partial first line
            raw = handle.read()
    except OSError:
        return []
    commands: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if '"command_execution"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "command_execution":
            continue
        layer = str(event.get("agent_layer") or event.get("actor") or "")
        if not layer.startswith("engineer"):
            continue
        try:
            ts = float(event.get("ts") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts < since_ts:
            continue
        text = str(event.get("text") or "").strip()
        if not text:
            continue
        first = text.splitlines()[0]
        more = " …" if "\n" in text else ""
        command = _one_line(first, _COMMAND_LIMIT) + more
        if commands and commands[-1] == command:
            continue
        commands.append(command)
    return commands[-limit:] if limit > 0 else commands


def _findings_lines(record: RoundRecord, *, compact: bool) -> list[str]:
    review = record.review
    status = _one_line(review.status, 40) or "unknown"
    reason = str(review.reason or "").strip()
    action = str(review.next_action or "").strip()
    lines = [f"Your previous findings (round {record.round_index}, `{status}`):"]
    if compact:
        lines.append(
            "- Your full reasoning and the results of the checks you ran are "
            "earlier in this thread."
        )
        requested = action or reason
        if requested:
            lines.append(
                "- What you asked for: " + _one_line(requested, _COMPACT_ACTION_LIMIT)
            )
        return lines
    if reason:
        lines.append(_one_line(reason, _FINDINGS_REASON_LIMIT))
    if action and action != reason and not reason.startswith(action[:200]):
        lines.append("What you asked for: " + _one_line(action, _FINDINGS_ACTION_LIMIT))
    evidence = _frontier_items(review.frontier_report, "evidence")
    if evidence:
        lines.append("Evidence you cited:")
        lines.extend(f"- {item}" for item in evidence)
    remaining = _frontier_items(review.frontier_report, "remaining_work")
    if remaining:
        lines.append("Open items you named:")
        lines.extend(f"- {item}" for item in remaining)
    return lines


def _earlier_lines(rounds: Iterable[RoundRecord]) -> list[str]:
    earlier = list(rounds)[-_EARLIER_VERDICTS:]
    if not earlier:
        return []
    lines = ["Your earlier judgments:"]
    for record in earlier:
        status = _one_line(record.review.status, 40) or "unknown"
        reason = _one_line(record.review.reason, _EARLIER_VERDICT_LIMIT) or "(no reason)"
        lines.append(f"- Round {record.round_index} — {status}: {reason}")
    return lines


def _change_set_lines(changed_paths: Sequence[str], commands: Sequence[str]) -> list[str]:
    lines = ["Change set since that review (host-observed):"]
    if changed_paths:
        shown = ", ".join(changed_paths[:_CHANGED_PATHS_SHOWN])
        if len(changed_paths) > _CHANGED_PATHS_SHOWN:
            shown += f", +{len(changed_paths) - _CHANGED_PATHS_SHOWN} more"
        lines.append(f"- Workspace files modified, newest first: {shown}")
    else:
        lines.append(
            "- No workspace file was modified since then (outputs written "
            "outside the workspace would not show here)."
        )
    if commands:
        lines.append(
            "- Commands the Engineer ran this round, from its execution log "
            "(a record of what ran, not proof that it passed):"
        )
        lines.extend(f"  - `{command}`" for command in commands)
    lines.append(
        "- The Engineer's account of this round and any raw evidence follow below."
    )
    return lines


def _guidance_lines(workdir: Path) -> list[str]:
    return [
        f"Full deliverable: `{workdir}` — all of it remains in scope.",
        "Start from your open findings and this change set; that is the default "
        "focus, not a boundary. Verify each change with your own checks rather "
        "than the Engineer's report of them. Reuse a result you obtained earlier "
        "only when the change set leaves its inputs untouched; rerun it when it "
        "does. Widen the review beyond the change set whenever a change touches "
        "code, data, or configuration other parts depend on, the change set looks "
        "incomplete against the Engineer's account, or you suspect a regression "
        "elsewhere. Approve only on evidence you checked yourself.",
    ]


def build_rereview_context(
    *,
    rounds: Sequence[RoundRecord],
    round_index: int,
    workdir: Path,
    changes_since_ts: float | None,
    engineer_log_path: str | Path | None,
    commands_since_ts: float | None,
) -> RereviewContext | None:
    """Build the round >= 2 Reviewer block, or ``None`` for a first review.

    A round with no earlier judgment by this Reviewer (round 1, or only
    self-reviews so far) gets ``None`` and the unchanged first-review prompt.
    """
    if round_index <= 1:
        return None
    reviewed = _reviewer_rounds(rounds)
    if not reviewed:
        return None
    latest = reviewed[-1]
    try:
        from ..roles.prompts.reviewer import changed_workspace_paths

        changed = changed_workspace_paths(
            workdir,
            None if changes_since_ts is None
            else changes_since_ts - _MTIME_SLACK_SECONDS,
        )
    except Exception:  # noqa: BLE001 - the change set is advisory context
        log.debug("re-review change scan failed", exc_info=True)
        changed = []
    commands = engineer_commands_since(engineer_log_path, commands_since_ts)
    header = (
        f"## Re-review (round {round_index}): your previous findings and what "
        "changed since"
    )
    change_set = _change_set_lines(changed, commands)
    guidance = _guidance_lines(workdir)
    earlier = _earlier_lines(reviewed[:-1])

    def render(*, compact: bool) -> str:
        parts = [header, *_findings_lines(latest, compact=compact)]
        if earlier and not compact:
            parts.extend(earlier)
        parts.extend(change_set)
        parts.extend(guidance)
        return _clean("\n".join(parts))

    return RereviewContext(full=render(compact=False), resumed=render(compact=True))


def own_reviewer_thread(
    resume_thread_id: str | None,
    *,
    foreign_thread_ids: Iterable[str | None],
) -> str | None:
    """Return the Reviewer's resume id only when it is not another role's thread.

    The Reviewer's independence rests on never continuing the Engineer's
    conversation. Capsules are already per-role, so this guards against a
    corrupted or mis-seeded capsule rather than an expected path.
    """
    if not resume_thread_id:
        return None
    foreign = {str(item) for item in foreign_thread_ids if item}
    return None if str(resume_thread_id) in foreign else resume_thread_id


__all__ = [
    "RereviewContext",
    "build_rereview_context",
    "engineer_commands_since",
    "own_reviewer_thread",
]
