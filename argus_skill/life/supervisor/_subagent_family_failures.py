"""Cross-run subagent failure-streak detector for the L4 planner.

Observed pathology (2 real projects, 2026-07-04..07-06): a continuous-mode
daemon's L4 planner kept re-queuing "fix the SWE-bench full-canary run" as a
*freshly worded* task every cycle (title/objective rephrased each time by the
LLM planner), for ~20 consecutive attempts over 2 days, each one relaunching
``swebench_api_patch_pilot.py`` via the subagent tool (up to 1200 hosted model
calls per attempt) before failing on the same class of error. The existing
duplicate-task / recent-no-progress-failure dedup in
:mod:`argus_skill.life.supervisor._core` never caught this because:

1. It matches on an *exact* (NFKC + casefold) title/objective signature, and
   the planner never phrased the retry the same way twice.
2. It only inspects ``mission_failed`` journal entries with
   ``terminal_status == "no_progress"`` — but the supervisor's own missions
   were graded ``success=True`` each round (the engineer legitimately
   resubmitted the job, polled it, and updated docs; that IS real work at the
   mission level). The actual repeated failure lived one layer down, in the
   subagent registry (``.argus_subagents/*.json``), which the planner's dedup
   never looks at.

This module closes that gap by reading the subagent registry directly and
surfacing "this experiment family has failed N times in a row without a
successful completion" as a first-class, structured fact — independent of
whatever prose the planner used to describe it, and independent of whether the
supervisor's own mission bookkeeping considered the round "successful".

Kept dependency-free (stdlib only), mirroring
:mod:`argus_skill.engineer.background_subagents`, whose in-flight (non-
terminal) classification this module complements rather than duplicates: that
module answers "is this running job already watched, so the engineer should
not babysit it"; this module answers "has this family of attempts, across
MULTIPLE runs, been failing without a break, so the planner should not just
resubmit it again".
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

# Mirrors argus_skill.tools.subagent._core.REGISTRY_DIR; duplicated as a bare
# name so this module stays import-light (see background_subagents.py, same
# convention).
_REGISTRY_DIRNAME = ".argus_subagents"

# A "clean" terminal completion: the wrapped command exited 0. Breaks a
# family's failure streak (the most recent attempt of this class DID work).
_SUCCESS_STATE = "done"

# Terminal states that count toward a failure streak. ``early_stopped`` is
# included: it means the subagent's OWN supervisor judged the run
# degrading/stuck/diverging and intervened — that attempt did not succeed
# either, even though it is a "graceful" stop rather than a crash.
_FAILURE_STATES = frozenset({"error", "timeout", "early_stopped"})

# All states this module considers "concluded" (i.e. eligible for the streak
# walk at all). Anything else (running/starting/preflight/discussing) is a
# still-in-flight record for that family and is excluded from the streak
# computation entirely — it has not concluded yet.
_TERMINAL_STATES = _FAILURE_STATES | {_SUCCESS_STATE}

# Task ids are minted as ``<family-slug>-<UTC-compact-timestamp>`` (e.g.
# ``swebench-verified-full-canary-20260706T123839Z``); strip that trailing
# timestamp to recover the family. A task id with no such suffix is its own
# one-member family (still eligible for a streak if literally resubmitted
# under the identical id, which is harmless/rare).
_FAMILY_SUFFIX_RE = re.compile(r"-\d{8}T\d{6}Z$")


def family_from_task_id(task_id: str) -> str:
    """Strip the trailing ``-YYYYMMDDTHHMMSSZ`` launch timestamp from a
    subagent ``task_id`` to recover its experiment "family" slug."""
    text = str(task_id or "").strip()
    return _FAMILY_SUFFIX_RE.sub("", text) or text


@dataclass(frozen=True)
class SubagentFamilyFailure:
    """A subagent task family with an unresolved streak of terminal failures."""

    family: str
    streak: int
    last_task_id: str
    last_state: str
    last_reason: str
    last_started_at: float


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _failure_reason(record: dict) -> str:
    """Best-effort one-line reason a terminal record failed (never raises)."""
    for key in ("stop_reason", "supervisor_concern", "error", "last_supervisor_concern"):
        value = record.get(key)
        if value:
            return " ".join(str(value).split())[:300]
    return ""


def recent_subagent_family_failures(
    workdir: Path | str,
    *,
    now: float | None = None,
    window_seconds: float = 72 * 3600.0,
    min_streak: int = 3,
) -> dict[str, SubagentFamilyFailure]:
    """Scan ``<workdir>/.argus_subagents/*.json`` for experiment families that
    have failed ``min_streak`` or more times in a row, most-recent-first,
    within the last ``window_seconds``, with no intervening ``done`` success.

    Returns ``{family: SubagentFamilyFailure}`` for families at/over the
    threshold. Empty dict when the registry is absent, unreadable, or no
    family qualifies. Never raises — this feeds a planning-time circuit
    breaker and a bad/missing registry must never block planning.
    """
    now = time.time() if now is None else now
    registry_dir = Path(workdir) / _REGISTRY_DIRNAME
    try:
        paths = sorted(registry_dir.glob("*.json"))
    except OSError:
        return {}
    if not paths:
        return {}

    by_family: dict[str, list[tuple[float, dict]]] = {}
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        state = str(record.get("state") or "").strip().lower()
        if state not in _TERMINAL_STATES:
            continue
        task_id = str(record.get("task_id") or path.stem)
        started_at = record.get("started_at")
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = now
        ts = _coerce_float(started_at, mtime)
        if (now - ts) > window_seconds:
            continue
        family = family_from_task_id(task_id)
        by_family.setdefault(family, []).append((ts, record))

    results: dict[str, SubagentFamilyFailure] = {}
    for family, entries in by_family.items():
        entries.sort(key=lambda pair: pair[0], reverse=True)
        streak = 0
        last_task_id = ""
        last_state = ""
        last_reason = ""
        last_started_at = 0.0
        for ts, record in entries:
            state = str(record.get("state") or "").strip().lower()
            if state == _SUCCESS_STATE:
                break
            if state not in _FAILURE_STATES:
                continue
            if streak == 0:
                last_task_id = str(record.get("task_id") or "")
                last_state = state
                last_reason = _failure_reason(record)
                last_started_at = ts
            streak += 1
        if streak >= max(1, min_streak):
            results[family] = SubagentFamilyFailure(
                family=family,
                streak=streak,
                last_task_id=last_task_id,
                last_state=last_state,
                last_reason=last_reason,
                last_started_at=last_started_at,
            )
    return results
