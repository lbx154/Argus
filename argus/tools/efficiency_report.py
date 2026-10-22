"""Where one project's model calls went, read from its event log (roadmap HO-1).

Every efficiency change has to show its effect here. The report is read-only:
it reads ``events.jsonl`` (every retained generation, oldest first) and the
call ledger ``usage.jsonl``, and writes nothing; the round handoff files carry
no fact the round events lack.

    python -m argus.tools.efficiency_report <project-state-dir> [--json]

A call is a row of the ledger, placed among the events where it completed; its
role comes from its run label. "No new
information" counts the calls the log itself shows as repeats: a Planner turn
that added no new work, and a Manager supervision whose decision was never
applied or kept the course. ``avoided`` counts the times the Planner began to
wait without a call because nothing it reads had changed (``model_call_skipped``).
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core.event_catalog import canonical_event_type
from ..core.json_codec import loads_finite_json
from ..core.jsonl_reader import retained_jsonl_paths
from ..core.metrics import cost_per_passed_task

ROLES = ("planner", "reviewer", "engineer", "manager")
TOKENS = ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens")
TURN_BUCKETS = ((1, "1"), (3, "2-3"), (7, "4-7"), (15, "8-15"), (math.inf, "16+"))


def role_of(run_label: Any) -> str:
    label = str(run_label or "").lower()
    return next((role for role in ROLES if role in label), re.split(r"[.:]", label)[0] or "unlabeled")


def task_of(usage: dict[str, Any]) -> str:
    """The task a call was charged to; its usage names the attempt (``<task>:attempt:<n>``)."""
    return str(usage.get("mission_id") or "").partition(":attempt:")[0]


def _rows(log: Path) -> Iterator[dict[str, Any]]:
    for path in retained_jsonl_paths(log):
        with path.open("rb") as handle:
            for line in handle:
                try:
                    row = loads_finite_json(line)
                except ValueError:
                    continue  # a torn line is not a call
                if isinstance(row, dict):
                    yield row


def _timeline(life_dir: Path) -> Iterator[tuple[float, str | None, dict[str, Any]]]:
    """``(when, event type, row)`` in order: the event log, and each ledger call
    (type ``None``) where it completed."""
    events = (
        (float(row.get("ts") or 0), canonical_event_type(row.get("type")), row)
        for row in _rows(life_dir / "events.jsonl")
    )
    calls = (
        (float(row.get("completed_at") or 0), None, row)
        for row in _rows(life_dir / "usage.jsonl")
    )
    return heapq.merge(events, calls, key=lambda entry: entry[0])


def _tally(rows: Iterable[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    rows = list(rows)
    return {
        "calls": len(rows),
        **{key: sum(int(row.get(key) or 0) for row in rows) for key in TOKENS},
        "cost_usd": round(sum(float(row.get("cost_usd") or 0) for row in rows), 4),
        **extra,
    }


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else 0


def _context(rows: list[dict[str, Any]], turns: dict[str, list[int]]) -> dict[str, Any]:
    sizes = [int(row.get("input_tokens") or 0) for row in rows]
    return {
        "p50": _percentile(sizes, 0.5),
        "p90": _percentile(sizes, 0.9),
        "max": max(sizes, default=0),
        "mean_by_session_turn": {
            name: round(sum(values) / len(values)) for _, name in TURN_BUCKETS if (values := turns.get(name))
        },
    }


def efficiency_report(life_dir: Path | str) -> dict[str, Any]:
    calls: dict[str, dict[str, Any]] = {}  # call_id -> usage row
    planner_cycle: list[str] = []  # this Planner cycle's calls, until its outcome
    planner_idle: list[str] = []
    planner_skipped = 0
    supervisions: dict[str, tuple[str, str]] = {}  # supervision_id -> (call_id, action)
    applied: set[str] = set()
    tasks: defaultdict[str, Counter[str]] = defaultdict(Counter)
    passed: dict[str, bool] = {}
    elapsed: defaultdict[str, float] = defaultdict(float)
    session_turns: defaultdict[str, defaultdict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for _, kind, event in _timeline(Path(life_dir)):
        task = str(event.get("item_id") or "")
        if kind is None:
            call = str(event.get("call_id") or f"#{len(calls)}")
            calls[call] = event
            if role_of(event.get("run_label")) == "planner":
                planner_cycle.append(call)
        elif kind in ("life.planner.start", "life.planner.verdict"):
            planner_cycle = []
        elif kind == "life.planner.idle":
            if event.get("model_call_skipped"):
                planner_skipped += 1
            planner_idle += planner_cycle
            planner_cycle = []
        elif kind == "life.manager.supervision.issued":
            supervisions[str(event.get("supervision_id"))] = (
                str(event.get("call_id") or ""), str(event.get("action") or ""),
            )
        elif kind == "life.manager.supervision.applied":
            applied.add(str(event.get("supervision_id")))
        elif kind == "round.review.completed":
            if event.get("status") == "continue":
                tasks[task]["continue"] += 1
        elif kind == "life.mission.completed":
            elapsed[task] += float(event.get("elapsed_seconds") or 0)
            passed[task] = event.get("success") is True
        elif kind == "role.session.turn":
            turn = int(event.get("turns_on_session") or 0)
            bucket = next(name for bound, name in TURN_BUCKETS if turn <= bound)
            session_turns[str(event.get("role"))][bucket].append(int(event.get("input_tokens") or 0))

    by_role: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    by_task: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in calls.values():
        by_role[role_of(row.get("run_label"))].append(row)
        by_task[task_of(row)].append(row)
    total = _tally(calls.values())
    roles = sorted(((role, _tally(rows)) for role, rows in by_role.items()), key=lambda pair: -pair[1]["cost_usd"])

    def picked(ids: Iterable[str]) -> list[dict[str, Any]]:
        return [calls[call] for call in ids if call in calls]

    return {
        "project": str(life_dir),
        "total": total,
        "roles": {
            role: {**tally, "cost_share": round(tally["cost_usd"] / total["cost_usd"], 4) if total["cost_usd"] else 0.0}
            for role, tally in roles
        },
        "no_new_information": {
            "planner_no_new_work": _tally(
                picked(planner_idle), of_calls=len(by_role["planner"]), avoided=planner_skipped,
            ),
            "manager_not_applied": _tally(
                picked(call for sid, (call, _) in supervisions.items() if sid not in applied),
                of_calls=len(supervisions), avoided=0,
            ),
            "manager_kept_course": _tally(
                picked(call for sid, (call, action) in supervisions.items() if sid in applied and action == "continue"),
                of_calls=len(supervisions), avoided=0,
            ),
        },
        "cost_per_passed_task": cost_per_passed_task(
            total["cost_usd"], sum(passed.values()),
        ),
        "passed_tasks": [
            {
                "task": task,
                "reviewer_calls": sum(role_of(row.get("run_label")) == "reviewer" for row in by_task[task]),
                "continue": tasks[task]["continue"],
                "elapsed_s": round(elapsed[task], 1),
                **_tally(by_task[task]),
            }
            for task, success in passed.items() if success
        ],
        "context": {role: _context(by_role[role], session_turns[role]) for role, _ in roles},
        "failures": {
            "failed": _tally(row for row in calls.values() if row.get("status") != "completed"),
            "retries": _tally(row for row in calls.values() if re.search(r"repair|retry", str(row.get("run_label") or ""))),
        },
    }


def _cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _table(title: str, header: list[str], rows: list[list[Any]]) -> list[str]:
    cells = [header, *([_cell(value) for value in row] for row in rows)]
    widths = [max(len(row[column]) for row in cells) for column in range(len(header))]
    return [title, *(
        "  ".join(cell.rjust(width) if column else cell.ljust(width)
                  for column, (cell, width) in enumerate(zip(row, widths))).rstrip()
        for row in cells
    ), ""]


def render(report: dict[str, Any]) -> str:
    total = report["total"]
    usage = ("calls", *TOKENS, "cost_usd")
    usage_header = ["calls", "input", "cache read", "cache write", "output", "cost $"]
    per_task = report["cost_per_passed_task"]
    lines = [
        f"{report['project']}: {total['calls']:,} calls, ${total['cost_usd']:,.2f}",
        "cost per passed task: " + (f"${per_task:,.2f}" if per_task is not None else "no task passed yet"),
        "",
    ]
    lines += _table("By role", ["role", *usage_header, "share"], [
        [role, *(tally[key] for key in usage), f"{tally['cost_share']:.1%}"] for role, tally in report["roles"].items()
    ])
    lines += _table("No new information", ["", "calls", "of", "cost $", "avoided"], [
        [name.replace("_", " "), row["calls"], row["of_calls"], row["cost_usd"], row["avoided"]]
        for name, row in report["no_new_information"].items()
    ])
    lines += _table("Passed tasks", ["task", "reviews", "continue", "elapsed s",
                                     "calls", "input", "output", "cost $"], [
        [row[key] for key in ("task", "reviewer_calls", "continue", "elapsed_s",
                              "calls", "input_tokens", "output_tokens", "cost_usd")]
        for row in report["passed_tasks"]
    ])
    lines += _table("Context: input tokens per call; mean by turn on session", [
        "role", "p50", "p90", "max", *(f"turn {name}" for _, name in TURN_BUCKETS),
    ], [
        [role, row["p50"], row["p90"], row["max"], *(row["mean_by_session_turn"].get(name, "") for _, name in TURN_BUCKETS)]
        for role, row in report["context"].items()
    ])
    lines += _table("Failures and retries", ["", *usage_header], [
        [name, *(row[key] for key in usage)] for name, row in report["failures"].items()
    ])
    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus.tools.efficiency_report", description=__doc__.splitlines()[0])
    parser.add_argument("life_dir", type=Path, metavar="project-state-dir", help="the directory holding events.jsonl")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    if not (args.life_dir / "events.jsonl").is_file():
        parser.error(f"no events.jsonl in {args.life_dir}")
    report = efficiency_report(args.life_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
