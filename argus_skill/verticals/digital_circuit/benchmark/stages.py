"""Direct, single-stage workflow for fixed-harness RTL benchmarks."""
from __future__ import annotations

from ....skills.stage_checklists import ChecklistItem
from ..stages import role_banner as _digital_circuit_role_banner


STAGE_ORDER = ("execute",)
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "direct"
completion_gate = "none"

_PIPELINE_CHECK = (
    "Pipeline state present",
    "test -f research/PIPELINE_STATE.json",
)

STAGE_CHECKS = {
    "execute": [
        _PIPELINE_CHECK,
        (
            "Benchmark interface manifest ready",
            "test -s design/BENCHMARK_INTERFACE.json "
            "&& grep -q '\"status\"[[:space:]]*:[[:space:]]*\"ready\"' "
            "design/BENCHMARK_INTERFACE.json",
        ),
        (
            "Non-empty generated RTL present",
            "find rtl -maxdepth 1 -type f \\( -name '*.v' -o -name '*.sv' \\) "
            "-size +0c | head -1 | grep -q .",
        ),
        (
            "Pre-score interface/elaboration gate passed",
            "test -s evidence/preflight.json "
            "&& grep -q '\"status\"[[:space:]]*:[[:space:]]*\"pass\"' "
            "evidence/preflight.json",
        ),
        (
            "Benchmark delivery summary present",
            "test -s delivery/BENCHMARK_RESULT.md || test -s DELIVERY.md",
        ),
    ]
}

REVIEWER_CHECKLISTS = {
    "execute": (
        "reviewer/digital-circuit-benchmark-review.md",
        "Review one bounded fixed-harness iteration only. Confirm public-context "
        "closure, exact interface manifest fidelity, non-empty RTL, prompt-derived "
        "local tests, a passing pre-score elaboration report, hidden/golden "
        "non-exposure, and an immutable attempt handoff. Do not create additional "
        "specification, synthesis, or delivery missions; this execute node is the "
        "entire pre-score workflow.",
        [
            "design/BENCHMARK_INTERFACE.json",
            "rtl/",
            "verification/",
            "evidence/preflight.json",
            "delivery/BENCHMARK_RESULT.md",
        ],
    )
}

CHECKLIST_ITEMS = {
    "execute": (
        ChecklistItem(
            id="benchmark.public-contract",
            statement=(
                "All prompt-referenced public inputs are present and the exact output "
                "path, top module, ports, parameters, reset/control semantics, and "
                "latency are frozen before RTL."
            ),
            evidence_hint="design/BENCHMARK_INTERFACE.json plus public-context audit",
        ),
        ChecklistItem(
            id="benchmark.rtl-local-gate",
            statement=(
                "Generated RTL is non-empty and prompt-derived local tests cover the "
                "visible contract without using evaluator-only inputs."
            ),
            evidence_hint="rtl/ plus verification logs",
        ),
        ChecklistItem(
            id="benchmark.pre-score",
            statement=(
                "The exact expected top module passes Icarus elaboration and the "
                "precomputed answer mapping matches the public output schema."
            ),
            evidence_hint="evidence/preflight.json and attempt answer artifact",
        ),
        ChecklistItem(
            id="benchmark.integrity-handoff",
            statement=(
                "The attempt handoff preserves backend/model provenance, hidden/golden "
                "non-exposure, iteration number, and append-only scoring semantics."
            ),
            evidence_hint="delivery/BENCHMARK_RESULT.md",
        ),
    )
}


def role_banner(role: str) -> str:
    return _digital_circuit_role_banner(role) + (
        "\nBENCHMARK SUBVERTICAL: complete the whole pre-score task in ONE bounded "
        "execute mission: public contract closure, RTL, prompt-derived local tests, "
        "pre-score elaboration, and immutable handoff. Do not create or wait for "
        "separate specification, RTL, verification, synthesis, or delivery stages."
    )


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
]
