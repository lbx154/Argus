from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .codex_runner import CodexRunner, RunnerOptions


SCHEMA_PATH = str(Path(__file__).with_name("objective_rewrite_schema.json"))


@dataclass(frozen=True)
class ObjectiveRewriteResult:
    original_objective: str
    rewritten_objective: str
    applied: bool
    failure_reason: str | None = None


def rewrite_run_objective(
    *,
    runner: CodexRunner,
    objective: str,
    working_dir: str,
    project_name: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> ObjectiveRewriteResult:
    original = normalize_objective_text(objective)
    if not original:
        return ObjectiveRewriteResult(
            original_objective="",
            rewritten_objective="",
            applied=False,
            failure_reason="Objective is empty.",
        )
    resolved_project_name = (project_name or Path(working_dir).resolve().name or "current project").strip()
    prompt = build_objective_rewrite_prompt(
        objective=original,
        working_dir=working_dir,
        project_name=resolved_project_name,
    )
    try:
        result = runner.run_exec(
            prompt=prompt,
            resume_thread_id=None,
            options=RunnerOptions(
                model=model,
                reasoning_effort=reasoning_effort,
                skip_git_repo_check=True,
                working_dir=working_dir,
                output_schema_path=SCHEMA_PATH,
            ),
            run_label="objective-rewrite",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return ObjectiveRewriteResult(
            original_objective=original,
            rewritten_objective=original,
            applied=False,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )
    rewritten = parse_objective_rewrite_text(result.last_agent_message or "")
    if not rewritten:
        failure = result.fatal_error or result.last_agent_message or f"Rewrite returned empty output. exit={result.exit_code}"
        return ObjectiveRewriteResult(
            original_objective=original,
            rewritten_objective=original,
            applied=False,
            failure_reason=failure[:500],
        )
    return ObjectiveRewriteResult(
        original_objective=original,
        rewritten_objective=rewritten,
        applied=(rewritten != original),
    )


def build_objective_rewrite_prompt(*, objective: str, working_dir: str, project_name: str) -> str:
    return (
        "You rewrite raw user /run requests into a cleaner ArgusBot objective.\n"
        "Return valid JSON matching the provided schema.\n"
        "Do not wrap the response in markdown fences.\n\n"
        "Rules:\n"
        "1. Preserve the user's actual intent. Do not invent requirements, datasets, experiments, deadlines, or acceptance criteria.\n"
        "2. When helpful, format the request with these ArgusBot sections:\n"
        "   Final Goal\n"
        "   Current Task\n"
        "   Acceptance Criteria\n"
        "   Constraints\n"
        "   Notes\n"
        "3. Keep the rewrite concise, actionable, and ready to hand to the main agent.\n"
        "4. Match the user's dominant language. If the input is mixed, keep the rewrite natural and readable instead of forcing bilingual output.\n"
        "5. If the original request is already clear, only normalize it lightly.\n"
        "6. Do not mention that you rewrote the objective.\n\n"
        f"Project name:\n{project_name}\n\n"
        f"Working directory:\n{working_dir}\n\n"
        "Raw /run objective:\n"
        f"{objective}\n"
    )


def parse_objective_rewrite_text(text: str) -> str | None:
    candidate = text.strip()
    parsed = _load_json(candidate)
    if parsed is None:
        left = candidate.find("{")
        right = candidate.rfind("}")
        if left >= 0 and right > left:
            parsed = _load_json(candidate[left : right + 1])
    if parsed is None:
        return None
    rewritten = parsed.get("rewritten_objective")
    if not isinstance(rewritten, str):
        return None
    normalized = normalize_objective_text(rewritten)
    return normalized or None


def normalize_objective_text(text: str) -> str:
    if not text:
        return ""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    return "\n".join(lines).strip()


def format_objective_rewrite_message(result: ObjectiveRewriteResult) -> str:
    if result.applied:
        header = (
            "[daemon] objective rewrite enabled. Using the rewritten objective for the main agent.\n"
            "[CN] 已启用目标改写；daemon 会先整理你的 /run，再把改写后的目标交给 main agent。\n"
            "[EN] Objective rewrite is enabled. The daemon will hand the rewritten objective to the main agent."
        )
    else:
        header = (
            "[daemon] objective rewrite enabled. The objective already looked clear, so daemon kept it with light normalization.\n"
            "[CN] 已启用目标改写；当前 /run 已经足够清晰，因此 daemon 仅做了轻量规范化后直接交给 main agent。\n"
            "[EN] Objective rewrite is enabled. The /run objective already looked clear, so the daemon kept it with only light normalization."
        )
    return (
        f"{header}\n\n"
        f"Original / 原始:\n{_truncate_block(result.original_objective)}\n\n"
        f"Sent to Main Agent / 发送给 Main Agent:\n{_truncate_block(result.rewritten_objective)}"
    )


def format_objective_rewrite_failure_message(result: ObjectiveRewriteResult) -> str:
    reason = (result.failure_reason or "unknown error").strip()
    return (
        "[daemon] objective rewrite failed, so daemon will use the original /run text.\n"
        "[CN] 目标改写失败，daemon 已回退到原始 /run 文本。\n"
        "[EN] Objective rewrite failed, so the daemon fell back to the original /run text.\n\n"
        f"Reason / 原因:\n{_truncate_block(reason, max_chars=600)}"
    )


def _truncate_block(text: str, *, max_chars: int = 1500) -> str:
    normalized = normalize_objective_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _load_json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value
