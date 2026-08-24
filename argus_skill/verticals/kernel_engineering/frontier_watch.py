"""Validate and persist continuous online frontier-search evidence.

The agent performs the actual web research.  This module makes that research a
fresh, stage-scoped artifact instead of an unverifiable sentence in a summary.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...core.file_lock import exclusive_file_lock

SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 1
STAGES = ("scope", "discover", "environment", "baseline", "optimize", "validate", "report")
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
        "secondary_discovery",
    }
)


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
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
        expected_date = searched_at.date().isoformat()
        if record.get("frontier_as_of") != expected_date:
            errors.append(f"frontier_as_of must equal searched_at date {expected_date}")

    queries = record.get("queries")
    if not isinstance(queries, list) or not queries:
        errors.append("focused online query evidence is required")
    else:
        for index, query in enumerate(queries):
            if not isinstance(query, dict):
                errors.append(f"queries[{index}] must be an object")
                continue
            for key in ("query", "channel", "purpose"):
                if not _nonempty_text(query.get(key)):
                    errors.append(f"queries[{index}].{key} is empty")
                elif "REPLACE" in str(query.get(key)):
                    errors.append(f"queries[{index}].{key} is still a template placeholder")

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
    if not isinstance(sources, list) or not sources:
        errors.append("online source evidence is required")
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
            source_type = str(source.get("source_type") or "")
            if source_type not in PRIMARY_SOURCE_TYPES:
                errors.append(f"sources[{index}].source_type is unsupported: {source_type}")

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


def record_digest(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def ledger_binding(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "kind": "frontier_snapshot_binding",
        "stage": str(record.get("stage") or ""),
        "record_id": str(record.get("record_id") or ""),
        "recorded_at": str(record.get("recorded_at") or ""),
        "frontier_as_of": str(record.get("frontier_as_of") or ""),
        "snapshot_sha256": record_digest(record),
    }


def _is_ledger_binding(record: dict[str, Any]) -> bool:
    return (
        record.get("kind") == "frontier_snapshot_binding"
        and _nonempty_text(record.get("snapshot_sha256"))
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _compact_legacy_ledger_unlocked(project_root: Path) -> Path | None:
    path = ledger_path(project_root)
    try:
        original = path.read_bytes()
    except OSError:
        return None
    compact_rows: list[dict[str, Any]] = []
    has_legacy = False
    for raw in original.decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            has_legacy = True
            continue
        if not isinstance(row, dict):
            has_legacy = True
            continue
        if _is_ledger_binding(row):
            compact_rows.append(row)
        else:
            has_legacy = True
            compact_rows.append(ledger_binding(row))
    if not has_legacy:
        return None

    digest = hashlib.sha256(original).hexdigest()
    archive = (
        Path(project_root)
        / "research"
        / "raw"
        / f"frontier_watch_legacy_full-{digest[:16]}.jsonl.gz"
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive_valid = False
    try:
        archive_valid = gzip.decompress(archive.read_bytes()) == original
    except (OSError, EOFError):
        pass
    if not archive_valid:
        _atomic_write(archive, gzip.compress(original, mtime=0))

    compact = "".join(
        json.dumps(row, sort_keys=True) + "\n" for row in compact_rows
    ).encode()
    _atomic_write(path, compact)
    return archive


def write_record(project_root: Path, stage: str, record: dict[str, Any]) -> Path:
    ledger = ledger_path(project_root)
    lock_path = ledger.with_suffix(".jsonl.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        with exclusive_file_lock(lock):
            _compact_legacy_ledger_unlocked(project_root)
            target = snapshot_path(project_root, stage)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(ledger_binding(record), sort_keys=True) + "\n"
                )
    return target


def latest_ledger_record(project_root: Path, stage: str) -> dict[str, Any] | None:
    """Return the latest same-stage audit row without retaining the full ledger."""
    latest: dict[str, Any] | None = None
    try:
        lines = ledger_path(project_root).open("r", encoding="utf-8")
    except OSError:
        return None
    with lines:
        for raw in lines:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("stage") == stage:
                latest = row
    return latest


def validate_ledger_binding(
    project_root: Path,
    *,
    stage: str,
    snapshot: dict[str, Any],
) -> list[str]:
    latest = latest_ledger_record(project_root, stage)
    if latest is None:
        return [f"no {stage!r} record exists in FRONTIER_WATCH.jsonl"]
    if _is_ledger_binding(latest):
        if latest.get("snapshot_sha256") == record_digest(snapshot):
            return []
    elif latest == snapshot:
        return []
    if latest != snapshot:
        return [
            f"latest {stage!r} FRONTIER_WATCH.jsonl record does not match "
            "the current snapshot; use `frontier_watch record` instead of "
            "editing either artifact directly"
        ]
    return []


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
                "query": "REPLACE with concise stage-relevant online queries",
                "channel": "REPLACE",
                "purpose": "REPLACE",
            },
        ],
        "sources": [
            {
                "url": "https://example.invalid/replace-target-repo",
                "title": "REPLACE",
                "source_type": "official_repo",
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
    )
    errors.extend(
        validate_ledger_binding(
            root,
            stage=args.stage,
            snapshot=record,
        )
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"frontier watch: {args.stage} evidence recorded as of {record['frontier_as_of']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
