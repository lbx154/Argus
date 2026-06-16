"""Run-evidence health structural gate (Opt #6).

Anti-fab gate that walks evidence bundles for ``raw_status: "call_failed"``
in per-task verifier outputs. ``summary.tsv``'s ``n_errored_trials``
only counts trials that errored at the *trial* level; verifier-side
``call_failed`` (the verifying LLM call itself broke) is invisible in
``summary.tsv`` even though it means the reward number for that trial
is bogus.

Empirical observation (the bundle this gate was written against):

* a prompt-only study bundle had 29
  ``ctrf.json`` files; **7 (24%)** carry ``raw_status: "call_failed"``,
  yet ``summary.tsv`` reports ``accepted=True, reward=1`` for all 12
  tasks. A paper claiming "method X reaches reward=1" off this bundle
  is structurally unsound — the verifier never ran for ~24% of judged
  outcomes.

The gate is structural (not quality): it does not score "is the
reward delta meaningful"; it only enforces "the underlying reward
numbers are not built on broken verifier calls". Threshold is a
floor (25%) — below that we surface the count as advisory text on
the GateResult detail; at-or-above blocks.

CLI:
    python -m argus_skill.skills.run_evidence_health --project-root .
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

MAX_CALL_FAILED_FRACTION = 0.25
EVIDENCE_DIR = Path("benchmarks") / "evidence"
# We scan any ctrf.json under a bundle's jobs/raw/. There are multiple
# verification-reward* subdirs (exported, latest, etc.); count distinct
# task-level verifier runs to avoid double-counting reruns.
CTRF_GLOB = "jobs/raw/**/verifier/ctrf.json"


@dataclass
class BundleHealth:
    bundle_name: str
    ctrf_total: int = 0
    ctrf_call_failed: int = 0
    failing_examples: list[str] = field(default_factory=list)

    @property
    def call_failed_fraction(self) -> float:
        if self.ctrf_total == 0:
            return 0.0
        return self.ctrf_call_failed / self.ctrf_total


@dataclass
class HealthIssue:
    code: str
    detail: str


@dataclass
class RunEvidenceHealthReport:
    evidence_dir: Path
    bundles: list[BundleHealth] = field(default_factory=list)
    issues: list[HealthIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_text(self) -> str:
        lines = []
        if not self.ok:
            lines.append(
                f"{len(self.issues)} run-evidence health violation(s); "
                "reward numbers below are built on broken verifier runs:"
            )
            for issue in self.issues:
                lines.append(f"  [{issue.code}] {issue.detail}")
            lines.append("")
        lines.append("Per-bundle verifier health:")
        for b in self.bundles:
            lines.append(
                f"  {b.bundle_name}: {b.ctrf_call_failed}/{b.ctrf_total} "
                f"call_failed ({b.call_failed_fraction:.1%})"
            )
            if b.failing_examples:
                for ex in b.failing_examples[:3]:
                    lines.append(f"      e.g. {ex}")
                if len(b.failing_examples) > 3:
                    lines.append(f"      ... and {len(b.failing_examples) - 3} more")
        if not self.bundles:
            lines.append("  (no evidence bundles found — gate is a no-op)")
        return "\n".join(lines)


def _scan_ctrf(path: Path) -> bool:
    """Return True iff this ctrf.json carries at least one
    ``raw_status: "call_failed"`` somewhere in its tree."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    def walk(node: object) -> bool:
        if isinstance(node, dict):
            if node.get("raw_status") == "call_failed":
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(v) for v in node)
        return False

    return walk(data)


def _scan_bundle(bundle_dir: Path) -> BundleHealth:
    health = BundleHealth(bundle_name=bundle_dir.name)
    ctrf_files = sorted(bundle_dir.glob(CTRF_GLOB))
    health.ctrf_total = len(ctrf_files)
    for ctrf in ctrf_files:
        if _scan_ctrf(ctrf):
            health.ctrf_call_failed += 1
            try:
                rel = ctrf.relative_to(bundle_dir).as_posix()
            except ValueError:
                rel = ctrf.name
            health.failing_examples.append(rel)
    return health


def validate_run_evidence_health(project_root: Path) -> RunEvidenceHealthReport:
    evidence_root = project_root / EVIDENCE_DIR
    report = RunEvidenceHealthReport(evidence_dir=evidence_root)
    if not evidence_root.is_dir():
        return report  # no-op when there's no evidence to check

    for bundle in sorted(p for p in evidence_root.iterdir() if p.is_dir()):
        health = _scan_bundle(bundle)
        # Only report bundles that had any verifier runs at all; empty
        # bundles are typically scaffolds without raw jobs yet.
        if health.ctrf_total == 0:
            continue
        report.bundles.append(health)
        if health.call_failed_fraction >= MAX_CALL_FAILED_FRACTION:
            report.issues.append(HealthIssue(
                code="high_verifier_call_failed_rate",
                detail=(
                    f"bundle {health.bundle_name!r}: "
                    f"{health.ctrf_call_failed}/{health.ctrf_total} "
                    f"verifier runs returned call_failed "
                    f"({health.call_failed_fraction:.1%}, threshold "
                    f"{MAX_CALL_FAILED_FRACTION:.0%}); the rewards in this "
                    "bundle's summary.tsv are built on broken verifier "
                    "calls and cannot be cited as evidence — rerun the "
                    "failed tasks or quarantine the bundle"
                ),
            ))

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_run_evidence_health(args.project_root.resolve())
    if args.json:
        payload = {
            "ok": report.ok,
            "evidence_dir": str(report.evidence_dir),
            "bundles": [
                {
                    "bundle_name": b.bundle_name,
                    "ctrf_total": b.ctrf_total,
                    "ctrf_call_failed": b.ctrf_call_failed,
                    "call_failed_fraction": b.call_failed_fraction,
                    "failing_examples": b.failing_examples[:10],
                }
                for b in report.bundles
            ],
            "issues": [
                {"code": i.code, "detail": i.detail} for i in report.issues
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_text())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
