"""No-op / undifferentiated-treatment detector for RL optimizer runs.

Motivation
----------
The harness already enforces that experiments are *real* (no synthetic
benchmarks, full-scale evidence, evaluator authenticity, ...). It had **no**
check that the *proposed method actually differs from the baseline*. A run can
pass every anti-fraud gate while the "novel method" is, by construction,
identical to the baseline — a real run, real data, real matrix, but the
treatment changes nothing.

Concrete failure this gate targets (observed in the wild): a "CV-GRPO" reward
``compute_score_cv = base_score * confidence`` where ``confidence`` is ~always
``1.0`` on the benchmark, so the proposed reward equalled the vanilla reward on
~100% of rollouts. The two conditions differed only by the reward-function
*name* in the config; their reward outcomes were statistically
indistinguishable. Days of GPU time were spent training a no-op.

What this gate computes (robust + general)
------------------------------------------
For each (proposed, baseline) condition pair it reads each condition's
``config_snapshot.json`` and aggregate reward stats, then surfaces:

* **config differentiation** — does the proposed run's config differ from the
  baseline beyond labels/paths? It flags the two glaring no-op shapes:
  ``differs only by reward-function name`` and ``identical command except
  labels`` (a relabelled duplicate).
* **outcome equivalence** — are the two conditions' ``reward_mean`` and
  ``frac_reward_zero_std`` statistically indistinguishable?

Anti-fraud vs research-quality boundary
---------------------------------------
Per the harness philosophy ("harness 没有 agent 自己聪明"; see
``automated_gates.py`` docstring), this gate does **not** judge whether a
*different* method is *good enough* — that Δreward call belongs to the reviewer
(and to ``anti_mediocrity``). It only catches the mechanical fact that the
treatment is **not actually applied / not actually different**:

* A relabelled duplicate (configs identical except the condition label) is a
  structural defect — two "conditions" that are the same command. Reported
  structurally at ``analysis``.
* "Differs only by reward-function name AND outcomes indistinguishable" is a
  strong but probabilistic signal; surfaced as an advisory so the reviewer
  rules (and routes back to ``run`` to prove the reward functions differ).

Cross-run per-rollout reward traces are intentionally NOT positionally diffed:
two independent runs log tasks in different orders even with a fixed seed, so
positional alignment is unreliable.

CLI::

    python -m argus_skill.skills.method_differentiation --project-root . \
        [--proposed-condition cv_grpo --baseline-condition vanilla_grpo]
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Reuse the single source of truth for "what counts as an optimizer run".
from .rl_training_plots import (
    MIN_OPTIMIZER_STEPS,
    RUN_PREFIX,
    RUNS_SUBDIR,
    _is_probe,
    _read_optimizer_steps,
)

# How many reward-trace rows to scan when deriving a per-run reward mean from
# the raw trace (only used as a fallback; bounded so a giant trace is cheap).
_MAX_TRACE_ROWS = 50_000

# Outcome-equivalence surfacing heuristics (NOT pass/fail thresholds — they only
# decide whether to *say* "looks indistinguishable" in the advisory; the
# reviewer rules). Two conditions whose aggregate reward stats are this close
# are treated as indistinguishable for the no-op heuristic.
_REWARD_MEAN_EPS = 0.01
_ZERO_STD_FRAC_EPS = 0.02

# config_snapshot ``command`` keys that are pure bookkeeping or
# infra/throughput/memory/IO knobs — differences here are never a "method"
# difference (they change speed/footprint, not the RL objective). The reward
# function *path* is deliberately NOT noise: swapping the reward file is a real
# method change.
_NOISE_KEY_RE = re.compile(
    r"(experiment_name|project_name|default_local_dir|train_files|val_files"
    r"|model\.path|_dir$|logger|trace"
    # rollout engine / throughput / memory / IO knobs:
    r"|gpu_memory_utilization|max_num_seqs|max_num_batched_tokens|enforce_eager"
    r"|enable_chunked_prefill|disable_log_stats|layered_summon|load_format"
    r"|max_model_len|use_dynamic_bsz|max_token_len_per_gpu|param_offload"
    r"|optimizer_offload|gradient_checkpointing|activation_offload"
    r"|entropy_checkpointing|tensor_model_parallel_size|nnodes|n_gpus_per_node"
    # logging / checkpointing / resume cadence:
    r"|save_freq|test_freq|val_before_train|resume_mode|balance_batch)",
    re.IGNORECASE,
)

# Condition labels that look like a baseline/reference rather than a proposal.
_BASELINE_NAME_RE = re.compile(
    r"(vanilla|baseline|^base$|_base$|no_skill|reference|sft_only|control)",
    re.IGNORECASE,
)

# A run is treated as a throwaway probe only when its name carries a probe
# token AND it never ran beyond this many optimizer steps. This avoids
# excluding a real long run whose name merely mentions a probe token (e.g. a
# resume tagged ``...preflightfix`` that actually trained 1000+ steps).
_PROBE_MAX_STEPS = 50

# The reward-function-name config key (the exact knob the observed no-op
# swapped). Kept explicit so "differs only by reward-function name" is a
# first-class shape rather than a generic diff.
_REWARD_FN_NAME_KEY = "reward.custom_reward_function.name"


@dataclass
class ConditionRun:
    """One optimizer run selected to represent a condition."""

    condition: str
    run_name: str
    run_dir: Path
    state: str
    optimizer_steps: int
    config_args: dict[str, str]
    reward_mean: float | None
    frac_reward_zero_std: float | None
    reward_rows: int


@dataclass
class PairFinding:
    proposed_condition: str
    baseline_condition: str
    proposed_run: str
    baseline_run: str
    # "labels_only" | "reward_name_only" | "differentiated" | "unknown"
    config_diff_kind: str
    meaningful_diffs: dict[str, list[str]]  # key -> [baseline_value, proposed_value]
    reward_mean_delta: float | None
    zero_std_frac_delta: float | None
    outcomes_indistinguishable: bool
    # Mechanical, structural defect: same command, two labels.
    duplicate_condition: bool
    # Strong-but-probabilistic: reward-fn-name-only swap + indistinguishable.
    no_op_suspected: bool

    def to_text(self) -> str:
        head = f"{self.proposed_condition!r} vs baseline {self.baseline_condition!r}"
        lines = [f"- {head}"]
        lines.append(
            f"    runs: proposed={self.proposed_run} baseline={self.baseline_run}"
        )
        lines.append(f"    config: {self.config_diff_kind}")
        if self.meaningful_diffs:
            shown = list(self.meaningful_diffs.items())[:6]
            for key, (bval, pval) in shown:
                lines.append(f"      {key}: {bval!r} -> {pval!r}")
            if len(self.meaningful_diffs) > 6:
                lines.append(f"      ... and {len(self.meaningful_diffs) - 6} more")
        if self.reward_mean_delta is not None:
            lines.append(
                f"    reward_mean Δ(proposed-baseline) = {self.reward_mean_delta:+.4f}"
            )
        if self.zero_std_frac_delta is not None:
            lines.append(
                f"    frac_reward_zero_std Δ = {self.zero_std_frac_delta:+.4f}"
            )
        if self.duplicate_condition:
            lines.append(
                "    DUPLICATE CONDITION: the proposed command is identical to the "
                "baseline except for labels — these are the same experiment run "
                "twice, not a method vs a baseline."
            )
        elif self.no_op_suspected:
            lines.append(
                "    NO-OP TREATMENT SUSPECTED: the proposed condition differs from "
                "the baseline only by reward-function name and produces "
                "statistically indistinguishable reward outcomes. Prove the reward "
                "functions actually differ on real rollouts before citing this as a "
                "method (route back to `run`)."
            )
        return "\n".join(lines)


@dataclass
class MethodDifferentiationReport:
    runs_dir: Path
    conditions: list[str] = field(default_factory=list)
    pairs: list[PairFinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def duplicate_pairs(self) -> list[PairFinding]:
        return [p for p in self.pairs if p.duplicate_condition]

    @property
    def no_op_pairs(self) -> list[PairFinding]:
        return [p for p in self.pairs if p.no_op_suspected]

    def to_text(self) -> str:
        if not self.pairs:
            cond = ", ".join(self.conditions) or "none"
            return f"no comparable proposed/baseline condition pair (conditions: {cond})"
        return "\n".join(p.to_text() for p in self.pairs)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _parse_config_args(config: dict | None) -> dict[str, str]:
    """Flatten a config_snapshot ``command`` arg list into ``key=value`` pairs.

    verl is launched as ``python -m verl.trainer.main_ppo key=value ...``; the
    snapshot stores that argv under ``command``. Non ``key=value`` tokens
    (interpreter, ``-m``, module) are ignored.
    """
    args: dict[str, str] = {}
    if not config:
        return args
    command = config.get("command")
    if not isinstance(command, list):
        return args
    for token in command:
        if not isinstance(token, str) or "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.lstrip("+")  # verl uses ``+key=value`` to append
        if key:
            args[key] = value
    return args


def _condition_of(run_dir: Path, config: dict | None) -> str:
    status = _read_json(run_dir / "status.json")
    if isinstance(status, dict):
        cond = status.get("condition")
        if isinstance(cond, str) and cond.strip():
            return cond.strip()
    if isinstance(config, dict):
        cond = config.get("condition")
        if isinstance(cond, str) and cond.strip():
            return cond.strip()
    return ""


def _reward_stats(run_dir: Path) -> tuple[float | None, float | None, int]:
    """Return (reward_mean, frac_reward_zero_std, rows) for a run.

    Prefer the authoritative ``status.json`` ``reward_trace_stats`` block; fall
    back to streaming a bounded prefix of ``reward_trace.jsonl``.
    """
    status = _read_json(run_dir / "status.json")
    if isinstance(status, dict):
        stats = status.get("reward_trace_stats")
        if isinstance(stats, dict):
            rm = stats.get("reward_mean")
            f0 = stats.get("frac_reward_zero_std")
            rows = stats.get("reward_trace_rows")
            return (
                float(rm) if isinstance(rm, (int, float)) else None,
                float(f0) if isinstance(f0, (int, float)) else None,
                int(rows) if isinstance(rows, (int, float)) else 0,
            )
    # Fallback: derive a mean from the raw trace.
    trace = run_dir / "reward_trace.jsonl"
    if not trace.exists():
        return None, None, 0
    total = 0.0
    rows = 0
    try:
        with trace.open(encoding="utf-8") as handle:
            for line in handle:
                if rows >= _MAX_TRACE_ROWS:
                    break
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                score = obj.get("score") if isinstance(obj, dict) else None
                if isinstance(score, (int, float)):
                    total += float(score)
                    rows += 1
    except OSError:
        return None, None, 0
    return (total / rows if rows else None), None, rows


def _run_mtime(run_dir: Path) -> float:
    for name in ("progress.jsonl", "status.json"):
        p = run_dir / name
        if p.exists():
            try:
                return p.stat().st_mtime
            except OSError:
                continue
    return 0.0


def _select_runs_by_condition(runs_dir: Path) -> tuple[dict[str, ConditionRun], list[str]]:
    """Pick one representative run per condition.

    The ``status.json`` ``optimizer_steps`` field is unreliable after a resume
    (it can record a stale post-resume counter), so we do NOT rank by step
    count. Instead we mirror ``rl_training_health``: prefer a live run, then the
    most-recently-touched one. That selects the *current* generation's run for
    each condition rather than an old pilot.
    """
    warnings: list[str] = []
    # condition -> (is_not_running, -mtime) sort key of the incumbent.
    best: dict[str, ConditionRun] = {}
    best_key: dict[str, tuple[bool, float]] = {}
    for run_dir in sorted(runs_dir.glob(f"{RUN_PREFIX}*")):
        if not run_dir.is_dir():
            continue
        state, steps = _read_optimizer_steps(run_dir)
        if state not in ("running", "completed") or steps < MIN_OPTIMIZER_STEPS:
            continue
        # Only skip name-probe runs that never grew past a smoke-sized step
        # count; a long resume tagged ``...preflightfix`` is a real run.
        if _is_probe(run_dir.name) and steps < _PROBE_MAX_STEPS:
            continue
        config = _read_json(run_dir / "config_snapshot.json")
        condition = _condition_of(run_dir, config)
        if not condition:
            continue
        reward_mean, zero_std, rows = _reward_stats(run_dir)
        candidate = ConditionRun(
            condition=condition,
            run_name=run_dir.name,
            run_dir=run_dir,
            state=state,
            optimizer_steps=steps,
            config_args=_parse_config_args(config),
            reward_mean=reward_mean,
            frac_reward_zero_std=zero_std,
            reward_rows=rows,
        )
        key = (state != "running", -_run_mtime(run_dir))
        incumbent_key = best_key.get(condition)
        if incumbent_key is None or key < incumbent_key:
            best[condition] = candidate
            best_key[condition] = key
    return best, warnings


def _resolve_pairs(
    conditions: list[str],
    *,
    proposed_condition: str | None,
    baseline_condition: str | None,
) -> list[tuple[str, str]]:
    """Return (proposed, baseline) condition-label pairs to compare."""
    cset = set(conditions)
    if proposed_condition and baseline_condition:
        if proposed_condition in cset and baseline_condition in cset:
            return [(proposed_condition, baseline_condition)]
        return []
    # Auto-detect: exactly one baseline-looking condition pairs with every
    # other (proposed) condition. Ambiguous shapes pair nothing.
    baselines = [c for c in conditions if _BASELINE_NAME_RE.search(c)]
    proposals = [c for c in conditions if not _BASELINE_NAME_RE.search(c)]
    if len(baselines) == 1 and proposals:
        return [(p, baselines[0]) for p in proposals]
    return []


def _meaningful_config_diffs(
    baseline_args: dict[str, str], proposed_args: dict[str, str]
) -> dict[str, list[str]]:
    diffs: dict[str, list[str]] = {}
    for key in sorted(set(baseline_args) | set(proposed_args)):
        if _NOISE_KEY_RE.search(key):
            continue
        bval = baseline_args.get(key)
        pval = proposed_args.get(key)
        if bval != pval:
            diffs[key] = [bval if bval is not None else "<absent>",
                          pval if pval is not None else "<absent>"]
    return diffs


def _classify_config_diff(
    diffs: dict[str, list[str]], *, have_config: bool
) -> str:
    if not have_config:
        return "unknown"
    if not diffs:
        return "labels_only"
    if set(diffs) == {_REWARD_FN_NAME_KEY}:
        return "reward_name_only"
    return "differentiated"


def _build_pair(
    proposed: ConditionRun, baseline: ConditionRun
) -> PairFinding:
    have_config = bool(proposed.config_args) and bool(baseline.config_args)
    diffs = _meaningful_config_diffs(baseline.config_args, proposed.config_args)
    diff_kind = _classify_config_diff(diffs, have_config=have_config)

    reward_delta: float | None = None
    if proposed.reward_mean is not None and baseline.reward_mean is not None:
        reward_delta = proposed.reward_mean - baseline.reward_mean
    zero_delta: float | None = None
    if (
        proposed.frac_reward_zero_std is not None
        and baseline.frac_reward_zero_std is not None
    ):
        zero_delta = proposed.frac_reward_zero_std - baseline.frac_reward_zero_std

    indistinguishable = (
        reward_delta is not None
        and abs(reward_delta) <= _REWARD_MEAN_EPS
        and (zero_delta is None or abs(zero_delta) <= _ZERO_STD_FRAC_EPS)
    )
    # Same command, two labels: a mechanical duplicate (structural-eligible).
    duplicate = diff_kind == "labels_only" and proposed.condition != baseline.condition
    # Reward-fn-name-only swap that produced indistinguishable outcomes.
    no_op = diff_kind == "reward_name_only" and indistinguishable

    return PairFinding(
        proposed_condition=proposed.condition,
        baseline_condition=baseline.condition,
        proposed_run=proposed.run_name,
        baseline_run=baseline.run_name,
        config_diff_kind=diff_kind,
        meaningful_diffs=diffs,
        reward_mean_delta=reward_delta,
        zero_std_frac_delta=zero_delta,
        outcomes_indistinguishable=bool(indistinguishable),
        duplicate_condition=duplicate,
        no_op_suspected=no_op,
    )


def validate_method_differentiation(
    project_root: Path,
    *,
    proposed_condition: str | None = None,
    baseline_condition: str | None = None,
) -> MethodDifferentiationReport:
    runs_dir = project_root / RUNS_SUBDIR
    report = MethodDifferentiationReport(runs_dir=runs_dir)
    if not runs_dir.is_dir():
        return report

    by_condition, warnings = _select_runs_by_condition(runs_dir)
    report.warnings.extend(warnings)
    report.conditions = sorted(by_condition)
    if len(by_condition) < 2:
        return report

    for proposed_label, baseline_label in _resolve_pairs(
        report.conditions,
        proposed_condition=proposed_condition,
        baseline_condition=baseline_condition,
    ):
        proposed = by_condition.get(proposed_label)
        baseline = by_condition.get(baseline_label)
        if proposed is None or baseline is None:
            continue
        report.pairs.append(_build_pair(proposed, baseline))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--proposed-condition", type=str, default=None)
    parser.add_argument("--baseline-condition", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_method_differentiation(
        args.project_root.resolve(),
        proposed_condition=args.proposed_condition,
        baseline_condition=args.baseline_condition,
    )
    if args.json:
        payload = {
            "runs_dir": str(report.runs_dir),
            "conditions": report.conditions,
            "pairs": [
                {
                    "proposed_condition": p.proposed_condition,
                    "baseline_condition": p.baseline_condition,
                    "proposed_run": p.proposed_run,
                    "baseline_run": p.baseline_run,
                    "config_diff_kind": p.config_diff_kind,
                    "meaningful_diffs": p.meaningful_diffs,
                    "reward_mean_delta": p.reward_mean_delta,
                    "zero_std_frac_delta": p.zero_std_frac_delta,
                    "outcomes_indistinguishable": p.outcomes_indistinguishable,
                    "duplicate_condition": p.duplicate_condition,
                    "no_op_suspected": p.no_op_suspected,
                }
                for p in report.pairs
            ],
            "warnings": report.warnings,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_text())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
