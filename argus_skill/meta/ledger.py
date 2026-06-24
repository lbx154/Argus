"""Persistent meta ledger — the never-cleared forbidden-directions record.

Two files under ``<project_root>/research/``:

  * ``META_LEDGER.json`` — the durable state: a ``forbidden`` list (agent-DECLARED
    regime directions that are dead; **never cleared** so a successor cannot
    silently re-anchor on an abandoned regime — the STAR-PólyaMath posture) and
    a ``coverage`` map (per-axis attempt counts, last updated). This is bounded
    and structured — the antidote to leaning on the 1.5 MB freeform
    GROUND_TRUTH for "where has the search been".
  * ``META_LEDGER.jsonl`` — append-only decision log, one row per planner
    convening: ``{step_id, mode, was_jump, strategy_type, performance,
    diversity_score, ts}`` (the spec's required logging contract).

Critically, the harness NEVER invents a forbidden direction. It only persists
and re-injects the directions the AGENT itself declared dead. Everything is
fail-soft: a missing/corrupt ledger reads as empty, and a failed write is
swallowed (the meta layer is best-effort visibility+enforcement, never a
blocker on the round loop).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LEDGER_JSON_RELPATH = "research/META_LEDGER.json"
LEDGER_JSONL_RELPATH = "research/META_LEDGER.jsonl"

#: Caps so the never-cleared record stays bounded (a forbidden list is small by
#: nature — there are only a handful of regime axes — but a buggy author must not
#: be able to grow it without limit).
MAX_FORBIDDEN = 24
MAX_FORBIDDEN_CHARS = 200


@dataclass
class MetaLedger:
    """In-memory view of ``META_LEDGER.json`` (fail-soft)."""

    forbidden: list[str] = field(default_factory=list)
    coverage: dict[str, int] = field(default_factory=dict)
    jump_pending: bool = False
    updated_at: float = 0.0

    def forbidden_axes(self) -> set[str]:
        """Lowercased forbidden tokens for membership checks."""
        return {f.strip().lower() for f in self.forbidden if f and f.strip()}


def _json_path(project_root: object) -> Path:
    return Path(str(project_root)) / LEDGER_JSON_RELPATH


def _jsonl_path(project_root: object) -> Path:
    return Path(str(project_root)) / LEDGER_JSONL_RELPATH


def load_ledger(project_root: object) -> MetaLedger:
    """Read ``META_LEDGER.json`` → ``MetaLedger`` (empty on any failure)."""
    try:
        p = _json_path(project_root)
        if not p.exists():
            return MetaLedger()
        obj = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return MetaLedger()
        forbidden = [
            str(x).strip()[:MAX_FORBIDDEN_CHARS]
            for x in (obj.get("forbidden") or [])
            if str(x).strip()
        ][:MAX_FORBIDDEN]
        cov = obj.get("coverage") or {}
        coverage = {
            str(k): int(v)
            for k, v in cov.items()
            if isinstance(v, (int, float))
        } if isinstance(cov, dict) else {}
        try:
            updated_at = float(obj.get("updated_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0
        return MetaLedger(
            forbidden=forbidden,
            coverage=coverage,
            jump_pending=bool(obj.get("jump_pending", False)),
            updated_at=updated_at,
        )
    except Exception:  # noqa: BLE001 — fail-soft: corrupt ledger reads as empty
        return MetaLedger()


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        k = it.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it.strip()[:MAX_FORBIDDEN_CHARS])
    return out


def _write_ledger(project_root: object, ledger: MetaLedger) -> None:
    """Persist the full ledger (best-effort)."""
    try:
        p = _json_path(project_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "forbidden": ledger.forbidden,
                    "coverage": ledger.coverage,
                    "jump_pending": ledger.jump_pending,
                    "updated_at": ledger.updated_at,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — best-effort persistence
        pass


def merge_forbidden(
    project_root: object,
    new_forbidden: list[str],
    *,
    coverage: dict[str, int] | None = None,
    now: float | None = None,
) -> MetaLedger:
    """Merge AGENT-declared forbidden directions into the never-cleared record.

    Forbidden entries are only ever ADDED (never removed): a regime the agent
    ruled dead stays dead for successors. ``coverage`` (if given) is REPLACED
    with the latest computed map. ``jump_pending`` is preserved. Fail-soft.
    """
    ledger = load_ledger(project_root)
    merged = _dedupe_preserve(list(ledger.forbidden) + list(new_forbidden or []))[
        :MAX_FORBIDDEN
    ]
    ledger.forbidden = merged
    if coverage is not None:
        ledger.coverage = {str(k): int(v) for k, v in coverage.items()}
    ledger.updated_at = now if now is not None else time.time()
    _write_ledger(project_root, ledger)
    return ledger


def set_jump_pending(project_root: object, value: bool, *, now: float | None = None) -> None:
    """Set the consume-once jump-reset flag (preserving forbidden/coverage)."""
    ledger = load_ledger(project_root)
    ledger.jump_pending = bool(value)
    ledger.updated_at = now if now is not None else time.time()
    _write_ledger(project_root, ledger)


def consume_jump_pending(project_root: object) -> bool:
    """Return the jump-reset flag and clear it (consume-once). Fail-soft False."""
    try:
        ledger = load_ledger(project_root)
        if not ledger.jump_pending:
            return False
        ledger.jump_pending = False
        ledger.updated_at = time.time()
        _write_ledger(project_root, ledger)
        return True
    except Exception:  # noqa: BLE001
        return False


def append_decision(project_root: object, row: dict[str, Any]) -> None:
    """Append one decision row to ``META_LEDGER.jsonl`` (best-effort)."""
    try:
        p = _jsonl_path(project_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — logging must never break the loop
        pass


def next_step_id(project_root: object) -> int:
    """Best-effort monotonically increasing step id from the jsonl length."""
    try:
        p = _jsonl_path(project_root)
        if not p.exists():
            return 0
        with p.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except Exception:  # noqa: BLE001
        return 0
