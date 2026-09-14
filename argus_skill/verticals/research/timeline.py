"""Estimate proposal timelines: python -m argus_skill.verticals.research.timeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .timeline_models import Proposal, label, number, resources
from .timeline_schedule import forecast


def estimate(payload: dict[str, Any]) -> dict[str, Any]:
    """Preview all candidate proposals independently; never dispatch experiments."""
    if not isinstance(payload, dict):
        raise ValueError("timeline input must be an object")
    proposals = payload.get("proposals")
    if not isinstance(proposals, list) or not 1 <= len(proposals) <= 20:
        raise ValueError("proposals must contain 1 to 20 proposals")
    capacity = resources(payload.get("resources", {}))
    now = number(payload.get("now_hours", 0), "now_hours")
    deadline = payload.get("deadline_hours")
    if deadline is not None:
        deadline = number(deadline, "deadline_hours")
    defer = payload.get("defer_optional", False)
    if not isinstance(defer, bool):
        raise ValueError("defer_optional must be boolean")
    parsed = [Proposal.parse(row, capacity, now) for row in proposals]
    if len({p.id for p in parsed}) != len(parsed):
        raise ValueError("duplicate proposal id")
    selected = label(payload.get("selected_proposal_id"), "selected_proposal_id")
    if selected not in {p.id for p in parsed}:
        raise ValueError("selected_proposal_id must identify a proposal")
    report = dict(
        schema_version=1,
        selected_proposal_id=selected,
        now_hours=now,
        deadline_hours=deadline,
        resources=capacity,
        estimate_kind="preliminary_scenario_estimate",
        time_basis="elapsed hours from project start; continuous availability",
        method="PERT task means (lower + 4*likely + upper)/6; dependency/resource list scheduling",
        limitations=[
            "Ranges are scenario estimates, not calibrated confidence intervals or guarantees.",
            "Unknown pivots and unplanned experiments can exceed the upper scenario.",
            "Candidate proposals are alternatives, not concurrent resource reservations.",
            "Model difficulty affects supplied durations and rationale, not a hidden multiplier.",
        ],
        proposals=[forecast(p, capacity, now, deadline, defer) for p in parsed],
    )
    # Finite inputs can still overflow when many durations are added together.
    json.dumps(report, allow_nan=False)
    return report


def render_markdown(report: dict) -> str:
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# Research timeline / 研究工期预估",
        "",
        "Preliminary / 初步估计 · elapsed hours from project start / 从项目开始计时（小时）。",
        "Ranges are scenarios, not confidence intervals; unplanned pivots may exceed them.",
        "区间不是置信区间；新增实验或更换方向可能超出上限。",
        "",
    ]
    for proposal in report["proposals"]:
        chosen = " · selected / 已选" if proposal["id"] == report["selected_proposal_id"] else ""
        lines.extend([f"## {cell(proposal['title'])}{chosen}", ""])
        interval = proposal["finish_hours"]
        if interval:
            lines.append(
                f"Estimated finish / 预计完成：**{interval['expected']:.1f} h** "
                f"({interval['lower']:.1f}–{interval['upper']:.1f} h); "
                f"remaining / 剩余：{proposal['remaining_hours']:.1f} h."
            )
        else:
            lines.append("Completion forecast blocked / 完成时间待重规划；见失败及受阻原因。")
        gap = proposal["deadline_gap_hours"]
        if gap is not None:
            lines.append(f"Deadline overrun / 预计超期：{gap:.1f} h.")
        if proposal["deferred_task_ids"]:
            lines.append(
                "Deferred optional work / 延后可选项：" + ", ".join(proposal["deferred_task_ids"])
            )
        lines.extend(
            [
                "",
                "| Task / 任务 | Phase | Start h | Finish h | Queue h | Resources | Basis / 依据 |",
                "| --- | --- | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for row in proposal["schedule"]:
            lines.append(
                f"| {cell(row['title'])} ({row['status']}) | {cell(row['phase'])} | "
                f"{row['start_hours']:.1f} | {row['finish_hours']:.1f} | "
                f"{row['resource_wait_hours']:.1f} | {cell(row['resources'])} | {cell(row['basis'])} |"
            )
        for row in proposal["failed_tasks"] + proposal["blocked_tasks"]:
            lines.append(f"\n- {cell(row['id'])}: {cell(row['reason'])}")
        if proposal["assumptions"]:
            lines.extend(
                ["", "Assumptions / 假设：" + "; ".join(map(cell, proposal["assumptions"]))]
            )
        lines.append("")
    if revision := report.get("revision"):

        def hours(value):
            return "unavailable / 暂无" if value is None else f"{value:.1f} h"

        lines.extend(
            [
                "## Revision / 调整原因",
                "",
                cell(revision["reason"]),
                "",
                f"Original estimate / 初版：{hours(revision['baseline_finish_hours'])}; "
                f"previous / 上版：{hours(revision['previous_finish_hours'])}.",
                f"Delta from original estimate / 相对初版变化：{hours(revision['baseline_delta_hours'])}.",
            ]
        )
        for task in revision["changed_tasks"]:
            lines.append(
                f"\n- {cell(task['proposal_id'])}/{cell(task['id'])}: {cell(task['reason'])} "
                f"[{task['reason_status']}]; evidence: {cell(', '.join(task['evidence']))}"
            )
        for task in revision["task_variances"]:
            kind = "observed / 实际" if task["observed"] else "forecast / 预测"
            lines.append(
                f"\n- {cell(task['id'])}: {kind} delay / 延期 {task['delay_hours']:.1f} h; "
                f"{cell(task['reason'])}; resource queue / 资源排队 {task['resource_wait_hours']:.1f} h."
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="Print JSON rather than Markdown")
    parser.add_argument("--project-root", type=Path, help="Record an immutable forecast revision")
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if args.project_root is not None:
            from .timeline_store import record

            if args.expected_version is None:
                raise ValueError("recording requires --expected-version (0 for the first revision)")
            report = record(
                args.project_root,
                payload,
                expected_version=args.expected_version,
                reason=args.reason,
            )["report"]
        else:
            if args.expected_version is not None or args.reason:
                raise ValueError("--expected-version and --reason require --project-root")
            report = estimate(payload)
        print(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
            if args.json
            else render_markdown(report),
            end="\n",
        )
        return 0
    except (OSError, ValueError, TypeError, TimeoutError) as exc:
        print(f"timeline: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
