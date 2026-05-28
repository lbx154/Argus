"""Markdown 检查工具 - 整合 markdownlint 规则和自定义飞书验证。

本模块提供 Markdown 格式检查功能，支持：
1. 调用 markdownlint (mdl) 进行标准规则检查
2. 使用自定义验证器进行飞书特定检查
3. 生成结构化报告
4. 验证和修复 Markdown 语法（原 feishu_markdown_validator.py 已合并至此）
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# =============================================================================
# 基础 Markdown 验证和修复函数（原 feishu_markdown_validator.py）
# =============================================================================

def validate_and_fix_markdown(text: str) -> str:
    """验证并修复 Markdown 语法。

    修复以下问题：
    1. 未闭合的代码块（```）
    2. 标题后缺少换行
    3. 列表项格式不正确
    4. 换行符不完整

    Args:
        text: 原始 Markdown 文本

    Returns:
        修复后的 Markdown 文本
    """
    if not text:
        return text

    result = text

    # 1. 修复未闭合的代码块
    result = fix_unclosed_code_blocks(result)

    # 2. 确保标题后有足够换行
    result = ensure_headers_have_newlines(result)

    # 3. 确保列表格式正确
    result = fix_list_format(result)

    # 4. 确保代码块前后有换行
    result = ensure_code_blocks_have_newlines(result)

    # 5. 移除多余的空行（连续 3 个以上空行缩减为 2 个）
    result = re.sub(r'\n{4,}', '\n\n\n', result)

    return result


def fix_unclosed_code_blocks(text: str) -> str:
    """修复未闭合的代码块。

    检测并添加缺失的闭合标记 ```。

    Args:
        text: Markdown 文本

    Returns:
        修复后的文本
    """
    lines = text.split('\n')
    result_lines: list[str] = []
    in_code_block = False
    code_block_start_line = -1

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 检测代码块开始/结束标记
        if stripped.startswith('```'):
            if in_code_block:
                # 闭合代码块
                in_code_block = False
                result_lines.append(line)
            else:
                # 开始代码块
                in_code_block = True
                code_block_start_line = i
                result_lines.append(line)
        else:
            result_lines.append(line)

    # 如果代码块未闭合，添加闭合标记
    if in_code_block:
        result_lines.append('```')

    return '\n'.join(result_lines)


def check_unclosed_blocks(text: str) -> list[str]:
    """检测未闭合的代码块。

    Args:
        text: Markdown 文本

    Returns:
        问题描述列表
    """
    issues: list[str] = []
    lines = text.split('\n')
    in_code_block = False
    code_block_start_line = -1

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith('```'):
            if in_code_block:
                in_code_block = False
            else:
                in_code_block = True
                code_block_start_line = i

    if in_code_block:
        issues.append(f"代码块在第 {code_block_start_line + 1} 行开始但未闭合")

    return issues


def ensure_headers_have_newlines(text: str) -> str:
    """确保标题后有足够换行。

    Markdown 标题（#、##、### 等）后应该有空行，
    否则后续内容可能不会被正确识别为新段落。

    Args:
        text: Markdown 文本

    Returns:
        修复后的文本
    """
    lines = text.split('\n')
    result_lines: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        result_lines.append(line)

        # 检查是否是标题行
        if re.match(r'^#{1,6}\s+.*$', line.strip()):
            # 检查下一行是否是空行
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                # 如果下一行不是空行且不是另一个标题，添加空行
                if next_line.strip() and not re.match(r'^#{1,6}\s+.*$', next_line.strip()):
                    result_lines.append('')

        i += 1

    return '\n'.join(result_lines)


def fix_list_format(text: str) -> str:
    """修复列表格式。

    确保列表项：
    1. 前面有空行（除非在开头）
    2. 使用正确的标记（-、*、+ 或数字.）
    3. 列表项之间有适当的间距

    Args:
        text: Markdown 文本

    Returns:
        修复后的文本
    """
    lines = text.split('\n')
    result_lines: list[str] = []
    in_list = False
    prev_was_list_item = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        is_list_item = bool(re.match(r'^(\s*)([-*+]|\d+\.)\s+', stripped))

        if is_list_item:
            # 如果列表项前不是空行且不是列表继续，添加空行
            if not in_list and result_lines and result_lines[-1].strip():
                result_lines.append('')

            in_list = True
            result_lines.append(line)
            prev_was_list_item = True
        else:
            if in_list and not stripped:
                # 空行可能表示列表结束
                in_list = False
            elif in_list and stripped:
                # 非空非列表行，列表结束
                in_list = False
            result_lines.append(line)
            prev_was_list_item = False

    return '\n'.join(result_lines)


def ensure_code_blocks_have_newlines(text: str) -> str:
    """确保代码块前后有换行。

    代码块标记（```）前后应该有空行，
    以确保正确渲染。

    Args:
        text: Markdown 文本

    Returns:
        修复后的文本
    """
    lines = text.split('\n')
    result_lines: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith('```'):
            # 代码块标记前添加空行（如果不是开头且前一行不是空行）
            if result_lines and result_lines[-1].strip():
                result_lines.append('')

            result_lines.append(line)

            # 检查是否是闭合标记，如果是，后面添加空行
            if len(result_lines) > 1:
                # 检查是否是闭合标记（前面有代码内容）
                prev_lines = [l for l in result_lines[:-1] if not l.strip().startswith('```')]
                if prev_lines and any(l.strip() for l in prev_lines):
                    # 这是一个闭合标记，检查是否需要添加空行
                    pass  # 空行会在后续处理中自动添加
        else:
            result_lines.append(line)

    return '\n'.join(result_lines)


