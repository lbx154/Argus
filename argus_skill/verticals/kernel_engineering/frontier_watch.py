"""Validate and persist continuous online frontier-search evidence.

The agent performs the actual web research.  This module makes that research a
fresh, stage-scoped artifact instead of an unverifiable sentence in a summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_HOURS = 6.0
STAGES = ("scope", "environment", "baseline", "optimize", "validate", "report")
REQUIRED_SURFACES = frozenset({"target_repository", "official_toolchains", "research_frontier"})
PRIMARY_SOURCE_TYPES = frozenset(
    {
        "official_repo",
        "official_docs",
        "official_release",
        "issue",
        "pull_request",
        "paper",
        "preprint",
        "author_repo",
        "standard",
    }
)


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def snapshot_path(project_root: Path, stage: str) -> Path:
    return project_root / "research" / "frontier" / f"{stage}.json"


def ledger_path(project_root: Path) -> Path:
    return project_root / "research" / "FRONTIER_WATCH.jsonl"


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_record(
    record: dict[str, Any],
    *,
    expected_stage: str,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {record.get('schema_version')!r}")
    if record.get("stage") != expected_stage:
        errors.append(f"stage mismatch: expected {expected_stage!r}, got {record.get('stage')!r}")
    if record.get("network_status") != "online":
        errors.append("network_status must be 'online'; offline research cannot certify freshness")

    searched_at = _parse_time(record.get("searched_at"))
    if searched_at is None:
        errors.append("searched_at is missing or invalid")
    else:
        current = now or datetime.now(UTC)
        age_hours = (current - searched_at).total_seconds() / 3600
        if age_hours < -0.1:
            errors.append("searched_at is in the future")
        elif age_hours > max_age_hours:
            errors.append(f"frontier snapshot is stale ({age_hours:.1f}h > {max_age_hours:.1f}h)")
        expected_date = searched_at.date().isoformat()
        if record.get("frontier_as_of") != expected_date:
            errors.append(f"frontier_as_of must equal searched_at date {expected_date}")

    queries = record.get("queries")
    if not isinstance(queries, list) or len(queries) < 3:
        errors.append("at least three focused online queries are required")
    else:
        for index, query in enumerate(queries):
            if not isinstance(query, dict):
                errors.append(f"queries[{index}] must be an object")
                continue
            for key in ("query", "channel", "purpose"):
                if not _nonempty_text(query.get(key)):
                    errors.append(f"queries[{index}].{key} is empty")

    surfaces = record.get("checked_surfaces")
    surface_set = (
        {str(value).strip() for value in surfaces if str(value).strip()}
        if isinstance(surfaces, list)
        else set()
    )
    missing_surfaces = sorted(REQUIRED_SURFACES - surface_set)
    if missing_surfaces:
        errors.append("missing checked surfaces: " + ", ".join(missing_surfaces))

    sources = record.get("sources")
    primary_count = 0
    if not isinstance(sources, list) or len(sources) < 3:
        errors.append("at least three sources are required")
    else:
        seen_urls: set[str] = set()
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"sources[{index}] must be an object")
                continue
            url = str(source.get("url") or "").strip()
            if not url.startswith("https://"):
                errors.append(f"sources[{index}].url must be https")
            elif "example.invalid" in url:
                errors.append(f"sources[{index}].url is still a template placeholder")
            elif url in seen_urls:
                errors.append(f"duplicate source URL: {url}")
            seen_urls.add(url)
            for key in ("title", "source_type", "relevance"):
                if not _nonempty_text(source.get(key)):
                    errors.append(f"sources[{index}].{key} is empty")
                elif "REPLACE" in str(source.get(key)):
                    errors.append(f"sources[{index}].{key} is still a template placeholder")
            if str(source.get("source_type") or "") in PRIMARY_SOURCE_TYPES:
                primary_count += 1
        if primary_count < 2:
            errors.append("at least two primary sources are required")

    no_update = record.get("no_material_update") is True
    updates = record.get("material_updates")
    if not isinstance(updates, list):
        errors.append("material_updates must be a list")
    elif not updates and not no_update:
        errors.append("record material_updates or set no_material_update=true")
    else:
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                errors.append(f"material_updates[{index}] must be an object")
                continue
            for key in ("finding", "impact", "action"):
                if not _nonempty_text(update.get(key)):
                    errors.append(f"material_updates[{index}].{key} is empty")
    if no_update and updates:
        errors.append("no_material_update cannot be true when material_updates is non-empty")
    if not _nonempty_text(record.get("decision_impact")):
        errors.append("decision_impact is empty")
    elif "REPLACE" in str(record.get("decision_impact")):
        errors.append("decision_impact is still a template placeholder")
    return list(dict.fromkeys(errors))


def canonicalize(record: dict[str, Any], *, stage: str) -> dict[str, Any]:
    payload = dict(record)
    payload["schema_version"] = SCHEMA_VERSION
    payload["stage"] = stage
    if not payload.get("searched_at"):
        payload["searched_at"] = datetime.now(UTC).isoformat()
    searched_at = _parse_time(payload.get("searched_at"))
    if searched_at is not None and not payload.get("frontier_as_of"):
        payload["frontier_as_of"] = searched_at.date().isoformat()
    digest_input = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["record_id"] = hashlib.sha256(digest_input).hexdigest()[:16]
    payload["recorded_at"] = datetime.now(UTC).isoformat()
    return payload


def write_record(project_root: Path, stage: str, record: dict[str, Any]) -> Path:
    target = snapshot_path(project_root, stage)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ledger = ledger_path(project_root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return target


def template(stage: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "network_status": "online",
        "searched_at": now.isoformat(),
        "frontier_as_of": now.date().isoformat(),
        "trigger": "stage_entry",
        "checked_surfaces": [
            "target_repository",
            "official_toolchains",
            "research_frontier",
            "adjacent_implementations",
        ],
        "queries": [
            {
                "query": "target repository open pull requests issues releases latest",
                "channel": "github",
                "purpose": "avoid duplicate work and capture current upstream direction",
            },
            {
                "query": "selected toolchain official release notes target GPU latest",
                "channel": "official_docs",
                "purpose": "capture new APIs, fixes, architecture support, and deprecations",
            },
            {
                "query": "target operation latest paper implementation benchmark",
                "channel": "arxiv_openreview_author_repos",
                "purpose": "find new mechanisms and stronger public baselines",
            },
        ],
        "sources": [
            {
                "url": "https://example.invalid/replace-target-repo",
                "title": "REPLACE",
                "source_type": "official_repo",
                "relevance": "REPLACE",
            },
            {
                "url": "https://example.invalid/replace-toolchain-docs",
                "title": "REPLACE",
                "source_type": "official_docs",
                "relevance": "REPLACE",
            },
            {
                "url": "https://example.invalid/replace-paper-or-author-repo",
                "title": "REPLACE",
                "source_type": "paper",
                "relevance": "REPLACE",
            },
        ],
        "material_updates": [],
        "no_material_update": True,
        "decision_impact": "REPLACE: what changed or why the current plan remains best",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "template", "record"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--project-root", type=Path, default=Path.cwd())
        cmd.add_argument("--stage", choices=STAGES, required=True)
        if name in {"check", "record"}:
            cmd.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
        if name == "record":
            cmd.add_argument("--input", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.resolve()
    if args.command == "template":
        print(json.dumps(template(args.stage), indent=2, sort_keys=True))
        return 0
    if args.command == "record":
        try:
            text = (
                sys.stdin.read()
                if str(args.input) == "-"
                else args.input.read_text(encoding="utf-8")
            )
            raw = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            print(f"frontier input unreadable: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(raw, dict):
            print("frontier input root must be an object", file=sys.stderr)
            return 2
        record = canonicalize(raw, stage=args.stage)
        errors = validate_record(
            record,
            expected_stage=args.stage,
            max_age_hours=args.max_age_hours,
        )
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 2
        target = write_record(root, args.stage, record)
        print(target)
        return 0

    path = snapshot_path(root, args.stage)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"frontier snapshot unreadable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(record, dict):
        print("frontier snapshot root must be an object", file=sys.stderr)
        return 2
    errors = validate_record(
        record,
        expected_stage=args.stage,
        max_age_hours=args.max_age_hours,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"frontier watch: {args.stage} fresh as of {record['frontier_as_of']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
