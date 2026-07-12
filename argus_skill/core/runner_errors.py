"""Structured recognition of runner failures that happen before a model turn."""

from __future__ import annotations

from typing import Any

_MISSING_RESUME_TARGET = "No session, task, or name matched"
_REFUSED_BEFORE_START = "refused before start:"


def is_missing_resume_target_error(value: object) -> bool:
    return _MISSING_RESUME_TARGET in str(value or "")


def is_pre_provider_refusal_error(value: object) -> bool:
    text = str(value or "")
    return (
        is_missing_resume_target_error(text)
        or _REFUSED_BEFORE_START in text.lower()
    )


def result_has_missing_resume_target(result: Any) -> bool:
    parts = [
        getattr(result, "fatal_error", ""),
        *(getattr(result, "stderr_lines", None) or []),
    ]
    return is_missing_resume_target_error("\n".join(map(str, parts)))


def result_has_pre_provider_refusal(result: Any) -> bool:
    parts = [
        getattr(result, "fatal_error", ""),
        *(getattr(result, "stderr_lines", None) or []),
    ]
    return is_pre_provider_refusal_error("\n".join(map(str, parts)))


__all__ = [
    "is_missing_resume_target_error",
    "is_pre_provider_refusal_error",
    "result_has_missing_resume_target",
    "result_has_pre_provider_refusal",
]
