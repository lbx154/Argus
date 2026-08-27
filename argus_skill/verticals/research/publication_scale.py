"""Publication-scale evidence contract for publishable research.

This gate prevents an underpowered pilot from becoming a "publishable boundary
paper" merely by narrowing the prose claim. It deliberately avoids universal
sample, seed, benchmark, or model-count thresholds. Instead, the project records
how its claim-bearing evidence compares with recent accepted papers in the same
area, and the independent Reviewer judges whether that calibration is credible.

It also prevents an unmatched baseline from reading as an intervention win. In
campaign run-06-control, the method reported 632/750 versus 520/750 (+14.93pp),
but only the method arm used ``no_repeat_ngram_size=2`` and a repetition
penalty. Declared arm configs are therefore compared key-by-key, with only named
intended differences allowed.

``arm_configs`` is required-if-present rather than a new unconditional field.
Older valid assessments predate the contract, some primary claims are not
comparisons, and every string returned here blocks stage completion; labeling a
missing declaration "advisory" would still break those campaigns. Omission is
thus grandfathered, while any declaration is enforced fail-closed.
"""
from __future__ import annotations

import argparse
import json
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...core.manuscript_snapshot import (
    MANUSCRIPT_SNAPSHOT_FIELD,
    manuscript_review_status,
    manuscript_snapshot,
)
from ...core.research_contract import (
    normalize_research_target_level,
    resolve_research_target_level,
)

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


_MISSING = object()


def _load_arm_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
        elif suffix == ".toml":
            with path.open("rb") as handle:
                payload = tomllib.load(handle)
        else:
            raise ValueError("config must use .json or .toml")
    except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise ValueError("config must contain an object/table at the top level")
    return payload


def _flatten_config(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten_config(child, dotted))
        return flattened
    return {prefix: value}


def _display_config_value(value: Any) -> str:
    if value is _MISSING:
        return "<missing>"
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def _arm_config_issues(
    project_root: Path,
    row: dict[str, Any],
    *,
    prefix: str,
) -> list[str]:
    declaration = row.get("arm_configs")
    if declaration is None:
        return []
    if not isinstance(declaration, dict):
        return [f"{prefix}.arm_configs must be an object"]

    intended = declaration.get("intended_differences", [])
    if not isinstance(intended, list) or not all(
        isinstance(item, str) and item.strip() for item in intended
    ):
        return [f"{prefix}.arm_configs.intended_differences must be a list of keys"]
    allowed = {item.strip() for item in intended}

    loaded: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for arm in ("method", "baseline"):
        raw_path = declaration.get(arm)
        config_path, error = _contained_file(project_root, raw_path)
        if error:
            issues.append(f"{prefix}.arm_configs.{arm}: {error}")
            continue
        assert config_path is not None
        try:
            loaded[arm] = _load_arm_config(config_path)
        except ValueError as exc:
            issues.append(
                f"{prefix}.arm_configs.{arm}: unreadable config "
                f"{config_path.relative_to(project_root).as_posix()}: {exc}"
            )
    if len(loaded) != 2:
        return issues

    method = _flatten_config(loaded["method"])
    baseline = _flatten_config(loaded["baseline"])
    for key in sorted(method.keys() | baseline.keys()):
        method_value = method.get(key, _MISSING)
        baseline_value = baseline.get(key, _MISSING)
        if method_value != baseline_value and key not in allowed:
            issues.append(
                f"{prefix}.arm_configs has unmatched difference at {key}: "
                f"method={_display_config_value(method_value)}, "
                f"baseline={_display_config_value(baseline_value)}; add it to "
                "intended_differences only if the scientific intervention requires it"
            )
    return issues


def publication_scale_issues(
    project_root: Path,
    *,
    research_target_level: str | None = None,
) -> tuple[str, ...]:
    """Return fail-closed issues for publishable/doctoral research targets."""
    root = project_root.resolve()
    target = (
        normalize_research_target_level(research_target_level)
        if research_target_level is not None
        else resolve_research_target_level(root)
    )
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
    freshness = manuscript_review_status(payload, root)
    if freshness["status"] != "current":
        issues.append(
            "publication-scale assessment " + str(freshness["message"])
        )
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
                issues.extend(_arm_config_issues(root, row, prefix=prefix))
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


def scaffold_issues(project_root: Path) -> tuple[str, ...]:
    """Ensure the assessment file exists in this schema, adding only what is absent.

    The shape was previously described nowhere: the checklist said "write
    `paper/PUBLICATION_SCALE_ASSESSMENT.json`" and the required keys lived only
    in the validator. Asked to emit an undocumented nested structure in one
    turn, three concurrent campaigns each invented a different one --
    ``schema_version`` 3, ``"publication_scale_assessment_v1"``, and no file at
    all -- so ``publication_scale_issues`` failed at its first predicate and the
    hundred lines of substantive checks below it never ran on any of them. The
    campaigns were not evading the gate; they could not see it.

    So the harness writes the skeleton and the campaign fills it in as the
    evidence arrives, rather than reproducing the schema from memory at the end.
    Merging key-by-key keeps that incremental: a claim already answered is never
    overwritten, and a file in some other shape keeps its own keys alongside the
    ones this contract reads.

    Blank is not an answer. Every scaffolded value is empty or ``null``, which
    fails ``_substantive`` and the three ``assessment`` booleans exactly as a
    missing file did -- the gate still refuses until a human-meant value
    replaces it. Returns the issues remaining after scaffolding.
    """
    root = project_root.resolve()
    path = root / ASSESSMENT_PATH
    payload, _ = _load(root)
    if payload is None:
        payload = {}

    # Two kinds of field, and merging them the same way is what made the first
    # version of this useless. ``schema_version`` and ``research_target_level``
    # identify the contract; they are the harness's to state, so they are
    # stamped over whatever is there -- a campaign that wrote
    # ``"publication_scale_assessment_v1"`` keeps failing at predicate one
    # forever if a merge politely leaves it alone. Everything below is the
    # campaign's own claim about its evidence and is never overwritten, only
    # added when absent.
    payload["schema_version"] = SCHEMA_VERSION
    payload["research_target_level"] = resolve_research_target_level(root) or ""
    payload.setdefault("created_at", datetime.now(UTC).isoformat())
    payload.setdefault(
        MANUSCRIPT_SNAPSHOT_FIELD,
        manuscript_snapshot(root, recorded_at=str(payload["created_at"])),
    )

    skeleton: dict[str, Any] = {
        "contribution_shape": "",
        "accepted_comparators": [],
        "claim_bearing_evidence": [],
        "scale_dimensions": {field: "" for field in _SCALE_DIMENSIONS},
        "assessment": {
            "pilot_only": None,
            "proxy_only": None,
            "publication_scale_supported": None,
            "independent_value": "",
            "comparison_to_accepted_work": "",
            "strongest_reject_reason": "",
        },
    }
    for key, value in skeleton.items():
        if key not in payload:
            payload[key] = value
        elif isinstance(value, dict) and isinstance(payload[key], dict):
            for sub_key, sub_value in value.items():
                payload[key].setdefault(sub_key, sub_value)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return publication_scale_issues(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--scaffold",
        action="store_true",
        help=(
            "create or complete paper/PUBLICATION_SCALE_ASSESSMENT.json in this "
            "schema without overwriting answered fields, then report what is "
            "still unanswered"
        ),
    )
    args = parser.parse_args(argv)
    issues = (
        scaffold_issues(args.project_root)
        if args.scaffold
        else publication_scale_issues(args.project_root)
    )
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
