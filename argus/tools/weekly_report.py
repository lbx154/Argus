"""One week of every project's tasks, handoffs and spend, with the change from the week before (roadmap EV-4).

Each project's numbers come from the HO-1 report (``efficiency_report``) over
the week's events and calls, and cost per passed task is the CE-1 figure. It
reads only, unless ``--out`` names a directory to write ``<week>.md`` and
``<week>.json`` into, as a weekly timer does.

    python -m argus.tools.weekly_report [<argus-home or project-state-dir> ...] [--week YYYY-Www] [--json] [--out DIR]

With no directory it reads every project under the Argus home. Weeks are ISO
weeks in UTC; the default is the last full week. A task counts in the week its
last settlement falls in: passed, failed, or parked (paused, waiting for a
person). The success rate is passed over passed plus failed; the first-round
pass rate is the share of those that passed on their first attempt in one round.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.metrics import cost_per_passed_task
from ..core.paths import global_root
from .efficiency_report import efficiency_report

WEEK = timedelta(days=7)
COUNTS = (
    "projects", "started", "passed", "failed", "parked", "first_round_passed",
    "reviews", "continue_verdicts", "passed_task_reviews", "calls", "no_new_information_calls",
)
COSTS = ("cost_usd", "no_new_information_usd")
RATES = ("success_rate", "first_round_pass_rate", "reviews_per_passed_task", "cost_per_passed_task_usd")


def week_bounds(week: str | None = None, *, now: float | None = None) -> tuple[str, float, float]:
    """``(YYYY-Www, start, end)`` of an ISO week in UTC; by default the last full week."""
    if week is None:
        today = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() if now is None else now, timezone.utc)
        year, number, _ = (today - WEEK).isocalendar()
    else:
        year_text, _, number_text = week.upper().partition("-W")
        year, number = int(year_text), int(number_text)
    start = datetime.fromisocalendar(year, number, 1).replace(tzinfo=timezone.utc)
    return f"{year}-W{number:02d}", start.timestamp(), (start + WEEK).timestamp()


def project_dirs(paths: Iterable[Path]) -> list[Path]:
    """Each path is a project's state directory (it holds ``events.jsonl``) or an
    Argus home whose ``projects/`` holds them."""
    found: list[Path] = []
    for path in paths:
        if (path / "events.jsonl").is_file():
            found.append(path)
        elif (path / "projects").is_dir():
            found += sorted(child for child in (path / "projects").iterdir() if (child / "events.jsonl").is_file())
    return found


def _rates(row: dict[str, Any]) -> dict[str, Any]:
    ended = row["passed"] + row["failed"]
    return {
        **row,
        "success_rate": round(row["passed"] / ended, 4) if ended else None,
        "first_round_pass_rate": round(row["first_round_passed"] / ended, 4) if ended else None,
        "reviews_per_passed_task": round(row["passed_task_reviews"] / row["passed"], 2) if row["passed"] else None,
        "cost_per_passed_task_usd": cost_per_passed_task(row["cost_usd"], row["passed"]),
    }


def project_week(life_dir: Path, since: float, until: float) -> dict[str, Any]:
    report = efficiency_report(life_dir, since=since, until=until)
    waste = report["no_new_information"].values()
    tasks = report["tasks"]
    active = report["total"]["calls"] or tasks["started"] or tasks["passed"] + tasks["failed"] + tasks["parked"]
    return _rates({
        "project": life_dir.name,
        "projects": int(bool(active)),
        **report["tasks"],
        "reviews": report["reviews"]["completed"],
        "continue_verdicts": report["reviews"]["continue"],
        "passed_task_reviews": sum(row["reviewer_calls"] for row in report["passed_tasks"]),
        "calls": report["total"]["calls"],
        "no_new_information_calls": sum(row["calls"] for row in waste),
        "cost_usd": report["total"]["cost_usd"],
        "no_new_information_usd": round(sum(row["cost_usd"] for row in waste), 4),
    })


def _total(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _rates({
        **{key: sum(row[key] for row in rows) for key in COUNTS},
        **{key: round(sum(row[key] for row in rows), 4) for key in COSTS},
    })


def weekly_report(paths: Iterable[Path | str], week: str | None = None, *, now: float | None = None) -> dict[str, Any]:
    name, since, until = week_bounds(week, now=now)
    dirs = project_dirs(Path(path).expanduser() for path in paths)
    rows = [project_week(life_dir, since, until) for life_dir in dirs]
    total = _total(rows)
    previous = _total([project_week(life_dir, since - WEEK.total_seconds(), since) for life_dir in dirs])
    return {
        "week": name,
        "since": since,
        "until": until,
        "projects": [row for row in rows if row["projects"]],
        "total": total,
        "previous_week": previous,
        "change": {
            key: None if total[key] is None or previous[key] is None else round(total[key] - previous[key], 4)
            for key in (*COUNTS, *COSTS, *RATES)
        },
    }


def _value(value: Any, key: str) -> str:
    if value is None:
        return "-"
    if key.endswith("rate"):
        return f"{value:.1%}"
    if key.endswith("usd"):
        return f"${value:,.2f}"
    return f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"


def _change(value: Any, key: str) -> str:
    if value is None:
        return "-"
    if key.endswith("rate"):
        return f"{value * 100:+.1f} pt"
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}" if key.endswith("usd") else f"{value:+,.2f}" if isinstance(value, float) else f"{value:+,}"


LABELS = {
    "projects": "projects with activity",
    "started": "tasks started",
    "passed": "tasks passed",
    "failed": "tasks failed",
    "parked": "tasks parked",
    "success_rate": "success rate",
    "first_round_passed": "passed in the first round",
    "first_round_pass_rate": "first-round pass rate",
    "reviews": "reviews",
    "continue_verdicts": "\"continue\" verdicts",
    "reviews_per_passed_task": "reviews per passed task",
    "calls": "model calls",
    "no_new_information_calls": "calls with no new information",
    "no_new_information_usd": "spent on calls with no new information",
    "cost_usd": "spent",
    "cost_per_passed_task_usd": "cost per passed task",
}


def render(report: dict[str, Any]) -> str:
    since = datetime.fromtimestamp(report["since"], timezone.utc).date()
    until = datetime.fromtimestamp(report["until"], timezone.utc).date() - timedelta(days=1)
    total, previous, change = report["total"], report["previous_week"], report["change"]
    lines = [
        f"# Argus weekly report {report['week']} ({since} to {until}, UTC)",
        "",
        "| | this week | last week | change |",
        "|---|---:|---:|---:|",
        *(f"| {label} | {_value(total[key], key)} | {_value(previous[key], key)} | {_change(change[key], key)} |"
          for key, label in LABELS.items()),
    ]
    if report["projects"]:
        columns = ("started", "passed", "failed", "parked", "success_rate", "first_round_pass_rate",
                   "reviews_per_passed_task", "cost_usd", "cost_per_passed_task_usd")
        lines += [
            "",
            "## By project",
            "",
            "| project | " + " | ".join(LABELS[key] for key in columns) + " |",
            "|---|" + "---:|" * len(columns),
            *(f"| {row['project']} | " + " | ".join(_value(row[key], key) for key in columns) + " |"
              for row in report["projects"]),
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m argus.tools.weekly_report", description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, metavar="dir",
                        help="an Argus home or a project's state directory (default: the Argus home)")
    parser.add_argument("--week", help="ISO week, e.g. 2026-W42 (default: the last full week)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--out", type=Path, help="also write <week>.md and <week>.json into this directory")
    args = parser.parse_args(argv)
    try:
        week_bounds(args.week)
    except ValueError:
        parser.error(f"--week must look like 2026-W42, not {args.week!r}")
    paths = args.paths or [global_root()]
    if not project_dirs(Path(path).expanduser() for path in paths):
        parser.error("no project state directory (with events.jsonl) under " + ", ".join(map(str, paths)))
    report = weekly_report(paths, args.week)
    text, data = render(report), json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{report['week']}.md").write_text(text + "\n", encoding="utf-8")
        (args.out / f"{report['week']}.json").write_text(data + "\n", encoding="utf-8")
    print(data if args.json else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
