from __future__ import annotations

import json
import mimetypes
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .telegram_control import normalize_command_prefix, parse_command_text, parse_mode_selection_text
from .telegram_notifier import format_event_message
from .md_checker import validate_and_fix_markdown, quick_fix_for_feishu, check_markdown
from .output_extractor import (
    extract_and_format_reviewer,
    extract_and_format_planner,
    extract_message_content,
)

__all__ = [
    # Core classes
    'FeishuNotifier',
    'FeishuCommandPoller',
    'FeishuConfig',
    'FeishuCommand',
    # Constants
    'FEISHU_CARD_MAX_BYTES',
    'FEISHU_TEXT_MAX_BYTES',
    'FEISHU_ERROR_CODE_MESSAGE_TOO_LONG',
    'FEISHU_ERROR_CODE_CARD_CONTENT_FAILED',
    'FEISHU_COLOR_GREEN',
    'FEISHU_COLOR_BLUE',
    'FEISHU_COLOR_YELLOW',
    'FEISHU_COLOR_RED',
    'FEISHU_COLOR_ORANGE',
    'FEISHU_COLOR_PURPLE',
    'FEISHU_COLOR_GRAY',
    'FEISHU_OUTPUT_LENGTH_PROTECTION',
    # Utilities
    'split_feishu_message',
    'build_interactive_card',
    'format_feishu_event_card',
    'format_feishu_event_message',
    'strip_leading_feishu_mentions',
    'parse_feishu_command_text',
    'is_feishu_self_message',
    'extract_feishu_text',
    'format_reviewer_json_to_markdown',
    'format_planner_json_to_markdown',
    'format_planner_to_elements',
]

_FEISHU_MENTION_PREFIX = re.compile(r"^(?:@[_\w-]+\s+)+")
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

# 飞书消息长度限制 (官方文档)
# 卡片消息：30 KB (请求体最大长度，包含模板数据)
# 文本消息：150 KB
# 错误码：230025 - 消息体长度超出限制
#        230099 - 创建卡片内容失败
FEISHU_CARD_MAX_BYTES = 30 * 1024  # 30 KB
FEISHU_TEXT_MAX_BYTES = 150 * 1024  # 150 KB
FEISHU_ERROR_CODE_MESSAGE_TOO_LONG = 230025
FEISHU_ERROR_CODE_CARD_CONTENT_FAILED = 230099

# 飞书卡片颜色模板 (用于不同事件状态)
FEISHU_COLOR_GREEN = "green"    # 成功/完成
FEISHU_COLOR_BLUE = "blue"      # 进行中/信息
FEISHU_COLOR_YELLOW = "yellow"  # 警告/继续
FEISHU_COLOR_RED = "red"        # 失败/受阻
FEISHU_COLOR_ORANGE = "orange"  # 已停止
FEISHU_COLOR_PURPLE = "purple"  # 规划相关
FEISHU_COLOR_GRAY = "gray"      # 中性/默认

# 输出长度保护开关 - 测试时可设为 False
# 当设置为 True 时，会对 reviewer/planner 输出进行截断保护
# 当设置为 False 时，会输出完整内容 (可能导致飞书 API 报错)
FEISHU_OUTPUT_LENGTH_PROTECTION = True


def _normalize_internal_markdown_headers(text: str) -> str:
    """标准化内部 Markdown 的标题层级，避免与外层标题冲突。

    将所有标题降级到最低级别（######），确保字号一致且较小。
    同时移除与外层重复的标题（如"本轮总结"、"完成证据"等）。

    Args:
        text: 原始 Markdown 文本

    Returns:
        标题层级调整后的文本
    """
    if not text:
        return text

    result = text

    # 移除可能重复的标题
    duplicate_headers = [
        (r'^##\s*本轮总结\s*$', ''),
        (r'^##\s*完成证据\s*$', ''),
        (r'^###\s*本轮总结\s*$', ''),
        (r'^###\s*完成证据\s*$', ''),
        (r'^####\s*本轮总结\s*$', ''),
        (r'^####\s*完成证据\s*$', ''),
    ]
    for pattern, replacement in duplicate_headers:
        result = re.sub(pattern, replacement, result, flags=re.MULTILINE)

    # 将所有标题降级到最低级别（######），确保字号最小
    result = re.sub(r'^######?\s+(.+)$', r'###### \1', result, flags=re.MULTILINE)
    result = re.sub(r'^#####\s+(.+)$', r'###### \1', result, flags=re.MULTILINE)
    result = re.sub(r'^####\s+(.+)$', r'###### \1', result, flags=re.MULTILINE)
    result = re.sub(r'^###\s+(.+)$', r'###### \1', result, flags=re.MULTILINE)
    result = re.sub(r'^##\s+(.+)$', r'###### \1', result, flags=re.MULTILINE)

    # 移除多余的空行（由于移除标题产生）
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()


