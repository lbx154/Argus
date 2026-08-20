"""Accepted-paper argument and official-code organization learning gate.

The artifact is a transfer map, not a reproduction requirement and not a prose
template. Agents read accepted same-area papers and available official code,
extract how the work is organized, then adapt those roles to local claims and
evidence without copying sentences, examples, figures, or implementation.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from ...core.research_contract import (
    normalize_research_target_level,
    resolve_research_target_level,
)

ARGUMENT_ORGANIZATION_PATH = Path(
    "paper/style_ref/ARGUMENT_ORGANIZATION.json"
)
SCHEMA_VERSION = 1
MIN_EXEMPLARS = 2
_FINAL_TARGETS = frozenset({"publishable", "doctoral"})
_ARGUMENT_FIELDS = (
    "problem_setup",
    "gap_move",
    "organizing_insight",
    "contribution_sequence",
    "method_decomposition",
    "evidence_sequence",
    "figure1_role",
    "limitations_role",
    "conclusion_move",
)
_CODE_FIELDS = (
    "entry_points",
    "module_map",
    "config_and_evaluation_flow",
    "reusable_organization_lessons",
)
_TRANSFER_FIELDS = (
    "local_argument_arc",
    "section_roles",
    "method_narrative",
    "experiment_narrative",
    "figure1_job",
    "code_structure_lessons",
    "evidence_based_deviations",
)
_PLACEHOLDER = re.compile(r"\b(?:todo|tbd|replace|unknown|placeholder)\b", re.I)


def _substantive(value: Any, *, minimum: int = 20) -> bool:
    text = str(value or "").strip()
    return len(text) >= minimum and not _PLACEHOLDER.search(text)


def _project_file(project_root: Path, raw: Any) -> tuple[Path | None, str]:
    value = str(raw or "").strip()
    if not value:
        return None, "empty local file path"
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
        return None, f"local file escapes project root: {value}"
    if not resolved.is_file():
        return None, f"local file does not exist: {relative.as_posix()}"
    return resolved, ""


def _project_directory(project_root: Path, raw: Any) -> tuple[Path | None, str]:
    value = str(raw or "").strip()
    if not value:
        return None, "empty local checkout path"
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
        return None, f"local checkout escapes project root: {value}"
    if not resolved.is_dir():
        return None, f"local checkout does not exist: {relative.as_posix()}"
    return resolved, ""


def _load(project_root: Path) -> tuple[dict[str, Any] | None, str]:
    path = project_root / ARGUMENT_ORGANIZATION_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"missing {ARGUMENT_ORGANIZATION_PATH.as_posix()}"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable {ARGUMENT_ORGANIZATION_PATH.as_posix()}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{ARGUMENT_ORGANIZATION_PATH.as_posix()} must be a JSON object"
    return payload, ""


def argument_organization_issues(
    project_root: Path,
    *,
    research_target_level: str | None = None,
) -> tuple[str, ...]:
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
            + "; before drafting, read accepted same-area full papers and "
            "available official code, then map their argument/code organization "
            "to this paper without copying prose",
        )

    issues: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        issues.append(
            f"unsupported argument-organization schema_version: "
            f"{payload.get('schema_version')!r}"
        )
    if str(payload.get("research_target_level") or "").strip() != target:
        issues.append(
            "argument-organization target does not match canonical "
            f"research target {target!r}"
        )
    if payload.get("no_prose_copy_attestation") is not True:
        issues.append("no_prose_copy_attestation must be true")
    if payload.get("reproduction_not_required_attestation") is not True:
        issues.append(
            "reproduction_not_required_attestation must be true; code is studied "
            "for organization, not treated as a mandatory reproduction"
        )

    code_requirement = str(payload.get("code_requirement") or "").strip()
    if code_requirement not in {"required", "not_applicable_with_reason"}:
        issues.append(
            "code_requirement must be required or not_applicable_with_reason"
        )
    if (
        code_requirement == "not_applicable_with_reason"
        and not _substantive(payload.get("code_not_applicable_reason"), minimum=30)
    ):
        issues.append("code_not_applicable_reason is missing or too thin")

    exemplars = payload.get("exemplars")
    available_code = 0
    if not isinstance(exemplars, list) or len(exemplars) < MIN_EXEMPLARS:
        issues.append(
            f"exemplars must contain at least {MIN_EXEMPLARS} accepted same-area papers"
        )
    else:
        seen_slugs: set[str] = set()
        for index, exemplar in enumerate(exemplars):
            prefix = f"exemplars[{index}]"
            if not isinstance(exemplar, dict):
                issues.append(f"{prefix} must be an object")
                continue
            slug = str(exemplar.get("slug") or "").strip()
            if not slug or slug in seen_slugs:
                issues.append(f"{prefix}.slug is empty or duplicated")
            seen_slugs.add(slug)
            for field, minimum in (
                ("title", 8),
                ("venue", 3),
                ("official_acceptance_url", 12),
                ("why_same_area_and_shape", 30),
            ):
                if not _substantive(exemplar.get(field), minimum=minimum):
                    issues.append(f"{prefix}.{field} is missing or templated")
            acceptance_url = str(
                exemplar.get("official_acceptance_url") or ""
            ).strip()
            if acceptance_url and not acceptance_url.startswith(("https://", "http://")):
                issues.append(f"{prefix}.official_acceptance_url must be an HTTP URL")
            for field in ("local_pdf", "text_extract"):
                _path, error = _project_file(root, exemplar.get(field))
                if error:
                    issues.append(f"{prefix}.{field}: {error}")

            argument_map = exemplar.get("argument_map")
            if not isinstance(argument_map, dict):
                issues.append(f"{prefix}.argument_map must be an object")
            else:
                for field in _ARGUMENT_FIELDS:
                    if not _substantive(argument_map.get(field), minimum=30):
                        issues.append(
                            f"{prefix}.argument_map.{field} is missing or too thin"
                        )

            code = exemplar.get("official_code")
            if not isinstance(code, dict):
                issues.append(f"{prefix}.official_code must be an object")
                continue
            availability = str(code.get("availability") or "").strip()
            if availability not in {
                "available",
                "not_released",
                "not_applicable",
            }:
                issues.append(f"{prefix}.official_code.availability is invalid")
                continue
            if availability == "available":
                available_code += 1
                repo_url = str(code.get("repo_url") or "").strip()
                if not repo_url.startswith(("https://", "http://")):
                    issues.append(f"{prefix}.official_code.repo_url must be an HTTP URL")
                if not _substantive(code.get("revision"), minimum=7):
                    issues.append(f"{prefix}.official_code.revision is missing")
                checkout, checkout_error = _project_directory(
                    root,
                    code.get("local_checkout"),
                )
                if checkout_error:
                    issues.append(
                        f"{prefix}.official_code.local_checkout: {checkout_error}"
                    )
                files = code.get("files_inspected")
                if not isinstance(files, list) or len(files) < 2 or not all(
                    _substantive(item, minimum=3) for item in files
                ):
                    issues.append(
                        f"{prefix}.official_code.files_inspected needs at least two files"
                    )
                else:
                    for raw_file in files:
                        inspected, error = _project_file(root, raw_file)
                        if error:
                            issues.append(
                                f"{prefix}.official_code.files_inspected: {error}"
                            )
                            continue
                        if checkout is not None:
                            try:
                                inspected.relative_to(checkout)
                            except ValueError:
                                issues.append(
                                    f"{prefix}.official_code.files_inspected path is "
                                    f"outside local_checkout: {raw_file}"
                                )
                for field in _CODE_FIELDS:
                    if not _substantive(code.get(field), minimum=30):
                        issues.append(
                            f"{prefix}.official_code.{field} is missing or too thin"
                        )
            elif not _substantive(code.get("reason"), minimum=20):
                issues.append(
                    f"{prefix}.official_code.reason is required when code is unavailable"
                )

    if code_requirement == "required" and available_code == 0:
        issues.append(
            "at least one accepted exemplar must have available official code "
            "inspected at a pinned revision"
        )

    transfer = payload.get("transfer_plan")
    if not isinstance(transfer, dict):
        issues.append("transfer_plan must be an object")
    else:
        for field in _TRANSFER_FIELDS:
            if not _substantive(transfer.get(field), minimum=40):
                issues.append(f"transfer_plan.{field} is missing or too thin")

    return tuple(dict.fromkeys(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    issues = argument_organization_issues(args.project_root)
    if args.json:
        print(json.dumps({"ok": not issues, "issues": list(issues)}, indent=2))
    elif issues:
        for issue in issues:
            print(f"ERROR: {issue}")
    else:
        print("accepted-paper argument/code organization: PASS")
    return 0 if not issues else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
