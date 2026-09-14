"""Evidence-anchored change-plan validator for the learning vertical."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MANIFEST_RELPATH = Path("learning/MATERIAL_MANIFEST.json")
PLAN_RELPATH = Path("learning/CHANGE_PLAN.json")
_ACTIONS = frozenset({"create", "update", "archive", "no_op"})
_LAYERS = frozenset({"skill", "wiki"})


def _safe_relative(value: object) -> Path | None:
    text = str(value or "").strip()
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        return None
    return path


def _material_pages(root: Path, source_id: str) -> list[Path]:
    relpath = _safe_relative(source_id)
    if relpath is None:
        return []
    return sorted(
        path
        for path in (root / ".autors").glob(f"*/wiki/pages/{relpath.as_posix()}.md")
        if path.is_file() and path.stat().st_size > 0
    )


def _load_manifest(root: Path) -> tuple[dict[str, str], list[str]]:
    path = root / MANIFEST_RELPATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [f"missing {MANIFEST_RELPATH.as_posix()}"]
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"invalid {MANIFEST_RELPATH.as_posix()}: {exc}"]
    rows = payload.get("materials") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return {}, [f"{MANIFEST_RELPATH.as_posix()} must contain materials"]
    sources: dict[str, str] = {}
    issues: list[str] = []
    for index, row in enumerate(rows):
        source_id = str(row.get("source_id") or "").strip() if isinstance(row, dict) else ""
        if not source_id or source_id in sources:
            issues.append(f"materials[{index}] has a missing or duplicate source_id")
            continue
        pages = _material_pages(root, source_id)
        if len(pages) != 1:
            issues.append(
                f"source {source_id!r} must resolve to exactly one immutable Wiki page"
            )
            continue
        sources[source_id] = pages[0].read_text(encoding="utf-8")
    return sources, issues


def _validate_plan(root: Path, sources: dict[str, str]) -> list[str]:
    path = root / PLAN_RELPATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"missing {PLAN_RELPATH.as_posix()}"]
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid {PLAN_RELPATH.as_posix()}: {exc}"]
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return [f"{PLAN_RELPATH.as_posix()} must declare version 1"]
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        return [f"{PLAN_RELPATH.as_posix()} must contain operations"]
    issues: list[str] = []
    seen_targets: set[str] = set()
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            issues.append(f"operations[{index}] must be an object")
            continue
        action = str(operation.get("action") or "").strip().lower()
        reason = str(operation.get("reason") or "").strip()
        if action not in _ACTIONS:
            issues.append(f"operations[{index}] has unsupported action {action!r}")
            continue
        if not reason:
            issues.append(f"operations[{index}] requires a reason")
        if action == "no_op":
            if len(operations) != 1:
                issues.append("no_op must be the only operation")
            continue
        layer = str(operation.get("layer") or "").strip().lower()
        target_text = str(operation.get("target") or "").strip()
        target = _safe_relative(target_text)
        if layer not in _LAYERS:
            issues.append(f"operations[{index}] layer must be skill or wiki")
        if target is None:
            issues.append(f"operations[{index}] target must stay project-relative")
        elif target_text in seen_targets:
            issues.append(f"operations[{index}] repeats target {target_text!r}")
        else:
            seen_targets.add(target_text)
            if action in {"create", "update"}:
                candidate = (root / target).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    issues.append(f"operations[{index}] target escapes project")
                else:
                    if not candidate.is_file() or candidate.stat().st_size <= 0:
                        issues.append(
                            f"operations[{index}] target is not a non-empty file: {target_text!r}"
                        )
        evidence = operation.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            issues.append(f"operations[{index}] requires evidence spans")
            continue
        for span_index, span in enumerate(evidence):
            if not isinstance(span, dict):
                issues.append(f"operations[{index}].evidence[{span_index}] must be an object")
                continue
            source_id = str(span.get("source_id") or "").strip()
            locator = str(span.get("locator") or "").strip()
            quote = str(span.get("quote") or "").strip()
            source = sources.get(source_id)
            if source is None:
                issues.append(
                    f"operations[{index}].evidence[{span_index}] names unknown source_id"
                )
            if not locator:
                issues.append(
                    f"operations[{index}].evidence[{span_index}] requires a locator"
                )
            if not quote or (source is not None and quote not in source):
                issues.append(
                    f"operations[{index}].evidence[{span_index}] quote is not verbatim in source"
                )
    return issues


def validate_curation(project_root: object, stage: str) -> list[str]:
    root = Path(str(project_root or ".")).resolve()
    stage_name = (stage or "").strip().lower()
    if stage_name not in {"ingest", "study", "curate", "review"}:
        return [f"unsupported learning stage: {stage_name!r}"]
    sources, issues = _load_manifest(root)
    if stage_name == "ingest":
        return issues
    study = root / "learning" / "STUDY.md"
    if not study.is_file() or study.stat().st_size <= 0:
        issues.append("study requires non-empty learning/STUDY.md")
    if stage_name == "study":
        return issues
    issues.extend(_validate_plan(root, sources))
    if stage_name == "review":
        indexes = [
            path
            for path in (root / ".autors").glob("*/wiki/INDEX.md")
            if path.is_file() and path.stat().st_size > 0
        ]
        if not indexes:
            issues.append("review requires a non-empty Wiki INDEX.md")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="learning-curation")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--stage", choices=["ingest", "study", "curate", "review"], required=True)
    args = parser.parse_args(argv)
    issues = validate_curation(args.project_root, args.stage)
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1
    print(f"learning curation valid for {args.stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())