"""Signal de-risk evidence validator for the default measured-signal workflow.

The research stage today passes on *form* (a reviewer checklist of problem
clarity / timeline / source diversity / real-search audit, plus shell checks
that only test file existence). It never verifies that the chosen idea is
*alive on this machine* — that its core signal actually moves on a model/data
the local box can run. A beautifully-cited brief can still wrap a dead idea
(observed: a safety idea whose harmful-prompt signal never moved because the
only available frontier model refuses every harmful prompt — discovered 3.5h /
$26 later, in the run stage).

This module turns "the idea survived a minimal judgemental experiment" into a
**mechanically checkable provenance fact**, NOT a scientific verdict. Consistent
with :mod:`argus_skill.skills.run_contract` ("the harness is not smarter than
the agent"), it does not decide whether the science is good — only that a real
measured experiment was run and that its baseline vs proposed
metrics are non-degenerate and move in the claimed direction. Whether the idea
is *worth pursuing* stays with the L2 reviewer.

Artifact:

* :class:`SignalDerisk` — the verdict object, emitted at the END of the research
  stage to ``research/SIGNAL_DERISK.json`` by the
  ``engineer/idea-feasibility-derisk`` skill, with the raw commands + outputs of
  the experiment captured in ``research/SIGNAL_DERISK_LOG.txt``.

The L2 reviewer may run this tool to check arithmetic, provenance, and the
default signal artifact's internal contract. Its exit code does not advance or
hold a stage by itself; the reviewer decides against the active Planner-authored
checklist, which may replace this workflow entirely for another research shape.

CLI::

    python -m argus_skill.verticals.research.signal_derisk validate --project-root . \\
        --derisk research/SIGNAL_DERISK.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1

DEFAULT_DERISK_PATH = "research/SIGNAL_DERISK.json"
DEFAULT_LOG_PATH = "research/SIGNAL_DERISK_LOG.txt"

# Two measured metrics within this are "the same number" (degenerate).
_METRIC_EPS = 1e-9
# Tolerance for the self-reported delta matching proposed - baseline.
_DELTA_CONSISTENCY_EPS = 1e-6

_VALID_DIRECTIONS = ("higher", "lower")
_VALID_VERDICTS = ("pass", "fail")


@dataclass
class DeriskIssue:
    """A single provenance/consistency violation. ``code`` is a stable id."""

    code: str
    detail: str


@dataclass
class SignalDerisk:
    idea_id: str
    metric_name: str
    success_direction: str  # "higher" | "lower"
    model_id: str
    model_source: str
    data_source: str
    n_examples: int
    baseline_metric: float
    proposed_metric: float
    delta: float
    min_meaningful_delta: float
    signal_moved: bool
    cost_usd: float
    duration_s: float
    log_path: str
    verdict: str  # "pass" | "fail"
    commands: list[str] = field(default_factory=list)
    pivoted: bool = False
    smoke_only: bool = False
    notes: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


# Required fields the engineer MUST fill (everything that is a measurement or a
# provenance anchor). ``pivoted`` / ``smoke_only`` / ``notes`` default.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "idea_id",
    "metric_name",
    "success_direction",
    "model_id",
    "model_source",
    "data_source",
    "n_examples",
    "baseline_metric",
    "proposed_metric",
    "delta",
    "min_meaningful_delta",
    "signal_moved",
    "cost_usd",
    "duration_s",
    "log_path",
    "verdict",
    "commands",
)


def _derisk_bool(value: object) -> bool:
    """Strict boolean read.

    ``smoke_only`` waives the movement/direction checks, so it fails closed:
    only a genuine ``true`` (bool, ``1``, or ``"true"``) waives. ``bool("false")``
    would otherwise be truthy and silently exempt a dead idea from the gate.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def load_signal_derisk(path: Path) -> tuple[SignalDerisk | None, list[DeriskIssue]]:
    """Load + structurally validate a SignalDerisk JSON file."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [DeriskIssue("derisk_missing", f"{path} not found")]
    except (OSError, json.JSONDecodeError) as exc:
        return None, [DeriskIssue("derisk_unreadable", f"{path}: {exc}")]
    if not isinstance(raw, dict):
        return None, [DeriskIssue("derisk_malformed", f"{path}: not a JSON object")]

    missing = [k for k in _REQUIRED_FIELDS if k not in raw or raw.get(k) in (None, "")]
    if missing:
        return None, [DeriskIssue(
            "derisk_incomplete", f"missing/empty fields: {', '.join(missing)}")]

    commands_raw = raw.get("commands")
    if not isinstance(commands_raw, list):
        return None, [DeriskIssue(
            "derisk_malformed", "`commands` must be a JSON array of command strings")]
    commands = [str(c) for c in commands_raw if str(c).strip()]

    try:
        derisk = SignalDerisk(
            idea_id=str(raw["idea_id"]),
            metric_name=str(raw["metric_name"]),
            success_direction=str(raw["success_direction"]).strip().lower(),
            model_id=str(raw["model_id"]),
            model_source=str(raw["model_source"]),
            data_source=str(raw["data_source"]),
            n_examples=int(raw["n_examples"]),
            baseline_metric=float(raw["baseline_metric"]),
            proposed_metric=float(raw["proposed_metric"]),
            delta=float(raw["delta"]),
            min_meaningful_delta=float(raw["min_meaningful_delta"]),
            signal_moved=_derisk_bool(raw["signal_moved"]),
            cost_usd=float(raw["cost_usd"]),
            duration_s=float(raw["duration_s"]),
            log_path=str(raw["log_path"]),
            verdict=str(raw["verdict"]).strip().lower(),
            commands=commands,
            pivoted=_derisk_bool(raw.get("pivoted", False)),
            smoke_only=_derisk_bool(raw.get("smoke_only", False)),
            notes=str(raw.get("notes", "")),
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
        )
    except (TypeError, ValueError) as exc:
        return None, [DeriskIssue("derisk_malformed", f"{path}: {exc}")]
    return derisk, []


def validate_signal_derisk(
    d: SignalDerisk, *, project_root: Path
) -> list[DeriskIssue]:
    """Provenance + non-degeneracy checks. Does NOT itself act on ``verdict`` /
    ``pivoted`` (that is :func:`validate_for_gate`'s job) — it only reports when
    the measured facts are degenerate or contradict the
    self-report."""
    issues: list[DeriskIssue] = []

    # --- field sanity ---
    if d.verdict not in _VALID_VERDICTS:
        issues.append(DeriskIssue(
            "bad_verdict_field",
            f"verdict={d.verdict!r} not in {_VALID_VERDICTS}"))
    if d.success_direction not in _VALID_DIRECTIONS:
        issues.append(DeriskIssue(
            "bad_direction_field",
            f"success_direction={d.success_direction!r} not in {_VALID_DIRECTIONS}"))
    if d.n_examples < 1:
        issues.append(DeriskIssue(
            "no_examples", f"n_examples={d.n_examples} < 1; nothing was scored"))

    # --- measured resource facts ---
    if d.cost_usd < 0:
        issues.append(DeriskIssue("negative_cost", f"cost_usd={d.cost_usd} < 0"))
    if d.duration_s < 0:
        issues.append(DeriskIssue("negative_duration", f"duration_s={d.duration_s} < 0"))

    # --- provenance: the log must carry the real run behind the numbers ---
    if not d.commands:
        issues.append(DeriskIssue(
            "no_commands",
            "`commands` is empty; record the exact commands that hit the "
            "model/API/data so a reviewer can audit the log"))
    log_abs = (Path(project_root) / d.log_path)
    try:
        log_size = log_abs.stat().st_size
    except OSError:
        log_size = -1
    if log_size < 0:
        issues.append(DeriskIssue(
            "log_missing", f"{d.log_path} not found; capture raw commands + outputs"))
    elif log_size == 0:
        issues.append(DeriskIssue(
            "log_empty", f"{d.log_path} is empty; it must hold the real run's "
            "commands and stdout/stderr"))

    # --- delta consistency: catch a hand-edited delta ---
    expected_delta = d.proposed_metric - d.baseline_metric
    if abs(d.delta - expected_delta) > _DELTA_CONSISTENCY_EPS:
        issues.append(DeriskIssue(
            "delta_inconsistent",
            f"delta={d.delta:g} != proposed-baseline={expected_delta:g}; the "
            "delta was edited away from the measured numbers"))

    # A smoke/wiring-only screen waives the movement + direction bounds (it is an
    # infra check, not an idea-alive proof) — but it may NOT later be cited as
    # "the idea is alive" (reviewer dim-8 enforces that). Log checks still hold.
    if d.smoke_only:
        return issues

    # --- non-degeneracy: the signal must actually move ---
    if d.min_meaningful_delta <= 0:
        issues.append(DeriskIssue(
            "bad_min_delta",
            f"min_meaningful_delta={d.min_meaningful_delta:g} <= 0; declare the "
            "smallest delta that counts as 'moved' BEFORE running"))
    if abs(expected_delta) <= _METRIC_EPS:
        issues.append(DeriskIssue(
            "baseline_equals_proposed",
            f"baseline_metric={d.baseline_metric:g} == proposed_metric="
            f"{d.proposed_metric:g}; the condition makes no measurable difference "
            "(dead idea) — PIVOT"))
    elif d.min_meaningful_delta > 0 and abs(d.delta) < d.min_meaningful_delta:
        issues.append(DeriskIssue(
            "signal_unmoved",
            f"|delta|={abs(d.delta):g} < min_meaningful_delta="
            f"{d.min_meaningful_delta:g}; the core signal did not move (dead "
            "idea) — PIVOT"))
    # --- direction: a metric that moved the WRONG way is not a pass ---
    elif d.success_direction == "higher" and d.delta < d.min_meaningful_delta:
        issues.append(DeriskIssue(
            "wrong_direction",
            f"success_direction=higher needs delta >= {d.min_meaningful_delta:g} "
            f"but delta={d.delta:g}; the idea hurt the metric"))
    elif d.success_direction == "lower" and d.delta > -d.min_meaningful_delta:
        issues.append(DeriskIssue(
            "wrong_direction",
            f"success_direction=lower needs delta <= {-d.min_meaningful_delta:g} "
            f"but delta={d.delta:g}; the idea hurt the metric"))

    # --- self-report agreement ---
    truly_moved = (
        abs(expected_delta) > _METRIC_EPS
        and d.min_meaningful_delta > 0
        and abs(d.delta) >= d.min_meaningful_delta
    )
    if d.signal_moved and not truly_moved:
        issues.append(DeriskIssue(
            "signal_moved_overclaim",
            "signal_moved=true but the measured delta does not clear "
            "min_meaningful_delta; do not overclaim movement"))
    if d.verdict == "pass" and d.pivoted:
        issues.append(DeriskIssue(
            "pass_while_pivoted",
            "verdict=pass while pivoted=true is contradictory; a pivoted idea "
            "did not pass"))
    return issues


def validate_for_gate(
    project_root: Path, derisk_path: Path
) -> tuple[bool, str]:
    """Stage-gate interlock for the research stage.

    Returns ``(reject, concern)``. ``reject`` is True when the locked idea has
    NOT survived a real, non-degenerate, in-budget minimal experiment in the
    claimed direction. ``concern`` names the first actionable violation. A
    legitimate ``verdict=fail`` / ``pivoted`` also rejects: the stage must hold
    until a passing de-risk exists (the pivot rule), so the same check enforces
    "do not enter plan on a dead idea". Scientific worth is left to the reviewer.
    """
    derisk, load_issues = load_signal_derisk(derisk_path)
    if derisk is None:
        detail = load_issues[0].detail if load_issues else ""
        msg = (f"produce {DEFAULT_DERISK_PATH} via the engineer/idea-feasibility-"
               "derisk skill before leaving the research stage")
        return True, f"{msg} ({detail})" if detail else msg

    issues = validate_signal_derisk(derisk, project_root=project_root)
    if issues:
        return True, _first_concern(issues)

    if derisk.verdict == "fail":
        return True, (
            "[verdict_fail] the locked idea failed its signal de-risk; PIVOT the "
            "idea (update RESEARCH_BRIEF.md + IDEA_REJECTION_LOG.md) and re-run "
            "engineer/idea-feasibility-derisk — do not enter plan on a dead idea")
    if derisk.pivoted:
        return True, (
            "[pivoted] the idea was pivoted; re-run engineer/idea-feasibility-"
            "derisk on the new idea so SIGNAL_DERISK.json reflects a passing screen")
    return False, ""


def _first_concern(issues: list[DeriskIssue], *, fallback: str = "") -> str:
    if not issues:
        return fallback
    head = issues[0]
    return f"[{head.code}] {head.detail}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    derisk_path = root / args.derisk
    reject, concern = validate_for_gate(root, derisk_path)
    if reject:
        print(f"REJECT: {concern}", file=sys.stderr)
        return 1
    print("OK: the locked idea survived a real, non-degenerate, in-budget signal "
          "de-risk in the claimed direction")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser(
        "validate",
        help="diagnose a missing/degenerate/over-budget/"
        "fabricated signal de-risk")
    # --project-root lives on the subparser so the documented
    # `validate --project-root . --derisk ...` form parses; argparse global
    # options must otherwise precede the subcommand.
    v.add_argument("--project-root", type=Path, default=Path("."))
    v.add_argument("--derisk", default=DEFAULT_DERISK_PATH)
    v.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
