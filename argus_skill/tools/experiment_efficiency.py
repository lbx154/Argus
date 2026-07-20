"""Protocol-declared sequential stopping for expensive experiment repeats."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


class ExperimentEfficiencyError(ValueError):
    pass


def _critical_95(n: int) -> float:
    return _T95.get(max(1, n - 1), 1.96)


def _read_values(path: Path, field: str) -> list[float]:
    values: list[float] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            value = row[field]
            if isinstance(value, bool):
                raise TypeError
            values.append(float(value))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ExperimentEfficiencyError(
                f"invalid observation at line {index}: expected numeric {field!r}"
            ) from exc
    if not values:
        raise ExperimentEfficiencyError("observation file contains no values")
    return values


def evaluate_stopping(
    values: Sequence[float],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    if not values:
        raise ExperimentEfficiencyError("at least one observation is required")
    direction = str(config.get("direction") or "maximize").strip().lower()
    if direction not in {"maximize", "minimize"}:
        raise ExperimentEfficiencyError("direction must be maximize or minimize")
    min_repeats = max(2, int(config.get("min_repeats", 3)))
    max_repeats = max(min_repeats, int(config.get("max_repeats", 10)))
    min_improvement = float(config.get("min_improvement", 0.0))
    absolute_half_width = config.get("absolute_half_width")
    relative_half_width = config.get("relative_half_width")
    if absolute_half_width is not None:
        absolute_half_width = max(0.0, float(absolute_half_width))
    if relative_half_width is not None:
        relative_half_width = max(0.0, float(relative_half_width))
    baseline = config.get("baseline")
    if baseline is not None:
        baseline = float(baseline)

    n = len(values)
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if n >= 2 else 0.0
    half_width = (
        _critical_95(n) * stdev / math.sqrt(n) if n >= 2 else math.inf
    )
    lower = mean - half_width
    upper = mean + half_width
    oriented_effect = oriented_lower = oriented_upper = None
    if baseline is not None:
        if direction == "maximize":
            oriented_effect = mean - baseline
            oriented_lower = lower - baseline
            oriented_upper = upper - baseline
        else:
            oriented_effect = baseline - mean
            oriented_lower = baseline - upper
            oriented_upper = baseline - lower

    decision = "CONTINUE"
    reason = f"need at least {min_repeats} repeats" if n < min_repeats else "uncertainty remains"
    if n >= min_repeats:
        if oriented_lower is not None and oriented_lower >= min_improvement:
            decision = "STOP_SUPPORTED_EFFECT"
            reason = "95% interval clears the declared minimum improvement"
        elif oriented_upper is not None and oriented_upper < min_improvement:
            decision = "STOP_FUTILITY"
            reason = "95% interval cannot reach the declared minimum improvement"
        else:
            precision_met = False
            if absolute_half_width is not None and half_width <= absolute_half_width:
                precision_met = True
            if relative_half_width is not None:
                scale = max(abs(mean), 1e-12)
                precision_met = precision_met or half_width <= relative_half_width * scale
            if precision_met:
                decision = "STOP_PRECISION_REACHED"
                reason = "declared confidence-interval precision reached"
            elif n >= max_repeats:
                decision = "STOP_MAX_REPEATS"
                reason = "declared maximum repeats reached"

    return {
        "schema_version": 1,
        "decision": decision,
        "reason": reason,
        "n": n,
        "mean": mean,
        "sample_stdev": stdev,
        "confidence": 0.95,
        "ci_lower": lower,
        "ci_upper": upper,
        "ci_half_width": half_width,
        "baseline": baseline,
        "direction": direction,
        "minimum_improvement": min_improvement,
        "oriented_effect": oriented_effect,
        "oriented_effect_ci_lower": oriented_lower,
        "oriented_effect_ci_upper": oriented_upper,
        "min_repeats": min_repeats,
        "max_repeats": max_repeats,
        "all_observations_retained": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.tools.experiment_efficiency"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--field", default="value")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ExperimentEfficiencyError("config must be a JSON object")
        values = _read_values(args.observations, args.field)
        result = evaluate_stopping(values, config)
    except (OSError, json.JSONDecodeError, ExperimentEfficiencyError) as exc:
        print(json.dumps({"status": "INVALID_PROTOCOL", "error": str(exc)}))
        return 2
    result["config_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
    result["observations_sha256"] = hashlib.sha256(
        args.observations.read_bytes()
    ).hexdigest()
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