def format_reviewer_json_to_markdown(raw_json: str, *, enable_length_protection: bool = True) -> str:
    """将 Reviewer JSON 输出转换为分层 Markdown 格式（飞书卡片专用）。

    与 output_extractor 中的版本不同，此函数专为飞书卡片优化：
    - 更紧凑的格式
    - 适合卡片阅读的层级结构
    - 可选的长度保护
    - 自动处理内部 Markdown 的标题层级

    Args:
        raw_json: Reviewer JSON 输出
        enable_length_protection: 是否启用长度保护

    Returns:
        格式化的 Markdown 文本
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return raw_json

    if not isinstance(data, dict):
        return raw_json

    lines: list[str] = []

    # 标题：状态（使用 #### 缩小字号）
    status = data.get("status", "unknown")
    status_icons = {
        "done": "✅",
        "continue": "🔄",
        "blocked": "🚫",
    }
    icon = status_icons.get(status, "❓")
    lines.append(f"#### {icon} Reviewer 评审")
    lines.append("")

    # 核心状态行
    confidence = data.get("confidence", 0)
    lines.append(f"**状态**: `{status}` | **置信度**: {confidence:.0%}")
    lines.append("")

    # 评审原因 (优先级最高)（使用 ##### 缩小字号）
    reason = data.get("reason", "")
    if reason:
        if enable_length_protection:
            if len(reason) > 2000:
                reason = reason[:2000] + "...(truncated)"
            reason = _remove_code_blocks(reason)
        lines.append("##### 评审原因")
        lines.append(reason)
        lines.append("")

    # 本轮总结
    round_summary = data.get("round_summary_markdown", "") or data.get("round_summary", "")
    if round_summary:
        if enable_length_protection:
            if len(round_summary) > 3000:
                round_summary = round_summary[:3000] + "...(truncated)"
            round_summary = _remove_code_blocks(round_summary)
        # 标准化内部 Markdown 的标题层级
        round_summary = _normalize_internal_markdown_headers(round_summary)
        lines.append("##### 本轮总结")
        lines.append(round_summary)
        lines.append("")

    # 完成证据
    completion = data.get("completion_summary_markdown", "") or data.get("completion_summary", "")
    if completion:
        if enable_length_protection:
            if len(completion) > 2500:
                completion = completion[:2500] + "...(truncated)"
            completion = _remove_code_blocks(completion)
        # 标准化内部 Markdown 的标题层级
        completion = _normalize_internal_markdown_headers(completion)
        lines.append("##### 完成证据")
        lines.append(completion)
        lines.append("")

    # 下一步行动（使用 ##### 缩小字号）
    next_action = data.get("next_action", "")
    if next_action:
        if enable_length_protection:
            if len(next_action) > 800:
                next_action = next_action[:800] + "...(truncated)"
        lines.append("##### 下一步行动")
        lines.append(next_action)

    return "\n".join(lines)


def format_planner_json_to_markdown(raw_json: str, *, enable_length_protection: bool = True) -> str:
    """将 Planner JSON 输出转换为分层 Markdown 格式（飞书卡片专用）。

    与 output_extractor 中的版本不同，此函数专为飞书卡片优化：
    - 表格展示工作流状态
    - 紧凑的摘要格式
    - 可选的长度保护

    Args:
        raw_json: Planner JSON 输出
        enable_length_protection: 是否启用长度保护

    Returns:
        格式化的 Markdown 文本
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return raw_json

    if not isinstance(data, dict):
        return raw_json

    lines: list[str] = []

    # 标题（使用 #### 缩小字号）
    lines.append("#### 📋 Planner 规划")
    lines.append("")

    # 经理总结（使用粗体而非标题）
    summary = data.get("summary", "")
    if summary:
        if enable_length_protection:
            if len(summary) > 1500:
                summary = summary[:1500] + "...(truncated)"
            summary = _remove_code_blocks(summary)
        lines.append("**经理总结**")
        lines.append(summary)
        lines.append("")

    # 工作流状态表格
    workstreams = data.get("workstreams", [])
    if workstreams:
        lines.append("**工作流状态**")
        lines.append("")
        lines.append("| 工作流 | 状态 |")
        lines.append("|--------|------|")
        for ws in workstreams:
            area = ws.get("area", "未知")
            status = ws.get("status", "unknown")
            status_label = {
                "done": "✅",
                "in_progress": "🔄",
                "todo": "⏳",
                "blocked": "🚫",
            }.get(status, status)
            lines.append(f"| {area} | {status_label} |")
        lines.append("")

        # 工作流详情（仅在有证据或下一步时显示）
        has_details = any(ws.get("evidence") or ws.get("next_step") for ws in workstreams)
        if has_details:
            lines.append("**详情**")
            for ws in workstreams:
                area = ws.get("area", "未知")
                evidence = ws.get("evidence", "")
                next_step = ws.get("next_step", "")
                if evidence:
                    if enable_length_protection:
                        if len(evidence) > 500:
                            evidence = evidence[:500] + "...(truncated)"
                        evidence = _remove_code_blocks(evidence)
                    lines.append(f"- **{area}**: {evidence}")
                if next_step:
                    if enable_length_protection:
                        if len(next_step) > 300:
                            next_step = next_step[:300] + "...(truncated)"
                    lines.append(f"  - ➡️ {next_step}")
            lines.append("")

    # 完成项和剩余项（合并显示）
    done_items = data.get("done_items", [])
    remaining_items = data.get("remaining_items", [])
    if done_items or remaining_items:
        if done_items:
            done_count = len(done_items)
            show_items = done_items[:5] if enable_length_protection and done_count > 5 else done_items
            lines.append(f"**✅ 已完成 ({done_count}项)**")
            for item in show_items:
                lines.append(f"- {item}")
            if enable_length_protection and done_count > 5:
                lines.append(f"- ... 还有{done_count - 5}项")
            lines.append("")

        if remaining_items:
            remaining_count = len(remaining_items)
            show_items = remaining_items[:5] if enable_length_protection and remaining_count > 5 else remaining_items
            lines.append(f"**⏳ 剩余 ({remaining_count}项)**")
            for item in show_items:
                lines.append(f"- {item}")
            if enable_length_protection and remaining_count > 5:
                lines.append(f"- ... 还有{remaining_count - 5}项")
            lines.append("")

    # 风险
    risks = data.get("risks", [])
    if risks:
        lines.append("**⚠️ 风险**")
        for risk in risks:
            lines.append(f"- {risk}")
        lines.append("")

    # 推荐下一步
    next_steps = data.get("next_steps", [])
    if next_steps:
        lines.append("**➡️ 推荐下一步**")
        for step in next_steps:
            lines.append(f"- {step}")
        lines.append("")

    # 建议的下一目标
    suggested_objective = data.get("suggested_next_objective", "")
    if suggested_objective:
        if enable_length_protection:
            if len(suggested_objective) > 500:
                suggested_objective = suggested_objective[:500] + "...(truncated)"
        lines.append("**🎯 建议下一目标**")
        lines.append(suggested_objective)

    return "\n".join(lines)


