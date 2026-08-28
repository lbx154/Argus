"""Advisory finding generator (formerly F3 anti-mediocrity gate).

**Architectural note (post-c6b11d3 rewrite):**

The earlier version of this module hard-coded research-quality thresholds
(``min_delta = 0.02``, ``min_benchmark_families = 3``) and counted gate
failures into the per-round exit code. That violated argus-skill's core
rule — *the harness must not make research-quality judgments; that's the
reviewer agent's job* (see ``README.md`` and ``docs/PRINCIPLES.md``).

This module is now a **pure fact extractor**. It loads aggregate rows
from ``benchmarks/evidence/*/summary.tsv`` and surfaces structured facts:

- which conditions have clean aggregate evidence
- best reward per condition
- delta between a proposed and a baseline condition (if both supplied)
- distinct benchmark families (dataset_id) seen across bundles

It does **not**:

- compare any number against a threshold
- emit pass / fail / verdict
- affect any exit code beyond reporting structural I/O errors

The output is meant to be read directly by an agent so the reviewer can make
the call from surfaced facts. The CLI exits 0 unconditionally on a successful read;
the only non-zero exit is for an I/O / parse error, which is structural
(the user gave us a bad ``--evidence-root`` etc.).

CLI:
    python -m argus_skill.skills.anti_mediocrity \\
        --project-root . \\
        [--proposed-condition X --baseline-condition Y]
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EVIDENCE_ROOT = Path("benchmarks/evidence")
# Aggregates with this fraction or more errored trials are flagged in the
# finding as "noisy" so the reviewer can downweight them. We do NOT use
# this fraction to refuse to show the row — surfacing dirty aggregates
# is still useful (the reviewer may decide they're fine for a smoke
# baseline). The threshold is purely a presentation label, NOT a verdict.
_NOISY_ERROR_FRACTION = 0.25


@dataclass
class AggregateRow:
    """A parsed aggregate row from a bundle's summary.tsv. Pure facts."""

    bundle: str
    condition: str
    reward: float | None
    n_total_trials: int | None
    n_completed_trials: int | None
    n_errored_trials: int | None

    @property
    def is_noisy(self) -> bool:
        """Heuristic: 25%+ errored trials → mark as noisy for reviewer.

        Not a verdict; just a flag the reviewer can take or leave.
        """
        if not self.n_total_trials or self.n_total_trials <= 0:
            return False
        errored = self.n_errored_trials or 0
        return errored / self.n_total_trials >= _NOISY_ERROR_FRACTION


@dataclass
class MediocrityFinding:
    """Structured facts about evidence — no verdict.

    The reviewer agent reads these facts and decides whether the project
    is publishable. The harness does not make that call.
    """

    project_root: Path
    proposed_condition: str | None
    baseline_condition: str | None
    aggregates: list[AggregateRow] = field(default_factory=list)
    benchmark_families: list[str] = field(default_factory=list)
    # Only structural / I/O issues; never quality verdicts.
    structural_errors: list[str] = field(default_factory=list)

    @property
    def best_proposed_reward(self) -> float | None:
        if not self.proposed_condition:
            return None
        rewards = [
            a.reward for a in self.aggregates
            if a.condition == self.proposed_condition and a.reward is not None
        ]
        return max(rewards) if rewards else None

    @property
    def best_baseline_reward(self) -> float | None:
        if not self.baseline_condition:
            return None
        rewards = [
            a.reward for a in self.aggregates
            if a.condition == self.baseline_condition and a.reward is not None
        ]
        return max(rewards) if rewards else None

    @property
    def proposed_minus_baseline(self) -> float | None:
        p = self.best_proposed_reward
        b = self.best_baseline_reward
        if p is None or b is None:
            return None
        return p - b

    @property
    def ok(self) -> bool:
        """True iff the read itself succeeded (i.e. no I/O errors).

        NEVER conflate with "research is good". Quality judgment is the
        reviewer's. This property is here only so the CLI can decide its
        exit code: 0 = facts surfaced cleanly, 1 = couldn't read.
        """
        return not self.structural_errors

    def to_dict(self) -> dict:
        return {
            "project_root": str(self.project_root),
            "proposed_condition": self.proposed_condition,
            "baseline_condition": self.baseline_condition,
            "best_proposed_reward": self.best_proposed_reward,
            "best_baseline_reward": self.best_baseline_reward,
            "proposed_minus_baseline": self.proposed_minus_baseline,
            "benchmark_families": list(self.benchmark_families),
            "n_aggregates": len(self.aggregates),
            "aggregates": [
                {
                    "bundle": a.bundle,
                    "condition": a.condition,
                    "reward": a.reward,
                    "n_total_trials": a.n_total_trials,
                    "n_completed_trials": a.n_completed_trials,
                    "n_errored_trials": a.n_errored_trials,
                    "is_noisy": a.is_noisy,
                }
                for a in self.aggregates
            ],
            "ok": self.ok,
            "structural_errors": list(self.structural_errors),
        }


