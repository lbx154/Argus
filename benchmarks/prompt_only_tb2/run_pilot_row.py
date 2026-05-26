from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from argus_skill.core.pricing import usd_for_tokens
from benchmarks.prompt_only_tb2.summarize_runs import _normalized_zero_touch_success

GENERATED_DIR = BASE_DIR / "generated"
RUNS_DIR = BASE_DIR / "runs"
ASSIGNMENT_PATH = GENERATED_DIR / "self_pilot_assignment.csv"
RUNS_CSV_PATH = GENERATED_DIR / "pilot_runs.csv"
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_BRACKETED_PASTE_START = "\x1b[200~"
_BRACKETED_PASTE_END = "\x1b[201~"
DEFAULT_CODEX_BASELINE_MODEL = "gpt-5.4"


def _safe_filename(value: str) -> str:
    safe = _SAFE_FILENAME_RE.sub("-", value).strip(".-")
    return safe or "task"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_assignment(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(
            f"assignment file not found: {path}\n"
            "Run extract_tb2_prompts.py first."
        )
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _select_row(
    rows: list[dict[str, str]],
    *,
    order: int | None,
    condition: str | None,
    task_id: str | None,
) -> dict[str, str]:
    matches = rows
    if order is not None:
        matches = [row for row in matches if int(row["order"]) == order]
    if condition is not None:
        matches = [row for row in matches if row["condition"] == condition]
    if task_id is not None:
        matches = [row for row in matches if row["task_id"] == task_id]
    if not matches:
        raise SystemExit("no assignment row matched the requested selector")
    if len(matches) > 1:
        choices = ", ".join(f"order={row['order']}" for row in matches[:10])
        raise SystemExit(f"selector matched multiple rows; pass --order ({choices})")
    return matches[0]


def _read_prompt(generated_dir: Path, row: dict[str, str]) -> tuple[Path, str]:
    rel = row.get("prompt_file", "")
    if not rel:
        raise SystemExit("assignment row has no prompt_file; regenerate prompts")
    path = generated_dir / rel
    if not path.exists():
        raise SystemExit(f"prompt file not found: {path}")
    return path, path.read_text(encoding="utf-8")


def _run_id(row: dict[str, str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        f"{stamp}-o{int(row['order']):03d}-"
        f"{row['condition']}-{_safe_filename(row['task_id'])}"
    )


def _default_timeout_seconds(row: dict[str, str], override_minutes: float | None) -> float:
    if override_minutes is not None:
        return override_minutes * 60.0
    # Hard TB tasks often have long official limits. Use task metadata when
    # present, but keep a practical self-pilot cap.
    raw = row.get("agent_timeout_sec", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 2 * 60 * 60
    return min(max(value, 30 * 60), 3 * 60 * 60)


def _version(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    return " ".join(proc.stdout.strip().split())[:300]


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return 0
    return 0


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("$", "").replace(",", "")
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0


def _jsonl_objects(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _nested_dicts(event: dict[str, Any]) -> list[dict[str, Any]]:
    out = [event]
    for key in ("usage", "content", "msg", "message"):
        value = event.get(key)
        if isinstance(value, dict):
            out.append(value)
            nested_usage = value.get("usage")
            if isinstance(nested_usage, dict):
                out.append(nested_usage)
    return out


def _sum_token_counts(events: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Return the last non-zero cumulative token triple from Codex JSONL events."""
    last_in = 0
    last_cached = 0
    last_out = 0
    for event in events:
        in_tok = 0
        cached_tok = 0
        out_tok = 0
        for payload in _nested_dicts(event):
            in_tok = in_tok or _coerce_int(payload.get("input_tokens"))
            cached_tok = cached_tok or _coerce_int(payload.get("cached_input_tokens"))
            out_tok = out_tok or _coerce_int(payload.get("output_tokens"))
        if in_tok > 0:
            last_in = in_tok
        if cached_tok > 0:
            last_cached = cached_tok
        if out_tok > 0:
            last_out = out_tok
    return last_in, last_cached, last_out


def _empty_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "layers": [],
    }


def _add_usage(
    stats: dict[str, dict[str, Any]],
    *,
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    layer: str,
) -> None:
    if input_tokens <= 0 and cached_input_tokens <= 0 and output_tokens <= 0:
        return
    row = stats.setdefault(model, _empty_usage())
    row["input_tokens"] += max(0, int(input_tokens))
    row["cached_input_tokens"] += max(0, int(cached_input_tokens))
    row["output_tokens"] += max(0, int(output_tokens))
    layers = set(row.get("layers", []))
    layers.add(layer)
    row["layers"] = sorted(layers)


def _event_usage_delta(
    event: dict[str, Any],
    *,
    layer: str,
    baselines: dict[tuple[str, str], tuple[int, int, int]],
    input_key: str = "input_tokens",
    cached_key: str = "cached_input_tokens",
    output_key: str = "output_tokens",
) -> tuple[int, int, int]:
    raw = (
        _coerce_int(event.get(input_key)),
        _coerce_int(event.get(cached_key)),
        _coerce_int(event.get(output_key)),
    )
    if str(event.get("usage_scope") or "delta").lower() != "cumulative":
        return raw

    session_id = str(
        event.get("session_id")
        or event.get("thread_id")
        or event.get("actor")
        or "__global__"
    )
    key = (layer, session_id)
    previous = baselines.get(key)
    baselines[key] = raw
    if previous is None:
        return raw
    delta = (
        raw[0] - previous[0],
        raw[1] - previous[1],
        raw[2] - previous[2],
    )
    if any(value < 0 for value in delta):
        return raw
    return delta


def _finalize_usage(stats: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for model, row in sorted(stats.items()):
        finalized = dict(row)
        finalized["cost_usd"] = usd_for_tokens(
            model,
            int(row.get("input_tokens", 0) or 0),
            int(row.get("cached_input_tokens", 0) or 0),
            int(row.get("output_tokens", 0) or 0),
        )
        finalized["layers"] = sorted(set(finalized.get("layers", [])))
        out[model] = finalized
    return out


def _usage_totals(stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "cost_usd": sum(float(row.get("cost_usd", 0.0) or 0.0) for row in stats.values()),
        "input_tokens": sum(int(row.get("input_tokens", 0) or 0) for row in stats.values()),
        "cached_input_tokens": sum(
            int(row.get("cached_input_tokens", 0) or 0) for row in stats.values()
        ),
        "output_tokens": sum(int(row.get("output_tokens", 0) or 0) for row in stats.values()),
    }


def _usage_json(stats: dict[str, dict[str, Any]]) -> str:
    return json.dumps(stats, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _model_from_events(events: list[dict[str, Any]], fallback: str) -> str:
    for event in events:
        for payload in _nested_dicts(event):
            model = payload.get("model") or payload.get("model_name")
            if isinstance(model, str) and model.strip():
                return model.strip()
    return fallback


def _cost_model_for(args: argparse.Namespace, row: dict[str, str]) -> str:
    if row["condition"] == "argus":
        return "multiple"
    return (
        args.cost_model
        or args.codex_model
        or os.environ.get("CODEX_MODEL", "")
        or os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "")
        or DEFAULT_CODEX_BASELINE_MODEL
    )


def _assigned_model_for_row(
    args: argparse.Namespace,
    row: dict[str, str],
    *,
    env: dict[str, str],
) -> str:
    if row["condition"] == "argus":
        return env.get("ARGUS_SKILL_ENGINEER_MODEL", "")
    return args.codex_model or env.get("CODEX_MODEL", "") or DEFAULT_CODEX_BASELINE_MODEL


def _actual_model_from_stats(cost: dict[str, Any], fallback: str) -> str:
    model_stats = cost.get("model_token_stats")
    if isinstance(model_stats, dict) and model_stats:
        names = sorted(str(name) for name in model_stats if str(name).strip())
        if len(names) == 1:
            return names[0]
        if names:
            return "multiple"
    cost_model = str(cost.get("cost_model") or "").strip()
    return cost_model or fallback


def _human_interactions_after_assignment(row: dict[str, Any]) -> int:
    human_messages = _coerce_int(row.get("human_messages"))
    nudges = _coerce_int(row.get("nudges"))
    status_checks = _coerce_int(row.get("status_checks"))
    manual_commands = _coerce_int(row.get("manual_commands"))
    return max(0, human_messages + nudges + status_checks + manual_commands - 1)


def _intervention_severity(needs_human: bool, manual_rescue: str) -> str:
    if manual_rescue.strip():
        return "manual_rescue"
    if needs_human:
        return "needs_human"
    return "zero_touch"


def _argus_model_defaults(env: dict[str, str] | None = None) -> dict[str, str]:
    source = env or os.environ
    engineer = source.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")
    reviewer = source.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4")
    scientist = source.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4")
    matcher = source.get("ARGUS_SKILL_MATCHER_MODEL", engineer)
    return {
        "engineer": engineer,
        "reviewer": reviewer,
        "critic": reviewer,
        "planner": reviewer,
        "matcher": matcher,
        "scientist": scientist,
        "distiller": scientist,
    }


def _codex_cost_from_logs(stdout_path: Path, *, model: str) -> dict[str, Any]:
    events = _jsonl_objects(stdout_path)
    model = _model_from_events(events, model)
    input_tokens, cached_input_tokens, output_tokens = _sum_token_counts(events)
    model_stats: dict[str, dict[str, Any]] = {}
    _add_usage(
        model_stats,
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        layer="codex",
    )
    model_stats = _finalize_usage(model_stats)
    totals = _usage_totals(model_stats)
    source = "argus_pricing_codex_json_tokens" if model_stats else "codex_json_no_usage"
    return {
        "cost_usd": totals["cost_usd"],
        "cost_source": source,
        "cost_scope": "turn_delta",
        "cost_model": model if model_stats else "",
        "input_tokens": totals["input_tokens"],
        "cached_input_tokens": totals["cached_input_tokens"],
        "output_tokens": totals["output_tokens"],
        "model_token_stats": model_stats,
    }


def _argus_events(run_root: Path) -> list[dict[str, Any]]:
    root = run_root / ".argus-skill"
    events: list[dict[str, Any]] = []
    for path in sorted(root.glob("**/events.jsonl")) + sorted(root.glob("**/events.jsonl.1")):
        events.extend(_jsonl_objects(path))
    return events


def _argus_journal_rows(run_root: Path) -> list[dict[str, Any]]:
    root = run_root / ".argus-skill"
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("**/journal.jsonl")) + sorted(root.glob("**/journal.jsonl.1")):
        rows.extend(_jsonl_objects(path))
    return rows


def _argus_cost_from_logs(run_root: Path, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    events = _argus_events(run_root)
    models = _argus_model_defaults(env)
    model_stats: dict[str, dict[str, Any]] = {}
    usage_baselines: dict[tuple[str, str], tuple[int, int, int]] = {}
    for event in events:
        etype = str(event.get("type") or "")
        if etype == "round.main.completed":
            input_tokens, cached_input_tokens, output_tokens = _event_usage_delta(
                event,
                layer="engineer",
                baselines=usage_baselines,
            )
            _add_usage(
                model_stats,
                model=models["engineer"],
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                layer="engineer",
            )
            continue
        if etype == "round.review.completed":
            input_tokens, cached_input_tokens, output_tokens = _event_usage_delta(
                event,
                layer="reviewer",
                baselines=usage_baselines,
            )
            _add_usage(
                model_stats,
                model=models["reviewer"],
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                layer="reviewer",
            )
            continue
        if etype == "life.iteration.critic":
            input_tokens, cached_input_tokens, output_tokens = _event_usage_delta(
                event,
                layer="critic",
                baselines=usage_baselines,
            )
            _add_usage(
                model_stats,
                model=models["critic"],
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                layer="critic",
            )
            continue
        if etype.startswith("life.planner."):
            input_tokens, cached_input_tokens, output_tokens = _event_usage_delta(
                event,
                layer="planner",
                baselines=usage_baselines,
            )
            _add_usage(
                model_stats,
                model=models["planner"],
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                layer="planner",
            )
            continue
        if etype == "skill.outcome":
            _add_usage(
                model_stats,
                model=models["matcher"],
                input_tokens=_coerce_int(event.get("matcher_input_tokens")),
                cached_input_tokens=_coerce_int(event.get("matcher_cached_input_tokens")),
                output_tokens=_coerce_int(event.get("matcher_output_tokens")),
                layer="matcher",
            )
            _add_usage(
                model_stats,
                model=models["distiller"],
                input_tokens=_coerce_int(event.get("distiller_input_tokens")),
                cached_input_tokens=_coerce_int(event.get("distiller_cached_input_tokens")),
                output_tokens=_coerce_int(event.get("distiller_output_tokens")),
                layer="distiller",
            )

    journal_rows = _argus_journal_rows(run_root)
    mission_rows = [
        row for row in journal_rows
        if row.get("kind") in {"mission_complete", "mission_failed", "mission_iterated"}
    ]
    if not model_stats:
        for row in mission_rows:
            extra_value = row.get("extra")
            extra = extra_value if isinstance(extra_value, dict) else {}
            _add_usage(
                model_stats,
                model=str(extra.get("engineer_model") or models["engineer"]),
                input_tokens=_coerce_int(extra.get("input_tokens")),
                cached_input_tokens=_coerce_int(extra.get("cached_input_tokens")),
                output_tokens=_coerce_int(extra.get("output_tokens")),
                layer="mission_total",
            )

    model_stats = _finalize_usage(model_stats)
    totals = _usage_totals(model_stats)
    cost_source = "argus_pricing_event_tokens" if model_stats else "argus_no_usage"

    return {
        "cost_usd": totals["cost_usd"],
        "cost_source": cost_source,
        "cost_scope": "task_event_delta",
        "cost_model": "multiple" if len(model_stats) > 1 else next(iter(model_stats), ""),
        "input_tokens": totals["input_tokens"],
        "cached_input_tokens": totals["cached_input_tokens"],
        "output_tokens": totals["output_tokens"],
        "model_token_stats": model_stats,
    }


def _cost_from_logs(
    row: dict[str, str],
    *,
    args: argparse.Namespace,
    run_root: Path,
    stdout_path: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    model = _cost_model_for(args, row)
    if row["condition"] == "codex":
        return _codex_cost_from_logs(stdout_path, model=model)
    if row["condition"] == "argus":
        return _argus_cost_from_logs(run_root, env=env)
    return {
        "cost_usd": 0.0,
        "cost_source": "unknown_condition",
        "cost_scope": "unknown",
        "cost_model": model,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "model_token_stats": {},
    }


def _prompt_with_runner_context(prompt: str, *, run_id: str, run_root: Path) -> str:
    return (
        "## Automated pilot runner context\n\n"
        f"- Pilot run id: `{run_id}`\n"
        f"- Current working directory / pilot root: `{run_root}`\n"
        "- The runner has sent this initial assignment automatically. If you can "
        "finish without more input, do so. If a human decision is required, stop "
        "and make the request explicit in your final response.\n\n"
        f"{prompt}"
    )


def _codex_command(args: argparse.Namespace, run_root: Path) -> list[str]:
    cmd = [
        args.codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        args.codex_sandbox,
        "--cd",
        str(run_root),
    ]
    if args.codex_json:
        cmd.append("--json")
    if args.codex_model:
        cmd.extend(["--model", args.codex_model])
    for extra in args.codex_extra_arg or []:
        cmd.append(extra)
    cmd.append("-")
    return cmd


def _argus_command(args: argparse.Namespace, run_root: Path) -> list[str]:
    if args.argus_cmd:
        cmd = args.argus_cmd[:]
    else:
        cmd = [sys.executable, "-m", "argus_skill"]
    cmd.extend([
        "--no-daemon",
        "--life-dir",
        str(run_root / ".argus-skill"),
    ])
    return cmd


def _argus_stdin(prompt: str) -> str:
    # Preserve prompt blank lines as one REPL message; otherwise piped stdin's
    # blank-line block parsing splits one benchmark assignment into missions.
    return (
        "/config iterate=false\n"
        f"{_BRACKETED_PASTE_START}{prompt}{_BRACKETED_PASTE_END}\n"
        "/exit\n"
    )


def _command_for_row(args: argparse.Namespace, row: dict[str, str], run_root: Path) -> list[str]:
    if row["condition"] == "codex":
        return _codex_command(args, run_root)
    if row["condition"] == "argus":
        return _argus_command(args, run_root)
    raise SystemExit(f"unknown condition: {row['condition']}")


def _env_for_row(row: dict[str, str], run_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    if row["condition"] == "argus":
        env["ARGUS_SKILL_LIFE_BACKEND"] = "codex"
        env["ARGUS_SKILL_WORKDIR"] = str(run_root)
        env["ARGUS_SKILL_BENCHMARK_MODE"] = "1"
        env["ARGUS_SKILL_BENCHMARK_VERIFIER_GATE"] = "1"
        env.pop("ARGUS_SKILL_NO_REVIEWER", None)
        env.setdefault("ARGUS_SKILL_BENCHMARK_TERSE", "1")
        env.setdefault("ARGUS_SKILL_DISTILL_ON_MISS", "0")
        env.setdefault("ARGUS_SKILL_SKILL_WRITEBACK", "0")
        engineer_model = env.setdefault("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")
        env.setdefault("ARGUS_SKILL_REVIEWER_MODEL", engineer_model)
        env.setdefault("ARGUS_SKILL_SCIENTIST_MODEL", engineer_model)
        env.setdefault("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "low")
        env.setdefault("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "low")
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(REPO_ROOT) if not existing else f"{REPO_ROOT}{os.pathsep}{existing}"
        )
    return env


def _human_request_hint(text: str) -> bool:
    lower = text.lower()
    needles = [
        "need clarification",
        "need more information",
        "cannot proceed",
        "can't proceed",
        "please provide",
        "requires human",
        "human action",
        "docker is unavailable",
        "permission denied",
    ]
    return any(needle in lower for needle in needles)


def _append_csv(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(row)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_process(
    *,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    stdin_text: str,
    timeout_seconds: float,
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[int, bool]:
    timed_out = False
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        try:
            proc.communicate(stdin_text, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=20)
    return int(proc.returncode or 0), timed_out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one TB v2 prompt-only pilot row.")
    parser.add_argument("--order", type=int, help="Assignment order to run.")
    parser.add_argument("--condition", choices=["codex", "argus"], help="Condition selector.")
    parser.add_argument("--task-id", help="Task selector.")
    parser.add_argument("--assignment", type=Path, default=ASSIGNMENT_PATH)
    parser.add_argument("--generated-dir", type=Path, default=GENERATED_DIR)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--results-csv", type=Path, default=RUNS_CSV_PATH)
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--timeout-minutes", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--codex-sandbox", default="danger-full-access")
    parser.add_argument(
        "--codex-model",
        default=os.environ.get("CODEX_MODEL") or DEFAULT_CODEX_BASELINE_MODEL,
        help=(
            "Model for direct Codex baseline rows. Defaults to gpt-5.4 so the "
            "baseline is not accidentally run with the cheaper mini model."
        ),
    )
    parser.add_argument(
        "--cost-model",
        default=os.environ.get("TB2_PILOT_COST_MODEL", ""),
        help="Model used for USD estimation when the runner reports tokens but no cost.",
    )
    parser.add_argument(
        "--no-codex-json",
        action="store_false",
        dest="codex_json",
        help="Do not pass --json to `codex exec` (cost may be unavailable).",
    )
    parser.set_defaults(codex_json=True)
    parser.add_argument(
        "--codex-extra-arg",
        action="append",
        default=[],
        help="Extra argument appended to `codex exec` (repeatable).",
    )
    parser.add_argument(
        "--argus-cmd",
        nargs="+",
        help="Command used to launch argus-skill before --no-daemon/--life-dir.",
    )
    args = parser.parse_args(argv)

    if args.order is None and args.condition is None and args.task_id is None:
        parser.error("pass --order, or a --condition/--task-id selector")

    rows = _load_assignment(args.assignment)
    row = _select_row(
        rows,
        order=args.order,
        condition=args.condition,
        task_id=args.task_id,
    )
    prompt_source, base_prompt = _read_prompt(args.generated_dir, row)
    run_id = _run_id(row)
    run_root = (args.run_root or args.runs_dir / run_id).resolve()
    run_root.mkdir(parents=True, exist_ok=False)
    prompt = _prompt_with_runner_context(base_prompt, run_id=run_id, run_root=run_root)
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    prompt_path = run_root / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    cmd = _command_for_row(args, row, run_root)
    env = _env_for_row(row, run_root)
    stdin_text = prompt
    if row["condition"] == "argus":
        stdin_text = _argus_stdin(prompt)
    timeout_seconds = _default_timeout_seconds(row, args.timeout_minutes)

    stdout_path = run_root / "stdout.log"
    stderr_path = run_root / "stderr.log"
    assigned_model = _assigned_model_for_row(args, row, env=env)
    metadata = {
        "run_id": run_id,
        "row": row,
        "run_root": str(run_root),
        "prompt_source": str(prompt_source),
        "prompt_path": str(prompt_path),
        "prompt_sha256": prompt_sha,
        "command": cmd,
        "timeout_seconds": timeout_seconds,
        "codex_json": bool(args.codex_json),
        "cost_model": _cost_model_for(args, row),
        "assigned_model": assigned_model,
        "pricing_source": "argus_skill.core.pricing.usd_for_tokens",
        "argus_benchmark_mode": env.get("ARGUS_SKILL_BENCHMARK_MODE", ""),
        "argus_benchmark_verifier_gate": env.get(
            "ARGUS_SKILL_BENCHMARK_VERIFIER_GATE", ""
        ),
        "argus_engineer_model": env.get("ARGUS_SKILL_ENGINEER_MODEL", ""),
        "argus_reviewer_model": env.get("ARGUS_SKILL_REVIEWER_MODEL", ""),
        "argus_scientist_model": env.get("ARGUS_SKILL_SCIENTIST_MODEL", ""),
        "argus_distill_on_miss": env.get("ARGUS_SKILL_DISTILL_ON_MISS", ""),
        "argus_no_reviewer": env.get("ARGUS_SKILL_NO_REVIEWER", ""),
        "argus_benchmark_terse": env.get("ARGUS_SKILL_BENCHMARK_TERSE", ""),
        "argus_engineer_reasoning_effort": env.get(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", ""
        ),
        "argus_reviewer_reasoning_effort": env.get(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", ""
        ),
        "argus_skill_writeback": env.get("ARGUS_SKILL_SKILL_WRITEBACK", ""),
        "codex_version": _version([args.codex_bin, "--version"]),
        "argus_version": _version([sys.executable, "-m", "argus_skill", "--version"]),
        "docker_version": _version(["docker", "--version"]),
    }
    _write_json(run_root / "metadata.json", metadata)

    if args.dry_run:
        print(f"run_root: {run_root}")
        print("command:")
        print("  " + " ".join(cmd))
        print(f"prompt: {prompt_path}")
        return 0

    started = _utc_now()
    t0 = time.monotonic()
    exit_code, timed_out = _run_process(
        cmd=cmd,
        cwd=run_root,
        env=env,
        stdin_text=stdin_text,
        timeout_seconds=timeout_seconds,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    wall_seconds = time.monotonic() - t0
    ended = _utc_now()

    combined_tail = ""
    for path in (stdout_path, stderr_path):
        try:
            combined_tail += path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
    workspace = row.get("workspace", "").removeprefix("./")
    workspace_exists = bool(workspace) and (run_root / workspace).exists()
    cost = _cost_from_logs(
        row,
        args=args,
        run_root=run_root,
        stdout_path=stdout_path,
        env=env,
    )
    human_hint = _human_request_hint(combined_tail)
    needs_human = timed_out or exit_code != 0 or human_hint or not workspace_exists
    notes = []
    if timed_out:
        notes.append("timed out")
    if exit_code != 0:
        notes.append(f"exit_code={exit_code}")
    if human_hint:
        notes.append("output appears to request human action")
    if not workspace_exists:
        notes.append(f"workspace not exported: {row.get('workspace', '')}")

    result = {
        "run_id": run_id,
        "order": row["order"],
        "pair_id": row["pair_id"],
        "condition": row["condition"],
        "task_id": row["task_id"],
        "workspace": row["workspace"],
        "docker_image": row["docker_image"],
        "run_root": str(run_root),
        "prompt_file": str(prompt_source),
        "prompt_sha256": prompt_sha,
        "started_at": started,
        "ended_at": ended,
        "wall_minutes": f"{wall_seconds / 60.0:.2f}",
        "cost_usd": f"{float(cost['cost_usd']):.6f}",
        "cost_source": cost["cost_source"],
        "cost_scope": cost["cost_scope"],
        "cost_model": cost["cost_model"],
        "pricing_source": "argus_skill.core.pricing.usd_for_tokens",
        "argus_benchmark_mode": env.get("ARGUS_SKILL_BENCHMARK_MODE", ""),
        "argus_benchmark_verifier_gate": env.get(
            "ARGUS_SKILL_BENCHMARK_VERIFIER_GATE", ""
        ),
        "argus_engineer_model": env.get("ARGUS_SKILL_ENGINEER_MODEL", ""),
        "argus_reviewer_model": env.get("ARGUS_SKILL_REVIEWER_MODEL", ""),
        "argus_scientist_model": env.get("ARGUS_SKILL_SCIENTIST_MODEL", ""),
        "argus_distill_on_miss": env.get("ARGUS_SKILL_DISTILL_ON_MISS", ""),
        "argus_no_reviewer": env.get("ARGUS_SKILL_NO_REVIEWER", ""),
        "argus_benchmark_terse": env.get("ARGUS_SKILL_BENCHMARK_TERSE", ""),
        "argus_engineer_reasoning_effort": env.get(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", ""
        ),
        "argus_reviewer_reasoning_effort": env.get(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", ""
        ),
        "argus_skill_writeback": env.get("ARGUS_SKILL_SKILL_WRITEBACK", ""),
        "assigned_model": assigned_model,
        "input_tokens": cost["input_tokens"],
        "cached_input_tokens": cost["cached_input_tokens"],
        "output_tokens": cost["output_tokens"],
        "model_token_stats": _usage_json(cost["model_token_stats"]),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "needs_human": needs_human,
        "human_messages": 1,
        "nudges": 0,
        "status_checks": 0,
        "manual_commands": 0,
        "manual_rescue": "",
        "human_interactions_after_assignment": 0,
        "active_touch_minutes_after_assignment": 0.0,
        "zero_touch_success": _normalized_zero_touch_success(
            {
                "needs_human": needs_human,
                "zero_touch_success": not needs_human,
                "human_interactions_after_assignment": 0,
                "manual_commands": 0,
                "nudges": 0,
                "status_checks": 0,
                "manual_rescue": "",
            }
        ),
        "intervention_severity": _intervention_severity(
            needs_human,
            "",
        ),
        "actual_model": _actual_model_from_stats(cost, assigned_model),
        "model_drift": False,
        "model_drift_reason": "",
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "notes": "; ".join(notes),
    }
    actual_model = result["actual_model"]
    model_drift = bool(actual_model and assigned_model and actual_model != assigned_model)
    if model_drift:
        drift_reason = f"assigned model {assigned_model!r} drifted to {actual_model!r}"
        notes.append(drift_reason)
        result["model_drift"] = True
        result["model_drift_reason"] = drift_reason
        result["needs_human"] = True
        result["zero_touch_success"] = _normalized_zero_touch_success(
            {
                **result,
                "needs_human": True,
                "zero_touch_success": False,
            }
        )
        result["intervention_severity"] = "model_drift"
        result["notes"] = "; ".join(notes)
        _write_json(run_root / "result.json", result)
        _append_csv(args.results_csv, result)
        print(f"run_root: {run_root}")
        print(f"result  : {run_root / 'result.json'}")
        print(f"logs    : {stdout_path} ; {stderr_path}")
        print(f"csv     : {args.results_csv}")
        print(
            f"cost    : ${float(cost['cost_usd']):.6f} "
            f"({cost['cost_source']}, model={cost['cost_model']})"
        )
        print(
            f"needs_human={str(needs_human).lower()} exit_code={exit_code} "
            f"timed_out={timed_out} model_drift=true"
        )
        return 2
    _write_json(run_root / "result.json", result)
    _append_csv(args.results_csv, result)

    print(f"run_root: {run_root}")
    print(f"result  : {run_root / 'result.json'}")
    print(f"logs    : {stdout_path} ; {stderr_path}")
    print(f"csv     : {args.results_csv}")
    print(
        f"cost    : ${float(cost['cost_usd']):.6f} "
        f"({cost['cost_source']}, model={cost['cost_model']})"
    )
    print(f"needs_human={str(needs_human).lower()} exit_code={exit_code} timed_out={timed_out}")
    return 1 if needs_human else 0


if __name__ == "__main__":
    raise SystemExit(main())