def format_planner_to_elements(raw_json: str, *, enable_length_protection: bool = True) -> list[dict[str, Any]]:
    """将 Planner JSON 输出转换为飞书卡片元素列表（使用 div + fields 模拟表格）。

    飞书卡片元素格式:
    - div + fields: 使用双列布局模拟表格效果
    - div + lark_md: 文本内容

    Args:
        raw_json: Planner JSON 输出
        enable_length_protection: 是否启用长度保护

    Returns:
        卡片元素列表，可直接用于 build_interactive_card
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return [{"tag": "div", "text": {"tag": "lark_md", "content": raw_json}}]

    if not isinstance(data, dict):
        return [{"tag": "div", "text": {"tag": "lark_md", "content": raw_json}}]

    elements: list[dict[str, Any]] = []

    # 1. 经理总结 (使用 div + lark_md)
    summary = data.get("summary", "")
    if summary:
        if enable_length_protection:
            if len(summary) > 1500:
                summary = summary[:1500] + "...(truncated)"
            summary = _remove_code_blocks(summary)
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**经理总结**\n{summary}"
            }
        })

    # 2. 工作流状态 (使用 div + fields 双列布局模拟表格)
    workstreams = data.get("workstreams", [])
    if workstreams:
        # 表格标题
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**工作流状态**"
            }
        })

        # 使用 fields 双列布局展示每个工作流
        for ws in workstreams:
            area = ws.get("area", "未知")
            status = ws.get("status", "unknown")
            evidence = ws.get("evidence", "")
            next_step = ws.get("next_step", "")

            status_icon = {
                "done": "✅",
                "in_progress": "🔄",
                "todo": "⏳",
                "blocked": "🚫",
            }.get(status, "❓")

            # 详情：证据和下一步
            detail_parts = []
            if evidence:
                if enable_length_protection and len(evidence) > 100:
                    evidence = evidence[:100] + "..."
                detail_parts.append(evidence)
            if next_step:
                if enable_length_protection and len(next_step) > 50:
                    next_step = next_step[:50] + "..."
                detail_parts.append(f"➡️ {next_step}")

            detail_text = "\\n".join(detail_parts) if detail_parts else "-"

            # 使用 fields 双列布局
            elements.append({
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**{area}**\n{status_icon}"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": detail_text
                        }
                    }
                ]
            })

    # 3. 完成项和剩余项 (使用 div + lark_md)
    done_items = data.get("done_items", [])
    remaining_items = data.get("remaining_items", [])

    if done_items or remaining_items:
        items_content = []

        if done_items:
            done_count = len(done_items)
            show_items = done_items[:5] if enable_length_protection and done_count > 5 else done_items
            items_content.append(f"**✅ 已完成 ({done_count}项)**")
            for item in show_items:
                items_content.append(f"- {item}")
            if enable_length_protection and done_count > 5:
                items_content.append(f"- ... 还有{done_count - 5}项")

        if remaining_items:
            remaining_count = len(remaining_items)
            show_items = remaining_items[:5] if enable_length_protection and remaining_count > 5 else remaining_items
            items_content.append(f"**⏳ 剩余 ({remaining_count}项)**")
            for item in show_items:
                items_content.append(f"- {item}")
            if enable_length_protection and remaining_count > 5:
                items_content.append(f"- ... 还有{remaining_count - 5}项")

        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n".join(items_content)
            }
        })

    # 4. 风险
    risks = data.get("risks", [])
    if risks:
        risk_lines = ["**⚠️ 风险**"]
        for risk in risks:
            risk_lines.append(f"- {risk}")
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n".join(risk_lines)
            }
        })

    # 5. 推荐下一步
    next_steps = data.get("next_steps", [])
    if next_steps:
        step_lines = ["**➡️ 推荐下一步**"]
        for step in next_steps:
            step_lines.append(f"- {step}")
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n".join(step_lines)
            }
        })

    # 6. 建议的下一目标
    suggested_objective = data.get("suggested_next_objective", "")
    if suggested_objective:
        if enable_length_protection:
            if len(suggested_objective) > 500:
                suggested_objective = suggested_objective[:500] + "...(truncated)"
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🎯 建议下一目标**\n{suggested_objective}"
            }
        })

    return elements


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


def build_interactive_card(
    title: str,
    content: str,
    template: str = "blue",
    actions: list[dict] | None = None,
    wide_screen_mode: bool = True,
) -> dict[str, Any]:
    """Build an interactive card message for Feishu.

    Args:
        title: Card header title text
        content: Main content text (supports Markdown-like formatting)
        template: Header color template (blue, green, red, yellow, purple, gray)
        actions: Optional list of action buttons
        wide_screen_mode: Enable wide screen mode (not used in schema 2.0)

    Returns:
        Interactive card message dict ready to be sent
    """
    elements: list[dict] = []

    # Add content as markdown element
    if content:
        elements.append({
            "tag": "markdown",
            "content": content
        })

    # Add action buttons if provided
    if actions:
        elements.append({
            "tag": "action",
            "actions": actions
        })

    # Use Feishu schema 2.0 format
    card_content = {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": title
            },
            "template": template
        },
        "body": {
            "elements": elements
        }
    }

    return card_content



@dataclass
class FeishuCommand:
    kind: str
    text: str


ErrorCallback = Callable[[str], None]
CommandCallback = Callable[[FeishuCommand], None]


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str
    chat_id: str
    events: set[str]
    receive_id_type: str = "chat_id"
    timeout_seconds: int = 10
    wide_screen_mode: bool = True
    card_template_id: str | None = None


class FeishuTokenManager:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        timeout_seconds: int,
        on_error: ErrorCallback | None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout_seconds = timeout_seconds
        self.on_error = on_error
        self._token: str | None = None
        self._expires_at = 0.0

    def get_token(self) -> str | None:
        now = time.time()
        if self._token and now < self._expires_at - 30:
            return self._token
        payload = json.dumps(
            {"app_id": self.app_id, "app_secret": self.app_secret},
            ensure_ascii=True,
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        parsed = _perform_json_request(
            req,
            timeout_seconds=self.timeout_seconds,
            on_error=self.on_error,
            label="feishu auth",
        )
        if not isinstance(parsed, dict):
            return None
        token = parsed.get("tenant_access_token")
        expire = parsed.get("expire")
        if not isinstance(token, str) or not token.strip():
            _emit(self.on_error, "feishu auth missing tenant_access_token")
            return None
        self._token = token.strip()
        self._expires_at = now + int(expire or 7200)
        return self._token


class FeishuNotifier:
    def __init__(self, config: FeishuConfig, on_error: ErrorCallback | None = None) -> None:
        self.config = config
        self.on_error = on_error
        self._tokens = FeishuTokenManager(
            app_id=config.app_id,
            app_secret=config.app_secret,
            timeout_seconds=config.timeout_seconds,
            on_error=on_error,
        )

    def notify_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type not in self.config.events:
            return

        # Always use interactive card format for all events
        card_result = format_feishu_event_card(event)
        if card_result:
            # Use formatted card for known event types
            title, content, template = card_result
            self.send_card_message(title=title, content=content, template=template)
        else:
            # For unknown event types, still send as card (not raw text)
            # Build a generic card from event data
            title = "ArgusBot 通知"
            content = f"**事件类型:** `{event_type}`\n\n"

            # Add event data as key-value pairs
            for key, value in event.items():
                if key != "type":
                    value_str = str(value)[:500]  # Truncate long values
                    content += f"**{key}:** {value_str}\n"

            self.send_card_message(
                title=title,
                content=content.strip(),
                template="blue"
            )

    def send_message(self, message: str, title: str = "ArgusBot 通知") -> bool:
        """Send a text message using Feishu schema 2.0 format.

        Uses schema 2.0 markdown element for proper Markdown rendering.
        This supports:
        - Headers: # H1, ## H2, ### H3
        - Bold: **text**
        - Italic: *text*
        - Lists: - item
        - Links: [text](url)
        - Code blocks: ```lang ... ```

        Before sending, validates and fixes common Markdown issues:
        - Unclosed code blocks
        - Missing newlines after headers
        - Incorrect list formatting

        Args:
            message: Message content (supports full Markdown syntax)
            title: Card header title (default: "ArgusBot 通知")

        Note: Message chunks are limited to FEISHU_CARD_MAX_BYTES (30 KB) to avoid
        error 230025 (message too long) and 230099 (card content failed).
        """
        token = self._tokens.get_token()
        if not token:
            return False

        # Validate and fix Markdown before sending
        fixed_message = validate_and_fix_markdown(message)

        ok = True
        for chunk in split_feishu_message(fixed_message, max_chunk_bytes=FEISHU_CARD_MAX_BYTES):
            # Build card content using Feishu schema 2.0 format with header
            card_content = {
                "schema": "2.0",
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title
                    },
                    "template": "blue"
                },
                "body": {
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": chunk
                        }
                    ]
                }
            }
            ok = (
                self._send_structured_message(
                    token=token,
                    msg_type="interactive",
                    content=card_content,
                )
                and ok
            )
        return ok

    def send_local_file(self, path: str | Path, *, caption: str = "") -> bool:
        token = self._tokens.get_token()
        if not token:
            return False
        file_path = Path(path)
        if not file_path.exists():
            _emit(self.on_error, f"feishu local file missing: {file_path}")
            return False
        try:
            file_bytes = file_path.read_bytes()
        except OSError as exc:
            _emit(self.on_error, f"feishu local file read failed: {exc}")
            return False

        suffix = file_path.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            image_key = self._upload_image(token=token, file_name=file_path.name, file_bytes=file_bytes)
            if not image_key:
                return False
            ok = self._send_structured_message(
                token=token,
                msg_type="image",
                content={"image_key": image_key},
            )
        else:
            is_video = suffix in _VIDEO_EXTENSIONS
            file_type = "mp4" if is_video and suffix == ".mp4" else "stream"
            file_key = self._upload_file(
                token=token,
                file_name=file_path.name,
                file_bytes=file_bytes,
                file_type=file_type,
            )
            if not file_key:
                return False
            ok = self._send_structured_message(
                token=token,
                msg_type="file",
                content={"file_key": file_key},
            )

        if caption.strip():
            ok = (
                self._send_structured_message(
                    token=token,
                    msg_type="text",
                    content={"text": caption[:1500]},
                )
                and ok
            )
        return ok


    def _send_structured_message(
        self,
        *,
        token: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> bool:
        """Send a structured message (interactive card, image, file, etc.).

        Handles Feishu API error codes:
        - 230025: Message too long - truncates content and retries
        - 230099: Card content failed - logs detailed error
        """
        body = json.dumps(
            {
                "receive_id": self.config.chat_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages"
            + f"?{urllib.parse.urlencode({'receive_id_type': self.config.receive_id_type})}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {token}",
            },
        )
        return (
            _perform_json_request(
                req,
                timeout_seconds=self.config.timeout_seconds,
                on_error=self.on_error,
                label="feishu send",
            )
            is not None
        )

    def _upload_image(self, *, token: str, file_name: str, file_bytes: bytes) -> str | None:
        parsed = self._post_multipart(
            token=token,
            url="https://open.feishu.cn/open-apis/im/v1/images",
            field_name="image",
            file_name=file_name,
            file_bytes=file_bytes,
            extra_form_fields={"image_type": "message"},
            label="feishu upload image",
        )
        if not isinstance(parsed, dict):
            return None
        data = parsed.get("data")
        if not isinstance(data, dict):
            _emit(self.on_error, "feishu upload image missing data")
            return None
        image_key = data.get("image_key")
        if not isinstance(image_key, str) or not image_key.strip():
            _emit(self.on_error, "feishu upload image missing image_key")
            return None
        return image_key.strip()

    def _upload_file(
        self,
        *,
        token: str,
        file_name: str,
        file_bytes: bytes,
        file_type: str,
    ) -> str | None:
        parsed = self._post_multipart(
            token=token,
            url="https://open.feishu.cn/open-apis/im/v1/files",
            field_name="file",
            file_name=file_name,
            file_bytes=file_bytes,
            extra_form_fields={"file_type": file_type, "file_name": file_name},
            label="feishu upload file",
        )
        if not isinstance(parsed, dict):
            return None
        data = parsed.get("data")
        if not isinstance(data, dict):
            _emit(self.on_error, "feishu upload file missing data")
            return None
        file_key = data.get("file_key")
        if not isinstance(file_key, str) or not file_key.strip():
            _emit(self.on_error, "feishu upload file missing file_key")
            return None
        return file_key.strip()

    def _post_multipart(
        self,
        *,
        token: str,
        url: str,
        field_name: str,
        file_name: str,
        file_bytes: bytes,
        extra_form_fields: dict[str, str] | None,
        label: str,
    ) -> dict[str, Any] | None:
        boundary = f"----argusbot{uuid4().hex}"
        body = bytearray()
        if extra_form_fields:
            for key, value in extra_form_fields.items():
                body.extend(_multipart_text_part(boundary, key, value))
        guessed_mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        body.extend(
            _multipart_file_part(
                boundary,
                field_name,
                file_name,
                file_bytes,
                content_type=guessed_mime_type,
            )
        )
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        req = urllib.request.Request(
            url,
            data=bytes(body),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        return _perform_json_request(
            req,
            timeout_seconds=self.config.timeout_seconds,
            on_error=self.on_error,
            label=label,
        )

    def close(self) -> None:
        return

    def send_card_message(
        self,
        title: str,
        content: str,
        template: str = "blue",
        actions: list[dict] | None = None,
    ) -> bool:
        """Send an interactive card message using Feishu schema 2.0 format.

        Uses schema 2.0 markdown element for proper Markdown rendering.
        This supports:
        - Headers: # H1, ## H2, ### H3
        - Bold: **text**
        - Italic: *text*
        - Lists: - item
        - Links: [text](url)
        - Code blocks: ```lang ... ```

        Args:
            title: Card header title
            content: Main content (supports full Markdown syntax)
            template: Header color (blue, green, red, yellow, purple, gray)
            actions: Optional list of button actions

        Returns:
            True if sent successfully, False otherwise
        """
        token = self._tokens.get_token()
        if not token:
            return False

        # Validate and fix Markdown before sending
        fixed_content = validate_and_fix_markdown(content)

        # Build elements array
        elements: list[dict] = []

        # Add content as markdown element (schema 2.0)
        if fixed_content:
            elements.append({
                "tag": "markdown",
                "content": fixed_content
            })

        # Add action buttons if provided
        if actions:
            elements.append({
                "tag": "action",
                "actions": actions
            })

        # Use Feishu schema 2.0 format with header at top level
        card_content = {
            "schema": "2.0",
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title
                },
                "template": template
            },
            "body": {
                "elements": elements
            }
        }

        return self._send_structured_message(
            token=token,
            msg_type="interactive",
            content=card_content,
        )


class FeishuCommandPoller:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        chat_id: str,
        on_command: CommandCallback,
        on_error: ErrorCallback | None = None,
        poll_interval_seconds: int = 2,
        plain_text_kind: str = "inject",
    ) -> None:
        self.chat_id = chat_id
        self.on_command = on_command
        self.on_error = on_error
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self.plain_text_kind = plain_text_kind
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tokens = FeishuTokenManager(
            app_id=app_id,
            app_secret=app_secret,
            timeout_seconds=20,
            on_error=on_error,
        )
        self._last_message_id: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            for item in self._fetch_messages():
                message_id = str(item.get("message_id") or "").strip()
                if message_id:
                    self._last_message_id = message_id
                if is_feishu_self_message(item):
                    continue
                if str(item.get("msg_type") or "") != "text":
                    continue
                text = extract_feishu_text(item)
                if not text:
                    continue
                parsed = parse_feishu_command_text(text=text, plain_text_kind=self.plain_text_kind)
                if parsed is None:
                    continue
                try:
                    self.on_command(parsed)
                except Exception as exc:
                    _emit(self.on_error, f"feishu command handler error: {exc}")
            self._stop_event.wait(self.poll_interval_seconds)

    def _fetch_messages(self) -> list[dict[str, Any]]:
        token = self._tokens.get_token()
        if not token:
            return []
        params = {
            "container_id_type": "chat",
            "container_id": self.chat_id,
            "sort_type": "ByCreateTimeDesc",
            "page_size": "50",
        }
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?" + urllib.parse.urlencode(params),
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        parsed = _perform_json_request(req, timeout_seconds=20, on_error=self.on_error, label="feishu list")
        if not isinstance(parsed, dict):
            return []
        data = parsed.get("data")
        if not isinstance(data, dict):
            return []
        items = data.get("items")
        if not isinstance(items, list):
            return []
        rows = [item for item in items if isinstance(item, dict)]
        if not self._last_message_id:
            if rows:
                latest_id = str(rows[0].get("message_id") or "").strip()
                if latest_id:
                    self._last_message_id = latest_id
            return []
        fresh: list[dict[str, Any]] = []
        for item in rows:
            if str(item.get("message_id") or "").strip() == self._last_message_id:
                break
            fresh.append(item)
        return list(reversed(fresh))


def parse_feishu_command_text(*, text: str, plain_text_kind: str) -> FeishuCommand | None:
    normalized = normalize_command_prefix(text.strip())
    normalized = strip_leading_feishu_mentions(normalized)
    if not normalized:
        return None
    parsed = parse_command_text(
        text=normalized,
        plain_text_as_inject=(plain_text_kind == "inject"),
    )
    if parsed is not None:
        return FeishuCommand(kind=parsed.kind, text=parsed.text)
    mode_selection = parse_mode_selection_text(normalized)
    if mode_selection is not None:
        return FeishuCommand(kind=mode_selection.kind, text=mode_selection.text)
    if normalized.startswith("/"):
        return None
    if plain_text_kind == "run":
        return FeishuCommand(kind="run", text=normalized)
    if plain_text_kind == "inject":
        return FeishuCommand(kind="inject", text=normalized)
    return None


def strip_leading_feishu_mentions(text: str) -> str:
    normalized = normalize_command_prefix((text or "").strip())
    if not normalized:
        return ""
    previous = None
    current = normalized
    while current and current != previous:
        previous = current
        current = _FEISHU_MENTION_PREFIX.sub("", current).lstrip()
    return current


def extract_feishu_text(item: dict[str, Any]) -> str:
    body = item.get("body")
    if not isinstance(body, dict):
        return ""
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        return ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return ""
    text = parsed.get("text")
    return text.strip() if isinstance(text, str) else ""


def is_feishu_self_message(item: dict[str, Any]) -> bool:
    sender = item.get("sender")
    if isinstance(sender, dict):
        sender_type = str(sender.get("sender_type") or "").strip().lower()
        if sender_type in {"app", "bot"}:
            return True
    text = extract_feishu_text(item)
    lowered = text.lower()
    return lowered.startswith("[daemon]") or lowered.startswith("[autoloop]") or lowered.startswith("[btw]")


def format_feishu_event_message(event: dict[str, Any]) -> str:
    """Format event message as text (legacy, for backward compatibility)."""
    return format_event_message(event)


def format_feishu_event_card(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Format event as interactive card (title, content, template_color).

    Returns:
        Tuple of (title, content, template) or None if event should not produce a card

    Event types handled:
        - loop.started: Blue card with objective
        - loop.completed: Color based on exit status
        - round.started: Blue card with round info
        - round.main.completed: Blue card with round completion
        - round.review.completed: Color based on status (green=done, yellow=continue, red=blocked)
        - round.checks.completed: Color based on check results
        - reviewer.output: Reviewer 输出，提取 Markdown 字段
        - planner.output: Planner 输出，提取 Markdown 字段
        - plan.completed: Planner 完成事件
    """
    event_type = str(event.get("type", ""))

    if event_type == "loop.started":
        objective = event.get("objective", "Unknown task")
        return (
            "任务启动",
            f"**目标:** {objective}\n\nArgusBot 已开始执行任务...",
            FEISHU_COLOR_BLUE
        )

    if event_type == "round.started":
        round_num = event.get("round_index", 0) + 1
        return (
            "新一轮执行",
            f"**第 {round_num} 轮**\n\n开始执行任务...",
            FEISHU_COLOR_BLUE
        )

    if event_type == "round.main.completed":
        round_num = event.get("round_index", 0) + 1
        turn_completed = event.get("main_turn_completed", 0)
        return (
            "本轮执行完成",
            f"**第 {round_num} 轮**\n\n完成 {turn_completed} 步操作",
            FEISHU_COLOR_BLUE
        )

    if event_type == "round.review.completed":
        review = event.get("review", {})
        status = str(review.get("status", "unknown"))
        reason = review.get("reason", "")
        round_num = event.get("round_index", 0) + 1

        status_map = {
            "done": ("审核通过", FEISHU_COLOR_GREEN),
            "continue": ("继续执行", FEISHU_COLOR_YELLOW),
            "blocked": ("执行受阻", FEISHU_COLOR_RED),
        }
        title, color = status_map.get(status, ("审核状态", FEISHU_COLOR_BLUE))

        content = f"**第 {round_num} 轮审核**\n\n"
        content += f"**状态:** {status}\n"
        if reason:
            content += f"\n{reason}"

        return title, content, color

    if event_type == "round.checks.completed":
        round_num = event.get("round_index", 0) + 1
        checks = event.get("checks", [])
        all_passed = all(c.get("passed", False) for c in checks)

        content = f"**第 {round_num} 轮验收检查**\n\n"
        for check in checks:
            cmd = check.get("command", "")[:100]
            passed = check.get("passed", False)
            status_icon = "✅" if passed else "❌"
            content += f"{status_icon} `{cmd}`\n"

        title = "验收检查通过" if all_passed else "验收检查失败"
        color = FEISHU_COLOR_GREEN if all_passed else FEISHU_COLOR_RED
        return (title, content, color)

    if event_type == "reviewer.output":
        # 处理 Reviewer JSON 输出，提取并格式化为结构化 Markdown
        raw_output = event.get("raw_output", "")
        if raw_output:
            # 使用飞书专用的 JSON 转 Markdown 处理函数
            formatted = format_reviewer_json_to_markdown(raw_output, enable_length_protection=FEISHU_OUTPUT_LENGTH_PROTECTION)
            return ("🔍 Reviewer 评审报告", formatted, FEISHU_COLOR_YELLOW)
        return None

    if event_type == "planner.output":
        # 处理 Planner JSON 输出，提取并格式化为结构化 Markdown
        raw_output = event.get("raw_output", "")
        if raw_output:
            # 使用飞书专用的 JSON 转 Markdown 处理函数
            formatted = format_planner_json_to_markdown(raw_output, enable_length_protection=FEISHU_OUTPUT_LENGTH_PROTECTION)
            return ("📋 Planner 规划报告", formatted, FEISHU_COLOR_YELLOW)
        return None

    if event_type == "plan.completed":
        # 处理 Planner 完成事件，包含原始 JSON 输出
        raw_output = event.get("raw_output", "")
        if raw_output:
            # 使用飞书专用的 JSON 转 Markdown 处理函数
            formatted = format_planner_json_to_markdown(raw_output, enable_length_protection=FEISHU_OUTPUT_LENGTH_PROTECTION)
            return ("📋 Planner 规划报告", formatted, FEISHU_COLOR_YELLOW)
        # 如果没有 raw_output，使用传统格式
        summary = str(event.get("main_instruction", ""))[:400]
        return ("📋 Planner 更新", summary, FEISHU_COLOR_YELLOW)

    if event_type == "loop.completed":
        rounds = event.get("rounds", [])
        total_rounds = len(rounds)
        exit_code = event.get("exit_code", 0)
        objective = event.get("objective", "任务")
        stop_reason = event.get("stop_reason", "")

        # 根据结束原因选择不同颜色
        # - FEISHU_COLOR_GREEN: 成功完成 (exit_code=0 且通过检查)
        # - FEISHU_COLOR_RED: 失败/受阻
        # - FEISHU_COLOR_YELLOW: 达到最大轮次
        # - FEISHU_COLOR_ORANGE: 被用户停止
        color = FEISHU_COLOR_GREEN
        status_text = "成功"

        if exit_code != 0:
            color = FEISHU_COLOR_RED
            status_text = "失败"
        elif "blocked" in str(stop_reason).lower():
            color = FEISHU_COLOR_RED
            status_text = "受阻"
        elif "max rounds" in str(stop_reason).lower():
            color = FEISHU_COLOR_YELLOW
            status_text = "达到最大轮次"
        elif "stopped" in str(stop_reason).lower() or "operator" in str(stop_reason).lower():
            color = FEISHU_COLOR_ORANGE
            status_text = "已停止"

        content = f"**任务{status_text}**\n\n"
        content += f"**目标:** {objective}\n"
        content += f"**总轮数:** {total_rounds}\n"
        content += f"**状态码:** {exit_code}\n"
        if stop_reason:
            content += f"**原因:** {stop_reason[:200]}"

        return "任务完成", content, color

    # Default fallback - still return a card format for unknown events
    return None


