from __future__ import annotations

import csv
import json
from pathlib import Path

from benchmarks.archive_tb2_run import export_tb2_run
from benchmarks.validate_results import validate_bundle_dir, validate_results_root


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n",
        encoding="utf-8",
    )


def _make_source_run_root(tmp_path: Path) -> Path:
    source = tmp_path / "experiments" / "tb2-argus-v12-redux-20260515T000000Z"
    _write_json(
        source / "manifest.json",
        {
            "bundle_root": str(source),
            "command": ["sg", "docker", "-c", "harbor run ..."],
            "command_text": "sg docker -c harbor run ...",
            "created_at": "2026-05-15T00:00:00+00:00",
            "cwd": "/home/argustest/argus-skill",
            "env": {"OPENAI_BASE_URL": "https://example.invalid/openai/v1/"},
            "env_config_hash": "deadbeef",
            "manifest_version": 1,
            "metadata": {
                "condition": "argus-v12-redux",
                "dataset_commit": "69671fbaac6d67a7ef0dfec016cc38a64ef7a77c",
                "dataset_id": "terminal-bench@2.0",
                "model_ids": {
                    "engineer": "openai/gpt-5.4-mini",
                    "reviewer": "gpt-5.4",
                    "scientist": "gpt-5.4",
                },
                "pricing_source": "argus_skill.core.pricing.usd_for_tokens",
                "reasoning_effort": "high",
                "verifier_reward_source": "terminal-bench official verifier /tests/test.sh",
            },
            "pid_path": "pid",
            "run_id": "tb2-argus-v12-redux-20260515T000000Z",
            "status_path": "status.json",
            "stderr_log": "stderr.log",
            "stdout_log": "stdout.log",
        },
    )
    _write_json(source / "status.json", {"run_id": "tb2-argus-v12-redux-20260515T000000Z", "state": "completed"})
    _write_text(source / "stdout.log", "root stdout\n")
    _write_text(source / "stderr.log", "root stderr\n")

    aggregate = source / "jobs" / "2026-05-15__00-00-00"
    _write_text(
        aggregate / "job.log",
        "Network tb2-aggregate_default Error\nfailed to create network ... all predefined address pools have been fully subnetted\n",
    )
    _write_json(
        aggregate / "result.json",
        {
            "finished_at": "2026-05-15T00:30:00Z",
            "id": "aggregate-id",
            "n_total_trials": 2,
            "started_at": "2026-05-15T00:00:00Z",
            "stats": {
                "cost_usd": None,
                "evals": {
                    "argus-skill-codex__gpt-5.4-mini__terminal-bench": {
                        "metrics": [{"mean": 0.5}],
                    }
                },
                "n_cache_tokens": 0,
                "n_completed_trials": 2,
                "n_errored_trials": 1,
                "n_input_tokens": 0,
                "n_output_tokens": 0,
                "n_pending_trials": 0,
                "n_running_trials": 0,
                "n_retries": 0,
                "exception_stats": {"RuntimeError": ["trial-b"]},
            },
        },
    )

    trial_a = aggregate / "trial-a__AAAA"
    _write_json(
        trial_a / "result.json",
        {
            "agent_result": {
                "cost_usd": None,
                "n_cache_tokens": 0,
                "n_input_tokens": 0,
                "n_output_tokens": 0,
            },
            "exception_info": None,
            "finished_at": "2026-05-15T00:10:00Z",
            "started_at": "2026-05-15T00:01:00Z",
            "task_id": {"path": "task-a"},
            "task_name": "task-a",
            "trial_name": "trial-a__AAAA",
            "verifier_result": {"rewards": {"reward": 1.0}},
        },
    )
    _write_text(trial_a / "trial.log", "trial a log\n")
    _write_json(trial_a / "config.json", {"trial": "a"})
    _write_text(trial_a / "agent" / "argus-skill-round-1.txt", "round 1\n")
    _write_jsonl(
        trial_a / "agent" / "sessions" / "2026" / "05" / "15" / "rollout-a.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "session-a"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 11,
                            "cached_input_tokens": 3,
                            "output_tokens": 7,
                        }
                    },
                },
            },
        ],
    )
    _write_jsonl(
        trial_a / "agent" / "sessions" / "2026" / "05" / "15" / "rollout-b.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "session-b"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 13,
                            "cached_input_tokens": 5,
                            "output_tokens": 9,
                        }
                    },
                },
            },
        ],
    )
    _write_text(trial_a / "verifier" / "test-stdout.txt", "verifier stdout a\n")
    _write_text(trial_a / "verifier" / "reward.txt", "1\n")
    _write_json(trial_a / "verifier" / "ctrf.json", {"reward": 1})

    trial_b = aggregate / "trial-b__BBBB"
    _write_json(
        trial_b / "result.json",
        {
            "agent_result": None,
            "exception_info": {
                "exception_message": "Docker compose command failed ... all predefined address pools have been fully subnetted",
                "exception_type": "RuntimeError",
            },
            "finished_at": "2026-05-15T00:12:00Z",
            "started_at": "2026-05-15T00:02:00Z",
            "task_id": {"path": "task-b"},
            "task_name": "task-b",
            "trial_name": "trial-b__BBBB",
            "verifier_result": None,
        },
    )
    _write_text(trial_b / "trial.log", "trial b log\n")
    _write_json(trial_b / "config.json", {"trial": "b"})
    _write_text(trial_b / "agent" / "argus-skill-round-1.txt", "round 1\n")
    _write_text(trial_b / "verifier" / "test-stdout.txt", "verifier stdout b\n")
    _write_text(trial_b / "verifier" / "reward.txt", "0\n")
    _write_json(trial_b / "verifier" / "ctrf.json", {"reward": 0})

    return source


