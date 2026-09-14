"""RL training-plot structural/advisory gate.

Operator requirement: **every substantive RL optimizer-step training run
must produce a training-curve plot** so the run is visually monitorable
(reward, loss, grad-norm, KL, entropy, throughput vs optimizer step). A
completed training run with no curve artifact is structurally incomplete
evidence — you cannot eyeball whether the policy actually moved.

This gate is *structural / provenance only*: it checks that a named curve
plot EXISTS under the run's ``plots/`` dir and is therefore tied to the
run. It does NOT judge whether the curve is good, smooth, improving, or
long enough — that is the reviewer's (agent's) call, per the harness
design philosophy ("harness 没有 agent 自己聪明").

Eligibility (deterministic, not mtime-based):

* run dir lives under ``experiments/runs/`` and is an RL optimizer run
  (``optimizer_`` name prefix — the runner's own convention);
* ``status.json`` reports ``state == "completed"``;
* it recorded real optimization, i.e. ``optimizer_steps >=
  MIN_OPTIMIZER_STEPS`` (so crashed 0-step runs and 1-step smoke probes
  do not create plot debt);
* the run is not an infra-fitting probe (name does not contain a token
  from ``PROBE_TOKENS`` such as ``microfit`` / ``memfit`` / ``stepfit`` /
  ``smoke`` / ``preflight`` / ``debug``).

Plot contract: at least one file matching ``plots/*<curve-token>*.{png,
pdf,svg}`` under the run dir, where ``<curve-token>`` is one of
``CURVE_TOKENS``. A named file under ``plots/`` (not just any stray image
anywhere) keeps the gate from being satisfied by an unrelated screenshot.

Advisory by construction: this reports facts at the ``run`` stage (surface a
repair queue while experiments are still evolving, never block) and
STRUCTURAL at the ``analysis`` stage (by analysis time the run is about to
be cited as evidence, so its curve must exist).

CLI:
    python -m argus_skill.skills.rl_training_plots --project-root .
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

MIN_OPTIMIZER_STEPS = 2
RUNS_SUBDIR = Path("experiments") / "runs"
RUN_PREFIX = "optimizer_"
PLOTS_SUBDIR = "plots"
PLOT_SUFFIXES = (".png", ".pdf", ".svg")
# Name tokens that mark an infra-fitting / smoke probe rather than an
# evidence-bearing training run. Matched case-insensitively against the
# run directory name.
PROBE_TOKENS = (
    "smoke",
    "preflight",
    "debug",
    "dry_run",
    "dryrun",
    "sanity",
    "cache",
    "memfit",
    "microfit",
    "stepfit",
)
# Tokens a plot filename must contain to count as a training curve.
CURVE_TOKENS = (
    "training_curve",
    "training_curves",
    "train_curve",
    "train_curves",
    "optimizer_metrics",
    "optimizer_curve",
    "reward_curve",
    "rl_curve",
    "live_curve",
    "live_curves",
)


@dataclass
class RunPlotStatus:
    run_name: str
    optimizer_steps: int
    has_plot: bool
    plot_examples: list[str] = field(default_factory=list)


@dataclass
class RLTrainingPlotsReport:
    runs_dir: Path
    eligible: list[RunPlotStatus] = field(default_factory=list)
    missing: list[RunPlotStatus] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def to_text(self) -> str:
        lines: list[str] = []
        if self.missing:
            lines.append(
                f"{len(self.missing)} completed RL optimizer run(s) have no "
                f"training-curve plot under plots/ (expected one of "
                f"{', '.join(CURVE_TOKENS[:4])}…*.png|pdf|svg); a completed "
                "training run with no curve is not visually monitorable "
                "evidence — emit the plot from the run's own progress.jsonl/"
                "verl_metrics.jsonl:"
            )
            for r in self.missing:
                lines.append(
                    f"  [missing_training_plot] {r.run_name} "
                    f"(optimizer_steps={r.optimizer_steps})"
                )
            lines.append("")
        lines.append("Eligible completed RL optimizer runs:")
        if not self.eligible:
            lines.append(
                "  (none — gate is a no-op until a real optimizer run completes)"
            )
        for r in self.eligible:
            mark = "ok" if r.has_plot else "MISSING"
            ex = f" e.g. {r.plot_examples[0]}" if r.plot_examples else ""
            lines.append(
                f"  {r.run_name}: steps={r.optimizer_steps} plot={mark}{ex}"
            )
        return "\n".join(lines)


def _is_probe(run_name: str) -> bool:
    low = run_name.lower()
    return any(tok in low for tok in PROBE_TOKENS)


def _read_optimizer_steps(run_dir: Path) -> tuple[str, int]:
    """Return (state, optimizer_steps). Prefer status.json; fall back to
    counting optimizer_step events in progress.jsonl."""
    state = ""
    steps = 0
    status_file = run_dir / "status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            state = str(data.get("state", "") or "")
            raw = data.get("optimizer_steps")
            if isinstance(raw, (int, float)):
                steps = int(raw)
        except (OSError, json.JSONDecodeError):
            pass
    if steps == 0:
        prog = run_dir / "progress.jsonl"
        if prog.exists():
            try:
                count = 0
                for line in prog.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("event") == "optimizer_step":
                        count += 1
                if count:
                    steps = count
            except OSError:
                pass
    return state, steps


def _find_curve_plots(run_dir: Path) -> list[str]:
    plots_dir = run_dir / PLOTS_SUBDIR
    if not plots_dir.is_dir():
        return []
    hits: list[str] = []
    for p in sorted(plots_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in PLOT_SUFFIXES:
            continue
        name = p.name.lower()
        if any(tok in name for tok in CURVE_TOKENS):
            try:
                hits.append(p.relative_to(run_dir).as_posix())
            except ValueError:
                hits.append(p.name)
    return hits


def validate_rl_training_plots(project_root: Path) -> RLTrainingPlotsReport:
    runs_dir = project_root / RUNS_SUBDIR
    report = RLTrainingPlotsReport(runs_dir=runs_dir)
    if not runs_dir.is_dir():
        return report  # no-op when there are no runs yet

    for run_dir in sorted(runs_dir.glob(f"{RUN_PREFIX}*")):
        if not run_dir.is_dir() or _is_probe(run_dir.name):
            continue
        state, steps = _read_optimizer_steps(run_dir)
        if state != "completed" or steps < MIN_OPTIMIZER_STEPS:
            continue
        plots = _find_curve_plots(run_dir)
        status = RunPlotStatus(
            run_name=run_dir.name,
            optimizer_steps=steps,
            has_plot=bool(plots),
            plot_examples=plots[:3],
        )
        report.eligible.append(status)
        if not status.has_plot:
            report.missing.append(status)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_rl_training_plots(args.project_root.resolve())
    if args.json:
        payload = {
            "ok": report.ok,
            "runs_dir": str(report.runs_dir),
            "eligible": [
                {
                    "run_name": r.run_name,
                    "optimizer_steps": r.optimizer_steps,
                    "has_plot": r.has_plot,
                    "plot_examples": r.plot_examples,
                }
                for r in report.eligible
            ],
            "missing": [r.run_name for r in report.missing],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_text())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
