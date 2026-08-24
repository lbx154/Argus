"""Deterministic conference research timeline calculations."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping

PIPELINE_STAGES: tuple[tuple[int, str, str, bool], ...] = (
    (180, "prompt_refresh", "刷新会议规则、来源快照与全部候选 Prompt Card", False),
    (150, "novelty_scan", "建立最近工作矩阵并执行机制级 collision 扫描", False),
    (120, "portfolio", "生成机制不同的候选路线并设计最便宜 falsifier", False),
    (90, "winner_gate", "人工锁定至多一个 winner 与两个兼容 fallback", True),
    (60, "locked_campaign", "冻结主张、指标、数据划分、seed、baseline 与预算", True),
    (45, "evidence_freeze", "完成主实验、消融、负控、鲁棒性与全文初稿", False),
    (30, "review_sprint", "启动双独立评审、修订与第一轮诚信检查；不从零找 Idea", False),
    (14, "repro_integrity", "复现、引用、匿名、伦理、artifact 与格式审计", False),
    (7, "claim_freeze", "冻结核心 claim；只修复阻断项", True),
    (3, "final_human_gate", "人工审核署名、AI 披露和最终稿", True),
    (2, "upload_buffer", "预留人工上传与系统故障缓冲；系统不自动投稿", True),
)


def build_pipeline(
    deadline: Mapping[str, Any], *, today: date | None = None
) -> dict[str, Any]:
    current = today or datetime.now(UTC).date()
    scheduling_date = str(
        deadline.get("forecast_window_start")
        or deadline.get("deadline_date")
        or ""
    )
    if not scheduling_date:
        raise ValueError("deadline_date or forecast_window_start is required")
    due = date.fromisoformat(scheduling_date)
    stages: list[dict[str, Any]] = []
    for offset, key, purpose, human_gate in PIPELINE_STAGES:
        stage_date = due - timedelta(days=offset)
        delta = (stage_date - current).days
        status = "overdue" if delta < 0 else "due" if delta == 0 else "upcoming"
        stages.append({
            "key": key,
            "offset_days": offset,
            "date": stage_date.isoformat(),
            "days_until_stage": delta,
            "status": status,
            "purpose": purpose,
            "human_gate": human_gate,
        })
    return {
        "deadline_id": deadline.get("id"),
        "deadline_date": deadline.get("deadline_date"),
        "scheduling_date": scheduling_date,
        "schedule_uses_forecast_window_start": bool(deadline.get("forecast_window_start")),
        "evidence_status": deadline.get("evidence_status", "unconfirmed"),
        "auto_submission": False,
        "d30_is_review_sprint_not_research_start": True,
        "stages": stages,
    }
