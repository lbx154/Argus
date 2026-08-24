from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from foundry.services import (
    ViewerQueue,
    build_evidence_snapshot,
    compile_prompt,
    differentiate_idea,
)
from foundry.workers.viewer_worker import IndependentEvaluatorProcess, process_viewer_request

VENUE = {
    "name": "ICLR", "edition": 2027, "track": "Main",
    "deadline": "2026-09-25 23:59 AoE", "scope": "representation learning",
}
DOMAIN = {
    "name": "AI", "evidence_requirements": ["fixed split", "compute-matched baselines"]
}
IDEA = {
    "title": "Interventional task grammar",
    "problem_gap": "correlations do not establish a causal task representation",
    "mechanism_hypothesis": "selective subspace interventions alter only the target rule",
    "public_data_or_tasks": "public benchmark@frozen-version",
    "kill_criterion": "random subspaces are equally effective",
    "oral_aspiration": True,
    "domain": DOMAIN,
}
RESOURCES = {
    "gpu_count": 4, "gpu_model": "RTX A6000", "gpu_hours": 160,
    "wall_clock_deadline": "2026-09-01T00:00:00Z",
}


def test_portfolio_prompt_contains_scientific_gates() -> None:
    compiled = compile_prompt(IDEA, VENUE, RESOURCES, "portfolio")
    assert "NO_WINNER_YET" in compiled.prompt
    assert "Oral / Best Paper" in compiled.prompt
    assert "INTEGRITY CHECK 1" in compiled.prompt
    assert "INTEGRITY CHECK 2" in compiled.prompt
    assert "INDEPENDENT REVIEW 1" in compiled.prompt
    assert "INDEPENDENT REVIEW 2" in compiled.prompt
    assert "论文名 baseline" in compiled.prompt
    assert "public benchmark@frozen-version" in compiled.prompt
    assert "不得把创建或发布新数据集作为主要贡献" in compiled.prompt
    assert compiled.manifest["oral_is_aspiration_only"] is True
    assert len(compiled.prompt_sha256) == 64


def test_locked_prompt_requires_frozen_contract() -> None:
    with pytest.raises(ValueError, match="locked idea"):
        compile_prompt(IDEA, VENUE, RESOURCES, "locked")
    locked = {
        **IDEA,
        "primary_claim": "selective intervention generalizes",
        "primary_metric": "selectivity gap",
        "minimum_effect": ">= 0.10",
        "data_split": "public split sha256:abc",
        "confirmatory_seeds": [11, 23, 47],
        "strongest_baselines": ["official-baseline@sha"],
    }
    assert "唯一主张" in compile_prompt(locked, VENUE, RESOURCES, "locked").prompt


def test_viewer_refuses_to_invent_score() -> None:
    result = process_viewer_request({"request_id": "r1", "venue": {"name": "ICLR"}})
    assert result["state"] == "awaiting_evaluator"
    assert result["overall"] is None


def test_viewer_calibrates_oral_as_aspiration() -> None:
    result = process_viewer_request({
        "request_id": "r2",
        "venue": {"name": "ICLR"},
        "evidence_refs": ["artifact://claim-matrix", "artifact://review-2"],
        "independent_dimension_scores": {
            "novelty": 9, "significance": 9, "technical_quality": 8,
            "empirical_rigor": 9, "clarity": 8, "reproducibility": 9,
            "venue_fit": 9,
        },
    })
    assert result["state"] == "scored"
    assert result["oral_readiness"] == "aspirational_gate_pass"
    assert "not acceptance probability" in result["calibration"]["scale"]


def test_viewer_queue_is_durable(tmp_path: Path) -> None:
    queue = ViewerQueue(tmp_path)
    receipt = queue.enqueue({"request_id": "review-1", "venue": {"name": "ICLR"}})
    assert receipt["state"] == "queued"
    path, request = queue.next_request() or (None, None)
    assert path is not None and request["protocol_version"] == 1
    output = queue.complete(path, process_viewer_request(request))
    assert json.loads(output.read_text(encoding="utf-8"))["state"] == "awaiting_evaluator"


def test_viewer_queue_atomically_claims_distinct_requests(tmp_path: Path) -> None:
    queue = ViewerQueue(tmp_path)
    queue.enqueue({"request_id": "review-a"})
    queue.enqueue({"request_id": "review-b"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: queue.next_request(), range(2)))

    assert all(item is not None for item in claimed)
    assert {item[1]["request_id"] for item in claimed if item} == {"review-a", "review-b"}
    assert len({item[0] for item in claimed if item}) == 2
    assert not list((tmp_path / "inbox").glob("*.json"))


