from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from pathlib import Path

import argus_skill.core.metrics as metrics_module
from argus_skill.core.metrics import (
    http_route_template,
    metrics_snapshot,
    record_metric,
    render_prometheus,
)


def _metric_writer(root: str, worker: int, count: int) -> None:
    for index in range(count):
        record_metric(
            root,
            "provider.call",
            labels={
                "provider": "codex",
                "status": "completed",
                "pricing_status": "priced",
            },
            fields={"worker": worker, "index": index},
        )


def test_metrics_snapshot_aggregates_rates_percentiles_and_slo(tmp_path: Path) -> None:
    for index, status in enumerate(["completed"] * 4 + ["error"]):
        record_metric(
            tmp_path,
            "provider.call",
            labels={"provider": "codex", "status": status},
            fields={"duration_ms": (index + 1) * 100},
            timestamp=100.0 + index,
        )
    for status in ("applied", "applied", "failed"):
        record_metric(
            tmp_path,
            "daemon.command",
            labels={"operation": "start", "status": status},
            timestamp=110.0,
        )
    record_metric(
        tmp_path,
        "web.request",
        labels={"method": "GET", "path": "/api/projects", "status": 500},
        fields={"duration_ms": 250},
        timestamp=120.0,
    )
    record_metric(
        tmp_path,
        "event.validation_failure",
        labels={"type": "agent.io.error"},
        timestamp=121.0,
    )

    snapshot = metrics_snapshot(root=tmp_path, now=200.0)

    assert snapshot["provider"]["completed"] == 4
    assert snapshot["provider"]["errors"] == 1
    assert snapshot["provider"]["success_rate"] == 0.8
    assert snapshot["provider"]["p95_duration_ms"] == 500
    assert snapshot["daemon_commands"]["success_rate"] == 2 / 3
    assert snapshot["web"]["error_rate_5xx"] == 1.0
    assert snapshot["event_validation_failures"] == 1
    assert snapshot["slo"]["status"] == "degraded"
    assert len(snapshot["slo"]["violations"]) == 4

    prometheus = render_prometheus(snapshot)
    assert "argus_slo_healthy 0" in prometheus
    assert 'argus_provider_calls_total{status="completed"} 4' in prometheus
    assert "argus_event_validation_failures_total 1" in prometheus


def test_empty_metrics_are_healthy_and_do_not_invent_failures(tmp_path: Path) -> None:
    snapshot = metrics_snapshot(root=tmp_path)
    assert snapshot["provider"]["success_rate"] == 1.0
    assert snapshot["web"]["error_rate_5xx"] == 0.0
    assert snapshot["event_validation_failures"] == 0
    assert snapshot["slo"] == {"status": "healthy", "violations": []}


def test_metrics_snapshot_never_takes_the_writer_lock(tmp_path: Path, monkeypatch) -> None:
    record_metric(
        tmp_path,
        "web.request",
        labels={"method": "GET", "path": "/api/projects", "status": 200},
    )

    def forbidden_lock(_root):
        raise AssertionError("snapshot reads must not block metric writers")

    monkeypatch.setattr(metrics_module, "_metrics_lock", forbidden_lock)

    assert metrics_snapshot(root=tmp_path)["web"]["requests"] == 1


def test_metric_labels_bucket_unbounded_values(tmp_path: Path) -> None:
    record_metric(
        tmp_path,
        "provider.call",
        labels={
            "provider": "user-supplied-provider-name",
            "status": "surprising",
            "pricing_status": "mystery",
            "call_id": "must-not-be-a-label",
        },
    )
    record_metric(
        tmp_path,
        "event.validation_failure",
        labels={"type": "attacker.generated.unique.event.123"},
    )
    record_metric(
        tmp_path,
        "web.request",
        labels={"method": "TRACE", "path": "raw-secret-id", "status": "broken"},
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "metrics.jsonl").read_text().splitlines()
    ]
    assert rows[0]["labels"] == {
        "provider": "other",
        "status": "error",
        "pricing_status": "unknown",
    }
    assert rows[1]["labels"] == {"type": "unknown"}
    assert rows[2]["labels"] == {
        "method": "OTHER",
        "path": "<unmatched>",
        "status": "unknown",
    }
    assert metrics_snapshot(root=tmp_path)["web"]["errors_5xx"] == 0


def test_http_route_template_never_falls_back_to_raw_identifier() -> None:
    class Route:
        path_format = "/api/projects/{sid}/snapshot"

    assert http_route_template({"route": Route()}, "/api/projects/secret/snapshot") == (
        "/api/projects/{sid}/snapshot"
    )
    assert http_route_template({}, "/api/projects/secret/snapshot") == "<unmatched>"


def test_metrics_rotation_is_read_through_and_prunes_old_archives(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_METRICS_MAX_BYTES", "220")
    monkeypatch.setenv("ARGUS_SKILL_METRICS_RETENTION_DAYS", "1")
    monkeypatch.setenv("ARGUS_SKILL_METRICS_MAX_ARCHIVES", "10")
    now = time.time()
    stale = tmp_path / "metrics.stale.jsonl"
    stale.write_text("{}\n", encoding="utf-8")
    old = now - 2 * 86_400
    os.utime(stale, (old, old))

    for index in range(5):
        record_metric(
            tmp_path,
            "web.request",
            labels={"method": "GET", "path": "/api/projects", "status": 200},
            fields={"duration_ms": index, "padding": "x" * 80},
            timestamp=now + index,
        )

    assert not stale.exists()
    assert list(tmp_path.glob("metrics.*.jsonl"))
    snapshot = metrics_snapshot(root=tmp_path, now=now + 10)
    assert snapshot["web"]["requests"] == 5


def test_multiprocess_metric_writes_remain_complete_json_lines(tmp_path: Path) -> None:
    context = mp.get_context("fork")
    processes = [
        context.Process(target=_metric_writer, args=(str(tmp_path), worker, 25))
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "metrics.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 100
    assert all(row["name"] == "provider.call" for row in rows)


def test_provider_budget_overrun_degrades_slo_and_exports_metrics(tmp_path: Path) -> None:
    record_metric(
        tmp_path,
        "provider.call",
        labels={
            "provider": "codex",
            "status": "completed",
            "pricing_status": "priced",
        },
        fields={"overrun_usd": 0.02},
    )
    snapshot = metrics_snapshot(root=tmp_path)
    assert snapshot["provider"]["overrun_calls"] == 1
    assert snapshot["provider"]["overrun_usd"] == 0.02
    assert snapshot["slo"]["status"] == "degraded"
    prometheus = render_prometheus(snapshot)
    assert "argus_provider_budget_overrun_calls 1" in prometheus
