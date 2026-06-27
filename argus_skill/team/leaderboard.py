"""Deterministic leaderboard fold for an agent team.

The Curator folds teammate result shards into a per-target leaderboard. This is
the HIGH-frequency, **deterministic** half of the curator design — pure code, no
LLM, no agent ritual (the LLM only runs the low-frequency ``distill`` step). It
exists to stop the "every mechanism scored once, all stuck at the same low
score" failure: ``objective_block`` shows a fresh teammate what has already been
tried so it builds depth instead of re-deriving breadth.

Generality red line: the metric and its direction are the only operator-specific
inputs, and both are DATA — the metric arrives in the shard (see
``teammate_entry``), the direction is an env flag. Nothing SOL/box-specific lives
here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import _store


def _env_lower_is_better() -> bool:
    return os.environ.get("ARGUS_LEADERBOARD_LOWER_IS_BETTER", "").strip().lower() \
        in ("1", "true", "yes", "on")


def _path(root: Path) -> Path:
    return Path(root) / "leaderboard.json"


def _read_shards(root: Path) -> list[dict[str, Any]]:
    d = Path(root) / "shards"
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.jsonl")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a corrupt line, keep the rest
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _better(a: float, b: float, lower_is_better: bool) -> bool:
    return a < b if lower_is_better else a > b


def fold(root: Path, *, lower_is_better: bool | None = None) -> dict[str, Any]:
    """Fold all teammate shards into ``leaderboard.json`` and return it.

    Per target: ``{"best": {mechanism, metric} | None, "attempts": [{mechanism,
    metric}]}``. Mechanisms are deduped keeping the best metric; an attempt with a
    null metric is recorded (so it counts as "tried") but never wins ``best``.
    Single-writer atomic write — teammates never touch this file.
    """
    global_dir = _env_lower_is_better() if lower_is_better is None else lower_is_better
    by_target: dict[str, list[dict[str, Any]]] = {}
    for rec in _read_shards(root):
        target = rec.get("target") or rec.get("task_id")
        if target:
            by_target.setdefault(str(target), []).append(rec)

    board: dict[str, Any] = {}
    for target, recs in by_target.items():
        # Per-target direction: a shard's explicit ``lower_is_better`` (the operator
        # sets it on the task at ``form`` time) wins; otherwise the global default.
        # So one campaign can mix higher-better (a speedup) and lower-better (a
        # latency / error-count / loss) targets without silently inverting.
        tdir = global_dir
        for r in recs:
            v = r.get("lower_is_better")
            if v is not None:
                tdir = bool(v)
                break
        per_mech: dict[str, float | None] = {}
        for r in recs:
            mech = str(r.get("mechanism") or "")
            metric = r.get("metric")
            if metric is None:
                per_mech.setdefault(mech, None)  # tried, but unmeasured
                continue
            metric = float(metric)
            cur = per_mech.get(mech)
            if cur is None or _better(metric, cur, tdir):
                per_mech[mech] = metric
        attempts = [{"mechanism": m, "metric": v} for m, v in sorted(per_mech.items())]
        measured = [(m, v) for m, v in per_mech.items() if v is not None]
        best = None
        if measured:
            chooser = min if tdir else max
            bm, bv = chooser(measured, key=lambda kv: kv[1])
            best = {"mechanism": bm, "metric": bv}
        board[target] = {"best": best, "attempts": attempts}

    _store.atomic_write_json(_path(root), board)
    return board


def read(root: Path) -> dict[str, Any]:
    """The last folded leaderboard (``{}`` if none yet)."""
    doc = _store.read_json(_path(root), default={})
    return doc if isinstance(doc, dict) else {}


def objective_block(root: Path, target: str) -> str:
    """A 'what's already been tried — do NOT re-derive' block for ``target``,
    prepended to a fresh teammate's objective so it builds DEPTH on the best or
    tries a genuinely new mechanism instead of re-running exhausted breadth.
    Empty string when the target has no recorded attempts yet."""
    entry = read(root).get(str(target))
    if not entry:
        return ""
    attempts = entry.get("attempts") or []
    if not attempts:
        return ""
    lines = ["## LEADERBOARD — already tried on this target (do NOT re-derive)"]
    best = entry.get("best")
    if best:
        lines.append(f"Current BEST: `{best.get('mechanism') or '(unnamed)'}` = {best.get('metric')}")
    lines.append("Mechanisms already attempted — build DEPTH on the best, or try a "
                 "genuinely NEW one; do NOT repeat these:")
    for a in attempts:
        m = a.get("metric")
        lines.append(f"- {a.get('mechanism') or '(unnamed)'}: "
                     f"{'unmeasured' if m is None else m}")
    return "\n".join(lines) + "\n\n"
