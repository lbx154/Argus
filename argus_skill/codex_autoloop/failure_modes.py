from __future__ import annotations

from typing import Any

INVALID_ENCRYPTED_CONTENT_PATTERNS = (
    "invalid_encrypted_content",
    "invalid encrypted content",
)

QUOTA_EXHAUSTION_PATTERNS = (
    "insufficient_quota",
    "quota exhausted",
    "quota exceeded",
    "quota has been exceeded",
    "exceeded your current quota",
    "reached your usage limit",
    "usage limit reached",
    "usage limit exceeded",
    "request would exceed your",
    "billing hard limit",
    "billing limit reached",
    "credit balance is too low",
    "monthly usage limit",
    "daily usage limit",
    "spend limit reached",
    "ran out of credits",
)


def normalize_error_text(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def looks_like_invalid_encrypted_content(fatal_error: str | None) -> bool:
    normalized = normalize_error_text(fatal_error)
    return any(pattern in normalized for pattern in INVALID_ENCRYPTED_CONTENT_PATTERNS)


def looks_like_quota_exhaustion(fatal_error: str | None) -> bool:
    normalized = normalize_error_text(fatal_error)
    return any(pattern in normalized for pattern in QUOTA_EXHAUSTION_PATTERNS)


def build_progress_signature(*, main_result: Any) -> str:
    last_message = str(getattr(main_result, "last_agent_message", "") or "").strip()
    if last_message:
        return f"msg:{last_message}"
    fatal_error = str(getattr(main_result, "fatal_error", "") or "").strip()
    exit_code = int(getattr(main_result, "exit_code", 0))
    turn_completed = bool(getattr(main_result, "turn_completed", False))
    turn_failed = bool(getattr(main_result, "turn_failed", False))
    return (
        "nomsg:"
        f"exit={exit_code}|completed={int(turn_completed)}|failed={int(turn_failed)}|fatal={fatal_error[:240]}"
    )


def build_quota_exhaustion_stop_reason(fatal_error: str | None) -> str:
    detail = (fatal_error or "Codex quota exhausted.").strip()
    if len(detail) > 240:
        detail = detail[:237].rstrip() + "..."
    return f"Main agent stopped after a non-recoverable Codex quota/billing limit error: {detail}"
