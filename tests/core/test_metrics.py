from __future__ import annotations

from pathlib import Path

from argus_skill.core.metrics import metrics_snapshot, record_metric, render_prometheus


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
