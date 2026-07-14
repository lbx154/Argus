"""Mechanical validation for claim-level web source evidence."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

SOURCE_EVIDENCE_PATH = Path("research/SOURCE_EVIDENCE.json")

SOURCE_TYPES = frozenset({
    "public_source_code",
    "reproducible_observation",
    "official_technical_docs",
    "official_changelog",
    "official_technical_talk",
    "vendor_marketing",
    "third_party",
})
CLAIM_CLASSIFICATIONS = frozenset({
    "public_fact",
    "reasonable_inference",
    "unknown_closed_source",
})
CLAIM_SCOPES = frozenset({
    "documented_behavior",
    "public_implementation",
    "measured_behavior",
    "vendor_claim",
    "unknown",
})
CONFIDENCE_VALUES = frozenset({"", "low", "medium", "high"})

_IMPLEMENTATION_SOURCES = frozenset({
    "public_source_code",
    "reproducible_observation",
})
_DOCUMENTED_BEHAVIOR_SOURCES = frozenset({
    "public_source_code",
    "reproducible_observation",
    "official_technical_docs",
    "official_changelog",
    "official_technical_talk",
})
_VENDOR_CLAIM_SOURCES = frozenset({
    "official_technical_docs",
    "official_changelog",
    "official_technical_talk",
    "vendor_marketing",
})

_SOURCE_KEYS = frozenset({
    "source_id",
    "url",
    "title",
    "publisher",
    "source_type",
    "published_at",
    "updated_at",
    "accessed_at",
    "retrieval_method",
    "supporting_excerpt",
    "content_hash",
})
_SOURCE_NONEMPTY_KEYS = _SOURCE_KEYS - {"published_at", "updated_at"}
_CLAIM_KEYS = frozenset({
    "claim_id",
    "text",
    "classification",
    "scope",
    "source_ids",
    "premise_claim_ids",
    "confidence",
    "falsifier",
    "search_scope",
})


@dataclass(frozen=True)
class SourceEvidenceIssue:
    code: str
    path: str
    message: str


def _issue(code: str, path: str, message: str) -> SourceEvidenceIssue:
    return SourceEvidenceIssue(code=code, path=path, message=message)


def _timezone_aware_iso8601(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _http_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(item).strip() for item in value if str(item).strip()]


def validate_source_evidence(payload: object) -> list[SourceEvidenceIssue]:
    issues: list[SourceEvidenceIssue] = []
    if not isinstance(payload, dict):
        return [_issue("root_invalid", "$", "root must be an object")]
    if payload.get("version") != 1:
        issues.append(_issue("version_invalid", "$.version", "version must be 1"))

    raw_sources = payload.get("sources")
    raw_claims = payload.get("claims")
    if not isinstance(raw_sources, list):
        issues.append(_issue("sources_invalid", "$.sources", "sources must be an array"))
        raw_sources = []
    if not isinstance(raw_claims, list):
        issues.append(_issue("claims_invalid", "$.claims", "claims must be an array"))
        raw_claims = []

    source_types_by_id: dict[str, str] = {}
    for index, raw in enumerate(raw_sources):
        path = f"$.sources[{index}]"
        if not isinstance(raw, dict):
            issues.append(_issue("source_invalid", path, "source must be an object"))
            continue
        missing = sorted(_SOURCE_KEYS - raw.keys())
        if missing:
            issues.append(
                _issue("source_fields_missing", path, f"missing fields: {missing}")
            )
        for key in sorted(_SOURCE_NONEMPTY_KEYS):
            if key in raw and not str(raw.get(key) or "").strip():
                issues.append(
                    _issue(
                        "source_field_empty",
                        f"{path}.{key}",
                        f"{key} must not be empty",
                    )
                )
        source_id = str(raw.get("source_id") or "").strip()
        source_type = str(raw.get("source_type") or "").strip()
        if source_id:
            if source_id in source_types_by_id:
                issues.append(
                    _issue(
                        "source_id_duplicate",
                        f"{path}.source_id",
                        f"duplicate source_id {source_id!r}",
                    )
                )
            else:
                source_types_by_id[source_id] = source_type
        if source_type and source_type not in SOURCE_TYPES:
            issues.append(
                _issue(
                    "source_type_invalid",
                    f"{path}.source_type",
                    f"unsupported source_type {source_type!r}",
                )
            )
        if "url" in raw and not _http_url(raw.get("url")):
            issues.append(_issue("source_url_invalid", f"{path}.url", "invalid URL"))
        if "accessed_at" in raw and not _timezone_aware_iso8601(
            raw.get("accessed_at")
        ):
            issues.append(
                _issue(
                    "accessed_at_invalid",
                    f"{path}.accessed_at",
                    "accessed_at must be timezone-aware ISO 8601",
                )
            )
        content_hash = str(raw.get("content_hash") or "").strip().lower()
        if content_hash and re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash) is None:
            issues.append(
                _issue(
                    "content_hash_invalid",
                    f"{path}.content_hash",
                    "content_hash must be one sha256:<64 hex chars> digest",
                )
            )

    claim_rows: list[tuple[str, dict[str, Any], str]] = []
    claim_ids: set[str] = set()
    for index, raw in enumerate(raw_claims):
        path = f"$.claims[{index}]"
        if not isinstance(raw, dict):
            issues.append(_issue("claim_invalid", path, "claim must be an object"))
            continue
        missing = sorted(_CLAIM_KEYS - raw.keys())
        if missing:
            issues.append(
                _issue("claim_fields_missing", path, f"missing fields: {missing}")
            )
        claim_id = str(raw.get("claim_id") or "").strip()
        if not claim_id:
            issues.append(
                _issue("claim_id_empty", f"{path}.claim_id", "claim_id is required")
            )
        elif claim_id in claim_ids:
            issues.append(
                _issue(
                    "claim_id_duplicate",
                    f"{path}.claim_id",
                    f"duplicate claim_id {claim_id!r}",
                )
            )
        else:
            claim_ids.add(claim_id)
        if not str(raw.get("text") or "").strip():
            issues.append(_issue("claim_text_empty", f"{path}.text", "text is required"))
        classification = str(raw.get("classification") or "").strip()
        scope = str(raw.get("scope") or "").strip()
        if classification not in CLAIM_CLASSIFICATIONS:
            issues.append(
                _issue(
                    "classification_invalid",
                    f"{path}.classification",
                    f"unsupported classification {classification!r}",
                )
            )
        if scope not in CLAIM_SCOPES:
            issues.append(
                _issue(
                    "claim_scope_invalid",
                    f"{path}.scope",
                    f"unsupported scope {scope!r}",
                )
            )
        if _string_list(raw.get("source_ids")) is None:
            issues.append(
                _issue(
                    "source_ids_invalid",
                    f"{path}.source_ids",
                    "source_ids must be an array",
                )
            )
        if _string_list(raw.get("premise_claim_ids")) is None:
            issues.append(
                _issue(
                    "premise_ids_invalid",
                    f"{path}.premise_claim_ids",
                    "premise_claim_ids must be an array",
                )
            )
        confidence = str(raw.get("confidence") or "").strip().lower()
        if confidence not in CONFIDENCE_VALUES:
            issues.append(
                _issue(
                    "confidence_invalid",
                    f"{path}.confidence",
                    "confidence must be low, medium, high, or empty",
                )
            )
        claim_rows.append((path, raw, claim_id))

    for path, raw, claim_id in claim_rows:
        classification = str(raw.get("classification") or "").strip()
        scope = str(raw.get("scope") or "").strip()
        source_ids = _string_list(raw.get("source_ids")) or []
        premise_ids = _string_list(raw.get("premise_claim_ids")) or []
        referenced_types: set[str] = set()
        for source_id in source_ids:
            source_type = source_types_by_id.get(source_id)
            if source_type is None:
                issues.append(
                    _issue(
                        "source_reference_missing",
                        f"{path}.source_ids",
                        f"unknown source_id {source_id!r}",
                    )
                )
            else:
                referenced_types.add(source_type)
        for premise_id in premise_ids:
            if premise_id not in claim_ids:
                issues.append(
                    _issue(
                        "premise_reference_missing",
                        f"{path}.premise_claim_ids",
                        f"unknown premise claim {premise_id!r}",
                    )
                )
            if premise_id == claim_id:
                issues.append(
                    _issue(
                        "premise_self_reference",
                        f"{path}.premise_claim_ids",
                        "claim cannot cite itself as a premise",
                    )
                )

        if classification == "public_fact":
            if not source_ids:
                issues.append(
                    _issue(
                        "public_fact_source_missing",
                        f"{path}.source_ids",
                        "public_fact requires at least one source",
                    )
                )
            if scope == "public_implementation" and not (
                referenced_types & _IMPLEMENTATION_SOURCES
            ):
                issues.append(
                    _issue(
                        "implementation_evidence_missing",
                        path,
                        "public implementation requires source code or a "
                        "reproducible observation",
                    )
                )
            elif scope == "documented_behavior" and not (
                referenced_types & _DOCUMENTED_BEHAVIOR_SOURCES
            ):
                issues.append(
                    _issue(
                        "documented_evidence_missing",
                        path,
                        "documented behavior requires a technical primary source",
                    )
                )
            elif scope == "measured_behavior" and (
                "reproducible_observation" not in referenced_types
            ):
                issues.append(
                    _issue(
                        "measurement_evidence_missing",
                        path,
                        "measured behavior requires a reproducible observation",
                    )
                )
            elif scope == "vendor_claim" and not (
                referenced_types & _VENDOR_CLAIM_SOURCES
            ):
                issues.append(
                    _issue(
                        "vendor_claim_source_missing",
                        path,
                        "vendor claim requires a first-party vendor source",
                    )
                )
            elif scope == "unknown":
                issues.append(
                    _issue(
                        "public_fact_scope_invalid",
                        f"{path}.scope",
                        "public_fact cannot use unknown scope",
                    )
                )
        elif classification == "reasonable_inference":
            if not premise_ids:
                issues.append(
                    _issue(
                        "inference_premise_missing",
                        f"{path}.premise_claim_ids",
                        "reasonable_inference requires premise claims",
                    )
                )
            if not str(raw.get("confidence") or "").strip():
                issues.append(
                    _issue(
                        "inference_confidence_missing",
                        f"{path}.confidence",
                        "reasonable_inference requires confidence",
                    )
                )
            if not str(raw.get("falsifier") or "").strip():
                issues.append(
                    _issue(
                        "inference_falsifier_missing",
                        f"{path}.falsifier",
                        "reasonable_inference requires a falsifier",
                    )
                )
        elif classification == "unknown_closed_source":
            if scope != "unknown":
                issues.append(
                    _issue(
                        "unknown_scope_invalid",
                        f"{path}.scope",
                        "unknown_closed_source must use unknown scope",
                    )
                )
            if not str(raw.get("search_scope") or "").strip():
                issues.append(
                    _issue(
                        "unknown_search_scope_missing",
                        f"{path}.search_scope",
                        "unknown_closed_source requires the searched public scope",
                    )
                )

    return issues


def validate_source_evidence_file(path: Path) -> list[SourceEvidenceIssue]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [_issue("file_missing", str(path), "source evidence file is missing")]
    except (OSError, json.JSONDecodeError) as exc:
        return [_issue("file_invalid", str(path), f"{type(exc).__name__}: {exc}")]
    return validate_source_evidence(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--path", default=str(SOURCE_EVIDENCE_PATH))
    args = parser.parse_args(argv)
    path = Path(args.project_root).expanduser() / Path(args.path)
    issues = validate_source_evidence_file(path)
    if not issues:
        print(f"PASS {path}")
        return 0
    for issue in issues:
        print(f"{issue.code}\t{issue.path}\t{issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CLAIM_CLASSIFICATIONS",
    "CLAIM_SCOPES",
    "SOURCE_EVIDENCE_PATH",
    "SOURCE_TYPES",
    "SourceEvidenceIssue",
    "validate_source_evidence",
    "validate_source_evidence_file",
]
