"""``argus-skill --skill-stats`` — empirical effectiveness report.

Reads ``<life_dir>/events.jsonl`` and rebuilds, for each mission:

  * skill_name (matched, distilled, or none)
  * skill_hit (matched a pre-existing skill)
  * skill_distilled (had to spend tokens authoring a new one)
  * matcher_tokens spent on the match step
  * rounds taken to reach reviewer-done

The report aggregates by skill name and by hit/miss/distill bucket so
operators can answer empirical questions like:

  * Are skills shortening convergence at all?
  * Which skills are worth keeping vs. pruning?
  * What's the matcher token bill per mission?

We use ``events.jsonl`` instead of ``journal.jsonl`` because journal
entries are coarse (one row per mission) and miss the matcher-token
breakdown emitted by the ``skill.outcome`` event in ``loop.py``.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read_events(life_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fname in ("events.jsonl.1", "events.jsonl"):  # roll first, then current
        path = life_dir / fname
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return rows


def collect_skill_outcomes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull all ``skill.outcome`` rows. One per completed mission."""
    return [e for e in events if e.get("type") == "skill.outcome"]


def aggregate(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a structured stats report keyed by skill name and bucket."""
    total = len(outcomes)
    hits = [o for o in outcomes if o.get("skill_hit")]
    distills = [o for o in outcomes if o.get("skill_distilled")]
    cold = [o for o in outcomes if not o.get("skill_hit") and not o.get("skill_distilled")]
    successes = [o for o in outcomes if o.get("success")]

    def _mean(xs: list[int] | list[float]) -> float:
        return float(statistics.mean(xs)) if xs else 0.0

    def _bucket_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        rounds = [int(r.get("rounds", 0) or 0) for r in rows]
        toks = [int(r.get("matcher_tokens", 0) or 0) for r in rows]
        wins = [r for r in rows if r.get("success")]
        return {
            "missions": len(rows),
            "successes": len(wins),
            "success_rate": (len(wins) / len(rows)) if rows else 0.0,
            "mean_rounds": _mean(rounds),
            "mean_matcher_tokens": _mean(toks),
        }

    by_skill: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "missions": 0,
        "hits": 0,
        "distills": 0,
        "successes": 0,
        "rounds": [],
    })
    for o in outcomes:
        name = (o.get("skill_name") or "").strip() or "(none)"
        b = by_skill[name]
        b["missions"] += 1
        if o.get("skill_hit"):
            b["hits"] += 1
        if o.get("skill_distilled"):
            b["distills"] += 1
        if o.get("success"):
            b["successes"] += 1
        b["rounds"].append(int(o.get("rounds", 0) or 0))

    by_skill_clean: dict[str, dict[str, Any]] = {}
    for name, b in by_skill.items():
        rounds = b["rounds"]
        by_skill_clean[name] = {
            "missions": b["missions"],
            "hits": b["hits"],
            "distills": b["distills"],
            "successes": b["successes"],
            "mean_rounds": _mean(rounds),
        }

    return {
        "totals": {
            "missions": total,
            "hits": len(hits),
            "distills": len(distills),
            "cold": len(cold),
            "successes": len(successes),
            "hit_rate": (len(hits) / total) if total else 0.0,
            "distill_rate": (len(distills) / total) if total else 0.0,
        },
        "by_bucket": {
            "hit": _bucket_stats(hits),
            "distilled": _bucket_stats(distills),
            "cold": _bucket_stats(cold),
        },
        "by_skill": by_skill_clean,
    }


def render_text(report: dict[str, Any]) -> str:
    out: list[str] = []
    t = report["totals"]
    out.append("argus-skill — skill effectiveness report")
    out.append("-" * 50)
    out.append(
        f"  missions   : {t['missions']}  "
        f"(hit={t['hits']} distill={t['distills']} cold={t['cold']})"
    )
    out.append(
        f"  hit rate   : {t['hit_rate'] * 100:.1f}%   "
        f"distill rate: {t['distill_rate'] * 100:.1f}%"
    )
    out.append(
        f"  successes  : {t['successes']} / {t['missions']}"
    )
    out.append("")
    out.append("Per-bucket:")
    for b_name in ("hit", "distilled", "cold"):
        b = report["by_bucket"][b_name]
        out.append(
            f"  {b_name:9s} missions={b['missions']:3d}  "
            f"success={b['success_rate'] * 100:5.1f}%  "
            f"mean_rounds={b['mean_rounds']:.2f}  "
            f"mean_matcher_tokens={b['mean_matcher_tokens']:.0f}"
        )
    out.append("")
    out.append("Per-skill:")
    rows = sorted(
        report["by_skill"].items(),
        key=lambda kv: (-kv[1]["missions"], kv[0]),
    )
    if not rows:
        out.append("  (no missions recorded yet)")
    for name, s in rows:
        out.append(
            f"  {name[:50]:50s}  missions={s['missions']:3d}  "
            f"hits={s['hits']:3d}  distills={s['distills']:3d}  "
            f"mean_rounds={s['mean_rounds']:.2f}"
        )
    return "\n".join(out)


def run_skill_stats(life_dir: Path, *, as_json: bool = False) -> int:
    life_dir = Path(life_dir)
    events = _read_events(life_dir)
    outcomes = collect_skill_outcomes(events)
    report = aggregate(outcomes)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


__all__ = [
    "run_skill_stats",
    "collect_skill_outcomes",
    "aggregate",
    "render_text",
]
