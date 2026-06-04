"""RL training-health advisory gate.

The harness ships ``rl-training-collapse-diagnosis.md`` — the authority on
whether a PPO/GRPO/RLVR optimizer run is a *fair, learnable* run (reward
variance, advantage signal, grad norm, KL, entropy, length/parse health).
But that skill is markdown the reviewer *might* read; nothing in the
harness actually COMPUTES those collapse signatures off the run logs and
puts them in front of the reviewer. The only RL gate, ``rl_training_plots``,
checks that a curve PNG *exists* — it never judges the curve. So a run can
saturate (reward pinned at the ceiling, per-group advantage → 0, gradient
→ 0, entropy collapsing, training on a handful of memorised task ids) and
sail through unflagged, burning GPU for nothing.

This gate closes that loop. It reads each optimizer run's OWN
``verl_metrics.jsonl`` (authoritative per-step trainer metrics) and
``progress.jsonl`` / ``reward_trace.jsonl``, computes the collapse-relevant
numbers over a small tail window, and surfaces them as neutral FACTS in the
reviewer's prompt.

It is **advisory only** (kind=``advisory``): it never blocks a round and
never renders a quality verdict. Per the harness design philosophy
("harness 没有 agent 自己聪明"), a saturated run is still real evidence the
agent may legitimately interpret; whether the numbers mean "stop and fix"
or "keep going" is the reviewer's call. The signal strings below name the
*mathematical condition observed* (e.g. ``zero_advantage``,
``near_zero_grad_norm``), not a judgment — they map onto the
collapse-diagnosis skill's signatures, which the reviewer applies. The
emit thresholds are surfacing heuristics, not pass/fail thresholds; the
raw numbers are always printed alongside so the reviewer rules on its own.

CLI:
    python -m argus_skill.skills.rl_training_health --project-root .
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

# Shared run-discovery policy lives in rl_training_plots so the two RL
# gates can never drift apart on what counts as an optimizer run.
from .rl_training_plots import (
    MIN_OPTIMIZER_STEPS,
    RUN_PREFIX,
    RUNS_SUBDIR,
    _is_probe,
    _read_optimizer_steps,
)

# How many trailing optimizer steps to summarise. Collapse is a tail-window
# property ("early warmup zeros are fine"); a small window keeps the prompt
# compact while still showing a trend.
TAIL_WINDOW = 8
# Below this many usable tail steps we refuse to assert a *sustained*
# collapse signature and only note single-step observations.
MIN_TAIL_FOR_SUSTAINED = 3
# Cap how many runs we describe so a long-lived project never floods the
# reviewer prompt: live runs first, then most-recently-touched completed.
MAX_RUNS_REPORTED = 6
# Bound the reward_trace scan on huge runs (only task-id uniqueness needed).
MAX_REWARD_TRACE_ROWS = 50_000

# Surfacing heuristics (NOT pass/fail thresholds — advisory labels only).
_ADVANTAGE_SPAN_EPS = 1e-6   # advantage max-min at/below this ≈ zero signal
_GRAD_NORM_EPS = 1e-3        # tail max grad_norm at/below this ≈ no update
_REWARD_CEILING = 0.99       # per-step reward mean/min at/above this = all-right
_REWARD_FLOOR = 0.01         # tail reward mean at/below this = all-wrong
_LOW_ENTROPY = 0.05          # last entropy at/below this = near-deterministic
_ENTROPY_DECAY_RATIO = 0.5   # last <= ratio*first over tail = declining
_DIVERSITY_UNIQUE_MAX = 16   # <= this many unique ids ...
_DIVERSITY_REPEAT_MIN = 8    # ... with >= this many rollouts per id = memorising
_KL_BLOWUP_RATIO = 5.0       # last kl >= ratio*first over tail = diverging


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


@dataclass
class RunHealth:
    run_name: str
    state: str
    optimizer_steps: int
    minimum_accepted_steps: int | None
    tail_n: int
    facts: dict[str, object] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        f = self.facts
        target = (
            f"/{self.minimum_accepted_steps}"
            if self.minimum_accepted_steps
            else ""
        )
        lines = [
            f"[{self.run_name}] state={self.state} "
            f"steps={self.optimizer_steps}{target} tail={self.tail_n}"
        ]
        reward_bits = []
        if "reward_mean_last" in f:
            reward_bits.append(f"last={f['reward_mean_last']:.4f}")
        if "reward_mean_min" in f:
            reward_bits.append(
                f"tail_range=[{f['reward_mean_min']:.4f},{f['reward_mean_max']:.4f}]"
            )
        if "reward_ceiling_hits" in f:
            reward_bits.append(
                f"ceiling(>={_REWARD_CEILING})={f['reward_ceiling_hits']}/{self.tail_n}"
            )
        if "reward_std_last" in f:
            reward_bits.append(f"std_last={f['reward_std_last']:.4f}")
        if reward_bits:
            lines.append("  reward: " + " ".join(reward_bits))
        adv_grad = []
        if "advantage_span_last" in f:
            adv_grad.append(f"advantage_span_last={f['advantage_span_last']:.3e}")
        if "advantage_span_tail_max" in f:
            adv_grad.append(
                f"advantage_span_tail_max={f['advantage_span_tail_max']:.3e}"
            )
        if "grad_norm_tail_max" in f:
            adv_grad.append(f"grad_norm_tail_max={f['grad_norm_tail_max']:.3e}")
        if "pg_loss_last" in f:
            adv_grad.append(f"pg_loss_last={f['pg_loss_last']:.3e}")
        if adv_grad:
            lines.append("  update: " + " ".join(adv_grad))
        ent_kl = []
        if "entropy_first" in f:
            ent_kl.append(
                f"entropy {f['entropy_first']:.3f}->{f['entropy_last']:.3f}"
            )
        if "kl_last" in f:
            ent_kl.append(f"kl_last={f['kl_last']:.3e}")
        if "clip_ratio_last" in f:
            ent_kl.append(f"resp_clip_ratio_last={f['clip_ratio_last']:.3f}")
        if ent_kl:
            lines.append("  explore: " + " ".join(ent_kl))
        if "unique_task_ids" in f:
            lines.append(
                f"  task_diversity: {f['unique_task_ids']} unique id(s) / "
                f"{f['reward_trace_rows']} rollout row(s)"
                + (
                    f"; admitted={f['admitted_ids']}"
                    if f.get("admitted_ids") is not None
                    else ""
                )
            )
        if "progress_age_sec" in f:
            lines.append(f"  liveness: progress.jsonl age={f['progress_age_sec']}s")
        lines.append(
            "  signals: " + (", ".join(self.signals) if self.signals else "none")
        )
        if self.warnings:
            lines.append("  warnings: " + ", ".join(sorted(set(self.warnings))))
        lines.append(
            "  -> map to rl-training-collapse-diagnosis.md signatures; "
            "reviewer rules continue vs concern"
        )
        return "\n".join(lines)


@dataclass
class RLTrainingHealthReport:
    runs_dir: Path
    runs: list[RunHealth] = field(default_factory=list)
    omitted: int = 0

    @property
    def flagged(self) -> list[RunHealth]:
        return [r for r in self.runs if r.signals]

    def to_text(self) -> str:
        if not self.runs:
            return (
                "No live or completed RL optimizer runs to inspect "
                "(gate is a no-op until one exists)."
            )
        lines = [r.to_text() for r in self.runs]
        if self.omitted:
            lines.append(f"... {self.omitted} older run(s) omitted")
        return "\n".join(lines)


def _read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    """Read a JSONL file defensively. A live run may leave a half-written
    final line; that is ignored (recorded as a benign warning), while an
    earlier malformed line is surfaced as a real read warning. Never
    raises for missing/locked files."""
    warnings: list[str] = []
    if not path.exists():
        return [], warnings
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], [f"unreadable:{path.name}"]
    rows: list[dict] = []
    nonempty = [(i, ln) for i, ln in enumerate(raw_lines) if ln.strip()]
    last_idx = nonempty[-1][0] if nonempty else -1
    for i, ln in nonempty:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            if i == last_idx:
                warnings.append(f"ignored_partial_final_line:{path.name}")
            else:
                warnings.append(f"malformed_line:{path.name}")
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows, warnings


def _unique_task_ids(path: Path) -> tuple[int, int, bool, list[str]]:
    """Stream reward_trace.jsonl: return (unique_count, rows_seen, capped,
    warnings). Bounded so a giant trace cannot blow up the gate."""
    warnings: list[str] = []
    if not path.exists():
        return 0, 0, False, warnings
    seen: set[str] = set()
    rows = 0
    capped = False
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                if rows >= MAX_REWARD_TRACE_ROWS:
                    capped = True
                    break
                rows += 1
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = obj.get("task_id") if isinstance(obj, dict) else None
                if isinstance(tid, str):
                    seen.add(tid)
    except OSError:
        warnings.append("unreadable:reward_trace.jsonl")
    return len(seen), rows, capped, warnings


def _verl_value(data: dict, key: str) -> float | None:
    val = data.get(key)
    return float(val) if _is_finite_number(val) else None


def _collect_facts(run_dir: Path, state: str, steps: int) -> RunHealth:
    minimum = None
    manifest = run_dir / "manifest.json"
    if manifest.exists():
        try:
            mdata = json.loads(manifest.read_text(encoding="utf-8"))
            raw = mdata.get("minimum_accepted_optimizer_steps")
            if isinstance(raw, (int, float)):
                minimum = int(raw)
        except (OSError, json.JSONDecodeError):
            pass

    facts: dict[str, object] = {}
    signals: list[str] = []
    warnings: list[str] = []
    saw_nan = False

    # --- authoritative per-step trainer metrics ---
    verl_rows, w = _read_jsonl(run_dir / "verl_metrics.jsonl")
    warnings += w
    verl_tail = verl_rows[-TAIL_WINDOW:]
    grad_norms: list[float] = []
    entropies: list[float] = []
    kls: list[float] = []
    adv_spans: list[float] = []
    last_pg_loss: float | None = None
    ceiling_steps_verl = 0
    for row in verl_tail:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        for key in (
            "critic/advantages/max",
            "critic/advantages/min",
            "actor/grad_norm",
            "actor/entropy",
        ):
            if key in data and not _is_finite_number(data[key]):
                saw_nan = True
        amax = _verl_value(data, "critic/advantages/max")
        amin = _verl_value(data, "critic/advantages/min")
        if amax is not None and amin is not None:
            adv_spans.append(amax - amin)
        gn = _verl_value(data, "actor/grad_norm")
        if gn is not None:
            grad_norms.append(gn)
        ent = _verl_value(data, "actor/entropy")
        if ent is not None:
            entropies.append(ent)
        kl = _verl_value(data, "actor/ppo_kl")
        if kl is None:
            kl = _verl_value(data, "actor/kl_loss")
        if kl is not None:
            kls.append(kl)
        last_pg_loss = _verl_value(data, "actor/pg_loss")
        rmin = _verl_value(data, "critic/rewards/min")
        if rmin is not None and rmin >= _REWARD_CEILING:
            ceiling_steps_verl += 1
        clip = _verl_value(data, "response_length/clip_ratio")
        if clip is not None:
            facts["clip_ratio_last"] = clip

    if adv_spans:
        facts["advantage_span_last"] = adv_spans[-1]
        facts["advantage_span_tail_max"] = max(adv_spans)
    if grad_norms:
        facts["grad_norm_tail_max"] = max(grad_norms)
    if last_pg_loss is not None:
        facts["pg_loss_last"] = last_pg_loss
    if entropies:
        facts["entropy_first"] = entropies[0]
        facts["entropy_last"] = entropies[-1]
    if kls:
        facts["kl_last"] = kls[-1]

    # --- reward stats from progress.jsonl (reward_trace_stats sub-dict) ---
    prog_rows, w = _read_jsonl(run_dir / "progress.jsonl")
    warnings += w
    opt_rows = [r for r in prog_rows if r.get("event") == "optimizer_step"]
    reward_means: list[float] = []
    reward_std_last: float | None = None
    ceiling_steps_prog = 0
    for row in opt_rows[-TAIL_WINDOW:]:
        stats = row.get("reward_trace_stats")
        if not isinstance(stats, dict):
            continue
        rm = stats.get("reward_mean")
        if _is_finite_number(rm):
            reward_means.append(float(rm))
            if float(rm) >= _REWARD_CEILING:
                ceiling_steps_prog += 1
        rs = stats.get("reward_std")
        if _is_finite_number(rs):
            reward_std_last = float(rs)
    if reward_means:
        facts["reward_mean_last"] = reward_means[-1]
        facts["reward_mean_min"] = min(reward_means)
        facts["reward_mean_max"] = max(reward_means)
    if reward_std_last is not None:
        facts["reward_std_last"] = reward_std_last
    ceiling_hits = max(ceiling_steps_verl, ceiling_steps_prog)
    if reward_means or verl_tail:
        facts["reward_ceiling_hits"] = ceiling_hits

    # --- training-set diversity (memorisation) ---
    uniq, rows_seen, capped, w = _unique_task_ids(run_dir / "reward_trace.jsonl")
    warnings += w
    if rows_seen:
        facts["unique_task_ids"] = uniq
        facts["reward_trace_rows"] = (
            f"{rows_seen}{'+' if capped else ''}"
        )
    status_admitted = run_dir / "status.json"
    if status_admitted.exists():
        try:
            sdata = json.loads(status_admitted.read_text(encoding="utf-8"))
            adm = sdata.get("admitted_math_ids")
            if isinstance(adm, int):
                facts["admitted_ids"] = adm
        except (OSError, json.JSONDecodeError):
            pass

    # --- liveness (facts only; no single-snapshot stall verdict) ---
    if state == "running":
        prog = run_dir / "progress.jsonl"
        if prog.exists():
            try:
                facts["progress_age_sec"] = int(time.time() - prog.stat().st_mtime)
            except OSError:
                pass

    # tail size = usable optimizer steps we actually summarised
    tail_n = max(len(verl_tail), len(opt_rows[-TAIL_WINDOW:]))

    # --- signal labels (advisory observations, qualified by tail size) ---
    sustained = tail_n >= MIN_TAIL_FOR_SUSTAINED
    if saw_nan:
        signals.append("nan_or_inf_metric")
    if adv_spans and max(adv_spans) <= _ADVANTAGE_SPAN_EPS:
        # sustained zero advantage across the whole tail window
        signals.append("zero_advantage" if sustained else "zero_advantage_single_step")
    elif adv_spans and abs(adv_spans[-1]) <= _ADVANTAGE_SPAN_EPS:
        # only the latest step collapsed — note it without asserting sustained
        signals.append("zero_advantage_last_step")
    if sustained and grad_norms and max(grad_norms) <= _GRAD_NORM_EPS:
        signals.append("near_zero_grad_norm")
    if sustained and ceiling_hits >= 2:
        signals.append("reward_ceiling_saturation")
    if sustained and reward_means and max(reward_means) <= _REWARD_FLOOR:
        signals.append("reward_floor_stuck")
    if entropies:
        if entropies[-1] <= _LOW_ENTROPY:
            signals.append("low_entropy")
        elif (
            sustained
            and entropies[0] > 0
            and entropies[-1] <= _ENTROPY_DECAY_RATIO * entropies[0]
        ):
            signals.append("entropy_declining")
    if (
        rows_seen
        and uniq
        and uniq <= _DIVERSITY_UNIQUE_MAX
        and rows_seen / uniq >= _DIVERSITY_REPEAT_MIN
    ):
        signals.append("low_task_diversity")
    if sustained and len(kls) >= 2 and kls[0] > 0 and kls[-1] >= _KL_BLOWUP_RATIO * kls[0]:
        signals.append("kl_blowup_candidate")
    if not sustained and signals:
        signals.append("insufficient_tail_for_sustained_collapse")

    return RunHealth(
        run_name=run_dir.name,
        state=state,
        optimizer_steps=steps,
        minimum_accepted_steps=minimum,
        tail_n=tail_n,
        facts=facts,
        signals=signals,
        warnings=warnings,
    )


def _run_mtime(run_dir: Path) -> float:
    for name in ("progress.jsonl", "status.json"):
        p = run_dir / name
        if p.exists():
            try:
                return p.stat().st_mtime
            except OSError:
                continue
    return 0.0


def validate_rl_training_health(project_root: Path) -> RLTrainingHealthReport:
    runs_dir = project_root / RUNS_SUBDIR
    report = RLTrainingHealthReport(runs_dir=runs_dir)
    if not runs_dir.is_dir():
        return report

    eligible: list[tuple[float, Path, str, int]] = []
    for run_dir in sorted(runs_dir.glob(f"{RUN_PREFIX}*")):
        if not run_dir.is_dir() or _is_probe(run_dir.name):
            continue
        state, steps = _read_optimizer_steps(run_dir)
        if state not in ("running", "completed") or steps < MIN_OPTIMIZER_STEPS:
            continue
        eligible.append((_run_mtime(run_dir), run_dir, state, steps))

    # Live runs first, then most-recently-touched; cap to keep prompt small.
    eligible.sort(key=lambda e: (e[2] != "running", -e[0]))
    selected = eligible[:MAX_RUNS_REPORTED]
    report.omitted = len(eligible) - len(selected)
    for _, run_dir, state, steps in selected:
        report.runs.append(_collect_facts(run_dir, state, steps))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_rl_training_health(args.project_root.resolve())
    if args.json:
        payload = {
            "runs_dir": str(report.runs_dir),
            "omitted": report.omitted,
            "runs": [
                {
                    "run_name": r.run_name,
                    "state": r.state,
                    "optimizer_steps": r.optimizer_steps,
                    "minimum_accepted_steps": r.minimum_accepted_steps,
                    "tail_n": r.tail_n,
                    "facts": r.facts,
                    "signals": r.signals,
                    "warnings": r.warnings,
                }
                for r in report.runs
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_text())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