def _coerce_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        f = _coerce_float(value)
        return int(f) if f is not None else None


def _load_aggregate_rows(
    project_root: Path, evidence_root: Path
) -> tuple[list[AggregateRow], list[str]]:
    """Scan ``benchmarks/evidence/*/summary.tsv`` for aggregate rows.

    Returns ``(rows, structural_errors)``. structural_errors collects
    I/O / parse problems only — never quality judgments.
    """
    rows: list[AggregateRow] = []
    errors: list[str] = []
    abs_root = (project_root / evidence_root).resolve()
    if not abs_root.exists():
        # Not an error — projects in incubating stage legitimately have
        # no evidence yet. The reviewer reads the empty finding and rules.
        return (rows, errors)
    for bundle_dir in sorted(abs_root.iterdir()):
        if not bundle_dir.is_dir():
            continue
        summary = bundle_dir / "summary.tsv"
        if not summary.exists():
            continue
        try:
            with summary.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    if (row.get("row_kind") or "").strip() != "aggregate":
                        continue
                    rows.append(
                        AggregateRow(
                            bundle=str(bundle_dir.relative_to(project_root)),
                            condition=(row.get("condition") or "").strip(),
                            reward=_coerce_float(row.get("reward") or ""),
                            n_total_trials=_coerce_int(row.get("n_total_trials") or ""),
                            n_completed_trials=_coerce_int(
                                row.get("n_completed_trials") or ""
                            ),
                            n_errored_trials=_coerce_int(
                                row.get("n_errored_trials") or ""
                            ),
                        )
                    )
        except (OSError, csv.Error) as exc:
            errors.append(
                f"could not parse {summary.relative_to(project_root)}: {exc}"
            )
    return (rows, errors)


def _extract_benchmark_families(
    project_root: Path, evidence_root: Path
) -> tuple[list[str], list[str]]:
    """Collect distinct ``dataset_id`` values from manifest.json / metadata.

    Returns ``(sorted_unique_dataset_ids, structural_errors)``.
    """
    families: set[str] = set()
    errors: list[str] = []
    abs_root = (project_root / evidence_root).resolve()
    if not abs_root.exists():
        return (sorted(families), errors)
    candidate_names = ("manifest.json", "metadata.json", "metadata.tsv")
    for bundle_dir in abs_root.iterdir():
        if not bundle_dir.is_dir():
            continue
        for name in candidate_names:
            path = bundle_dir / name
            if not path.exists():
                continue
            try:
                if name.endswith(".json"):
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    ds = _extract_dataset_id(payload)
                    if ds:
                        families.add(ds)
                elif name.endswith(".tsv"):
                    with path.open("r", encoding="utf-8", newline="") as fh:
                        reader = csv.DictReader(fh, delimiter="\t")
                        for row in reader:
                            ds = (row.get("dataset_id") or "").strip()
                            if ds:
                                families.add(ds)
            except (OSError, json.JSONDecodeError, csv.Error) as exc:
                errors.append(
                    f"could not parse {path.relative_to(project_root)}: {exc}"
                )
    return (sorted(families), errors)


def _extract_dataset_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    ds = payload.get("dataset_id")
    if isinstance(ds, str) and ds.strip():
        return ds.strip()
    md = payload.get("metadata")
    if isinstance(md, dict):
        ds = md.get("dataset_id")
        if isinstance(ds, str) and ds.strip():
            return ds.strip()
    return None


