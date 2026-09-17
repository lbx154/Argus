"""Append normalized evidence for a live Argus RL case, without model calls."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROLE_EVENTS = {
    "agent.io.start", "agent.io.complete", "usage.recorded", "life.mission.started",
    "life.mission.completed", "round.review.completed", "round.external_work_review.required",
    "round.external_work_review.completed", "life.plan.generated", "life.planner.waiting",
    "engineer.progress",
}
EVENT_FIELDS = {
    "type", "ts", "call_id", "run_label", "backend", "model", "reasoning_effort",
    "item_id", "attempt", "status", "reason", "review_source", "review_skipped",
    "review_status", "review_key", "work_id", "run_id", "phase", "model_call_skipped",
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
    "cost_usd", "known_cost_usd", "pricing_status", "duration_ms", "started_at", "completed_at",
    "tool_name", "actor", "agent_layer",
}
METRIC_FIELDS = {
    "step", "time", "global_step_before_update", "source", "task_ids", "instance_ids",
    "executable_rewards", "executable_reward_std", "reward_std", "grad_norm", "kl", "loss",
    "effective_loss_tokens", "effective_loss_tokens_total", "usable_samples", "usable_rollout_samples",
    "phase_seconds", "step_time", "rollout_tokens_per_second", "end_to_end_rollout_tokens_per_second",
    "rollout_generation_tokens", "rollout_stop_reasons", "optimizer_steps", "nonzero_test_variance_steps",
    "update_skipped_no_reward_contrast", "optimizer_update_allowed", "has_reward_contrast", "advantages",
    "model_revision", "model_path", "adapter_path", "retained_tokens", "generated_tokens",
    "discarded_tokens", "generation_call_seconds", "generation_seconds", "batch_size",
    "baseline_retained_tokens_per_second", "measured_retained_tokens_per_generation_second",
    "speedup", "full_group_allowed", "generation_and_training_seconds", "adapter_reloaded",
    "service_stopped_before_training", "candidate_count", "elapsed_seconds", "pilot",
    "completions/clipped_ratio", "gpu_memory_by_logical_device", "invalid_reward_count",
    "instance_id", "resolved", "reward", "wall_seconds", "tool_call_count", "length_stop_count",
    "input_tokens", "output_tokens", "total_tokens", "final_response_stop_reason",
    "model_output_tokens", "tool_observation_tokens", "policy_checkpoint", "stop_reason",
    "before", "after", "comparison_settings", "metric_type", "evidence_paths", "passed",
}
SAMPLE_FIELDS = {
    "candidate_index", "instance_id", "stop_reason", "ended_with_eos", "prompt_tokens",
    "completion_tokens", "model_output_tokens", "tool_observation_tokens", "peak_context_tokens",
    "completion_token_limit", "service_max_model_len", "effective_loss_tokens", "loss_mask_retained",
    "executed_tool_call_count", "executed_tool_iterations", "rolled_back_tool_observation_count",
}
METRIC_NAMES = {
    "progress.jsonl", "performance.jsonl", "summary.json", "rollout-boundaries.jsonl",
    "rollout-telemetry.jsonl", "conditional-update.json", "generation-calls.jsonl",
    "rollout-generation-summary.json", "pilot-performance-comparison.json", "stage-timing.json",
    "benchmark-outcome.json",
}


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def jsonl(path):
    try:
        with path.open() as stream:
            for line in stream:
                # A producer may be in the middle of an append.
                if not line.endswith("\n"):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def normalize_metric(row):
    result = {k: v for k, v in row.items() if k in METRIC_FIELDS}
    if isinstance(row.get("samples"), list):
        result["samples"] = [{k: v for k, v in sample.items() if k in SAMPLE_FIELDS}
                             for sample in row["samples"] if isinstance(sample, dict)]
    return result


def collect(root):
    root = Path(root)
    case = read_json(root / "case.json")
    workspace, session = Path(case["workspace"]), Path(case["session_root"])
    start = datetime.fromisoformat(case["started_at"]).timestamp()
    state = read_json(root / "collector-state.json", {})
    seen = set(state.get("seen", []))
    stamps = state.get("file_stamps", {})
    new = []
    now = time.time()

    def append(kind, source, payload, observed_time=None):
        key_text = json.dumps([kind, str(source), payload], sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(key_text.encode()).hexdigest()
        if key in seen:
            return
        seen.add(key)
        timestamp = now if observed_time is None else observed_time
        new.append({"id": key, "observed_at": now, "source_time": timestamp,
                    "period": "benchmark" if timestamp >= start else "historical",
                    "kind": kind, "source": str(source), "data": payload})

    events = []
    for path in sorted(session.glob("events.jsonl*")):
        if path.name.endswith(".lock"):
            continue
        for row in jsonl(path):
            if row.get("type") not in ROLE_EVENTS or row.get("ts", 0) < start:
                continue
            payload = {k: v for k, v in row.items() if k in EVENT_FIELDS}
            if row.get("type") == "engineer.progress":
                if row.get("tool_name") not in {"edit", "write", "bash", "apply_patch"}:
                    continue
                # Keep action metadata and explicit edit paths, never shell text
                # or the full agent conversation.
                payload["target_paths"] = re.findall(r'"(?:path|file_path)"\s*:\s*"([^"\n]+)"', row.get("text", ""))
            append("role_event", session / "events.jsonl", payload, row["ts"])
            events.append(payload)

    jobs = []
    for path in sorted((workspace / ".argus_subagents").glob("*.json")):
        job = read_json(path, {})
        fields = ("task_id", "run_id", "state", "mode", "run_dir", "submitted_at", "started_at",
                  "completed_at", "exit_code", "resource_demand", "resource_grant_id", "elapsed_seconds")
        payload = {k: job.get(k) for k in fields if k in job}
        append("job", path, payload, job.get("completed_at") or now)
        jobs.append(payload)

    runs = workspace / case.get("runs_relative_root", "runs")
    for path in sorted(runs.rglob("*")):
        if not path.is_file() or path.name not in METRIC_NAMES:
            continue
        stat = path.stat()
        stamp = [stat.st_mtime_ns, stat.st_size]
        if stamps.get(str(path)) == stamp:
            continue
        stamps[str(path)] = stamp
        value = None if path.suffix == ".jsonl" else read_json(path, {})
        values = jsonl(path) if path.suffix == ".jsonl" else (value if isinstance(value, list) else [value])
        for index, row in enumerate(values):
            if not isinstance(row, dict):
                continue
            payload = normalize_metric(row)
            if payload:
                payload["source_row_index"] = index
                append("metric", path, payload, row.get("time") or stat.st_mtime)

    # Snapshot only project code and the explicitly named training configurations.
    # auth/model-provider configs, conversations, datasets and weights are excluded.
    paths = [*workspace.glob("src/agentic_grpo/*.py"), *workspace.glob("scripts/*.py"),
             *workspace.glob("scripts/*.sh"), *workspace.glob("configs/grpo*.yaml")]
    paths.extend(workspace / p for p in case.get("decision_paths", ["CHECKPOINT.md"]))
    for skills in case.get("skills_roots", []):
        paths.extend((workspace / skills).rglob("*.md"))
    artifacts = root / "source-snapshots"
    artifacts.mkdir(exist_ok=True)
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        stamp = [stat.st_mtime_ns, stat.st_size]
        if stamps.get(str(path)) == stamp:
            continue
        stamps[str(path)] = stamp
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        target = artifacts / (digest + path.suffix)
        if not target.exists():
            target.write_bytes(content)
        append("source_revision", path, {"sha256": digest, "snapshot": str(target), "bytes": len(content)}, stat.st_mtime)

    with (root / "observations.jsonl").open("a") as stream:
        for entry in new:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    atomic_json(root / "collector-state.json", {"seen": sorted(seen), "file_stamps": stamps, "last_collection": now})

    all_records = list(jsonl(root / "observations.jsonl"))
    current = [r for r in all_records if r["period"] == "benchmark"]
    calls = Counter()
    usage = Counter()
    reviews = 0
    for entry in current:
        if entry["kind"] != "role_event":
            continue
        e = entry["data"]
        if e["type"] == "agent.io.start":
            calls[e.get("run_label", "unknown").split(".")[0].split("-r")[0]] += 1
        if e["type"] == "usage.recorded":
            for k in ("input_tokens", "cached_input_tokens", "output_tokens"):
                usage[k] += e.get(k) or 0
        if e["type"] == "round.external_work_review.completed":
            reviews += 1
    active = [j for j in jobs if j.get("state") in {"running", "starting", "preflight", "waiting_resource"}]
    learned = [r for r in current if r["kind"] == "metric" and Path(r["source"]).name == "performance.jsonl"
               and r["data"].get("executable_reward_std", 0) > 0 and r["data"].get("grad_norm", 0) > 0]
    status = {"updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
              "case_id": case["case_id"], "status": "ongoing", "active_jobs": active,
              "new_reward_contrast_optimizer_records": len(learned), "role_calls": dict(calls),
              "reviewed_external_transitions": reviews, "orchestration_token_usage": dict(usage),
              "interventions": len(list(jsonl(root / "interventions.jsonl"))),
              "learning_effect": "Requires actual matched base–adapter results; optimizer records alone do not establish gain."}
    atomic_json(root / "status.json", status)
    lines = [f"本机 RL benchmark：{case['case_id']}", "", f"更新时间：{status['updated_at']}",
             "状态：持续进行；训练步数本身不能证明能力提升。", "",
             f"起点：{case['started_at']}；沿用已有失败现场，外部修复单独记录。",
             f"本次新增有主奖励差异且非零梯度的优化记录：{len(learned)}。",
             f"已完成的新后台任务/结果 Reviewer 交接：{reviews}。",
             f"编排角色调用：{dict(calls)}。", f"编排 token（缓存输入包含在输入统计中）：{dict(usage)}。", "", "活动作业："]
    lines += [f"- {j['task_id']}：{j['state']}" for j in active] or ["- 暂无；检查角色实现/调度记录。"]
    lines += ["", "记录位置：observations.jsonl、interventions.jsonl、source-snapshots/。",
              "历史结果与起点之后的观测分别标记；GPU 排队不等于训练失败。数值训练参数由 Argus 决定。", ""]
    (root / "STATUS.zh-CN.md").write_text("\n".join(lines))
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    with (args.root / ".collector.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            print(json.dumps(collect(args.root), ensure_ascii=False), flush=True)
            if not args.watch:
                return
            time.sleep(60)


if __name__ == "__main__":
    main()
