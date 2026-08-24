from __future__ import annotations

import hashlib
import json
import runpy
import sys
from collections import Counter
from pathlib import Path


def test_exporter_writes_all_290_resource_bound_packets(
    tmp_path: Path, monkeypatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "prompt-catalog"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_prompts.py",
            "--gpu-count",
            "0",
            "--gpu-model",
            "CPU/API-only verification",
            "--gpu-hours",
            "1",
            "--wall-clock-deadline",
            "2026-08-24T23:59:00+08:00",
            "--max-parallel-jobs",
            "1",
            "--api-budget",
            "verification-only; zero external calls",
            "--output",
            str(output),
        ],
    )

    runpy.run_path(str(root / "scripts" / "export_prompts.py"), run_name="__main__")

    catalog = json.loads((output / "CATALOG.json").read_text(encoding="utf-8"))
    markdown_catalog = (output / "CATALOG.md").read_text(encoding="utf-8")
    objectives = list(output.glob("*/idea-*/OBJECTIVE.md"))
    manifests = list(output.glob("*/idea-*/MANIFEST.json"))
    rough_ideas = list(output.glob("*/idea-*/ROUGH_IDEA.md"))
    assert catalog["count"] == 290
    assert "58 个 venue × 5 个可重放" in markdown_catalog
    assert markdown_catalog.count("/OBJECTIVE.md)") == 290
    assert "seed coverage baseline" in markdown_catalog
    assert "不是 launch-ready Prompt" in markdown_catalog
    assert len(objectives) == len(manifests) == len(rough_ideas) == 290
    assert all(packet["prompt_sha256"] for packet in catalog["packets"])
    for packet in catalog["packets"]:
        packet_root = output / packet["packet_path"]
        objective_bytes = (packet_root / "OBJECTIVE.md").read_bytes()
        disk_sha256 = hashlib.sha256(objective_bytes).hexdigest()
        packet_manifest = json.loads(
            (packet_root / "MANIFEST.json").read_text(encoding="utf-8")
        )
        assert disk_sha256 == packet["prompt_sha256"]
        assert disk_sha256 == packet_manifest["prompt_sha256"]
        assert packet["personalization_state"] == "seed_coverage_baseline"
        assert packet["launch_ready"] is False
        assert packet["requires_team_condition_snapshot"] is True
        assert objective_bytes.startswith(b"# SEED COVERAGE BASELINE")
    assert all(packet["effective_planning_deadline"] for packet in catalog["packets"])
    assert all(packet["packet_path"] for packet in catalog["packets"])
    example_rough_idea = rough_ideas[0].read_text(encoding="utf-8")
    assert "## 初始方法" in example_rough_idea
    assert "## 最强基线候选（待核验）" in example_rough_idea
    assert "## 决定性实验" in example_rough_idea
    assert "## 历史选题算力假设（非当前库存）" in example_rough_idea
    venue_counts = Counter(packet["venue_key"] for packet in catalog["packets"])
    assert len(venue_counts) == 58
    assert set(venue_counts.values()) == {5}
    rolling = [
        packet for packet in catalog["packets"]
        if packet["has_fixed_submission_deadline"] is False
    ]
    assert len(rolling) == 5
    assert {packet["venue_key"] for packet in rolling} == {"CSCW"}
    assert {packet["deadline_evidence_status"] for packet in rolling} == {"rolling"}
    assert all(packet["deadline_date"] is None for packet in rolling)
    assert all(packet["effective_planning_deadline"] == "2026-08-24" for packet in rolling)
    fixed = [
        packet for packet in catalog["packets"]
        if packet["has_fixed_submission_deadline"] is True
    ]
    assert len(fixed) == 285
    assert {packet["deadline_evidence_status"] for packet in fixed} <= {
        "official_confirmed", "forecast"
    }
    assert all(packet["deadline_date"] for packet in fixed)
    assert all(packet["deadline_source_url"] for packet in catalog["packets"])
