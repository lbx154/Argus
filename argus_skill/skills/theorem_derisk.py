"""Mechanical research gate for pure finite-theorem projects.

This validator checks provenance and coverage, not mathematical truth by itself.
The L2 reviewer remains responsible for reading the proof and audit reasons.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_DERISK_PATH = "research/THEOREM_DERISK.json"

REQUIRED_LEMMA_IDS = (
    "matrix_identity",
    "minimum_degree",
    "nonadjacent_equal_degree",
    "complement_connected_without_universal",
    "regularity_without_universal",
    "regular_order_formula",
    "low_degree_and_n3",
    "spectral_eigenvalues_and_multiplicities",
    "square_root_integrality_and_contradiction",
    "universal_vertex_existence",
    "deletion_gives_perfect_matching",
    "isomorphism_parameter_unique",
    "converse_three_pair_types",
)
EXPECTED_ORDERS = [3, 4, 5, 6, 7]
EXPECTED_LABELED_COUNTS = [1, 0, 15, 0, 105]
EXPECTED_ISOMORPHISM_COUNTS = [1, 0, 1, 0, 1]
ERS_URL = "https://www.renyi.hu/~p_erdos/1966-06.pdf"

_FORBIDDEN_METRIC_FIELDS = frozenset(
    {
        "baseline_metric",
        "proposed_metric",
        "delta",
        "min_meaningful_delta",
        "signal_moved",
        "success_direction",
        "model_id",
        "model_source",
        "data_source",
        "n_examples",
        "cost_usd",
    }
)


@dataclass(frozen=True)
class TheoremDeriskIssue:
    code: str
    detail: str


def _load_json(path: Path) -> tuple[dict[str, Any] | None, list[TheoremDeriskIssue]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [TheoremDeriskIssue("derisk_missing", f"{path} not found")]
    except (OSError, json.JSONDecodeError) as exc:
        return None, [TheoremDeriskIssue("derisk_unreadable", f"{path}: {exc}")]
    if not isinstance(raw, dict):
        return None, [TheoremDeriskIssue("derisk_malformed", f"{path}: not a JSON object")]
    return raw, []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_file(
    project_root: Path,
    relpath: object,
    *,
    code: str,
    issues: list[TheoremDeriskIssue],
) -> Path | None:
    rel = str(relpath or "").strip()
    if not rel:
        issues.append(TheoremDeriskIssue(code, "missing relative path"))
        return None
    path = project_root / rel
    try:
        path.resolve().relative_to(project_root.resolve())
    except ValueError:
        issues.append(TheoremDeriskIssue(code, f"path escapes project root: {rel}"))
        return None
    if not path.is_file() or path.stat().st_size == 0:
        issues.append(TheoremDeriskIssue(code, f"missing or empty file: {rel}"))
        return None
    return path


def _check_hash(
    path: Path | None,
    claimed: object,
    *,
    code: str,
    issues: list[TheoremDeriskIssue],
) -> None:
    if path is None:
        return
    actual = _sha256(path)
    if str(claimed or "").strip().lower() != actual:
        issues.append(TheoremDeriskIssue(code, f"SHA-256 mismatch for {path}: {actual}"))


def _forbidden_fields(value: object, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            if str(key).lower() in _FORBIDDEN_METRIC_FIELDS:
                found.append(child_prefix)
            found.extend(_forbidden_fields(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_fields(child, f"{prefix}[{index}]"))
    return found


def _check_lemma_rows(
    rows: object,
    *,
    prefix: str,
    issues: list[TheoremDeriskIssue],
) -> None:
    if not isinstance(rows, list):
        issues.append(TheoremDeriskIssue(f"{prefix}_malformed", "lemmas must be an array"))
        return
    by_id = {
        str(row.get("id") or ""): row
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "")
    }
    missing = sorted(set(REQUIRED_LEMMA_IDS) - set(by_id))
    extra = sorted(set(by_id) - set(REQUIRED_LEMMA_IDS))
    if missing or extra or len(rows) != len(REQUIRED_LEMMA_IDS):
        issues.append(
            TheoremDeriskIssue(
                f"{prefix}_coverage",
                f"lemma coverage mismatch; missing={missing}, extra={extra}, rows={len(rows)}",
            )
        )
    for lemma_id in REQUIRED_LEMMA_IDS:
        row = by_id.get(lemma_id)
        if row is None:
            continue
        if str(row.get("verdict") or "").lower() != "pass":
            issues.append(TheoremDeriskIssue(f"{prefix}_failed", f"{lemma_id} did not pass"))
        if not str(row.get("evidence") or "").strip():
            issues.append(TheoremDeriskIssue(f"{prefix}_no_evidence", lemma_id))
        if not str(row.get("reason") or "").strip():
            issues.append(TheoremDeriskIssue(f"{prefix}_no_reason", lemma_id))


def _check_active_override(
    project_root: Path,
    issues: list[TheoremDeriskIssue],
) -> None:
    from .checklist_store import store_items_for_stage

    items = store_items_for_stage(project_root, "research")
    if items is None:
        issues.append(
            TheoremDeriskIssue(
                "planner_override_missing",
                "research/CHECKLISTS.json has no Planner-authored research stage",
            )
        )
        return
    signal_item = next((item for item in items if item.id == "research.signal_derisk"), None)
    if signal_item is None or "THEOREM_DERISK.json" not in (
        signal_item.statement + " " + signal_item.evidence_hint
    ):
        issues.append(
            TheoremDeriskIssue(
                "theorem_gate_inactive",
                "active research.signal_derisk item does not select THEOREM_DERISK.json",
            )
        )


def _git_value(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def validate_theorem_derisk(
    raw: dict[str, Any],
    *,
    project_root: Path,
) -> list[TheoremDeriskIssue]:
    root = project_root.resolve()
    issues: list[TheoremDeriskIssue] = []
    _check_active_override(root, issues)

    forbidden = _forbidden_fields(raw)
    if forbidden:
        issues.append(
            TheoremDeriskIssue(
                "performance_metrics_forbidden",
                f"theorem evidence contains inapplicable metric fields: {', '.join(forbidden)}",
            )
        )
    if raw.get("schema_version") != SCHEMA_VERSION:
        issues.append(TheoremDeriskIssue("bad_schema", "schema_version must be 1"))
    if raw.get("task_type") != "finite_graph_theorem":
        issues.append(TheoremDeriskIssue("bad_task_type", "task_type must be finite_graph_theorem"))
    if raw.get("verdict") != "pass":
        issues.append(TheoremDeriskIssue("verdict_not_pass", "top-level verdict must be pass"))
    if raw.get("performance_metrics_applicable") is not False:
        issues.append(
            TheoremDeriskIssue(
                "metrics_applicability",
                "performance_metrics_applicable must be false",
            )
        )
    if raw.get("no_performance_metrics") is not True:
        issues.append(
            TheoremDeriskIssue("metrics_prohibition_missing", "no_performance_metrics must be true")
        )

    proof = raw.get("proof")
    if not isinstance(proof, dict):
        issues.append(TheoremDeriskIssue("proof_malformed", "proof must be an object"))
    else:
        proof_path = _resolve_file(root, proof.get("path"), code="proof_missing", issues=issues)
        _check_hash(proof_path, proof.get("sha256"), code="proof_hash", issues=issues)
        _check_lemma_rows(proof.get("lemmas"), prefix="proof", issues=issues)

    audit_ref = raw.get("independent_audit")
    if not isinstance(audit_ref, dict):
        issues.append(TheoremDeriskIssue("audit_malformed", "independent_audit must be an object"))
    else:
        audit_path = _resolve_file(
            root, audit_ref.get("path"), code="audit_missing", issues=issues
        )
        _resolve_file(root, audit_ref.get("log_path"), code="audit_log_missing", issues=issues)
        if audit_ref.get("verdict") != "pass":
            issues.append(TheoremDeriskIssue("audit_ref_failed", "audit reference must pass"))
        if audit_path is not None:
            audit, audit_load_issues = _load_json(audit_path)
            issues.extend(audit_load_issues)
            if audit is not None:
                if audit.get("verdict") != "pass":
                    issues.append(TheoremDeriskIssue("audit_failed", "independent audit failed"))
                _check_lemma_rows(audit.get("lemmas"), prefix="audit", issues=issues)
                if audit.get("blocking_issues") != []:
                    issues.append(
                        TheoremDeriskIssue(
                            "audit_blockers",
                            "independent audit reports blocking issues",
                        )
                    )

    enumeration = raw.get("enumeration_audit")
    if not isinstance(enumeration, dict):
        issues.append(
            TheoremDeriskIssue("enumeration_malformed", "enumeration_audit must be an object")
        )
    else:
        enum_path = _resolve_file(
            root, enumeration.get("path"), code="enumeration_missing", issues=issues
        )
        _check_hash(
            enum_path,
            enumeration.get("sha256"),
            code="enumeration_hash",
            issues=issues,
        )
        if enumeration.get("orders") != EXPECTED_ORDERS:
            issues.append(TheoremDeriskIssue("enumeration_orders", "orders must be n=3..7"))
        if enumeration.get("qualifying_labeled_counts") != EXPECTED_LABELED_COUNTS:
            issues.append(
                TheoremDeriskIssue("enumeration_claimed_counts", "claimed counts do not match")
            )
        if enumeration.get("isomorphism_class_counts") != EXPECTED_ISOMORPHISM_COUNTS:
            issues.append(
                TheoremDeriskIssue(
                    "enumeration_claimed_classes",
                    "claimed isomorphism class counts do not match",
                )
            )
        if enumeration.get("non_probative") is not True:
            issues.append(
                TheoremDeriskIssue(
                    "enumeration_overclaim",
                    "finite enumeration must be explicitly non-probative for general n",
                )
            )
        if enum_path is not None:
            enum_raw, enum_load_issues = _load_json(enum_path)
            issues.extend(enum_load_issues)
            if enum_raw is not None:
                orders = enum_raw.get("orders")
                if not isinstance(orders, list):
                    issues.append(
                        TheoremDeriskIssue("enumeration_data_malformed", "orders is not an array")
                    )
                else:
                    actual_orders = [row.get("n") for row in orders if isinstance(row, dict)]
                    actual_counts = [
                        row.get("qualifying_labeled_graphs")
                        for row in orders
                        if isinstance(row, dict)
                    ]
                    actual_classes = [
                        row.get("isomorphism_classes")
                        for row in orders
                        if isinstance(row, dict)
                    ]
                    if actual_orders != EXPECTED_ORDERS:
                        issues.append(
                            TheoremDeriskIssue("enumeration_data_orders", str(actual_orders))
                        )
                    if actual_counts != EXPECTED_LABELED_COUNTS:
                        issues.append(
                            TheoremDeriskIssue("enumeration_data_counts", str(actual_counts))
                        )
                    if actual_classes != EXPECTED_ISOMORPHISM_COUNTS:
                        issues.append(
                            TheoremDeriskIssue("enumeration_data_classes", str(actual_classes))
                        )

    provenance = raw.get("source_provenance")
    if not isinstance(provenance, dict):
        issues.append(
            TheoremDeriskIssue("provenance_malformed", "source_provenance must be an object")
        )
    else:
        original = provenance.get("original")
        if not isinstance(original, dict) or (
            original.get("url") != ERS_URL or original.get("theorem") != 6
        ):
            issues.append(
                TheoremDeriskIssue(
                    "original_provenance",
                    "original source must identify ERS PDF, Theorem 6",
                )
            )
        formal = provenance.get("formalization")
        if not isinstance(formal, dict):
            issues.append(
                TheoremDeriskIssue("formalization_malformed", "formalization must be an object")
            )
        else:
            formal_path = _resolve_file(
                root, formal.get("path"), code="formalization_missing", issues=issues
            )
            _check_hash(
                formal_path,
                formal.get("sha256"),
                code="formalization_hash",
                issues=issues,
            )
            repo_rel = str(formal.get("repo_path") or "")
            repo = root / repo_rel
            try:
                origin = _git_value(repo, "remote", "get-url", "origin")
                commit = _git_value(repo, "rev-parse", "HEAD")
            except (OSError, subprocess.CalledProcessError):
                issues.append(
                    TheoremDeriskIssue(
                        "formalization_git",
                        f"cannot inspect git provenance at {repo_rel}",
                    )
                )
            else:
                if origin != formal.get("origin"):
                    issues.append(
                        TheoremDeriskIssue("formalization_origin", f"actual origin={origin}")
                    )
                if commit != formal.get("commit"):
                    issues.append(
                        TheoremDeriskIssue("formalization_commit", f"actual commit={commit}")
                    )

    _resolve_file(root, raw.get("log_path"), code="derisk_log_missing", issues=issues)
    commands = raw.get("commands")
    if not isinstance(commands, list) or not commands:
        issues.append(TheoremDeriskIssue("commands_missing", "commands must be a non-empty array"))
    elif not any("theorem_derisk validate" in str(command) for command in commands):
        issues.append(
            TheoremDeriskIssue(
                "validator_command_missing",
                "commands must include theorem_derisk validate",
            )
        )
    return issues


def validate_for_gate(project_root: Path, derisk_path: Path) -> tuple[bool, str]:
    raw, load_issues = _load_json(derisk_path)
    if raw is None:
        issue = load_issues[0]
        return True, f"[{issue.code}] {issue.detail}"
    issues = validate_theorem_derisk(raw, project_root=project_root)
    if issues:
        issue = issues[0]
        return True, f"[{issue.code}] {issue.detail}"
    return False, ""


def _cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    derisk_path = root / args.derisk
    reject, concern = validate_for_gate(root, derisk_path)
    if reject:
        print(f"REJECT: {concern}", file=sys.stderr)
        return 1
    print(
        "PASS: theorem de-risk verified proof coverage, independent audit, bounded "
        "enumeration, source provenance, and absence of performance metrics"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--project-root", type=Path, default=Path("."))
    validate.add_argument("--derisk", default=DEFAULT_DERISK_PATH)
    validate.set_defaults(func=_cmd_validate)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
