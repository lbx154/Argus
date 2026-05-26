from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from argus_skill.core.pricing import usd_for_tokens

RUNS_DIR = BASE_DIR / "runs"
VERIFICATION_SUMMARY_PATH = RUNS_DIR / "verification_summary.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass", "passed"}


def _float(value: Any) -> float:
    try:
        text = str(value).strip().replace("$", "").replace(",", "")
        return float(text) if text else 0.0
    except (TypeError, ValueError):
        return 0.0


def _maybe_float(value: Any) -> float | None:
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Any) -> int | None:
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _maybe_bool(value: Any) -> bool | None:
    text = str(value).strip()
    if not text:
        return None
    return _truthy(text)


def _rescue_succeeded(value: Any) -> bool:
    text = str(value).strip().lower()
    if not text:
        return False
    if text in {
        "no",
        "none",
        "false",
        "fail",
        "failed",
        "failure",
        "unsuccessful",
        "not",
    }:
        return False
    if _truthy(text):
        return True
    return text in {
        "rescued",
        "success",
        "successful",
        "succeeded",
        "done",
        "completed",
        "complete",
        "recovered",
        "fixed",
        "saved",
    }


def _manual_intervention_recorded(row: dict[str, Any]) -> bool:
    severity = str(row.get("intervention_severity") or "").strip()
    if severity in {"manual_followup", "manual_rescue", "model_drift"}:
        return True
    for column in (
        "human_interactions_after_assignment",
        "manual_commands",
        "nudges",
        "status_checks",
    ):
        value = _maybe_int(row.get(column))
        if value is not None and value > 0:
            return True
    rescue_outcome = row.get("rescue_outcome") or row.get("manual_rescue")
    return _rescue_succeeded(rescue_outcome)


def _normalized_zero_touch_success(row: dict[str, Any]) -> bool | None:
    zero_touch = _maybe_bool(row.get("zero_touch_success"))
    if zero_touch is not None:
        if zero_touch:
            return True
        needs_human = _maybe_bool(row.get("needs_human"))
        if needs_human is False and not _manual_intervention_recorded(row):
            return True
        return False
    needs_human = _maybe_bool(row.get("needs_human"))
    if needs_human is None:
        return None
    return not needs_human


def _row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("order") or ""),
        str(row.get("condition") or ""),
        str(row.get("task_id") or ""),
    )


def _recency_key(row: dict[str, Any]) -> str:
    return str(row.get("started_at") or row.get("run_id") or row.get("run_root") or "")


def load_result_rows(runs_dir: Path = RUNS_DIR) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    result_paths = sorted(runs_dir.glob("results.csv")) + sorted(
        runs_dir.glob("*/results.csv")
    )
    summary_paths = sorted(runs_dir.glob("summary.csv")) + sorted(
        runs_dir.glob("*/summary.csv")
    )
    for path in result_paths:
        for row in _read_csv(path):
            row.setdefault("source_csv", str(path))
            rows.append(row)
    for path in summary_paths:
        for row in _read_csv(path):
            row.setdefault("source_csv", str(path))
            rows.append(row)
    return rows


def load_verification_rows(path: Path = VERIFICATION_SUMMARY_PATH) -> dict[tuple[str, str, str], dict[str, str]]:
    if not path.exists():
        return {}
    return {_row_key(row): row for row in _read_csv(path)}


