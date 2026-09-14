"""Evidence chain validator (F4).

Validates the integrity of the claim → evidence → bundle → BUILD_INFO chain
that backs paper claims. Every claim in ``paper/claims_to_evidence.tsv`` must
trace down to (1) an existing evidence file, (2) a bundle directory with
``BUILD_INFO.md``, and (3) no tainted-bundle citation unless the claim
explicitly marks itself as ``historical_only`` or ``broken_current_evidence``.

This is the integrity side of the harness/agent boundary documented in
``AGENTS.md`` and ``docs/PRINCIPLES.md``: claim ↔ evidence ↔ bundle ↔
BUILD_INFO must line up, or the project's ``review`` stage fails and the draft
cannot advance to ``submission``.

CLI:
    python -m argus_skill.skills.evidence_chain \\
        --project-root . \\
        [--claims-tsv paper/claims_to_evidence.tsv]

Exits non-zero with a JSON report on stdout when any chain is broken.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

DEFAULT_CLAIMS_TSV = Path("paper/claims_to_evidence.tsv")

# Claim status values that are allowed to point at tainted/historical bundles
# without failing validation. Anything else must point only at clean evidence.
_HISTORICAL_STATUSES = frozenset(
    {"historical_only", "broken_current_evidence", "removed"}
)

# Evidence column names in claims_to_evidence.tsv. Extra columns named
# ``evidence_4``, ``evidence_5``, ... are also picked up automatically.
_EVIDENCE_COLUMN_PREFIX = "evidence_"

# Markers that identify a tainted-evidence bundle. Cross-referenced with
# ``docs/KNOWN_BUGS.md``; if a bundle's BUILD_INFO contains any of these or
# its path appears in the tainted list, citing it from a non-historical claim
# is a chain-integrity violation.
_TAINTED_MARKERS = (
    "TAINTED",
    "DO NOT CITE",
    "do not cite",
)


@dataclass
class ChainIssue:
    """One specific chain-integrity violation."""

    code: str
    claim_id: str
    evidence_path: str
    detail: str


@dataclass
class ChainReport:
    """Aggregated report from one validation pass."""

    project_root: Path
    claims_tsv: Path
    claims_checked: int = 0
    evidence_paths_checked: int = 0
    bundles_checked: int = 0
    issues: list[ChainIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict:
        return {
            "project_root": str(self.project_root),
            "claims_tsv": str(self.claims_tsv),
            "claims_checked": self.claims_checked,
            "evidence_paths_checked": self.evidence_paths_checked,
            "bundles_checked": self.bundles_checked,
            "ok": self.ok,
            "issue_count": len(self.issues),
            "issues": [
                {
                    "code": i.code,
                    "claim_id": i.claim_id,
                    "evidence_path": i.evidence_path,
                    "detail": i.detail,
                }
                for i in self.issues
            ],
        }


def _evidence_columns(fieldnames: Iterable[str]) -> list[str]:
    """Return evidence_1, evidence_2, ... columns sorted by suffix."""
    matched: list[tuple[int, str]] = []
    pattern = re.compile(rf"^{re.escape(_EVIDENCE_COLUMN_PREFIX)}(\d+)$")
    for name in fieldnames:
        m = pattern.match(name)
        if m:
            matched.append((int(m.group(1)), name))
    matched.sort()
    return [name for _, name in matched]


def _read_claims_rows(claims_tsv: Path) -> tuple[list[dict[str, str]], list[str]]:
    with claims_tsv.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            return [], []
        return list(reader), list(reader.fieldnames)


def _bundle_root_for(evidence_relpath: str) -> str | None:
    """If the evidence path lives under ``benchmarks/evidence/<bundle>/...``
    or ``experiments/<run>/...``, return the bundle root. Otherwise None.

    BUILD_INFO is only required for files under known bundle roots; raw
    paper artifacts (``paper/artifacts/*.tsv``) and protocol docs
    (``docs/USER_STUDY_PROTOCOL.md``) are not bundles and are checked only
    for existence.
    """
    parts = evidence_relpath.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "benchmarks" and parts[1] == "evidence":
        if len(parts) >= 3:
            return "/".join(parts[:3])
    if len(parts) >= 2 and parts[0] == "experiments":
        return "/".join(parts[:2])
    return None


def _bundle_is_tainted(project_root: Path, bundle_rel: str) -> tuple[bool, str]:
    """Check BUILD_INFO and PLAN/RESULTS for tainted markers. Returns
    (is_tainted, reason)."""
    candidates = ["BUILD_INFO.md", "PLAN.md", "RESULTS.md", "EXEMPT.md"]
    for fname in candidates:
        path = project_root / bundle_rel / fname
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for marker in _TAINTED_MARKERS:
            if marker in text:
                return True, f"{fname} contains marker {marker!r}"
    return False, ""


def _claim_status_allows_tainted(status: str) -> bool:
    return status.strip().lower() in _HISTORICAL_STATUSES


def validate_evidence_chain(
    project_root: Path,
    *,
    claims_tsv: Path | None = None,
) -> ChainReport:
    """Validate the claim → evidence → bundle → BUILD_INFO chain.

    Returns a :class:`ChainReport`. ``report.ok`` is True iff zero issues
    were found. Caller decides whether to exit non-zero.
    """
    claims_tsv = (claims_tsv or (project_root / DEFAULT_CLAIMS_TSV)).resolve()
    project_root = project_root.resolve()
    report = ChainReport(project_root=project_root, claims_tsv=claims_tsv)

    if not claims_tsv.exists():
        report.issues.append(
            ChainIssue(
                code="claims_tsv_missing",
                claim_id="",
                evidence_path=str(claims_tsv),
                detail=f"claims_to_evidence TSV not found at {claims_tsv}",
            )
        )
        return report

    rows, fieldnames = _read_claims_rows(claims_tsv)
    evidence_cols = _evidence_columns(fieldnames)
    if not evidence_cols:
        report.issues.append(
            ChainIssue(
                code="claims_tsv_schema",
                claim_id="",
                evidence_path=str(claims_tsv),
                detail=(
                    f"no evidence_* columns found in {claims_tsv.name}; "
                    f"got columns {fieldnames}"
                ),
            )
        )
        return report

    seen_bundles: set[str] = set()

    for row in rows:
        claim_id = (row.get("claim_id") or "").strip()
        status = (row.get("status") or "").strip()
        allow_tainted = _claim_status_allows_tainted(status)
        report.claims_checked += 1

        if not claim_id:
            report.issues.append(
                ChainIssue(
                    code="claim_id_blank",
                    claim_id="",
                    evidence_path="",
                    detail=f"row missing claim_id; columns: {row}",
                )
            )
            continue

        has_any_evidence = False
        for col in evidence_cols:
            relpath = (row.get(col) or "").strip()
            if not relpath:
                continue
            has_any_evidence = True
            report.evidence_paths_checked += 1
            abspath = project_root / relpath
            if not abspath.exists():
                report.issues.append(
                    ChainIssue(
                        code="evidence_path_missing",
                        claim_id=claim_id,
                        evidence_path=relpath,
                        detail=(
                            f"file does not exist at {abspath}; "
                            f"claim is dangling"
                        ),
                    )
                )
                continue

            bundle_rel = _bundle_root_for(relpath)
            if bundle_rel is None:
                # Not under a bundle root — only existence is required.
                continue

            if bundle_rel not in seen_bundles:
                seen_bundles.add(bundle_rel)
                report.bundles_checked += 1
                build_info = project_root / bundle_rel / "BUILD_INFO.md"
                if not build_info.exists():
                    report.issues.append(
                        ChainIssue(
                            code="bundle_missing_build_info",
                            claim_id=claim_id,
                            evidence_path=relpath,
                            detail=(
                                f"bundle {bundle_rel} has no BUILD_INFO.md "
                                f"(required for provenance)"
                            ),
                        )
                    )

            is_tainted, reason = _bundle_is_tainted(project_root, bundle_rel)
            if is_tainted and not allow_tainted:
                report.issues.append(
                    ChainIssue(
                        code="tainted_bundle_cited",
                        claim_id=claim_id,
                        evidence_path=relpath,
                        detail=(
                            f"claim status={status!r} cites tainted bundle "
                            f"{bundle_rel}; {reason}. "
                            f"Move the claim to status=historical_only/"
                            f"broken_current_evidence, or replace the "
                            f"evidence with a clean bundle."
                        ),
                    )
                )

        if not has_any_evidence:
            report.issues.append(
                ChainIssue(
                    code="claim_has_no_evidence",
                    claim_id=claim_id,
                    evidence_path="",
                    detail=f"claim {claim_id!r} has no evidence_* columns set",
                )
            )

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="Project root (defaults to cwd).",
    )
    parser.add_argument(
        "--claims-tsv",
        type=Path,
        default=None,
        help=(
            "Path to claims_to_evidence.tsv. Defaults to "
            "<project-root>/paper/claims_to_evidence.tsv."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON report on stdout instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    report = validate_evidence_chain(
        args.project_root, claims_tsv=args.claims_tsv
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_text_report(report)

    return 0 if report.ok else 1


def _print_text_report(report: ChainReport) -> None:
    print(f"Evidence chain check: {report.claims_tsv}")
    print(
        f"  claims_checked={report.claims_checked} "
        f"evidence_paths_checked={report.evidence_paths_checked} "
        f"bundles_checked={report.bundles_checked}"
    )
    if report.ok:
        print("OK — every claim resolves to existing evidence + BUILD_INFO.")
        return

    print(f"FAIL — {len(report.issues)} issue(s):")
    by_code: dict[str, list[ChainIssue]] = {}
    for issue in report.issues:
        by_code.setdefault(issue.code, []).append(issue)
    for code in sorted(by_code):
        bucket = by_code[code]
        print(f"  [{code}] x{len(bucket)}")
        for issue in bucket:
            head = issue.claim_id or "<no-claim>"
            tail = f" ({issue.evidence_path})" if issue.evidence_path else ""
            print(f"    - {head}{tail}: {issue.detail}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
