"""Publication-scale evidence contract for publishable research.

This gate prevents an underpowered pilot from becoming a "publishable boundary
paper" merely by narrowing the prose claim. It deliberately avoids universal
sample, seed, benchmark, or model-count thresholds. Instead, the project records
how its claim-bearing evidence compares with recent accepted papers in the same
area, and the independent Reviewer judges whether that calibration is credible.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from ...core.research_contract import resolve_research_target_level

ASSESSMENT_PATH = Path("paper/PUBLICATION_SCALE_ASSESSMENT.json")
SCHEMA_VERSION = 1
MIN_ACCEPTED_COMPARATORS = 2
_FINAL_TARGETS = frozenset({"publishable", "doctoral"})
_CONTRIBUTION_SHAPES = frozenset(
    {
        "method",
        "system",
        "theory",
        "empirical",
        "benchmark",
        "dataset",
        "diagnostic",
        "negative",
        "boundary",
        "literature_review",
    }
)
_SCALE_DIMENSIONS = (
    "models_or_systems",
    "public_sources",
    "evaluation_units",
    "repeats_or_proof_obligations",
    "strong_comparisons",
    "uncertainty_or_formal_guarantee",
)
_PLACEHOLDER = re.compile(r"\b(?:todo|tbd|replace|unknown|placeholder)\b", re.I)


def _substantive(value: Any, *, minimum: int = 12) -> bool:
    text = str(value or "").strip()
    return len(text) >= minimum and not _PLACEHOLDER.search(text)


def _contained_file(project_root: Path, raw: Any) -> tuple[Path | None, str]:
    value = str(raw or "").strip()
    if not value:
        return None, "empty artifact path"
    candidate = Path(value).expanduser()
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (project_root / candidate).resolve()
    )
    root = project_root.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return None, f"artifact escapes project root: {value}"
    if not resolved.is_file():
        return None, f"artifact does not exist: {relative.as_posix()}"
    return resolved, ""


def _load(project_root: Path) -> tuple[dict[str, Any] | None, str]:
    path = project_root / ASSESSMENT_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"missing {ASSESSMENT_PATH.as_posix()}"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable {ASSESSMENT_PATH.as_posix()}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{ASSESSMENT_PATH.as_posix()} must be a JSON object"
    return payload, ""


def publication_scale_issues(project_root: Path) -> tuple[str, ...]:
    """Return fail-closed issues for publishable/doctoral research targets."""
    root = project_root.resolve()
    target = resolve_research_target_level(root)
    if target not in _FINAL_TARGETS:
        return ()

    payload, load_error = _load(root)
    if payload is None:
        return (
            load_error
            + "; publishable research must compare its claim-bearing evidence "
            "with recent accepted same-area papers before analysis can close",
        )

    issues: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        issues.append(
            f"unsupported publication-scale schema_version: "
            f"{payload.get('schema_version')!r}"
        )
    if str(payload.get("research_target_level") or "").strip() != target:
        issues.append(
            "publication-scale assessment target does not match the canonical "
            f"research target {target!r}"
        )
    shape = str(payload.get("contribution_shape") or "").strip()
    if shape not in _CONTRIBUTION_SHAPES:
        issues.append(
            "contribution_shape must be one of "
            + ", ".join(sorted(_CONTRIBUTION_SHAPES))
        )

    comparators = payload.get("accepted_comparators")
    if not isinstance(comparators, list) or len(comparators) < MIN_ACCEPTED_COMPARATORS:
        issues.append(
            "accepted_comparators must contain at least "
            f"{MIN_ACCEPTED_COMPARATORS} recent accepted same-area papers"
        )
    else:
        for index, comparator in enumerate(comparators):
            prefix = f"accepted_comparators[{index}]"
            if not isinstance(comparator, dict):
                issues.append(f"{prefix} must be an object")
                continue
            for field in (
                "title",
                "venue",
                "official_acceptance_url",
                "why_comparable",
                "evidence_scale_summary",
            ):
                minimum = 3 if field == "venue" else 12
                if not _substantive(comparator.get(field), minimum=minimum):
                    issues.append(f"{prefix}.{field} is missing or templated")
            url = str(comparator.get("official_acceptance_url") or "").strip()
            if url and not url.startswith(("https://", "http://")):
                issues.append(f"{prefix}.official_acceptance_url must be an HTTP URL")

    evidence_rows = payload.get("claim_bearing_evidence")
    if not isinstance(evidence_rows, list) or not evidence_rows:
        issues.append("claim_bearing_evidence must contain at least one primary row")
    else:
        primary_rows = 0
        for index, row in enumerate(evidence_rows):
            prefix = f"claim_bearing_evidence[{index}]"
            if not isinstance(row, dict):
                issues.append(f"{prefix} must be an object")
                continue
            if str(row.get("role") or "").strip() == "primary":
                primary_rows += 1
            for field in (
                "claim",
                "source_type",
                "evaluation_unit",
                "uncertainty_method",
            ):
                if not _substantive(row.get(field)):
                    issues.append(f"{prefix}.{field} is missing or templated")
            comparisons = row.get("strongest_comparisons")
            if not isinstance(comparisons, list) or not any(
                _substantive(item, minimum=3) for item in comparisons
            ):
                issues.append(f"{prefix}.strongest_comparisons is empty")
            artifacts = row.get("artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                issues.append(f"{prefix}.artifacts is empty")
            else:
                for raw_path in artifacts:
                    _path, error = _contained_file(root, raw_path)
                    if error:
                        issues.append(f"{prefix}: {error}")
        if primary_rows == 0:
            issues.append("claim_bearing_evidence has no role=primary row")

    dimensions = payload.get("scale_dimensions")
    if not isinstance(dimensions, dict):
        issues.append("scale_dimensions must be an object")
    else:
        for field in _SCALE_DIMENSIONS:
            if not _substantive(dimensions.get(field)):
                issues.append(f"scale_dimensions.{field} is missing or templated")

    assessment = payload.get("assessment")
    if not isinstance(assessment, dict):
        issues.append("assessment must be an object")
    else:
        if assessment.get("pilot_only") is not False:
            issues.append(
                "assessment.pilot_only must be false; an underpowered pilot cannot "
                "become publishable through claim narrowing"
            )
        if assessment.get("proxy_only") is not False:
            issues.append(
                "assessment.proxy_only must be false; proxy/diagnostic evidence may "
                "support but cannot solely carry a publishable empirical claim"
            )
        if assessment.get("publication_scale_supported") is not True:
            issues.append("assessment.publication_scale_supported must be true")
        for field in (
            "independent_value",
            "comparison_to_accepted_work",
            "strongest_reject_reason",
        ):
            if not _substantive(assessment.get(field), minimum=30):
                issues.append(f"assessment.{field} is missing or too thin")

    return tuple(dict.fromkeys(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    issues = publication_scale_issues(args.project_root)
    if args.json:
        print(json.dumps({"ok": not issues, "issues": list(issues)}, indent=2))
    elif issues:
        for issue in issues:
            print(f"ERROR: {issue}")
    else:
        print("publication-scale evidence: PASS")
    return 0 if not issues else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