def split_feishu_message(
    message: str,
    *,
    max_chunk_chars: int = 1500,
    max_chunk_bytes: int | None = None,
) -> list[str]:
    """Split message into chunks that fit within Feishu API limits.

    Args:
        message: Message text to split
        max_chunk_chars: Maximum characters per chunk (default: 1500)
        max_chunk_bytes: Maximum bytes per chunk (default: None, use char limit only)
                        When set, ensures JSON-encoded message fits within limit

    Returns:
        List of message chunks with [n/total] prefix for multi-chunk messages

    Note:
        When max_chunk_bytes is set, the function accounts for JSON encoding overhead
        (escape sequences like \\n, unicode, etc.) to ensure the final request body
        stays within Feishu's 30 KB card message limit.
    """
    text = (message or "").strip()
    if not text:
        return []

    # Determine effective limit (bytes-aware)
    effective_limit = max_chunk_chars
    if max_chunk_bytes is not None:
        # Reserve ~30% space for JSON overhead when encoding
        # JSON escapes: \n → \\n, unicode → \\uXXXX, quotes → \\"
        estimated_overhead_factor = 1.3
        byte_limit = int(max_chunk_bytes / estimated_overhead_factor)
        # Use the smaller of char limit or byte-derived limit
        # (UTF-8: 1 char ≈ 1-3 bytes for common text)
        effective_limit = min(max_chunk_chars, byte_limit)

    if len(text.encode('utf-8')) <= (max_chunk_bytes or float('inf')):
        # Entire message fits within byte limit
        if len(text) <= effective_limit:
            return [text]
        # Message fits in bytes but exceeds char limit - use char-based splitting

    chunks: list[str] = []
    remaining = text
    while remaining:
        current_limit = effective_limit
        if max_chunk_bytes is not None:
            # Adjust limit based on actual byte size of current segment
            test_segment = remaining[:current_limit]
            test_bytes = len(_json_encode_for_feishu(test_segment).encode('utf-8'))
            # If exceeds byte limit, reduce character count
            while test_bytes > max_chunk_bytes and current_limit > 50:
                current_limit -= 50
                test_segment = remaining[:current_limit]
                test_bytes = len(_json_encode_for_feishu(test_segment).encode('utf-8'))

        if len(remaining) <= current_limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, current_limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, current_limit)
        if cut <= 0:
            cut = current_limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    total = len(chunks)
    if total <= 1:
        return chunks
    width = len(str(total))
    return [f"[{index + 1}/{total:0{width}d}]\n{chunk}" for index, chunk in enumerate(chunks)]


