from __future__ import annotations

from pathlib import Path


def test_small_custom_benchmarks_are_scientific_evidence_with_cost_accounting():
    playbook = (
        Path(__file__).resolve().parents[2]
        / "argus/verticals/research/skills/research-experiment-playbook.md"
    )
    text = " ".join(playbook.read_text(encoding="utf-8").split())
    for required in (
        "Small custom benchmarks are allowed",
        "scientific evidence for a scoped mechanism claim",
        "generation, labeling, evaluation, judging, and planned repetitions",
        "as well as local compute",
        "does not authorize a large API-backed synthetic campaign",
        "project-specific restrictions",
        "label derivation, splits, controls, and scoring",
        "must not masquerade as official benchmark coverage",
    ):
        assert required in text
    assert "Do not invent a benchmark or synthetic replacement tasks" not in text