def truncate_markdown_safely(text: str, max_chars: int) -> str:
    """安全地截断 Markdown 文本。

    避免在以下位置截断：
    1. 代码块内部
    2. 标题中间
    3. 列表项中间

    Args:
        text: Markdown 文本
        max_chars: 最大字符数

    Returns:
        截断后的文本
    """
    if len(text) <= max_chars:
        return text

    # 首先检查截断点是否在代码块内
    truncated = text[:max_chars]
    code_block_count = truncated.count('```')

    # 如果在代码块内截断，找到下一个代码块结束标记
    if code_block_count % 2 == 1:
        # 在代码块内，需要找到闭合标记或添加到末尾
        remaining = text[max_chars:]
        end_marker_pos = remaining.find('```')
        if end_marker_pos != -1:
            # 包含到闭合标记
            return truncated + remaining[:end_marker_pos + 3] + '\n\n...（内容被截断）'
        else:
            # 没有闭合标记，添加一个
            return truncated + '\n```\n\n...（内容被截断）'

    # 不在代码块内，尝试在段落边界截断
    # 优先在空行处截断
    last_double_newline = truncated.rfind('\n\n')
    if last_double_newline > max_chars * 0.7:  # 至少在 70% 位置之后
        return truncated[:last_double_newline] + '\n\n...（内容被截断）'

    # 其次在单换行处截断
    last_newline = truncated.rfind('\n')
    if last_newline > max_chars * 0.7:
        return truncated[:last_newline] + '\n\n...（内容被截断）'

    # 最后在单词边界截断
    last_space = truncated.rfind(' ')
    if last_space > max_chars * 0.7:
        return truncated[:last_space] + '\n\n...（内容被截断）'

    # 无法找到合适位置，直接截断
    return truncated + '\n\n...（内容被截断）'


def validate_markdown_for_feishu(text: str) -> tuple[bool, list[str]]:
    """验证 Markdown 是否适合飞书渲染。

    飞书的 markdown 组件有一些特殊要求：
    1. 代码块必须闭合
    2. 标题格式必须正确
    3. 换行符必须保留

    Args:
        text: Markdown 文本

    Returns:
        (是否有效，问题列表)
    """
    issues: list[str] = []

    # 检查未闭合代码块
    unclosed = check_unclosed_blocks(text)
    issues.extend(unclosed)

    # 检查是否有内容（飞书需要非空内容）
    if not text.strip():
        issues.append("消息内容为空")

    # 检查是否有不支持的 Markdown 语法
    # 飞书不支持表格、脚注等
    if re.search(r'^\|.*\|$', text, re.MULTILINE):
        issues.append("飞书不支持表格语法")

    if re.search(r'\[\^[^\]]+\]', text):
        issues.append("飞书不支持脚注")

    return (len(issues) == 0, issues)