def test_export_tb2_run_creates_valid_bundle(tmp_path: Path) -> None:
    source = _make_source_run_root(tmp_path)
    archive_root = tmp_path / "benchmarks" / "evidence"

    import benchmarks.archive_tb2_run as archive_module

    original_cost = archive_module._compute_model_cost_usd
    archive_module._compute_model_cost_usd = lambda *_args, **_kwargs: 12.34
    try:
        bundle = export_tb2_run(source, archive_root)
    finally:
        archive_module._compute_model_cost_usd = original_cost

    assert bundle == archive_root / "tb2-argus-v12-redux-20260515T000000Z"
    assert (bundle / "PLAN.md").exists()
    assert (bundle / "BUILD_INFO.md").exists()
    assert (bundle / "RESULTS.md").exists()
    assert (bundle / "manifest.json").exists()
    assert (bundle / "summary.tsv").exists()
    assert (bundle / "jobs" / "index.tsv").exists()
    assert (bundle / "jobs" / "raw" / "2026-05-15__00-00-00" / "trial-a__AAAA" / "agent" / "argus-skill-round-1.txt").exists()
    assert (bundle / "jobs" / "raw" / "2026-05-15__00-00-00" / "trial-b__BBBB" / "verifier" / "reward.txt").exists()

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundle_type"] == "tb2_fullbench_export"
    assert manifest["source_manifest"]["metadata"]["condition"] == "argus-v12-redux"

    with (bundle / "summary.tsv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    aggregate = next(row for row in rows if row["row_kind"] == "aggregate")
    assert aggregate["reward"] == "0.5"
    assert aggregate["input_tokens"] == "24"
    assert aggregate["cached_input_tokens"] == "8"
    assert aggregate["output_tokens"] == "16"
    assert aggregate["cost_usd"] == "12.34"
    assert aggregate["input_tokens_missing_cause"] == ""
    assert aggregate["cached_input_tokens_missing_cause"] == ""
    assert aggregate["output_tokens_missing_cause"] == ""
    assert aggregate["cost_usd_missing_cause"] == ""

    trial = next(row for row in rows if row["row_kind"] == "trial" and row["task_id"] == "task-a")
    assert trial["reward"] == "1"
    assert trial["input_tokens"] == "24"
    assert trial["cached_input_tokens"] == "8"
    assert trial["output_tokens"] == "16"
    assert trial["cost_usd"] == "12.34"
    assert trial["input_tokens_missing_cause"] == ""
    assert trial["cached_input_tokens_missing_cause"] == ""
    assert trial["output_tokens_missing_cause"] == ""
    assert trial["cost_usd_missing_cause"] == ""

    missing_trial = next(row for row in rows if row["row_kind"] == "trial" and row["task_id"] == "task-b")
    assert missing_trial["reward"] == "0"
    assert missing_trial["exception_kind"] == "RuntimeError"
    assert missing_trial["infra_failure_kind"] == "docker_address_pool_exhaustion"
    assert missing_trial["input_tokens_missing_cause"] == "trial_result_missing_token_total"
    assert missing_trial["cost_usd_missing_cause"] == "trial_result_missing_cost_total"

    trial_result = json.loads(
        (bundle / "jobs" / "raw" / "2026-05-15__00-00-00" / "trial-a__AAAA" / "result.json").read_text(encoding="utf-8")
    )
    assert trial_result["agent_result"]["n_input_tokens"] == 24
    assert trial_result["agent_result"]["n_cache_tokens"] == 8
    assert trial_result["agent_result"]["n_output_tokens"] == 16
    assert trial_result["agent_result"]["cost_usd"] == 12.34

    assert validate_bundle_dir(bundle) == []
    assert validate_results_root(archive_root) == []


def test_validate_tb2_export_requires_explicit_missing_cause(tmp_path: Path) -> None:
    source = _make_source_run_root(tmp_path)
    archive_root = tmp_path / "benchmarks" / "evidence"
    bundle = export_tb2_run(source, archive_root)

    summary = bundle / "summary.tsv"
    rows = list(csv.DictReader(summary.open(newline="", encoding="utf-8"), delimiter="\t"))
    rows[0]["input_tokens"] = ""
    rows[0]["input_tokens_missing_cause"] = ""
    with summary.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    issues = validate_bundle_dir(bundle)
    assert any("missing value and missing-cause for input_tokens" in issue.message for issue in issues)


def test_export_tb2_run_preserves_zero_cached_token_telemetry(tmp_path: Path) -> None:
    source = _make_source_run_root(tmp_path)
    trial_sessions = (
        source
        / "jobs"
        / "2026-05-15__00-00-00"
        / "trial-a__AAAA"
        / "agent"
        / "sessions"
    )
    for path in sorted(trial_sessions.rglob("*.jsonl")):
        path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "session_meta", "payload": {"id": path.stem}}),
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {
                                        "input_tokens": 11,
                                        "cached_input_tokens": 0,
                                        "output_tokens": 7,
                                    }
                                },
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    archive_root = tmp_path / "benchmarks" / "evidence"
    import benchmarks.archive_tb2_run as archive_module

    original_cost = archive_module._compute_model_cost_usd
    archive_module._compute_model_cost_usd = lambda *_args, **_kwargs: 1.23
    try:
        bundle = export_tb2_run(source, archive_root, bundle_name="tb2-zero-cache", force=True)
    finally:
        archive_module._compute_model_cost_usd = original_cost

    rows = list(csv.DictReader((bundle / "summary.tsv").open(newline="", encoding="utf-8"), delimiter="\t"))
    aggregate = next(row for row in rows if row["row_kind"] == "aggregate")
    trial = next(row for row in rows if row["row_kind"] == "trial" and row["task_id"] == "task-a")

    assert aggregate["cached_input_tokens"] == "0"
    assert aggregate["cached_input_tokens_missing_cause"] == ""
    assert trial["cached_input_tokens"] == "0"
    assert trial["cached_input_tokens_missing_cause"] == ""
    assert (bundle / "jobs" / "raw" / "2026-05-15__00-00-00" / "trial-a__AAAA" / "result.json").exists()
    trial_result = json.loads(
        (bundle / "jobs" / "raw" / "2026-05-15__00-00-00" / "trial-a__AAAA" / "result.json").read_text(encoding="utf-8")
    )
    assert trial_result["agent_result"]["n_cache_tokens"] == 0
