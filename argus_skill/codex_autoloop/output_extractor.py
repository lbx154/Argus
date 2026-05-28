"""Reviewer/Planner 输出提取器 - 将 JSON 输出转换为结构化 Markdown。

本模块用于从 Reviewer 和 Planner 的 JSON 输出中提取 Markdown 字段，
并重新格式化为多层级的结构化 Markdown 文本，适合飞书消息渲染。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ReviewerOutput:
    """Reviewer 输出提取结果。"""
    status: str
    confidence: float
    reason: str
    next_action: str
    round_summary: str
    completion_summary: str


@dataclass
class PlannerOutput:
    """Planner 输出提取结果。"""
    summary: str
    workstreams: list[dict]
    done_items: list[str]
    remaining_items: list[str]
    risks: list[str]
    next_steps: list[str]
    exploration_items: list[str]
    full_report: str


def try_repair_truncated_json(text: str) -> str:
    """尝试修复被截断的 JSON。

    Args:
        text: 可能被截断的 JSON 文本

    Returns:
        修复后的 JSON 文本
    """
    text = text.strip()

    # 移除 markdown 代码块标记
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    text = text.rstrip("`").rstrip()

    # 计算括号和引号的平衡
    brace_count = 0
    bracket_count = 0
    in_string = False
    escape_next = False

    for i, char in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if not in_string:
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
            elif char == "[":
                bracket_count += 1
            elif char == "]":
                bracket_count -= 1

    # 关闭未闭合的括号
    result = text
    if bracket_count > 0:
        result += "]" * bracket_count
    if brace_count > 0:
        result += "}" * brace_count

    return result.strip()


def extract_reviewer_output(json_text: str) -> ReviewerOutput | None:
    """从 Reviewer JSON 输出中提取结构化数据。

    Args:
        json_text: Reviewer 输出的 JSON 文本

    Returns:
        提取的 ReviewerOutput 对象，如果解析失败则返回 None
    """
    # 尝试直接解析
    try:
        data = json.loads(json_text)
        if isinstance(data, dict):
            return _build_reviewer_output(data)
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 块
    json_match = re.search(r'\{[\s\S]*\}', json_text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            return _build_reviewer_output(data)
        except json.JSONDecodeError:
            pass

    # 尝试修复被截断的 JSON
    repaired = try_repair_truncated_json(json_text)
    try:
        data = json.loads(repaired)
        return _build_reviewer_output(data)
    except json.JSONDecodeError:
        pass

    # 最后尝试：用正则提取各个字段
    return _extract_reviewer_output_regex(json_text)


def _build_reviewer_output(data: dict) -> ReviewerOutput:
    """从解析的 JSON 数据构建 ReviewerOutput。"""
    return ReviewerOutput(
        status=data.get("status", "unknown"),
        confidence=float(data.get("confidence", 0.0)),
        reason=data.get("reason", ""),
        next_action=data.get("next_action", ""),
        round_summary=data.get("round_summary_markdown", ""),
        completion_summary=data.get("completion_summary_markdown", ""),
    )


def _extract_reviewer_output_regex(json_text: str) -> ReviewerOutput | None:
    """使用正则表达式从被截断的 JSON 中提取 Reviewer 字段。"""
    def extract_string_field(text: str, field: str) -> str:
        # 匹配 "field": "value" 或 "field": "value...（被截断）"
        pattern = rf'"{field}"\s*:\s*"([^"]*(?:[^"\\]\\.)*?)"'
        match = re.search(pattern, text)
        if match:
            value = match.group(1)
            # 处理转义字符
            value = value.replace('\\"', '"').replace('\\n', '\n')
            return value
        return ""

    def extract_number_field(text: str, field: str) -> float:
        pattern = rf'"{field}"\s*:\s*([\d.]+)'
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return 0.0

    return ReviewerOutput(
        status=extract_string_field(json_text, "status"),
        confidence=extract_number_field(json_text, "confidence"),
        reason=extract_string_field(json_text, "reason"),
        next_action=extract_string_field(json_text, "next_action"),
        round_summary=extract_string_field(json_text, "round_summary_markdown"),
        completion_summary=extract_string_field(json_text, "completion_summary_markdown"),
    )


def extract_planner_output(json_text: str) -> PlannerOutput | None:
    """从 Planner JSON 输出中提取结构化数据。

    Args:
        json_text: Planner 输出的 JSON 文本

    Returns:
        提取的 PlannerOutput 对象，如果解析失败则返回 None
    """
    # 尝试直接解析
    try:
        data = json.loads(json_text)
        if isinstance(data, dict):
            return _build_planner_output(data)
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 块
    json_match = re.search(r'\{[\s\S]*\}', json_text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            return _build_planner_output(data)
        except json.JSONDecodeError:
            pass

    # 尝试修复被截断的 JSON
    repaired = try_repair_truncated_json(json_text)
    try:
        data = json.loads(repaired)
        return _build_planner_output(data)
    except json.JSONDecodeError:
        pass

    # 最后尝试：用正则提取各个字段
    return _extract_planner_fields_regex(json_text)


def _build_planner_output(data: dict) -> PlannerOutput:
    """从解析的 JSON 数据构建 PlannerOutput。"""
    return PlannerOutput(
        summary=data.get("summary", ""),
        workstreams=data.get("workstreams", []),
        done_items=data.get("done_items", []),
        remaining_items=data.get("remaining_items", []),
        risks=data.get("risks", []),
        next_steps=data.get("next_steps", []),
        exploration_items=data.get("exploration_items", []),
        full_report=data.get("report_markdown", ""),
    )


def _extract_planner_fields_regex(json_text: str) -> PlannerOutput | None:
    """使用正则表达式从被截断的 JSON 中提取 Planner 字段。"""
    def extract_string_field(text: str, field: str) -> str:
        pattern = rf'"{field}"\s*:\s*"([^"]*(?:[^"\\]\\.)*?)"'
        match = re.search(pattern, text)
        if match:
            value = match.group(1)
            value = value.replace('\\"', '"').replace('\\n', '\n')
            return value
        return ""

    def extract_array_field(text: str, field: str) -> list:
        # 匹配 "field": [...] 数组字段
        pattern = rf'"{field}"\s*:\s*\[([\s\S]*?)\]'
        match = re.search(pattern, text)
        if match:
            array_content = match.group(1)
            # 提取字符串数组项
            items = []
            item_pattern = r'"([^"]*(?:[^"\\]\\.)*?)"'
            for item_match in re.finditer(item_pattern, array_content):
                item = item_match.group(1).replace('\\"', '"').replace('\\n', '\n')
                items.append(item)
            return items
        return []

    def extract_workstreams(text: str) -> list[dict]:
        """提取 workstreams 数组。"""
        pattern = rf'"workstreams"\s*:\s*\[([\s\S]*?)\]'
        match = re.search(pattern, text)
        if not match:
            return []

        array_content = match.group(1)
        workstreams = []

        # 提取每个工作流对象
        obj_pattern = r'\{([^{}]+)\}'
        for obj_match in re.finditer(obj_pattern, array_content):
            obj_content = obj_match.group(1)
            ws = {}

            # 提取 area 字段
            area_match = re.search(r'"area"\s*:\s*"([^"]*)"', obj_content)
            if area_match:
                ws["area"] = area_match.group(1)

            # 提取 status 字段
            status_match = re.search(r'"status"\s*:\s*"([^"]*)"', obj_content)
            if status_match:
                ws["status"] = status_match.group(1)

            if ws:
                workstreams.append(ws)

        return workstreams

    return PlannerOutput(
        summary=extract_string_field(json_text, "summary"),
        workstreams=extract_workstreams(json_text),
        done_items=extract_array_field(json_text, "done_items"),
        remaining_items=extract_array_field(json_text, "remaining_items"),
        risks=extract_array_field(json_text, "risks"),
        next_steps=extract_array_field(json_text, "next_steps"),
        exploration_items=extract_array_field(json_text, "exploration_items"),
        full_report=extract_string_field(json_text, "report_markdown"),
    )


def format_reviewer_markdown(output: ReviewerOutput, *, enable_length_protection: bool = True) -> str:
    """将 Reviewer 输出格式化为多层级 Markdown。

    Args:
        output: ReviewerOutput 对象
        enable_length_protection: 是否启用长度保护 (截断 + 移除代码块)

    Returns:
        格式化的 Markdown 文本
    """
    lines: list[str] = []

    # 状态标题
    status_icons = {
        "done": "✅",
        "continue": "🔄",
        "blocked": "🚫",
    }
    icon = status_icons.get(output.status, "❓")
    lines.append(f"{icon} **Reviewer 评审结果**")
    lines.append("")

    # 核心状态
    lines.append(f"**状态**: {output.status}")
    lines.append(f"**置信度**: {output.confidence:.0%}")
    lines.append("")

    # 评审原因
    if output.reason:
        lines.append("**评审原因**")
        reason = output.reason
        if enable_length_protection:
            if len(reason) > 3000:
                reason = reason[:3000] + "...(truncated)"
            # 移除代码块，替换为简洁描述
            reason = _remove_code_blocks(reason)
        lines.append(reason)
        lines.append("")

    # 本轮总结
    if output.round_summary:
        lines.append("**本轮总结**")
        summary = output.round_summary
        if enable_length_protection:
            if len(summary) > 5000:
                summary = summary[:5000] + "...(truncated)"
            # 移除代码块，保持简洁
            summary = _remove_code_blocks(summary)
        lines.append(summary)
        lines.append("")

    # 完成总结
    if output.completion_summary:
        lines.append("**完成证据**")
        completion = output.completion_summary
        if enable_length_protection:
            if len(completion) > 4000:
                completion = completion[:4000] + "...(truncated)"
            completion = _remove_code_blocks(completion)
        lines.append(completion)
        lines.append("")

    # 下一步行动
    if output.next_action:
        action = output.next_action
        if enable_length_protection:
            if len(action) > 1000:
                action = action[:1000] + "...(truncated)"
        lines.append("**下一步行动**")
        lines.append(action)

    return "\n".join(lines)


def format_planner_markdown(output: PlannerOutput, *, enable_length_protection: bool = True) -> str:
    """将 Planner 输出格式化为多层级 Markdown。

    Args:
        output: PlannerOutput 对象
        enable_length_protection: 是否启用长度保护 (截断 + 移除代码块)

    Returns:
        格式化的 Markdown 文本
    """
    lines: list[str] = []

    # 标题
    lines.append("## 📋 Planner 规划报告")
    lines.append("")

    # 经理总结
    if output.summary:
        lines.append("**经理总结**")
        summary = output.summary
        if enable_length_protection:
            if len(summary) > 2000:
                summary = summary[:2000] + "...(truncated)"
            summary = _remove_code_blocks(summary)
        lines.append(summary)
        lines.append("")

    # 工作流表格
    if output.workstreams:
        lines.append("**工作流状态**")
        lines.append("")
        lines.append("| 工作流 | 状态 |")
        lines.append("|--------|------|")
        for ws in output.workstreams:
            area = ws.get("area", "未知")
            status = ws.get("status", "unknown")
            status_label = {
                "done": "✅ 完成",
                "in_progress": "🔄 进行中",
                "todo": "⏳ 待办",
                "blocked": "🚫 阻塞",
            }.get(status, status)
            lines.append(f"| {area} | {status_label} |")
        lines.append("")

        # 详细工作流信息
        lines.append("**工作流详情**")
        for ws in output.workstreams:
            area = ws.get("area", "未知")
            evidence = ws.get("evidence", "")
            next_step = ws.get("next_step", "")
            if evidence:
                if enable_length_protection:
                    if len(evidence) > 1000:
                        evidence = evidence[:1000] + "...(truncated)"
                    evidence = _remove_code_blocks(evidence)
                lines.append(f"- **{area}**: {evidence}")
            if next_step:
                if enable_length_protection:
                    if len(next_step) > 500:
                        next_step = next_step[:500] + "...(truncated)"
                lines.append(f"  - 下一步：{next_step}")
        lines.append("")

    # 完成项
    if output.done_items:
        lines.append("**✅ 完成项**")
        for item in output.done_items:
            lines.append(f"- {item}")
        lines.append("")

    # 剩余项
    if output.remaining_items:
        lines.append("**⏳ 剩余项**")
        for item in output.remaining_items:
            lines.append(f"- {item}")
        lines.append("")

    # 风险
    if output.risks:
        lines.append("**⚠️ 风险**")
        for risk in output.risks:
            lines.append(f"- {risk}")
        lines.append("")

    # 下一步
    if output.next_steps:
        lines.append("**➡️ 推荐下一步**")
        for step in output.next_steps:
            lines.append(f"- {step}")
        lines.append("")

    # 探索项
    if output.exploration_items:
        lines.append("**🔍 探索项**")
        for item in output.exploration_items:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def _remove_code_blocks(text: str) -> str:
    """移除文本中的代码块，替换为简洁描述。

    Args:
        text: 可能包含代码块的文本

    Returns:
        移除代码块后的文本
    """
    # 移除 ```xxx ... ``` 代码块
    result = re.sub(r'```\w*\n[\s\S]*?```', '[code block removed]', text)
    # 移除单行代码引用
    result = re.sub(r'`[^`]+`', '[code]', result)
    return result


def extract_and_format_reviewer(json_text: str, *, enable_length_protection: bool = True) -> str:
    """提取并格式化 Reviewer 输出。

    Args:
        json_text: Reviewer JSON 输出
        enable_length_protection: 是否启用长度保护

    Returns:
        格式化的 Markdown 文本，如果解析失败则返回原始文本
    """
    output = extract_reviewer_output(json_text)
    if output:
        return format_reviewer_markdown(output, enable_length_protection=enable_length_protection)
    return json_text


def extract_and_format_planner(json_text: str, *, enable_length_protection: bool = True) -> str:
    """提取并格式化 Planner 输出。

    Args:
        json_text: Planner JSON 输出
        enable_length_protection: 是否启用长度保护

    Returns:
        格式化的 Markdown 文本，如果解析失败则返回原始文本
    """
    output = extract_planner_output(json_text)
    if output:
        return format_planner_markdown(output, enable_length_protection=enable_length_protection)
    return json_text


def extract_message_content(json_text: str) -> dict[str, str]:
    """从 JSON 中提取所有 Markdown 字段。

    Args:
        json_text: JSON 文本

    Returns:
        包含所有 Markdown 字段的字典
    """
    markdown_fields = {
        "round_summary_markdown",
        "completion_summary_markdown",
        "overview_markdown",
        "report_markdown",
        "summary_markdown",
    }

    result: dict[str, str] = {}

    try:
        data = json.loads(json_text)
        if isinstance(data, dict):
            for field in markdown_fields:
                if field in data and isinstance(data[field], str):
                    result[field] = data[field]
    except json.JSONDecodeError:
        pass

    return result


def clean_json_output(text: str) -> str:
    """清理 JSON 输出，移除 markdown 代码块标记。

    Args:
        text: 可能包含 JSON 的文本

    Returns:
        纯 JSON 字符串
    """
    # 移除 ```json 和 ``` 标记
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    return text.strip()


def parse_agent_response(response_text: str) -> dict[str, Any] | None:
    """解析 Agent 响应，提取 JSON 数据。

    Args:
        response_text: Agent 响应文本

    Returns:
        解析后的 JSON 数据，如果失败则返回 None
    """
    # 清理文本
    cleaned = clean_json_output(response_text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试提取 JSON 块
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return None