def check_markdown_structure(text: str) -> dict[str, Any]:
    """检查 Markdown 结构完整性。

    Args:
        text: Markdown 文本

    Returns:
        检查结果字典，包含：
        - valid: 是否有效
        - issues: 问题列表
        - fixes_applied: 已应用的修复
    """
    issues: list[str] = []
    fixes_applied: list[str] = []

    # 检查未闭合代码块
    unclosed = check_unclosed_blocks(text)
    issues.extend(unclosed)

    # 检查标题格式
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if re.match(r'^#{1,6}\s+.*$', line.strip()):
            if i + 1 < len(lines) and lines[i + 1].strip():
                if not re.match(r'^#{1,6}\s+.*$', lines[i + 1].strip()):
                    issues.append(f"第 {i + 1} 行标题后缺少空行")

    # 检查连续空行过多
    if '\n\n\n\n' in text:
        issues.append("存在连续 4 个以上空行")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "fixes_applied": fixes_applied
    }


@dataclass
class MarkdownIssue:
    """Markdown 问题描述。"""
    rule_id: str
    description: str
    line_number: int | None = None
    severity: str = "warning"  # "error" | "warning" | "info"
    suggestion: str | None = None


@dataclass
class MarkdownCheckResult:
    """Markdown 检查结果。"""
    is_valid: bool
    issues: list[MarkdownIssue]
    fixed_content: str | None = None
    feishu_ready: bool = False


