from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re

from .checks import summarize_checks
from .models import RoundSummary


@dataclass(frozen=True)
class FinalReportRequest:
    objective: str
    report_path: str
    session_id: str | None
    operator_messages: list[str]
    round_summary: RoundSummary


@dataclass(frozen=True)
class PptxReportRequest:
    objective: str
    pptx_path: str
    session_id: str | None
    success: bool
    stop_reason: str
    operator_messages: list[str]
    rounds: list[RoundSummary]
    final_report_markdown: str = ""
    plan_mode: str = "off"


def resolve_final_report_file(
    *,
    explicit_path: str | None,
    review_summaries_dir: str | None,
    operator_messages_file: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> str:
    if explicit_path:
        return explicit_path
    if review_summaries_dir:
        return str(Path(review_summaries_dir).resolve() / "final-task-report.md")
    base = _resolve_artifact_dir(
        operator_messages_file=operator_messages_file,
        control_file=control_file,
        state_file=state_file,
        default_root=default_root,
    )
    return str(base / "final-task-report.md")


def build_final_report_prompt(request: FinalReportRequest) -> str:
    operator_text = "\n".join(f"- {item}" for item in request.operator_messages) if request.operator_messages else "- none"
    review = request.round_summary.review
    checks_text = summarize_checks(request.round_summary.checks)
    language_mode = infer_report_language_mode(request)
    return (
        "You are the main execution agent, now in the final handoff stage.\n"
        "Reviewer has already marked the task DONE, and acceptance checks have passed.\n"
        "Use the local $final-task-report skill if it exists.\n\n"
        "Your only task is to write the final Markdown report file. Do not make additional feature changes.\n"
        f"You must write the report to this exact absolute path and keep the file name unchanged:\n{request.report_path}\n\n"
        f"Report language mode: `{language_mode}`.\n"
        "Language rules:\n"
        "- `zh`: write the report in Chinese.\n"
        "- `en`: write the report in English.\n"
        "- `bilingual`: use bilingual headings and concise bilingual labels where it matters.\n\n"
        "The report must include these parts:\n"
        "0. Original User Task / 原始用户任务\n"
        "1. What this task/experiment proposed / 这一次实验提出了哪些点\n"
        "2. How it was done and what data was used / 具体是怎么做的，使用了什么数据\n"
        "3. What the main agent changed, what approach it used, and what experiments it ran / main agent 修改了什么，使用了什么思路，跑了什么实验\n"
        "4. Special Notes / 特殊说明\n\n"
        "Requirements:\n"
        "- Only write what was actually completed. Do not invent work, datasets, experiments, or skill usage.\n"
        "- If no dataset was used, no extra experiment was run, or some detail is unknown, state that explicitly.\n"
        "- Include the original user task in the Markdown.\n"
        "- Use only the user task and explicit user requirements from the provided context. Do not infer or mention unrelated skill context.\n"
        "- If there are noteworthy caveats, maintenance rules, follow-up constraints, or user-visible special notes, include them in the Special Notes section.\n"
        "- Follow the language of the task context instead of always using Chinese.\n"
        "- If the task context is mixed or unclear, prefer bilingual section titles.\n"
        "- Use Markdown headings and flat bullets so the report reads well on mobile.\n\n"
        f"Original user task:\n{request.objective}\n\n"
        "User follow-up requirements:\n"
        f"{operator_text}\n\n"
        f"Round index: {request.round_summary.round_index}\n"
        f"Session ID: {request.session_id or 'none'}\n\n"
        "Main agent latest summary:\n"
        f"{request.round_summary.main_last_message or 'none'}\n\n"
        "Reviewer round summary markdown:\n"
        f"{review.round_summary_markdown or 'none'}\n\n"
        "Reviewer completion summary markdown:\n"
        f"{review.completion_summary_markdown or 'none'}\n\n"
        "Acceptance checks:\n"
        f"{checks_text}\n\n"
        "After writing the file, reply with only these two lines:\n"
        f"REPORT_PATH: {request.report_path}\n"
        "REPORT_STATUS: written\n"
    )


def write_fallback_final_report(*, request: FinalReportRequest, failure_reason: str | None = None) -> str:
    path = Path(request.report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    review = request.round_summary.review
    operator_lines = request.operator_messages or ["none"]
    check_lines = _format_check_lines(request.round_summary)
    main_summary = request.round_summary.main_last_message.strip() or "none"
    failure_note = (failure_reason or "").strip()
    language_mode = infer_report_language_mode(request)
    lines = _build_fallback_report_lines(
        request=request,
        language_mode=language_mode,
        operator_lines=operator_lines,
        main_summary=main_summary,
        check_lines=check_lines,
    )
    if failure_note:
        lines.extend(_fallback_note_lines(language_mode=language_mode, failure_note=failure_note))
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return str(path)


def infer_report_language_mode(request: FinalReportRequest) -> str:
    texts = [request.objective, request.round_summary.main_last_message, *request.operator_messages]
    joined = "\n".join(item for item in texts if item).strip()
    if not joined:
        return "en"
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", joined))
    latin_count = len(re.findall(r"[A-Za-z]", joined))
    if cjk_count > 0 and latin_count == 0:
        return "zh"
    if latin_count > 0 and cjk_count == 0:
        return "en"
    if cjk_count >= max(8, latin_count // 3):
        return "bilingual"
    if latin_count >= max(12, cjk_count * 3):
        return "en"
    return "bilingual"


def _format_check_lines(round_summary: RoundSummary) -> list[str]:
    if not round_summary.checks:
        return ["- none configured"]
    lines: list[str] = []
    for item in round_summary.checks:
        lines.append(
            f"- `{item.command}` -> passed=`{item.passed}` exit_code=`{item.exit_code}`"
        )
    return lines


def _build_fallback_report_lines(
    *,
    request: FinalReportRequest,
    language_mode: str,
    operator_lines: list[str],
    main_summary: str,
    check_lines: list[str],
) -> list[str]:
    review = request.round_summary.review
    if language_mode == "zh":
        lines = [
            "# 最终任务报告",
            "",
            f"- 生成时间: `{datetime.now(timezone.utc).isoformat()}`",
            f"- Session ID: `{request.session_id or '-'}`",
            f"- 轮次: `{request.round_summary.round_index}`",
            "- 报告模式: `fallback`",
            "",
            "## 0. 原始用户任务",
            "",
            request.objective,
            "",
            "## 1. 本次实验提出了哪些点",
            "",
            "- 目标与变更点来自原始用户任务、主代理最终总结和 reviewer 结论。",
        ]
        lines.extend(f"- Operator message: {item}" for item in operator_lines)
        lines.extend(
            [
                "",
                "## 2. 具体是怎么做的，使用了什么数据",
                "",
                "- 实施方式：以下内容来自主代理最终总结、review 总结和验收检查记录。",
                f"- 主代理最终总结：{main_summary}",
                "- 使用数据：未明确说明时按“未说明”处理。",
                "- 数据说明：未说明。",
                "",
                "## 3. main agent 修改了什么，使用了什么思路，跑了什么实验",
                "",
                "### Main Agent Summary",
                "",
                main_summary,
                "",
                "### Reviewer Round Summary",
                "",
                review.round_summary_markdown.strip() or "none",
                "",
                "### Reviewer Completion Summary",
                "",
                review.completion_summary_markdown.strip() or "none",
                "",
                "### Acceptance Checks",
                "",
            ]
        )
        lines.extend(check_lines)
        lines.extend(
            [
                "## 4. 特殊说明",
                "",
                f"- Reviewer reason: {review.reason}",
                f"- Reviewer next action: {review.next_action}",
                "- 用户的额外要求应以原始任务和显式补充消息为准，不需要推断 skill 上下文。",
                "- 如果任务包含后续维护规则、边界条件或用户可见约束，应优先以 reviewer 与 operator messages 为准。",
            ]
        )
        return lines
    if language_mode == "en":
        lines = [
            "# Final Task Report",
            "",
            f"- Generated At: `{datetime.now(timezone.utc).isoformat()}`",
            f"- Session ID: `{request.session_id or '-'}`",
            f"- Round: `{request.round_summary.round_index}`",
            "- Report Mode: `fallback`",
            "",
            "## 0. Original User Task",
            "",
            request.objective,
            "",
            "## 1. What This Task Proposed",
            "",
            "- The key requested outcomes are derived from the original user task, the main agent summary, and the reviewer conclusion.",
        ]
        lines.extend(f"- Operator message: {item}" for item in operator_lines)
        lines.extend(
            [
                "",
                "## 2. How It Was Done and What Data Was Used",
                "",
                "- Method: the items below are compiled from the main-agent summary, reviewer summaries, and acceptance-check records.",
                f"- Main-agent final summary: {main_summary}",
                "- Data used: not explicitly documented in the current summaries.",
                "- Data note: none documented.",
                "",
                "## 3. What the Main Agent Changed, What Approach It Used, and What Experiments It Ran",
                "",
                "### Main Agent Summary",
                "",
                main_summary,
                "",
                "### Reviewer Round Summary",
                "",
                review.round_summary_markdown.strip() or "none",
                "",
                "### Reviewer Completion Summary",
                "",
                review.completion_summary_markdown.strip() or "none",
                "",
                "### Acceptance Checks",
                "",
            ]
        )
        lines.extend(check_lines)
        lines.extend(
            [
                "## 4. Special Notes",
                "",
                f"- Reviewer reason: {review.reason}",
                f"- Reviewer next action: {review.next_action}",
                "- Treat the original user task and explicit follow-up messages as the source of user requirements. Do not infer extra skill context.",
                "- If there are ongoing maintenance rules, caveats, or user-visible constraints, treat reviewer notes and operator messages as the highest-level summary context.",
            ]
        )
        return lines
    lines = [
        "# Final Task Report / 最终任务报告",
        "",
        f"- Generated At / 生成时间: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Session ID: `{request.session_id or '-'}`",
        f"- Round / 轮次: `{request.round_summary.round_index}`",
        "- Report Mode / 报告模式: `fallback`",
        "",
        "## 0. Original User Task / 原始用户任务",
        "",
        request.objective,
        "",
        "## 1. What This Task Proposed / 本次实验提出了哪些点",
        "",
        "- The requested outcomes below are summarized from the original task, the main-agent summary, and the reviewer conclusion.",
        "- 以下目标点来自原始任务、主代理最终总结与 reviewer 结论。",
    ]
    lines.extend(f"- Operator message: {item}" for item in operator_lines)
    lines.extend(
        [
            "",
            "## 2. How It Was Done and What Data Was Used / 具体是怎么做的，使用了什么数据",
            "",
            "- Method / 实施方式: compiled from the main-agent summary, reviewer summaries, and acceptance-check records.",
            f"- Main-agent final summary / 主代理最终总结: {main_summary}",
            "- Data used / 使用数据: not explicitly documented in the current summaries / 当前总结中未明确说明。",
            "",
            "## 3. What the Main Agent Changed, What Approach It Used, and What Experiments It Ran / main agent 修改了什么，使用了什么思路，跑了什么实验",
            "",
            "### Main Agent Summary",
            "",
            main_summary,
            "",
            "### Reviewer Round Summary",
            "",
            review.round_summary_markdown.strip() or "none",
            "",
            "### Reviewer Completion Summary",
            "",
            review.completion_summary_markdown.strip() or "none",
            "",
            "### Acceptance Checks",
            "",
        ]
    )
    lines.extend(check_lines)
    lines.extend(
        [
            "## 4. Special Notes / 特殊说明",
            "",
            f"- Reviewer reason: {review.reason}",
            f"- Reviewer next action: {review.next_action}",
            "- Treat the original user task and explicit follow-up messages as the source of user requirements.",
            "- 用户要求以原始任务和显式补充消息为准，不需要推断 skill 上下文。",
            "- If there are maintenance rules, caveats, or user-visible constraints, prioritize reviewer notes and operator messages.",
            "- 若存在维护规则、边界条件或用户可见约束，应优先参考 reviewer 与 operator messages。",
        ]
    )
    return lines


def _fallback_note_lines(*, language_mode: str, failure_note: str) -> list[str]:
    if language_mode == "zh":
        return [
            "",
            "### Fallback Note",
            "",
            f"- 主代理最终报告写入步骤失败，因此由本地 fallback 生成此文件：{failure_note}",
        ]
    if language_mode == "en":
        return [
            "",
            "### Fallback Note",
            "",
            f"- The main-agent final-report write step failed, so this file was generated locally via fallback: {failure_note}",
        ]
    return [
        "",
        "### Fallback Note / 回退说明",
        "",
        f"- The main-agent final-report write step failed, so this file was generated locally via fallback: {failure_note}",
        f"- 主代理最终报告写入步骤失败，因此由本地 fallback 生成此文件：{failure_note}",
    ]


def _resolve_artifact_dir(
    *,
    operator_messages_file: str | None,
    control_file: str | None,
    state_file: str | None,
    default_root: str | None = None,
) -> Path:
    if operator_messages_file:
        return Path(operator_messages_file).resolve().parent
    if control_file:
        return Path(control_file).resolve().parent
    if state_file:
        return Path(state_file).resolve().parent
    if default_root:
        return Path(default_root).resolve()
    return Path(".").resolve() / ".argusbot"


def build_pptx_report_prompt(request: PptxReportRequest) -> str:
    """Build the prompt for the main agent to generate a PPTX work presentation."""
    last_round = request.rounds[-1] if request.rounds else None
    checks_passed = 0
    checks_total = 0
    if last_round:
        for c in last_round.checks:
            checks_total += 1
            if c.passed:
                checks_passed += 1

    meta_lines = [
        f"- Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"- Session ID: {request.session_id or 'N/A'}",
        f"- Rounds executed: {len(request.rounds)}",
        f"- Outcome: {'Success' if request.success else 'Incomplete'}",
        f"- Checks: {checks_passed}/{checks_total} passed",
    ]
    meta_block = "\n".join(meta_lines)

    report_block = request.final_report_markdown.strip() if request.final_report_markdown else "No final report available."

    return (
        "You are the main execution agent, now in the PPTX presentation phase.\n"
        "The Markdown final report is already written. Now create a PPTX slide deck.\n"
        "Use the local $pptx-run-report skill and the $pptx skill (PptxGenJS).\n\n"
        "IMPORTANT: You are NOT making a 'loop run report' about rounds and checks.\n"
        "You are making a **work presentation** — something you'd show to a mentor, colleague, or classmate.\n"
        "Focus on WHAT was accomplished, WHY it matters, HOW it was done, and WHAT the results are.\n\n"
        f"Write the PPTX to this exact absolute path:\n{request.pptx_path}\n\n"
        "== ORIGINAL OBJECTIVE ==\n"
        f"{request.objective}\n\n"
        "== METADATA ==\n"
        f"{meta_block}\n\n"
        "== FINAL TASK REPORT (Markdown) ==\n"
        "Use this as your primary content source. Extract the key points for the slides.\n\n"
        f"{report_block}\n\n"
        "== INSTRUCTIONS ==\n"
        "1. Read the $pptx-run-report skill for slide structure and style guidance.\n"
        "2. Choose a color palette that fits the topic (don't always use the same one).\n"
        "3. Adapt the slide structure to the type of work (research/feature/bugfix/etc).\n"
        "4. Generate 6-10 slides. Quality over quantity.\n"
        "5. Match the language of the objective and report.\n"
        "6. Use PptxGenJS to write the .pptx file.\n\n"
        "After writing the PPTX file, reply with only these two lines:\n"
        f"PPTX_REPORT_PATH: {request.pptx_path}\n"
        "PPTX_REPORT_STATUS: written\n"
    )


def _build_pptx_data_payload(request: PptxReportRequest) -> dict[str, Any]:
    """Build the structured data dict for the PPTX report."""
    last_round = request.rounds[-1] if request.rounds else None

    final_checks: list[dict[str, Any]] = []
    checks_passed = 0
    checks_failed = 0
    if last_round:
        for check in last_round.checks:
            final_checks.append({
                "command": check.command,
                "exit_code": check.exit_code,
                "passed": check.passed,
            })
            if check.passed:
                checks_passed += 1
            else:
                checks_failed += 1

    round_data = []
    for r in request.rounds:
        round_data.append({
            "round_index": r.round_index,
            "review_status": r.review.status,
            "review_confidence": r.review.confidence,
            "checks_passed": all(c.passed for c in r.checks) if r.checks else True,
        })

    obj_short = request.objective[:80] + "..." if len(request.objective) > 80 else request.objective
    return {
        "objective": request.objective,
        "objective_short": obj_short,
        "session_id": request.session_id,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "success": request.success,
        "stop_reason": request.stop_reason,
        "total_rounds": len(request.rounds),
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
        "checks_total": checks_passed + checks_failed,
        "reviewer_verdict": last_round.review.status if last_round else None,
        "reviewer_reason": last_round.review.reason if last_round else None,
        "reviewer_next_action": last_round.review.next_action if last_round else None,
        "planner_follow_up_required": last_round.plan.follow_up_required if last_round and last_round.plan else None,
        "planner_next_explore": last_round.plan.next_explore if last_round and last_round.plan else None,
        "planner_main_instruction": last_round.plan.main_instruction if last_round and last_round.plan else None,
        "plan_mode": request.plan_mode,
        "final_checks": final_checks,
        "rounds": round_data,
        "operator_messages": request.operator_messages or [],
    }
