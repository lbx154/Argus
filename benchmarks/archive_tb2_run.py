"""Export tracked TB2 runs into validated archival bundles."""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from benchmarks.harbor_adapter import _compute_model_cost_usd, _sum_all_session_tokens

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
DEFAULT_ARCHIVE_ROOT = BASE_DIR / "evidence"
TB2_EXPORT_BUNDLE_TYPE = "tb2_fullbench_export"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text_or_none(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _json_read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.replace(path)


def _copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        env.pop(key, None)
    return env


def _git_output(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            env=_git_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        return f"unavailable: {exc}"
    text = proc.stdout.strip()
    return text if text else "unavailable"


def _parse_timestamp(value: Any) -> datetime | None:
    text = _text_or_none(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration_minutes(started_at: Any, finished_at: Any) -> str:
    start = _parse_timestamp(started_at)
    end = _parse_timestamp(finished_at)
    if start is None or end is None:
        return ""
    minutes = max(0.0, (end - start).total_seconds() / 60.0)
    return f"{minutes:.2f}"


def _infer_infra_failure(texts: Iterable[Any]) -> str:
    joined = "\n".join(_text_or_none(text) for text in texts).lower()
    if "all predefined address pools have been fully subnetted" in joined:
        return "docker_address_pool_exhaustion"
    if "docker compose command failed" in joined or "error response from daemon" in joined:
        return "docker_compose_failure"
    return "none"


def _maybe_positive_float(value: Any) -> str:
    text = _text_or_none(value).replace(",", "")
    if not text:
        return ""
    try:
        parsed = float(text)
    except ValueError:
        return ""
    return f"{parsed:g}"


def _maybe_float(value: Any) -> str:
    text = _text_or_none(value).replace(",", "")
    if not text:
        return ""
    try:
        parsed = float(text)
    except ValueError:
        return ""
    return f"{parsed:g}"


def _maybe_int(value: Any) -> str:
    text = _text_or_none(value).replace(",", "")
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except ValueError:
        return ""


def _token_cause(value: str, *, scope: str) -> str:
    return "" if value else f"{scope}_result_missing_token_total"


def _cost_cause(value: str, *, scope: str) -> str:
    return "" if value else f"{scope}_result_missing_cost_total"


def _prefer_stat_or_trial(stat_value: str, trial_total: float, trial_present: bool) -> str:
    if stat_value and stat_value != "0":
        return stat_value
    if trial_present:
        return f"{trial_total:g}"
    return stat_value


def _aggregate_reward(result: dict[str, Any]) -> str:
    stats = result.get("stats")
    if not isinstance(stats, dict):
        return ""
    evals = stats.get("evals")
    if not isinstance(evals, dict) or not evals:
        return ""
    first = evals.get(sorted(evals)[0])
    if not isinstance(first, dict):
        return ""
    metrics = first.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        return ""
    mean = metrics[0].get("mean")
    return _maybe_float(mean)


def _aggregate_exception_summary(result: dict[str, Any]) -> tuple[str, str]:
    stats = result.get("stats")
    if not isinstance(stats, dict):
        return ("none", "0")
    exception_stats = stats.get("exception_stats")
    if not isinstance(exception_stats, dict) or not exception_stats:
        return ("none", "0")
    items: list[str] = []
    total = 0
    for kind in sorted(exception_stats):
        names = exception_stats.get(kind)
        if isinstance(names, list):
            count = len(names)
        elif isinstance(names, dict):
            count = len(names)
        else:
            count = 0
        total += count
        items.append(f"{kind}:{count}")
    return (", ".join(items) if items else "none", str(total))


def _manifest_metadata(source_manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = source_manifest.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _model_name_from_result(
    result: dict[str, Any],
    source_manifest: dict[str, Any],
) -> str:
    agent_info = result.get("agent_info")
    if isinstance(agent_info, dict):
        model_info = agent_info.get("model_info")
        if isinstance(model_info, dict):
            name = _text_or_none(model_info.get("name"))
            if name:
                return name
    metadata = _manifest_metadata(source_manifest)
    model_ids = metadata.get("model_ids")
    if isinstance(model_ids, dict):
        for key in ("engineer", "agent", "model", "codex"):
            name = _text_or_none(model_ids.get(key))
            if name:
                return name
    return ""


def _trial_session_usage(job_dir: Path) -> dict[str, Any]:
    return _sum_all_session_tokens(job_dir / "agent" / "sessions")


def _update_trial_agent_result(
    *,
    result: dict[str, Any],
    result_path: Path,
    job_dir: Path,
    source_manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    agent_result = result.get("agent_result")
    if not isinstance(agent_result, dict):
        agent_result = {}

    session_usage = _trial_session_usage(job_dir)
    session_input = int(session_usage.get("total_input", 0) or 0)
    session_cached = int(session_usage.get("total_cached", 0) or 0)
    session_output = int(session_usage.get("total_output", 0) or 0)
    session_has_usage = any((session_input, session_cached, session_output))

    input_tokens = _maybe_positive_float(agent_result.get("n_input_tokens"))
    cached_input_tokens = _maybe_positive_float(agent_result.get("n_cache_tokens"))
    output_tokens = _maybe_positive_float(agent_result.get("n_output_tokens"))
    cost_usd = _maybe_positive_float(agent_result.get("cost_usd"))

    if session_has_usage:
        input_tokens = f"{session_input:g}"
        cached_input_tokens = f"{session_cached:g}"
        output_tokens = f"{session_output:g}"
        model_name = _model_name_from_result(result, source_manifest)
        session_cost = None
        if model_name and any((session_input, session_cached, session_output)):
            session_cost = _compute_model_cost_usd(
                model_name,
                session_input,
                session_output,
                session_cached,
            )
        if session_cost is not None:
            cost_usd = _maybe_positive_float(session_cost)
        agent_result = {
            **agent_result,
            "n_input_tokens": session_input,
            "n_cache_tokens": session_cached,
            "n_output_tokens": session_output,
            "cost_usd": session_cost if session_cost is not None else agent_result.get("cost_usd"),
        }
        result["agent_result"] = agent_result
        _write_json(result_path, result)

    telemetry = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }
    return result, telemetry


@dataclass(frozen=True)
class ExportedRow:
    data: dict[str, Any]


def _aggregate_row(
    *,
    source_run_root: Path,
    bundle_root: Path,
    raw_root: Path,
    source_jobs_root_name: str,
    source_manifest: dict[str, Any],
    source_result: dict[str, Any],
    trial_rows: list[dict[str, Any]],
) -> ExportedRow:
    aggregate_dir = raw_root / source_jobs_root_name
    aggregate_result_path = aggregate_dir / "result.json"
    aggregate_job_log = aggregate_dir / "job.log"
    aggregate_metadata = aggregate_dir / "metadata.json"
    exception_kind, exception_count = _aggregate_exception_summary(source_result)
    stat = source_result.get("stats")
    stat = stat if isinstance(stat, dict) else {}
    trial_input_tokens = sum(
        float(_text_or_none(row.get("input_tokens"))) if _text_or_none(row.get("input_tokens")) else 0.0
        for row in trial_rows
        if row.get("row_kind") == "trial"
    )
    trial_cached_tokens = sum(
        float(_text_or_none(row.get("cached_input_tokens"))) if _text_or_none(row.get("cached_input_tokens")) else 0.0
        for row in trial_rows
        if row.get("row_kind") == "trial"
    )
    trial_output_tokens = sum(
        float(_text_or_none(row.get("output_tokens"))) if _text_or_none(row.get("output_tokens")) else 0.0
        for row in trial_rows
        if row.get("row_kind") == "trial"
    )
    trial_cost_usd = sum(
        float(_text_or_none(row.get("cost_usd"))) if _text_or_none(row.get("cost_usd")) else 0.0
        for row in trial_rows
        if row.get("row_kind") == "trial"
    )
    infra_failure_kind = _infer_infra_failure(
        [
            aggregate_job_log.read_text(encoding="utf-8", errors="replace") if aggregate_job_log.exists() else "",
            json.dumps(source_result, ensure_ascii=False),
        ]
    )
    metadata = _manifest_metadata(source_manifest)
    stat_input_tokens = _maybe_positive_float(stat.get("n_input_tokens"))
    stat_cached_tokens = _maybe_positive_float(stat.get("n_cache_tokens"))
    stat_output_tokens = _maybe_positive_float(stat.get("n_output_tokens"))
    stat_cost_usd = _maybe_positive_float(stat.get("cost_usd"))
    trial_input_present = any(
        _text_or_none(row.get("input_tokens"))
        for row in trial_rows
        if row.get("row_kind") == "trial"
    )
    trial_cached_present = any(
        _text_or_none(row.get("cached_input_tokens"))
        for row in trial_rows
        if row.get("row_kind") == "trial"
    )
    trial_output_present = any(
        _text_or_none(row.get("output_tokens"))
        for row in trial_rows
        if row.get("row_kind") == "trial"
    )
    trial_cost_present = any(
        _text_or_none(row.get("cost_usd"))
        for row in trial_rows
        if row.get("row_kind") == "trial"
    )
    input_tokens = _prefer_stat_or_trial(stat_input_tokens, trial_input_tokens, trial_input_present)
    cached_input_tokens = _prefer_stat_or_trial(stat_cached_tokens, trial_cached_tokens, trial_cached_present)
    output_tokens = _prefer_stat_or_trial(stat_output_tokens, trial_output_tokens, trial_output_present)
    cost_usd = _prefer_stat_or_trial(stat_cost_usd, trial_cost_usd, trial_cost_present)
    if isinstance(stat, dict):
        stats_copy = dict(stat)
        if input_tokens:
            stats_copy["n_input_tokens"] = trial_input_tokens if trial_input_present else float(input_tokens)
        if cached_input_tokens:
            stats_copy["n_cache_tokens"] = trial_cached_tokens if trial_cached_present else float(cached_input_tokens)
        if output_tokens:
            stats_copy["n_output_tokens"] = trial_output_tokens if trial_output_present else float(output_tokens)
        if cost_usd:
            stats_copy["cost_usd"] = trial_cost_usd if trial_cost_present else float(cost_usd)
        if stats_copy != stat:
            source_result = dict(source_result)
            source_result["stats"] = stats_copy
            _write_json(aggregate_result_path, source_result)
    row = {
        "row_kind": "aggregate",
        "job_id": source_jobs_root_name,
        "trial_name": "",
        "condition": str(metadata.get("condition") or ""),
        "task_id": "",
        "source_run_root": str(source_run_root),
        "bundle_dir": str(aggregate_dir.relative_to(bundle_root)),
        "result_json": str(aggregate_result_path.relative_to(bundle_root)),
        "job_log": str(aggregate_job_log.relative_to(bundle_root)) if aggregate_job_log.exists() else "",
        "trial_log": "",
        "metadata_json": str(aggregate_metadata.relative_to(bundle_root)),
        "verification_log": "",
        "verification_reward_txt": "",
        "verification_ctrf_json": "",
        "agent_dir": "",
        "verifier_dir": "",
        "reward": _aggregate_reward(source_result),
        "wall_minutes": _duration_minutes(source_result.get("started_at"), source_result.get("finished_at")),
        "status": "completed" if _text_or_none(stat.get("n_running_trials")) in {"0", ""} else "incomplete",
        "n_total_trials": _maybe_int(source_result.get("n_total_trials")),
        "n_completed_trials": _maybe_int(stat.get("n_completed_trials")),
        "n_running_trials": _maybe_int(stat.get("n_running_trials")),
        "n_pending_trials": _maybe_int(stat.get("n_pending_trials")),
        "n_errored_trials": _maybe_int(stat.get("n_errored_trials")),
        "exception_kind": exception_kind,
        "exception_count": exception_count,
        "infra_failure_kind": infra_failure_kind,
        "infra_failure_count": "1" if infra_failure_kind != "none" else "0",
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "input_tokens_missing_cause": _token_cause(input_tokens, scope="aggregate"),
        "cached_input_tokens_missing_cause": _token_cause(cached_input_tokens, scope="aggregate"),
        "output_tokens_missing_cause": _token_cause(output_tokens, scope="aggregate"),
        "cost_usd_missing_cause": _cost_cause(cost_usd, scope="aggregate"),
    }
    _write_json(aggregate_metadata, {
        "job_id": source_jobs_root_name,
        "row_kind": "aggregate",
        "source_run_root": str(source_run_root),
        "source_manifest": source_manifest,
        "source_result": source_result,
        "exported_at": _utc_now(),
    })
    return ExportedRow(row)


def _trial_row(
    *,
    bundle_root: Path,
    source_run_root: Path,
    source_manifest: dict[str, Any],
    job_dir: Path,
    aggregate_raw_root: Path,
) -> ExportedRow:
    result_path = job_dir / "result.json"
    result = _json_read(result_path)
    result, telemetry = _update_trial_agent_result(
        result=result,
        result_path=result_path,
        job_dir=job_dir,
        source_manifest=source_manifest,
    )
    config_path = job_dir / "config.json"
    trial_log = job_dir / "trial.log"
    verifier_dir = job_dir / "verifier"
    verifier_log = verifier_dir / "test-stdout.txt"
    verifier_reward = verifier_dir / "reward.txt"
    verifier_ctrf = verifier_dir / "ctrf.json"
    agent_dir = job_dir / "agent"
    exception_info = result.get("exception_info")
    exception_kind = "none"
    exception_count = "0"
    infra_failure_kind = "none"
    infra_failure_count = "0"
    reward = "0"
    if isinstance(exception_info, dict) and exception_info:
        exception_kind = str(exception_info.get("exception_type") or "RuntimeError")
        exception_count = "1"
        infra_failure_kind = _infer_infra_failure(
            [exception_info.get("exception_message"), trial_log.read_text(encoding="utf-8", errors="replace") if trial_log.exists() else ""]
        )
        infra_failure_count = "1" if infra_failure_kind != "none" else "0"

    metadata = _manifest_metadata(source_manifest)
    verifier_result = result.get("verifier_result")
    if isinstance(verifier_result, dict):
        rewards = verifier_result.get("rewards")
        if isinstance(rewards, dict):
            reward = _maybe_float(rewards.get("reward"))
    input_tokens = telemetry["input_tokens"]
    cached_input_tokens = telemetry["cached_input_tokens"]
    output_tokens = telemetry["output_tokens"]
    cost_usd = telemetry["cost_usd"]
    token_cause = _token_cause(input_tokens, scope="trial")
    cached_cause = _token_cause(cached_input_tokens, scope="trial")
    output_cause = _token_cause(output_tokens, scope="trial")
    cost_cause = _cost_cause(cost_usd, scope="trial")

    trial_id = job_dir.relative_to(aggregate_raw_root).as_posix().replace("/", "__")
    row = {
        "row_kind": "trial",
        "job_id": trial_id,
        "trial_name": str(result.get("trial_name") or ""),
        "condition": str(metadata.get("condition") or ""),
        "task_id": str((result.get("task_id") or {}).get("path") or result.get("task_name") or ""),
        "source_run_root": str(source_run_root),
        "bundle_dir": str(job_dir.relative_to(bundle_root)),
        "result_json": str(result_path.relative_to(bundle_root)),
        "job_log": "",
        "trial_log": str(trial_log.relative_to(bundle_root)) if trial_log.exists() else "",
        "metadata_json": str(config_path.relative_to(bundle_root)) if config_path.exists() else "",
        "verification_log": str(verifier_log.relative_to(bundle_root)) if verifier_log.exists() else "",
        "verification_reward_txt": str(verifier_reward.relative_to(bundle_root)) if verifier_reward.exists() else "",
        "verification_ctrf_json": str(verifier_ctrf.relative_to(bundle_root)) if verifier_ctrf.exists() else "",
        "agent_dir": str(agent_dir.relative_to(bundle_root)) if agent_dir.exists() else "",
        "verifier_dir": str(verifier_dir.relative_to(bundle_root)) if verifier_dir.exists() else "",
        "reward": reward,
        "wall_minutes": _duration_minutes(result.get("started_at"), result.get("finished_at")),
        "status": "error" if isinstance(exception_info, dict) and exception_info else "completed",
        "n_total_trials": "",
        "n_completed_trials": "",
        "n_running_trials": "",
        "n_pending_trials": "",
        "n_errored_trials": "",
        "exception_kind": exception_kind,
        "exception_count": exception_count,
        "infra_failure_kind": infra_failure_kind,
        "infra_failure_count": infra_failure_count,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "input_tokens_missing_cause": token_cause,
        "cached_input_tokens_missing_cause": cached_cause,
        "output_tokens_missing_cause": output_cause,
        "cost_usd_missing_cause": cost_cause,
    }
    return ExportedRow(row)


def _bundle_build_info(source_run_root: Path, source_manifest: dict[str, Any], source_status: dict[str, Any]) -> str:
    metadata = _manifest_metadata(source_manifest)
    return "\n".join(
        [
            "# Build Info",
            "",
            f"- Exported at: {_utc_now()}",
            f"- Exporter: {Path(__file__).name}",
            f"- Source run root: {source_run_root}",
            f"- Source run id: {source_manifest.get('run_id', '')}",
            f"- Source status: {source_status.get('state', '')}",
            f"- Git commit: {_git_output(['rev-parse', 'HEAD'])}",
            f"- Git status: {_git_output(['status', '--short']) or 'clean'}",
            f"- Python: {sys.version.split()[0]}",
            f"- Platform: {platform.platform()}",
            "",
            "## Source Metadata",
            "",
            "```json",
            json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False),
            "```",
            "",
        ]
    )


def _bundle_plan(source_run_root: Path, source_manifest: dict[str, Any]) -> str:
    metadata = _manifest_metadata(source_manifest)
    return "\n".join(
        [
            "# Plan",
            "",
            "Archive a tracked TB2 run into a repo-visible evidence bundle.",
            "",
            "## Source",
            "",
            f"- Source run root: {source_run_root}",
            f"- Run id: {source_manifest.get('run_id', '')}",
            f"- Bundle type: {TB2_EXPORT_BUNDLE_TYPE}",
            f"- Condition: {metadata.get('condition', '')}",
            f"- Dataset id: {metadata.get('dataset_id', '')}",
            f"- Dataset commit: {metadata.get('dataset_commit', '')}",
            "",
            "## Scope",
            "",
            "- Preserve the aggregate Harbor run result.",
            "- Preserve every trial directory under `jobs/raw/`.",
            "- Copy root `stdout.log` and `stderr.log` into `logs/`.",
            "- Record trial, verifier, and artifact paths in `summary.tsv` and `jobs/index.tsv`.",
            "",
        ]
    )


def _bundle_results(rows: list[dict[str, Any]], source_result: dict[str, Any]) -> str:
    aggregate = next((row for row in rows if row.get("row_kind") == "aggregate"), {})
    return "\n".join(
        [
            "# Results",
            "",
            "This bundle archives the tracked TB2 run and its trial-level evidence.",
            "",
            "## Aggregate",
            "",
            f"- Reward: {aggregate.get('reward', '')}",
            f"- Wall minutes: {aggregate.get('wall_minutes', '')}",
            f"- Completed trials: {aggregate.get('n_completed_trials', '')}",
            f"- Errored trials: {aggregate.get('n_errored_trials', '')}",
            f"- Infra failure kind: {aggregate.get('infra_failure_kind', '')}",
            f"- Exception summary: {aggregate.get('exception_kind', '')}",
            "",
            "## Caveats",
            "",
            "- Token and cost totals are preserved only when present in the source result; otherwise the bundle records an explicit missing-cause field.",
            "- Trial raw artifacts live under `jobs/raw/` and include `trial.log`, `agent/`, and `verifier/` transcripts.",
            "",
            "## Source Result Keys",
            "",
            "```json",
            json.dumps(sorted(source_result.keys()), indent=2),
            "```",
            "",
        ]
    )


def export_tb2_run(source_run_root: Path, archive_root: Path = DEFAULT_ARCHIVE_ROOT, *, bundle_name: str | None = None, force: bool = False) -> Path:
    source_run_root = source_run_root.resolve()
    if not source_run_root.exists():
        raise FileNotFoundError(f"source run root not found: {source_run_root}")
    source_manifest_path = source_run_root / "manifest.json"
    source_status_path = source_run_root / "status.json"
    source_jobs_root = source_run_root / "jobs"
    source_stdout = source_run_root / "stdout.log"
    source_stderr = source_run_root / "stderr.log"
    if not source_manifest_path.exists():
        raise FileNotFoundError(f"missing manifest.json: {source_manifest_path}")
    if not source_jobs_root.exists():
        raise FileNotFoundError(f"missing jobs/ directory: {source_jobs_root}")

    archive_root = archive_root.resolve()
    archive_root.mkdir(parents=True, exist_ok=True)
    target_name = bundle_name or source_run_root.name
    bundle_root = archive_root / target_name
    if bundle_root.exists():
        if not force:
            raise FileExistsError(f"bundle already exists: {bundle_root}")
        shutil.rmtree(bundle_root)

    source_manifest = _json_read(source_manifest_path)
    source_status = _json_read(source_status_path) if source_status_path.exists() else {}
    source_result = {}
    aggregate_source_job_root = next((child for child in sorted(source_jobs_root.iterdir()) if child.is_dir()), None)
    if aggregate_source_job_root is not None:
        aggregate_result_path = aggregate_source_job_root / "result.json"
        if aggregate_result_path.exists():
            source_result = _json_read(aggregate_result_path)

    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "logs").mkdir(parents=True, exist_ok=True)
    (bundle_root / "jobs" / "raw").mkdir(parents=True, exist_ok=True)

    _write_json(bundle_root / "logs" / "source-manifest.json", source_manifest)
    _write_json(bundle_root / "logs" / "source-status.json", source_status)
    if source_stdout.exists():
        _copy_file(source_stdout, bundle_root / "logs" / "source-stdout.log")
    if source_stderr.exists():
        _copy_file(source_stderr, bundle_root / "logs" / "source-stderr.log")

    _copy_tree(source_jobs_root, bundle_root / "jobs" / "raw")

    manifest_payload = {
        "bundle_type": TB2_EXPORT_BUNDLE_TYPE,
        "bundle_root": str(bundle_root),
        "bundle_name": bundle_root.name,
        "created_at": _utc_now(),
        "exporter": Path(__file__).name,
        "source_run_root": str(source_run_root),
        "source_jobs_root": str(source_jobs_root),
        "source_manifest_json": "logs/source-manifest.json",
        "source_status_json": "logs/source-status.json",
        "source_stdout_log": "logs/source-stdout.log",
        "source_stderr_log": "logs/source-stderr.log",
        "source_manifest": source_manifest,
        "source_status": source_status,
    }
    if source_result:
        manifest_payload["source_result"] = source_result
    _write_json(bundle_root / "manifest.json", manifest_payload)

    raw_root = bundle_root / "jobs" / "raw"
    rows: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    trial_rows: list[dict[str, Any]] = []
    for result_path in sorted(raw_root.rglob("result.json")):
        job_dir = result_path.parent
        if aggregate_source_job_root is not None and job_dir == raw_root / aggregate_source_job_root.name:
            continue
        if job_dir == raw_root:
            continue
        if not (job_dir / "trial.log").exists():
            continue
        trial = _trial_row(
            bundle_root=bundle_root,
            source_run_root=source_run_root,
            source_manifest=source_manifest,
            job_dir=job_dir,
            aggregate_raw_root=raw_root,
        )
        rows.append(trial.data)
        trial_rows.append(trial.data)
        index_rows.append({k: trial.data.get(k, "") for k in (
            "job_id",
            "row_kind",
            "condition",
            "task_id",
            "source_run_root",
            "bundle_dir",
            "result_json",
            "job_log",
            "trial_log",
            "metadata_json",
            "verification_log",
            "verification_reward_txt",
            "verification_ctrf_json",
            "agent_dir",
            "verifier_dir",
        )})

    if aggregate_source_job_root is not None and (aggregate_source_job_root / "result.json").exists():
        aggregate = _aggregate_row(
            source_run_root=source_run_root,
            bundle_root=bundle_root,
            raw_root=raw_root,
            source_jobs_root_name=aggregate_source_job_root.name,
            source_manifest=source_manifest,
            source_result=source_result,
            trial_rows=trial_rows,
        )
        rows.append(aggregate.data)
        index_rows.append({k: aggregate.data.get(k, "") for k in (
            "job_id",
            "row_kind",
            "condition",
            "task_id",
            "source_run_root",
            "bundle_dir",
            "result_json",
            "job_log",
            "trial_log",
            "metadata_json",
            "verification_log",
            "verification_reward_txt",
            "verification_ctrf_json",
            "agent_dir",
            "verifier_dir",
        )})

    rows.sort(key=lambda row: (row.get("row_kind") != "aggregate", str(row.get("job_id") or "")))
    index_rows.sort(key=lambda row: (row.get("row_kind") != "aggregate", str(row.get("job_id") or "")))

    summary_fields = [
        "row_kind",
        "job_id",
        "trial_name",
        "condition",
        "task_id",
        "source_run_root",
        "bundle_dir",
        "result_json",
        "job_log",
        "trial_log",
        "metadata_json",
        "verification_log",
        "verification_reward_txt",
        "verification_ctrf_json",
        "agent_dir",
        "verifier_dir",
        "reward",
        "wall_minutes",
        "status",
        "n_total_trials",
        "n_completed_trials",
        "n_running_trials",
        "n_pending_trials",
        "n_errored_trials",
        "exception_kind",
        "exception_count",
        "infra_failure_kind",
        "infra_failure_count",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "cost_usd",
        "input_tokens_missing_cause",
        "cached_input_tokens_missing_cause",
        "output_tokens_missing_cause",
        "cost_usd_missing_cause",
    ]
    index_fields = [
        "job_id",
        "row_kind",
        "condition",
        "task_id",
        "source_run_root",
        "bundle_dir",
        "result_json",
        "job_log",
        "trial_log",
        "metadata_json",
        "verification_log",
        "verification_reward_txt",
        "verification_ctrf_json",
        "agent_dir",
        "verifier_dir",
    ]
    _write_tsv(bundle_root / "summary.tsv", rows, summary_fields)
    _write_tsv(bundle_root / "jobs" / "index.tsv", index_rows, index_fields)
    _write_text(bundle_root / "PLAN.md", _bundle_plan(source_run_root, source_manifest))
    _write_text(bundle_root / "BUILD_INFO.md", _bundle_build_info(source_run_root, source_manifest, source_status))
    _write_text(bundle_root / "RESULTS.md", _bundle_results(rows, source_result))
    _write_text(
        bundle_root / "logs" / "export.log",
        "\n".join(
            [
                f"exported_at={_utc_now()}",
                f"source_run_root={source_run_root}",
                f"bundle_root={bundle_root}",
                f"source_jobs_root={source_jobs_root}",
                f"source_trials={len(rows) - 1 if rows else 0}",
                f"bundle_type={TB2_EXPORT_BUNDLE_TYPE}",
            ]
        )
        + "\n",
    )
    return bundle_root


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_run_roots",
        nargs="+",
        help="Tracked experiments/tb2-* run roots to archive.",
    )
    parser.add_argument(
        "--archive-root",
        default=str(DEFAULT_ARCHIVE_ROOT),
        help="Archive root that will receive the bundle(s).",
    )
    parser.add_argument(
        "--bundle-name",
        help="Optional override bundle name when exporting a single run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing bundle if it already exists.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source_run_roots = [Path(text) for text in args.source_run_roots]
    archive_root = Path(args.archive_root)
    if args.bundle_name and len(source_run_roots) != 1:
        raise SystemExit("--bundle-name can only be used with a single source run root")
    for source_run_root in source_run_roots:
        bundle = export_tb2_run(
            source_run_root,
            archive_root,
            bundle_name=args.bundle_name,
            force=args.force,
        )
        print(bundle)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
