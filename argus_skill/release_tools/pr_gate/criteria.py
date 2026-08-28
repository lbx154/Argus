from __future__ import annotations

from typing import Any, Callable

FILE_KEYWORDS = {
    "tests": ("test", "tests", "unit", "integration", "coverage", "spec", "e2e"),
    "docs": ("doc", "docs", "readme", "documentation", "changelog"),
    "config": (
        "config",
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


def _file_score(changed: bool, mentioned: bool) -> float:
    if changed and mentioned:
        return 1.0
    if changed:
        return 0.6
    return 1.0


def file_type_consistency(
    message: str,
    patch: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    message = message.lower()
    counts = {
        "tests": patch["files_test_count"],
        "docs": patch["files_docs_count"],
        "config": patch["files_config_count"],
    }
    evidence: dict[str, Any] = {}
    scores: list[float] = []
    for category, count in counts.items():
        matched = [keyword for keyword in FILE_KEYWORDS[category] if keyword in message]
        score = _file_score(count > 0, bool(matched))
        scores.append(score)
        evidence[category] = {
            "actual_file_count": count,
            "mentioned": bool(matched),
            "matched_keywords": matched,
            "score": score,
        }
    return sum(scores) / len(scores), evidence


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