def check_with_markdownlint(
    text: str,
    style_file: Path | None = None,
    mdl_path: str = "mdl",
) -> list[MarkdownIssue]:
    """使用 markdownlint (mdl) 检查 Markdown 内容。

    Args:
        text: 要检查的 Markdown 文本
        style_file: 可选的风格配置文件路径
        mdl_path: mdl 可执行文件路径

    Returns:
        问题列表
    """
    issues: list[MarkdownIssue] = []

    try:
        # 准备 mdl 命令
        cmd = [mdl_path, "--json"]

        if style_file and style_file.exists():
            cmd.extend(["--style", str(style_file)])

        # 执行 mdl
        result = subprocess.run(
            cmd,
            input=text,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 解析 JSON 输出
        if result.stdout.strip():
            try:
                violations = json.loads(result.stdout)
                for violation in violations:
                    issue = MarkdownIssue(
                        rule_id=violation.get("rule", "UNKNOWN"),
                        description=violation.get("description", ""),
                        line_number=violation.get("line"),
                        severity="warning",
                    )
                    issues.append(issue)
            except json.JSONDecodeError:
                # 如果解析失败，尝试解析文本输出
                for line in result.stdout.strip().split("\n"):
                    if line:
                        issue = _parse_mdl_text_line(line)
                        if issue:
                            issues.append(issue)

    except subprocess.TimeoutExpired:
        issues.append(MarkdownIssue(
            rule_id="SYSTEM",
            description="markdownlint 检查超时",
            severity="error",
        ))
    except FileNotFoundError:
        # mdl 未安装，返回提示信息
        issues.append(MarkdownIssue(
            rule_id="SYSTEM",
            description="未找到 mdl 工具，请运行 'gem install mdl' 安装",
            severity="info",
            suggestion="或使用 pip install markdownlint-cli 安装 Node.js 版本",
        ))
    except Exception as exc:
        issues.append(MarkdownIssue(
            rule_id="SYSTEM",
            description=f"markdownlint 检查失败：{exc}",
            severity="error",
        ))

    return issues


def _parse_mdl_text_line(line: str) -> MarkdownIssue | None:
    """解析 mdl 文本输出一行。

    格式示例：README.md:1: MD013 Line length
    """
    # 尝试匹配标准格式
    import re
    match = re.match(r"^([^:]+):(\d+):\s*(MD\d+)\s*(.*)$", line)
    if match:
        return MarkdownIssue(
            rule_id=match.group(3),
            description=match.group(4).strip(),
            line_number=int(match.group(2)),
            severity="warning",
        )
    return None


def check_for_feishu(text: str) -> list[MarkdownIssue]:
    """针对飞书渲染的 Markdown 检查。

    检查飞书特定的 Markdown 兼容性问题：
    - 未闭合的代码块
    - 不支持的表格语法
    - 不支持的脚注
    - 标题后缺少换行

    Args:
        text: Markdown 文本

    Returns:
        问题列表
    """
    issues: list[MarkdownIssue] = []

    # 检查未闭合代码块
    unclosed = check_unclosed_blocks(text)
    for desc in unclosed:
        issues.append(MarkdownIssue(
            rule_id="FEISHU-MD001",
            description=desc,
            severity="error",
            suggestion="添加缺失的 ``` 闭合标记",
        ))

    # 检查飞书不支持的语法
    valid, feishu_issues = validate_markdown_for_feishu(text)
    for desc in feishu_issues:
        if "表格" in desc:
            issues.append(MarkdownIssue(
                rule_id="FEISHU-MD002",
                description=f"飞书不支持：{desc}",
                severity="warning",
                suggestion="使用代码块或纯文本展示表格内容",
            ))
        elif "脚注" in desc:
            issues.append(MarkdownIssue(
                rule_id="FEISHU-MD003",
                description=f"飞书不支持：{desc}",
                severity="warning",
                suggestion="使用普通文本替代脚注",
            ))
        elif "空" in desc:
            issues.append(MarkdownIssue(
                rule_id="FEISHU-MD004",
                description=desc,
                severity="error",
            ))
        else:
            issues.append(MarkdownIssue(
                rule_id="FEISHU-UNK",
                description=desc,
                severity="warning",
            ))

    # 检查标题后换行
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            if i + 1 < len(lines) and lines[i + 1].strip():
                if not lines[i + 1].strip().startswith("#"):
                    issues.append(MarkdownIssue(
                        rule_id="FEISHU-MD005",
                        description=f"第 {i + 1} 行标题后缺少空行",
                        line_number=i + 1,
                        severity="warning",
                        suggestion="在标题后添加一个空行",
                    ))

    return issues


def check_markdown(
    text: str,
    style_file: Path | None = None,
    check_feishu: bool = True,
    auto_fix: bool = False,
) -> MarkdownCheckResult:
    """完整的 Markdown 检查流程。

    Args:
        text: Markdown 文本
        style_file: markdownlint 风格文件路径
        check_feishu: 是否进行飞书兼容性检查
        auto_fix: 是否自动修复可修复的问题

    Returns:
        检查结果
    """
    all_issues: list[MarkdownIssue] = []

    # 1. markdownlint 标准检查
    mdl_issues = check_with_markdownlint(text, style_file)
    all_issues.extend(mdl_issues)

    # 2. 飞书特定检查
    if check_feishu:
        feishu_issues = check_for_feishu(text)
        all_issues.extend(feishu_issues)

    # 3. 判断是否有效
    is_valid = len(all_issues) == 0
    has_errors = any(i.severity == "error" for i in all_issues)

    # 4. 自动修复（如果请求）
    fixed_content = None
    if auto_fix and not has_errors:
        fixed_content = validate_and_fix_markdown(text)

    # 5. 飞书就绪状态
    feishu_ready = not has_errors and not any(
        "FEISHU" in i.rule_id and i.severity == "error"
        for i in all_issues
    )

    return MarkdownCheckResult(
        is_valid=is_valid or not has_errors,
        issues=all_issues,
        fixed_content=fixed_content,
        feishu_ready=feishu_ready,
    )


def format_check_report(
    result: MarkdownCheckResult,
    format_type: str = "text",
) -> str:
    """格式化检查报告。

    Args:
        result: 检查结果
        format_type: 输出格式 (text/json/markdown)

    Returns:
        格式化的报告
    """
    if format_type == "json":
        return json.dumps(
            {
                "is_valid": result.is_valid,
                "feishu_ready": result.feishu_ready,
                "issues": [
                    {
                        "rule_id": i.rule_id,
                        "description": i.description,
                        "line": i.line_number,
                        "severity": i.severity,
                        "suggestion": i.suggestion,
                    }
                    for i in result.issues
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    if format_type == "markdown":
        lines = ["# Markdown 检查报告", ""]
        lines.append(f"**状态:** {'通过' if result.is_valid else '未通过'}")
        lines.append(f"**飞书就绪:** {'是' if result.feishu_ready else '否'}")
        lines.append(f"**问题数:** {len(result.issues)}")
        lines.append("")

        if result.issues:
            lines.append("## 问题列表")
            lines.append("")
            for issue in result.issues:
                severity_icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(
                    issue.severity, "•"
                )
                line_info = f" (第 {issue.line_number} 行)" if issue.line_number else ""
                lines.append(
                    f"- {severity_icon} **{issue.rule_id}**{line_info}: {issue.description}"
                )
                if issue.suggestion:
                    lines.append(f"  - 建议：{issue.suggestion}")
            lines.append("")

        return "\n".join(lines)

    # 默认文本格式
    lines = []
    status = "通过" if result.is_valid else "未通过"
    lines.append(f"Markdown 检查：{status}")
    lines.append(f"飞书就绪：{'是' if result.feishu_ready else '否'}")
    lines.append(f"问题数：{len(result.issues)}")

    if result.issues:
        lines.append("")
        lines.append("问题详情:")
        for issue in result.issues:
            severity = {"error": "[错误]", "warning": "[警告]", "info": "[提示]"}.get(
                issue.severity, ""
            )
            line_info = f" (第 {issue.line_number} 行)" if issue.line_number else ""
            lines.append(f"  {severity} {issue.rule_id}{line_info}: {issue.description}")
            if issue.suggestion:
                lines.append(f"    建议：{issue.suggestion}")

    return "\n".join(lines)


def quick_fix_for_feishu(text: str) -> str:
    """快速修复飞书 Markdown 问题。

    这是 validate_and_fix_markdown 的增强版，
    专门针对飞书渲染优化。

    Args:
        text: 原始 Markdown 文本

    Returns:
        修复后的文本
    """
    # 使用基础验证器修复
    result = validate_and_fix_markdown(text)

    # 额外的飞书优化
    lines = result.split("\n")
    optimized_lines: list[str] = []

    for i, line in enumerate(lines):
        # 移除行尾空格
        line = line.rstrip()

        # 确保标题格式正确
        stripped = line.strip()
        if stripped.startswith("#"):
            # 确保 # 后有空格
            if not re.match(r"^#+\s", stripped) and len(stripped) > 1:
                line = re.sub(r"^(#+)", r"\1 ", stripped)

        optimized_lines.append(line)

    # 移除开头的空行
    while optimized_lines and not optimized_lines[0].strip():
        optimized_lines.pop(0)

    # 确保以单个换行结尾
    result = "\n".join(optimized_lines)
    if result and not result.endswith("\n"):
        result += "\n"

    return result


def check_file(
    file_path: Path | str,
    style_file: Path | None = None,
    check_feishu: bool = True,
) -> MarkdownCheckResult:
    """检查 Markdown 文件。

    Args:
        file_path: 文件路径
        style_file: markdownlint 风格文件路径
        check_feishu: 是否进行飞书兼容性检查

    Returns:
        检查结果
    """
    path = Path(file_path)
    if not path.exists():
        return MarkdownCheckResult(
            is_valid=False,
            issues=[
                MarkdownIssue(
                    rule_id="SYSTEM",
                    description=f"文件不存在：{file_path}",
                    severity="error",
                )
            ],
            feishu_ready=False,
        )

    text = path.read_text(encoding="utf-8")
    return check_markdown(text, style_file, check_feishu)


if __name__ == "__main__":
    # 命令行使用示例
    if len(sys.argv) < 2:
        print("用法：python -m codex_autoloop.md_checker <markdown 文件> [风格文件]")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    style_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    result = check_file(file_path, style_file)
    print(format_check_report(result))

    sys.exit(0 if result.is_valid else 1)