def _json_encode_for_feishu(text: str) -> str:
    """JSON encode text as Feishu API would receive it (for size estimation)."""
    return json.dumps(text, ensure_ascii=False)


def _perform_json_request(
    req: urllib.request.Request,
    *,
    timeout_seconds: int,
    on_error: ErrorCallback | None,
    label: str,
    max_retries: int = 2,
) -> dict[str, Any] | None:
    """Perform HTTP request with optional retry for transient errors.

    Retries on SSL/EOF errors and connection reset errors that are often transient.
    """
    attempt = 0
    last_error: Exception | None = None

    while attempt <= max_retries:
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                body = ""
            _emit(on_error, f"{label} http {exc.code}: {body[:300]}")
            return None
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            # Retry on SSL/EOF errors
            if "UNEXPECTED_EOF" in reason or "EOF occurred" in reason or "connection reset" in reason.lower():
                last_error = exc
                attempt += 1
                if attempt <= max_retries:
                    time.sleep(0.5 * attempt)  # Exponential backoff
                    continue
            _emit(on_error, f"{label} network error: {exc}")
            return None
        except (TimeoutError, socket.timeout) as exc:
            _emit(on_error, f"{label} timeout: {exc}")
            return None
        except OSError as exc:
            reason = str(exc)
            # Retry on connection reset errors
            if "Connection reset" in reason or "Broken pipe" in reason:
                last_error = exc
                attempt += 1
                if attempt <= max_retries:
                    time.sleep(0.5 * attempt)
                    continue
            _emit(on_error, f"{label} os error: {exc}")
            return None

        # Success - parse and return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            _emit(on_error, f"{label} non-JSON response")
            return None
        code = parsed.get("code", 0)
        if code not in {0, "0", None}:
            # Handle Feishu-specific error codes
            code_str = str(code)
            msg = parsed.get('msg', '')

            if code_str == str(FEISHU_ERROR_CODE_MESSAGE_TOO_LONG):
                # 230025: Message too long - caller should truncate and retry
                _emit(on_error, f"{label} message too long (code={code}): {msg}")
            elif code_str == str(FEISHU_ERROR_CODE_CARD_CONTENT_FAILED):
                # 230099: Card content failed - may contain markdown syntax issues
                _emit(on_error, f"{label} card content failed (code={code}): {msg}")
            else:
                _emit(on_error, f"{label} api error: code={code} msg={msg}")
            return None
        return parsed

    # All retries exhausted
    _emit(on_error, f"{label} failed after {max_retries} retries: {last_error}")
    return None


def _emit(on_error: ErrorCallback | None, message: str) -> None:
    if on_error is not None:
        on_error(message)


def _multipart_text_part(boundary: str, field: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _multipart_file_part(
    boundary: str,
    field: str,
    file_name: str,
    file_bytes: bytes,
    *,
    content_type: str,
) -> bytes:
    safe_name = file_name.replace('"', "_")
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{safe_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + file_bytes + b"\r\n"
