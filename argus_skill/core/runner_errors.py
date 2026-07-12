"""Structured recognition of runner failures that happen before a model turn."""

from __future__ import annotations

from typing import Any

_MISSING_RESUME_TARGET = "No session, task, or name matched"


def is_missing_resume_target_error(value: object) -> bool:
    return _MISSING_RESUME_TARGET in str(value or "")


def result_has_missing_resume_target(result: Any) -> bool:
    parts = [
        getattr(result, "fatal_error", ""),
        *(getattr(result, "stderr_lines", None) or []),
    ]
    return is_missing_resume_target_error("\n".join(map(str, parts)))


__all__ = [
    "is_missing_resume_target_error",
    "result_has_missing_resume_target",
]