def merge_verification(
    rows: list[dict[str, Any]],
    verification: dict[tuple[str, str, str], dict[str, str]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        verifier = verification.get(_row_key(row))
        if verifier:
            out["accepted"] = verifier.get("accepted", out.get("accepted", ""))
            out["verification_reward"] = verifier.get(
                "reward", out.get("verification_reward", "")
            )
            out["verifier_exit"] = verifier.get("verifier_exit", "")
            out["verification_log"] = verifier.get("log", "")
        merged.append(out)
    return merged


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _int(value: Any) -> int:
    try:
        text = str(value).strip().replace(",", "")
        return int(float(text)) if text else 0
    except (TypeError, ValueError):
        return 0


def reprice_codex_rows(rows: list[dict[str, Any]], *, model: str) -> list[dict[str, Any]]:
    """Return rows with direct Codex token usage re-priced as ``model``."""
    if not model:
        return rows
    repriced: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        if str(row.get("condition") or "") != "codex":
            repriced.append(out)
            continue

        stats = _json_dict(row.get("model_token_stats"))
        usage_rows = list(stats.values()) if stats else [row]
        cost = 0.0
        for usage in usage_rows:
            if not isinstance(usage, dict):
                continue
            cost += usd_for_tokens(
                model,
                _int(usage.get("input_tokens")),
                _int(usage.get("cached_input_tokens")),
                _int(usage.get("output_tokens")),
            )
        out["cost_usd"] = f"{cost:.6f}"
        out["cost_model"] = model
        source = str(out.get("cost_source") or "")
        out["cost_source"] = f"{source}+repriced" if source else "repriced"
        repriced.append(out)
    return repriced


def latest_per_assignment(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = _row_key(row)
        if key not in latest or _recency_key(row) >= _recency_key(latest[key]):
            latest[key] = row
    return [latest[key] for key in sorted(latest)]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_condition: dict[str, dict[str, Any]] = {}
    for row in rows:
        condition = str(row.get("condition") or "unknown")
        bucket = by_condition.setdefault(
            condition,
            {
                "rows": 0,
                "accepted": 0,
                "known_acceptance": 0,
                "needs_human": 0,
                "timeouts": 0,
                "cost_usd": 0.0,
                "wall_minutes": 0.0,
                "reward": 0.0,
                "known_reward": 0,
                "zero_touch_success": 0,
                "known_zero_touch_success": 0,
                "human_interactions_after_assignment": 0,
                "known_human_interactions_after_assignment": 0,
                "active_touch_minutes_after_assignment": 0.0,
                "known_active_touch_minutes_after_assignment": 0,
                "manual_commands": 0,
                "known_manual_commands": 0,
                "rescued": 0,
                "known_rescue_outcome": 0,
                "intervention_severity_counts": {},
            },
        )
        bucket["rows"] += 1
        accepted = str(row.get("accepted") or "").strip()
        if accepted:
            bucket["known_acceptance"] += 1
            if _truthy(accepted):
                bucket["accepted"] += 1
        if _truthy(row.get("needs_human")):
            bucket["needs_human"] += 1
        if _truthy(row.get("timed_out")) or "timed out" in str(row.get("notes") or "").lower():
            bucket["timeouts"] += 1
        bucket["cost_usd"] += _float(row.get("cost_usd"))
        bucket["wall_minutes"] += _float(row.get("wall_minutes"))
        reward = row.get("verification_reward") or row.get("reward")
        if str(reward or "").strip():
            bucket["known_reward"] += 1
            bucket["reward"] += _float(reward)

        zero_touch = _normalized_zero_touch_success(row)
        if zero_touch is not None:
            bucket["known_zero_touch_success"] += 1
            if zero_touch and _truthy(accepted):
                bucket["zero_touch_success"] += 1

        human_interactions = _maybe_int(row.get("human_interactions_after_assignment"))
        if human_interactions is not None:
            bucket["known_human_interactions_after_assignment"] += 1
            bucket["human_interactions_after_assignment"] += human_interactions

        active_touch_minutes = _maybe_float(
            row.get("active_touch_minutes_after_assignment")
        )
        if active_touch_minutes is not None:
            bucket["known_active_touch_minutes_after_assignment"] += 1
            bucket["active_touch_minutes_after_assignment"] += active_touch_minutes

        manual_commands = _maybe_int(row.get("manual_commands"))
        if manual_commands is not None:
            bucket["known_manual_commands"] += 1
            bucket["manual_commands"] += manual_commands

        rescue_outcome = row.get("rescue_outcome") or row.get("manual_rescue")
        rescue_text = str(rescue_outcome or "").strip()
        if rescue_text:
            bucket["known_rescue_outcome"] += 1
            if _rescue_succeeded(rescue_text):
                bucket["rescued"] += 1

        severity = str(row.get("intervention_severity") or "").strip() or "unknown"
        counts = bucket["intervention_severity_counts"]
        counts[severity] = counts.get(severity, 0) + 1

    for bucket in by_condition.values():
        rows_count = max(1, int(bucket["rows"]))
        known_acceptance = int(bucket["known_acceptance"])
        known_reward = int(bucket["known_reward"])
        bucket["accepted_rate"] = (
            bucket["accepted"] / known_acceptance if known_acceptance else None
        )
        bucket["needs_human_rate"] = bucket["needs_human"] / rows_count
        bucket["timeout_rate"] = bucket["timeouts"] / rows_count
        bucket["avg_cost_usd"] = bucket["cost_usd"] / rows_count
        bucket["avg_wall_minutes"] = bucket["wall_minutes"] / rows_count
        bucket["mean_reward"] = bucket["reward"] / known_reward if known_reward else None
        bucket["zero_touch_success_rate"] = (
            bucket["zero_touch_success"] / bucket["known_zero_touch_success"]
            if bucket["known_zero_touch_success"]
            else None
        )
        bucket["human_interactions_after_assignment_rate"] = (
            bucket["human_interactions_after_assignment"]
            / bucket["known_human_interactions_after_assignment"]
            if bucket["known_human_interactions_after_assignment"]
            else None
        )
        bucket["active_touch_minutes_after_assignment_rate"] = (
            bucket["active_touch_minutes_after_assignment"]
            / bucket["known_active_touch_minutes_after_assignment"]
            if bucket["known_active_touch_minutes_after_assignment"]
            else None
        )
        bucket["manual_commands_rate"] = (
            bucket["manual_commands"] / bucket["known_manual_commands"]
            if bucket["known_manual_commands"]
            else None
        )
        bucket["rescue_rate"] = (
            bucket["rescued"] / bucket["known_rescue_outcome"]
            if bucket["known_rescue_outcome"]
            else None
        )

    return {
        "total_rows": len(rows),
        "conditions": by_condition,
    }


def _print_table(summary: dict[str, Any]) -> None:
    print("condition,rows,accepted_rate,mean_reward,needs_human_rate,avg_cost_usd,avg_wall_minutes,timeouts")
    for condition, row in sorted(summary["conditions"].items()):
        accepted_rate = row["accepted_rate"]
        mean_reward = row["mean_reward"]
        print(
            ",".join(
                [
                    condition,
                    str(row["rows"]),
                    "" if accepted_rate is None else f"{accepted_rate:.3f}",
                    "" if mean_reward is None else f"{mean_reward:.3f}",
                    f"{row['needs_human_rate']:.3f}",
                    f"{row['avg_cost_usd']:.6f}",
                    f"{row['avg_wall_minutes']:.2f}",
                    str(row["timeouts"]),
                ]
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize prompt-only TB2 pilot runs.")
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--verification-summary", type=Path, default=VERIFICATION_SUMMARY_PATH)
    parser.add_argument(
        "--all-attempts",
        action="store_true",
        help="Include repeated attempts instead of keeping the latest row per order/condition/task.",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--reprice-codex-model",
        default="",
        help=(
            "Recompute direct Codex row cost from recorded token usage as this "
            "model. Useful for historical runs accidentally executed with a "
            "non-baseline Codex model."
        ),
    )
    args = parser.parse_args(argv)

    rows = merge_verification(
        load_result_rows(args.runs_dir),
        load_verification_rows(args.verification_summary),
    )
    rows = reprice_codex_rows(rows, model=args.reprice_codex_model)
    if not args.all_attempts:
        rows = latest_per_assignment(rows)
    summary = summarize(rows)
    _print_table(summary)
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
