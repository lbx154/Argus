from __future__ import annotations

import re
from typing import Any, Callable

FILE_KEYWORDS = {
    "tests": ("test", "tests", "unit", "integration", "coverage", "spec", "e2e"),
    "docs": ("doc", "docs", "readme", "documentation", "changelog"),
    "config": (
        "config",
        "configuration",
        "ci",
        "cd",
        "workflow",
        "yaml",
        "yml",
        "json",
        "toml",
        "deps",
        "dependency",
        "dependencies",
    ),
}
TOKEN_RE = re.compile(r"[a-z0-9]+")


def scope_adequacy(message: str, patch: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    word_count = len(message.split())
    churn = patch["total_churn"]
    if churn <= 0:
        return 1.0, {
            "message_word_count": word_count,
            "code_churn": churn,
            "not_applicable": "no_text_churn",
        }

    expected_words = int(min(50, max(10, churn / 20)))
    ratio = min(1.0, word_count / expected_words)
    score = 0.2 if word_count < 10 and churn > 100 else ratio
    return score, {
        "message_word_count": word_count,
        "code_churn": churn,
        "expected_words": expected_words,
    }


def file_type_consistency(
    message: str,
    patch: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    counts = {
        "tests": patch["files_test_count"],
        "docs": patch["files_docs_count"],
        "config": patch["files_config_count"],
    }
    changed = {category: count for category, count in counts.items() if count > 0}
    if not changed:
        return 1.0, {
            "not_applicable": "no_tracked_file_category_changed",
            "changed_categories": [],
            "mentioned_categories": [],
            "missing_categories": [],
            "categories": {},
        }

    tokens = set(TOKEN_RE.findall(message.lower()))
    categories: dict[str, Any] = {}
    mentioned_categories: list[str] = []
    missing_categories: list[str] = []
    for category, count in changed.items():
        matched = [keyword for keyword in FILE_KEYWORDS[category] if keyword in tokens]
        mentioned = bool(matched)
        if mentioned:
            mentioned_categories.append(category)
        else:
            missing_categories.append(category)
        categories[category] = {
            "actual_file_count": count,
            "mentioned": mentioned,
            "matched_keywords": matched,
        }

    score = len(mentioned_categories) / len(changed)
    return score, {
        "changed_categories": list(changed),
        "mentioned_categories": mentioned_categories,
        "missing_categories": missing_categories,
        "categories": categories,
    }


LOCAL_CHECKS: dict[
    str,
    Callable[[str, dict[str, Any]], tuple[float, dict[str, Any]]],
] = {
    "scope_adequacy": scope_adequacy,
    "file_type_consistency": file_type_consistency,
}


def evaluate(
    message: str,
    patch: dict[str, Any],
    criteria: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    unavailable: list[str] = []

    for name, settings in criteria.items():
        if not settings["enabled"]:
            results[name] = {"status": "disabled", "uses_llm": settings["uses_llm"]}
            continue
        if settings["uses_llm"]:
            unavailable.append(name)
            results[name] = {"status": "unavailable", "uses_llm": True}
            continue

        check = LOCAL_CHECKS.get(name)
        if check is None:
            raise ValueError(f"no local implementation for enabled criterion: {name}")
        score, evidence = check(message, patch)
        threshold = float(settings["threshold"])
        passed = score >= threshold
        results[name] = {
            "status": "passed" if passed else "failed",
            "uses_llm": False,
            "score": score,
            "threshold": threshold,
            "evidence": evidence,
        }
        if not passed:
            results[name]["error_message"] = settings["error_message"]
            errors.append({"criterion": name, "message": settings["error_message"]})

    status = "incomplete" if unavailable else "flagged" if errors else "passed"
    return {
        "schema_version": "pr-gate/1.0",
        "status": status,
        "errors": errors,
        "unavailable_criteria": unavailable,
        "patch": patch,
        "criteria": results,
    }
