"""Export all 290 resource-bound Portfolio prompt packets.

The exporter intentionally requires concrete resources. A generated objective is an
execution contract, so it must not silently inherit the seed snapshot's illustrative
compute assumptions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from foundry.services.prompt_compiler import PromptCompiler


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "item"


def write_utf8(path: Path, content: str) -> None:
    """Write deterministic UTF-8 bytes without platform newline translation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export all Flywheel Portfolio prompts")
    parser.add_argument("--gpu-count", type=int, required=True)
    parser.add_argument("--gpu-model", required=True)
    parser.add_argument("--gpu-hours", type=float, required=True)
    parser.add_argument("--wall-clock-deadline", required=True, help="ISO-8601 value")
    parser.add_argument("--max-parallel-jobs", type=int, default=1)
    parser.add_argument("--api-budget", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.gpu_count < 0 or args.gpu_hours <= 0 or args.max_parallel_jobs < 1:
        parser.error("resource counts and budgets must be positive (GPU count may be zero)")
    try:
        requested_wall_clock = datetime.fromisoformat(args.wall_clock_deadline)
    except ValueError:
        parser.error("wall-clock-deadline must be an ISO-8601 datetime with an explicit offset")
    if requested_wall_clock.tzinfo is None:
        parser.error("wall-clock-deadline must include an explicit UTC offset")

    root = Path(__file__).resolve().parents[1]
    seeds = root / "data" / "seeds"
    calendar = load_json(seeds / "conference_calendar_2026-08-22_2027-08-22.json")
    topics = load_json(seeds / "topics_all_58x5.json")["topics"]
    domains = load_json(seeds / "domain_evidence.json")["domains"]
    venue_by_key = {venue["key"]: venue for venue in calendar["venues"]}
    resources = {
        "gpu_count": args.gpu_count,
        "gpu_model": args.gpu_model,
        "gpu_hours": args.gpu_hours,
        "wall_clock_deadline": args.wall_clock_deadline,
        "max_parallel_jobs": args.max_parallel_jobs,
        "api_budget": args.api_budget,
    }
    compiler = PromptCompiler()
    generated: list[dict[str, Any]] = []

    for topic in topics:
        source_venue = venue_by_key[topic["venue_key"]]
        targets = sorted(
            source_venue.get("targets_in_window") or [],
            key=lambda row: (
                row.get("deadline_date")
                if row.get("evidence_status") == "official_confirmed"
                else row.get("forecast_window_start") or row.get("deadline_date")
            ),
        )
        target = targets[0] if targets else {}
        has_fixed_submission_deadline = bool(target)
        effective_deadline = (
            (
                target.get("deadline_date")
                if target.get("evidence_status") == "official_confirmed"
                else target.get("forecast_window_start") or target.get("deadline_date")
            )
            if has_fixed_submission_deadline
            else requested_wall_clock.date().isoformat()
        )
        deadline_evidence_status = target.get("evidence_status") or "rolling"
        deadline_source_url = (
            target.get("source_url")
            or (target.get("forecast_basis") or {}).get("source_url")
            or (source_venue.get("special_submission_model") or {}).get("source_url")
        )
        packet_resources = dict(resources)
        resource_deadline_clamped = bool(
            has_fixed_submission_deadline
            and effective_deadline
            and requested_wall_clock.date().isoformat() > effective_deadline
        )
        if resource_deadline_clamped:
            packet_resources["wall_clock_deadline"] = f"{effective_deadline}T00:00:00+00:00"
        policies = [
            "启动前从会议官方页面复核匿名、AI 使用、伦理、页数与 artifact 规则",
        ]
        if deadline_source_url:
            policies.append(f"Deadline/CFP source snapshot: {deadline_source_url}")
        if has_fixed_submission_deadline and target.get("evidence_status") != "official_confirmed":
            policies.append(
                "该日期是规划预测，不是会议事实；按预测区间最早端准备，任何不可逆执行前必须复核官方 CFP"
            )
        elif not has_fixed_submission_deadline:
            policies.append(
                f"该会议为官方 rolling 模式；{args.wall_clock_deadline} 只是操作者提供的内部规划 cutoff，"
                "不是会议投稿截止日"
            )
        if source_venue.get("special_submission_model"):
            policies.append(json.dumps(source_venue["special_submission_model"], ensure_ascii=False))
        if source_venue.get("partial_official_information"):
            policies.append(json.dumps(source_venue["partial_official_information"], ensure_ascii=False))
        venue = {
            "name": source_venue["display_name"],
            "edition": target.get("conference_year", "rolling"),
            "track": target.get("round_note") or (
                "Main / Full Paper" if has_fixed_submission_deadline else "Rolling submission"
            ),
            "deadline": (
                f"{target['deadline_date']} {target.get('timezone', 'AoE')} "
                f"({target.get('evidence_status', 'forecast')})"
                + (
                    f"; planning window {target.get('forecast_window_start')}..{target.get('forecast_window_end')}"
                    if target.get("evidence_status") != "official_confirmed"
                    else ""
                )
                if target else (
                    f"rolling; internal planning cutoff {args.wall_clock_deadline}; "
                    "not an official submission deadline"
                )
            ),
            "scope": source_venue.get("category_zh") or source_venue.get("official_name"),
            "policies": policies,
        }
        idea = {
            "title": topic["title_zh"],
            "problem_gap": topic["problem_gap"],
            "mechanism_hypothesis": topic["core_hypothesis"],
            "method_seed": topic.get("method"),
            "public_data_or_tasks": topic.get("public_data_or_tasks"),
            "kill_criterion": topic["kill_criterion"],
            "decisive_experiment": topic.get("decisive_experiments"),
            "predicted_observation": (
                "必须在检索与 pilot 设计后预注册；当前 seed 未声称任何方向或实验结果"
            ),
            "baseline_candidates": [topic.get("strongest_baselines") or "待检索并核验"],
            "oral_aspiration": True,
            "source_requirements": [
                "目标会议往届录用论文或官方 OpenReview/proceedings",
                "截至任务启动日的 arXiv/出版社一手论文",
                "作者或组织的官方 GitHub 仓库和固定 commit",
            ],
        }
        compiled = compiler.compile(
            venue=venue,
            domain=domains[source_venue["category_id"]],
            idea=idea,
            resources=packet_resources,
            phase="portfolio",
        )
        seed_header = """# SEED COVERAGE BASELINE — NOT PERSONALIZED

This packet is one reproducible coverage probe for the bundled 58×5 catalogue.
It is not a final idea for any team and is not launch-ready by itself.  Before
creating an Argus mission, use Flywheel Context Studio to freeze the actual team
expertise, methods, data permissions, resources, time, policies and completion
target.  A different condition snapshot must produce a different ideation
objective.  The seed has no novelty, feasibility, positive-result, acceptance,
or Oral presumption.

---

"""
        objective = seed_header + compiled.prompt
        packet_manifest = {
            **compiled.manifest,
            "prompt_sha256": hashlib.sha256(objective.encode("utf-8")).hexdigest(),
            "personalization_state": "seed_coverage_baseline",
            "launch_ready": False,
            "requires_team_condition_snapshot": True,
        }
        packet_dir = args.output.resolve() / safe_name(source_venue["key"]) / f"idea-{int(topic['topic_rank_within_venue']):02d}"
        packet_dir.mkdir(parents=True, exist_ok=True)
        write_utf8(packet_dir / "OBJECTIVE.md", objective)
        write_utf8(
            packet_dir / "MANIFEST.json",
            json.dumps(packet_manifest, ensure_ascii=False, indent=2),
        )
        write_utf8(
            packet_dir / "ROUGH_IDEA.md",
            f"# {topic['title_zh']}\n\n{topic['problem_gap']}\n\n"
            f"## 机制假设\n\n{topic['core_hypothesis']}\n\n"
            f"## 初始方法\n\n{topic['method']}\n\n"
            f"## 公开数据或任务\n\n{topic['public_data_or_tasks']}\n\n"
            f"## 最强基线候选（待核验）\n\n{topic['strongest_baselines']}\n\n"
            f"## 决定性实验\n\n{topic['decisive_experiments']}\n\n"
            f"## 历史选题算力假设（非当前库存）\n\n{topic['compute_fit']}\n\n"
            f"## 会议适配\n\n{topic['venue_fit_reason']}\n\n"
            f"## 风险与可复用研究线\n\n{topic['risk_level']} · {topic['reusable_program']}\n\n"
            f"## Kill criterion\n\n{topic['kill_criterion']}\n",
        )
        generated.append({
            **packet_manifest,
            "venue_key": source_venue["key"],
            "venue_name": source_venue["display_name"],
            "category_id": source_venue["category_id"],
            "rank": topic["topic_rank_within_venue"],
            "packet_path": (
                f"{safe_name(source_venue['key'])}/"
                f"idea-{int(topic['topic_rank_within_venue']):02d}"
            ),
            "has_fixed_submission_deadline": has_fixed_submission_deadline,
            "deadline_date": target.get("deadline_date"),
            "effective_planning_deadline": effective_deadline,
            "deadline_evidence_status": deadline_evidence_status,
            "deadline_source_url": deadline_source_url,
            "resource_wall_clock_deadline": packet_resources["wall_clock_deadline"],
            "resource_deadline_clamped": resource_deadline_clamped,
        })

    args.output.mkdir(parents=True, exist_ok=True)
    write_utf8(
        args.output / "CATALOG.json",
        json.dumps({"count": len(generated), "packets": generated}, ensure_ascii=False, indent=2),
    )
    catalog_lines = [
        "# Argus Portfolio Prompt Catalog",
        "",
        "> 这些是 58 个 venue × 5 个可重放的 **seed coverage baseline**，不是任何团队的最终 idea，",
        "> 也不是 launch-ready Prompt。必须先在 Context Studio 冻结团队专长、数据权限、资源、时间、政策与目标，",
        "> 再生成条件化 ideation objective；它们不代表新颖性、正向结果、录用或 Oral。",
        "",
        f"共 {len(generated)} 个候选包；每个包均包含结构化 Prompt、粗略想法和内容寻址 manifest。",
        "",
    ]
    for venue_key in sorted({str(packet["venue_key"]) for packet in generated}):
        packets = sorted(
            (packet for packet in generated if packet["venue_key"] == venue_key),
            key=lambda packet: int(packet["rank"]),
        )
        first = packets[0]
        target = (
            f"rolling · internal cutoff {first['resource_wall_clock_deadline']}"
            if not first["has_fixed_submission_deadline"]
            else f"{first['effective_planning_deadline']} · {first['deadline_evidence_status']}"
        )
        catalog_lines.extend([
            f"## {venue_key} · {first['venue_name']}",
            "",
            f"领域合同：`{first['category_id']}` · 保守规划目标：{target}",
            "",
        ])
        for packet in packets:
            relative = str(packet["packet_path"]).replace("\\", "/")
            catalog_lines.append(
                f"{int(packet['rank'])}. **{packet['idea_title']}** — "
                f"[Prompt]({relative}/OBJECTIVE.md) · "
                f"[粗略想法]({relative}/ROUGH_IDEA.md) · "
                f"[Manifest]({relative}/MANIFEST.json)"
            )
        catalog_lines.append("")
    write_utf8(args.output / "CATALOG.md", "\n".join(catalog_lines))
    print(f"exported {len(generated)} prompt packets to {args.output.resolve()}")


if __name__ == "__main__":
    main()