def collect_mediocrity_finding(
    project_root: Path,
    *,
    proposed_condition: str | None = None,
    baseline_condition: str | None = None,
    evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
) -> MediocrityFinding:
    """Read evidence and return a structured fact finding.

    The finding contains numbers (best rewards, delta, family count, etc.)
    but **no verdicts**. The reviewer agent reads the finding and decides
    whether the project is publishable.

    Pass ``proposed_condition`` / ``baseline_condition`` to enable
    delta computation between named conditions; they are pure labels (not
    thresholds), so passing them is safe — they never change pass/fail
    semantics because there is no pass/fail here.
    """
    finding = MediocrityFinding(
        project_root=project_root.resolve(),
        proposed_condition=proposed_condition,
        baseline_condition=baseline_condition,
    )
    rows, row_errors = _load_aggregate_rows(project_root, evidence_root)
    families, family_errors = _extract_benchmark_families(project_root, evidence_root)
    finding.aggregates = rows
    finding.benchmark_families = families
    finding.structural_errors = row_errors + family_errors
    return finding


# ---------------------------------------------------------------------------
# Reviewer-facing checklist text (rendered into stage_check stdout)
# ---------------------------------------------------------------------------


_REVIEWER_CHECKLIST = """\
Reviewer judgement points (you decide; the harness will not):

  1. Is the proposed condition meaningfully ahead of the baseline?
     - Compare the delta below against typical noise for this benchmark
       family. Reward scales differ across benchmarks; do not apply a
       universal "X% improvement" rule.
  2. Did the baseline reproduce strongly enough to anchor the comparison?
     - A noisy or partial baseline run undercuts a positive delta.
       The aggregates below carry an "[NOISY]" flag at 25% errored
       trials so you can spot weak reproductions.
  3. Is the evidence broad enough for the claim being made?
     - Count benchmark families below. Some claims need 2 strong ones;
       some need 5. There is no fixed threshold.
  4. Are the per-family rewards consistent, or does one carry the whole
     story? Look at all aggregates, not just the best.
"""


def format_finding(finding: MediocrityFinding) -> str:
    """Render the finding as the prompt block the reviewer reads."""
    lines: list[str] = [
        "## Evidence fact dump (no harness verdict — reviewer rules)",
        "",
    ]
    if finding.structural_errors:
        lines.append("Structural read errors:")
        for err in finding.structural_errors:
            lines.append(f"  - {err}")
        lines.append("")

    if finding.proposed_condition or finding.baseline_condition:
        lines.append(
            f"Comparison labels: proposed={finding.proposed_condition!r}, "
            f"baseline={finding.baseline_condition!r}"
        )
    bp = finding.best_proposed_reward
    bb = finding.best_baseline_reward
    delta = finding.proposed_minus_baseline
    if bp is not None or bb is not None:
        lines.append(f"  best_proposed_reward = {bp!r}")
        lines.append(f"  best_baseline_reward = {bb!r}")
        if delta is not None:
            lines.append(f"  proposed - baseline  = {delta:+.6f}")
        lines.append("")

    lines.append(
        f"Benchmark families covered ({len(finding.benchmark_families)}): "
        + (", ".join(finding.benchmark_families) if finding.benchmark_families else "(none)")
    )
    lines.append("")

    if finding.aggregates:
        lines.append(f"Aggregate rows ({len(finding.aggregates)}):")
        for a in finding.aggregates:
            noisy = " [NOISY]" if a.is_noisy else ""
            lines.append(
                f"  - {a.condition!r:30s} reward={a.reward!r:>10}  "
                f"trials={a.n_completed_trials}/{a.n_total_trials} "
                f"errored={a.n_errored_trials}{noisy}  "
                f"({a.bundle})"
            )
        lines.append("")
    else:
        lines.append("No aggregate rows found in evidence bundles.")
        lines.append("")

    lines.append(_REVIEWER_CHECKLIST.rstrip())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--proposed-condition",
        type=str,
        default=None,
        help="Optional label for the proposed condition (used only to "
             "compute the delta against the baseline; never as a threshold).",
    )
    parser.add_argument(
        "--baseline-condition",
        type=str,
        default=None,
        help="Optional label for the baseline condition.",
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=DEFAULT_EVIDENCE_ROOT,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    finding = collect_mediocrity_finding(
        args.project_root,
        proposed_condition=args.proposed_condition,
        baseline_condition=args.baseline_condition,
        evidence_root=args.evidence_root,
    )

    if args.json:
        print(json.dumps(finding.to_dict(), indent=2))
    else:
        print(format_finding(finding))

    # Exit code is structural-only: 0 unless we couldn't read evidence.
    # Quality verdicts are the reviewer's job; this command never blocks
    # a round for "research is mediocre".
    return 0 if finding.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