def test_viewer_queue_recovers_once_then_audits_stale_failure(tmp_path: Path) -> None:
    queue = ViewerQueue(tmp_path, claim_timeout_seconds=0, max_claim_attempts=2)
    queue.enqueue({"request_id": "stale-review"})
    first_path, _ = queue.next_request() or (None, None)
    assert first_path is not None
    first = json.loads(first_path.read_text(encoding="utf-8"))
    first["_viewer_claim"]["hostname"] = "definitely-not-this-host"
    first_path.write_text(json.dumps(first), encoding="utf-8")
    os.utime(first_path, (1, 1))

    second_path, second = queue.next_request() or (None, None)
    assert second_path is not None
    assert second["_viewer_claim"]["attempt"] == 2
    second["_viewer_claim"]["hostname"] = "definitely-not-this-host"
    second_path.write_text(json.dumps(second), encoding="utf-8")
    os.utime(second_path, (1, 1))

    assert queue.next_request() is None
    assert list((tmp_path / "failed").glob("*.json"))
    audit = [json.loads(path.read_text(encoding="utf-8")) for path in (tmp_path / "audit").glob("*.json")]
    assert {row["event"] for row in audit} == {"stale_claim_requeued", "claim_failed"}


class _ArtifactClient:
    def __init__(self, details: dict[str, dict]) -> None:
        self.details = details
        self.requested: list[tuple[str, str]] = []

    def artifact(self, sid: str, path: str) -> dict:
        self.requested.append((sid, path))
        return self.details[path]


def test_evidence_snapshot_is_bounded_content_addressed_and_read_only(tmp_path: Path) -> None:
    client = _ArtifactClient({
        "paper/notes.md": {
            "path": "paper/notes.md", "exists": True, "kind": "markdown",
            "size": 1000, "preview": "正面证据" * 100, "truncated": False,
        },
        "results/table.json": {
            "path": "results/table.json", "exists": True, "kind": "json",
            "size": 12, "preview": "{\"metric\":1}", "truncated": False,
        },
    })
    index = [
        {"path": "paper/notes.md", "exists": True, "kind": "markdown"},
        {"path": "results/table.json", "exists": True, "kind": "json"},
        {"path": "../../secrets.txt", "exists": True, "kind": "text"},
        {"path": "paper/main.pdf", "exists": True, "kind": "pdf"},
    ]
    campaign = {"id": "campaign-1", "argus_project_id": "argus-1"}

    snapshot = build_evidence_snapshot(
        campaign, client, index, output_root=tmp_path / "snapshots",
        max_preview_bytes=24, max_total_preview_bytes=48,
    )
    again = build_evidence_snapshot(
        campaign, client, index, output_root=tmp_path / "snapshots",
        max_preview_bytes=24, max_total_preview_bytes=48,
    )

    assert snapshot.state == "evidence"
    assert snapshot.artifact_count == 2
    assert snapshot.path == again.path
    assert snapshot.path.parent.name == snapshot.sha256
    content = snapshot.path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == snapshot.sha256
    assert not (snapshot.path.stat().st_mode & stat.S_IWUSR)
    document = json.loads(content)
    assert document["score"] is None
    assert document["artifacts"][0]["truncated"] is True
    assert len(document["artifacts"][0]["sha256"]) == 64
    assert client.requested == [
        ("argus-1", "paper/notes.md"), ("argus-1", "results/table.json"),
        ("argus-1", "paper/notes.md"), ("argus-1", "results/table.json"),
    ]


def test_evidence_snapshot_explicitly_records_empty_without_score(tmp_path: Path) -> None:
    client = _ArtifactClient({})
    snapshot = build_evidence_snapshot(
        {"id": "campaign-2", "argus_project_id": "argus-2"},
        client,
        [{"path": "paper/main.pdf", "exists": True, "kind": "pdf"}],
        output_root=tmp_path / "snapshots",
    )

    assert snapshot.state == "empty"
    assert snapshot.artifact_count == 0
    assert snapshot.document["score"] is None
    assert snapshot.document["artifacts"] == []
    assert client.requested == []

    result = process_viewer_request({
        "request_id": "empty-review",
        **snapshot.viewer_request_fields(),
        "evidence_refs": ["artifact://caller-claimed-proof"],
        "independent_dimension_scores": {key: 10 for key in (
            "novelty", "significance", "technical_quality", "empirical_rigor",
            "clarity", "reproducibility", "venue_fit",
        )},
    })
    assert result["state"] == "awaiting_evidence"
    assert result["overall"] is None
    assert result["evidence_refs"] == []


def test_viewer_rejects_tampered_evidence_snapshot(tmp_path: Path) -> None:
    client = _ArtifactClient({
        "notes.md": {
            "path": "notes.md", "exists": True, "kind": "markdown",
            "size": 4, "preview": "proof", "truncated": False,
        }
    })
    snapshot = build_evidence_snapshot(
        {"id": "campaign-3", "argus_project_id": "argus-3"}, client,
        [{"path": "notes.md", "exists": True, "kind": "markdown"}],
        output_root=tmp_path / "snapshots",
    )
    fields = snapshot.viewer_request_fields()
    fields["evidence_snapshot"]["artifacts"][0]["preview"] = "tampered"

    result = process_viewer_request({"request_id": "tampered-review", **fields})

    assert result["state"] == "invalid_input"
    assert result["overall"] is None
    assert "hash mismatch" in result["error"]


def test_viewer_runs_independent_evaluator_process_with_provenance(tmp_path: Path) -> None:
    external_result = {
        "independent_dimension_scores": {
            "novelty": 8, "significance": 8, "technical_quality": 8,
            "empirical_rigor": 8, "clarity": 8, "reproducibility": 8,
            "venue_fit": 8,
        },
        "blockers": [],
        "evidence_refs": ["artifact://frozen-review-packet"],
        "report": "Independent fixture evaluator inspected the frozen packet.",
    }
    code = "import json,sys; json.load(sys.stdin); print(json.dumps(" + repr(external_result) + "))"
    evaluator = IndependentEvaluatorProcess(
        [sys.executable, "-c", code], work_root=tmp_path / "fresh-viewer"
    )
    result = process_viewer_request(
        {
            "request_id": "external-1",
            "campaign_pid": os.getpid(),
            "venue": {"name": "ICLR"},
            "evidence_refs": ["artifact://frozen-review-packet"],
        },
        evaluator,
    )
    assert result["state"] == "scored"
    provenance = result["evaluator_provenance"]
    assert provenance["mode"] == "separate_process"
    assert provenance["pid"] != os.getpid()
    assert provenance["process_is_independent"] is True
    assert Path(provenance["fresh_workdir"]).is_dir()
    assert len(provenance["stdout_sha256"]) == 64


def test_idea_delta_reports_heuristic_collision_and_snapshot_change() -> None:
    idea = {
        "title": "Causal task representation grammar",
        "problem_gap": "task representations are only correlational",
        "mechanism_hypothesis": "selective subspace intervention changes target rules",
    }
    previous = [{"item_id": "old", "title": "Unrelated old work", "url": "u"}]
    current = [{
        "item_id": "new",
        "title": "Causal task representation through selective subspace intervention",
        "url": "https://example.test/new",
        "metadata": {"abstract": "Intervention changes a target task rule."},
    }]
    delta = differentiate_idea(idea, current, previous_items=previous)
    assert delta.novelty_risk == "high_collision_risk"
    assert delta.changed_since_snapshot == {
        "added": ("new",), "removed": ("old",), "changed": (),
    }
    assert delta.nearest_items[0].item_id == "new"
    assert "not a novelty score" in delta.heuristic_notice
    assert "NOVELTY_COLLISION" in delta.suggested_refresh_prompt


def test_idea_delta_uses_observed_adapter_changes_not_current_snapshot_membership() -> None:
    idea = {"title": "Bounded evidence refresh"}
    current = [
        {"item_id": "already-cached", "title": "Existing evidence", "url": "u"},
        {"item_id": "metadata-updated", "title": "Changed evidence", "url": "v"},
    ]

    delta = differentiate_idea(
        idea,
        current,
        observed_changes={
            "added": ("actually-new",),
            "removed": ("no-longer-present",),
            "changed": ("metadata-updated",),
        },
    )

    assert delta.changed_since_snapshot == {
        "added": ("actually-new",),
        "removed": ("no-longer-present",),
        "changed": ("metadata-updated",),
    }
    assert "already-cached" not in delta.changed_since_snapshot["added"]
    assert delta.change_basis == "adapter_source_updates"
    assert "本次新增 1、移除 1、变化 1 条" in delta.suggested_refresh_prompt
