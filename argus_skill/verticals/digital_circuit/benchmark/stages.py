"""Direct, single-stage workflow for fixed-harness RTL benchmarks."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ....core.repair_freshness import (
    FreshnessExpectation,
    FreshnessGateResult,
    evaluate_repair_freshness,
    hash_project_files,
    load_freshness_expectation,
    load_repair_freshness_evidence,
    repair_state_lock,
    write_freshness_expectation,
)
from ....skills.stage_machine import ChecklistItem
from ..stages import role_banner as _digital_circuit_role_banner

STAGE_ORDER = ("execute",)
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "direct"
completion_gate = "none"
REQUIRE_INDEPENDENT_REVIEW = True

_PIPELINE_CHECK = (
    "Pipeline state present",
    "test -f .argus/PIPELINE_STATE.json",
)

STAGE_CHECKS = {
    "execute": [
        _PIPELINE_CHECK,
        (
            "Benchmark interface manifest ready",
            "{python} -m argus_skill.verticals.digital_circuit.evidence "
            "benchmark-interface --project-root .",
        ),
        (
            "Non-empty generated candidate present",
            "{python} -m argus_skill.verticals.path_evidence --project-root . "
            "--glob 'rtl/*.v' --glob 'rtl/*.sv' "
            "--glob 'dut.py' "
            "--glob 'reference/*.py' --glob 'reference/*.cc' "
            "--glob 'reference/*.cpp'",
        ),
        (
            "Pre-score interface/elaboration gate passed",
            "{python} -m argus_skill.verticals.digital_circuit.evidence "
            "preflight --project-root .",
        ),
        (
            "Benchmark delivery summary present",
            "test -s delivery/BENCHMARK_RESULT.md || test -s DELIVERY.md",
        ),
        (
            "Repair artifacts are fresh for the current generation",
            "{python} -m argus_skill.verticals.digital_circuit.benchmark.stages "
            "--project-root . --check-repair-freshness",
        ),
    ]
}

REPAIR_FRESHNESS_EVIDENCE = Path("evidence") / "repair_freshness.json"


def _preflight_requires_expectation(root: Path) -> bool:
    path = root / "evidence" / "preflight.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    generation = payload.get("generation")
    iteration = payload.get("iteration")
    return (
        isinstance(generation, int)
        and not isinstance(generation, bool)
        and generation > 1
    ) or (
        isinstance(iteration, int)
        and not isinstance(iteration, bool)
        and iteration > 1
    ) or bool(str(payload.get("repair_mission_id") or "").strip())


def prepare_repair_expectation(
    project_root: Path | str,
    *,
    generation: int,
    iteration: int,
    mission_id: str,
    answer_paths: tuple[str, ...],
) -> Path:
    """Create trusted pre-dispatch state for one bounded repair attempt."""
    root = Path(project_root).resolve()
    expectation = FreshnessExpectation(
        generation=generation,
        iteration=iteration,
        mission_id=mission_id,
        repair=True,
        answer_paths=answer_paths,
        prior_answer_hash=hash_project_files(root, answer_paths),
        created_at=time.time(),
    )
    with repair_state_lock(root):
        return write_freshness_expectation(root, expectation)


def validate_external_scoring_handoff(
    project_root: Path | str,
    *,
    require_expectation: bool = False,
) -> FreshnessGateResult:
    root = Path(project_root)
    try:
        expectation = load_freshness_expectation(root)
    except FileNotFoundError:
        if (
            require_expectation
            or (root / ".argus" / "repair-objective.json").exists()
            or _preflight_requires_expectation(root)
        ):
            return FreshnessGateResult(False, ("missing_freshness_expectation",))
        return FreshnessGateResult(True)
    except (OSError, TypeError, ValueError, KeyError):
        return FreshnessGateResult(False, ("invalid_freshness_expectation",))
    if not expectation.repair:
        return FreshnessGateResult(True)
    try:
        evidence = load_repair_freshness_evidence(root / REPAIR_FRESHNESS_EVIDENCE)
    except FileNotFoundError:
        return FreshnessGateResult(False, ("missing_repair_freshness_evidence",))
    except (OSError, TypeError, ValueError, KeyError):
        return FreshnessGateResult(False, ("invalid_repair_freshness_evidence",))
    return evaluate_repair_freshness(root, expectation, evidence)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--check-repair-freshness", action="store_true")
    operation.add_argument("--prepare-repair-expectation", action="store_true")
    parser.add_argument("--require-expectation", action="store_true")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--iteration", type=int)
    parser.add_argument("--mission-id")
    parser.add_argument("--answer-path", action="append", default=[])
    args = parser.parse_args(argv)
    if args.prepare_repair_expectation:
        if (
            args.generation is None
            or args.iteration is None
            or not str(args.mission_id or "").strip()
            or not args.answer_path
        ):
            parser.error(
                "preparation requires --generation, --iteration, --mission-id, "
                "and at least one --answer-path"
            )
        path = prepare_repair_expectation(
            args.project_root,
            generation=args.generation,
            iteration=args.iteration,
            mission_id=args.mission_id,
            answer_paths=tuple(args.answer_path),
        )
        print(f"wrote trusted repair expectation: {path}")
        return 0
    result = validate_external_scoring_handoff(
        args.project_root,
        require_expectation=args.require_expectation,
    )
    if result.passed:
        return 0
    print("repair freshness gate failed: " + ", ".join(result.issues))
    return 1

REVIEWER_CHECKLISTS = {
    "execute": (
        "reviewer/digital-circuit-benchmark-review.md",
        "Review one bounded fixed-harness iteration only. Confirm public-context "
        "closure, exact interface manifest fidelity, non-empty RTL, prompt-derived "
        "local semantic tests, a passing pre-score elaboration report, hidden/golden "
        "non-exposure, infrastructure-versus-RTL classification, and an immutable "
        "attempt handoff. Do not create additional "
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
                "latency are frozen before RTL. Ambiguities are recorded; the public "
                "interface is never silently corrected without an explicit prompt request."
            ),
            evidence_hint="design/BENCHMARK_INTERFACE.json plus public-context audit",
        ),
        ChecklistItem(
            id="benchmark.rtl-local-gate",
            statement=(
                "Generated RTL or reference-model source is non-empty and prompt-derived "
                "local tests cover the "
                "visible contract without using evaluator-only inputs: exact width/signing, "
                "combinational versus prior-state sequential behavior, reset polarity/"
                "synchronicity, latency, initialization uncertainty, and exhaustive or "
                "metamorphic cases where the public space permits."
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
                "non-exposure, iteration identity, and append-only scoring semantics. "
                "Manager, Planner, Engineer, and independent Reviewer execution is "
                "recorded for the attempt. Evaluator infrastructure/no-execution records "
                "do not consume a model attempt number or enter Pass@k denominators. "
                "A repair additionally proves fresh preflight/regression evidence and "
                "a mechanically verified answer hash for its current generation. "
                "No-execution infrastructure failures imply no RTL verdict; an unchanged "
                "official signature requires a changed public-only hypothesis and test."
            ),
            evidence_hint=(
                "delivery/BENCHMARK_RESULT.md and evidence/repair_freshness.json"
            ),
        ),
    )
}


def role_banner(role: str) -> str:
    return _digital_circuit_role_banner(role) + (
        "\nBENCHMARK SUBVERTICAL: complete the whole pre-score task in ONE bounded "
        "execute mission: public contract closure, RTL, prompt-derived local tests, "
        "pre-score elaboration, and immutable handoff. Do not create or wait for "
        "separate specification, RTL, verification, synthesis, or delivery stages. "
        "If `.argus/repair-objective.json` exists, read its generation, iteration, "
        "answer_paths, prior_answer_hash, and created_at. Write fresh preflight JSON "
        "with matching generation/iteration, then write "
        "`evidence/repair_freshness.json` with artifact_generation, "
        "artifact_iteration, current_answer_hash, preflight_path, "
        "preflight_hash, regression_evidence, failure_classification, and the "
        "categorical official_failure_signature_status (never vectors). Set "
        "failure_classification to exactly the string `answer_change_required` or "
        "`infrastructure_only`; do not use an object. Compute current_answer_hash "
        "with a trusted controller-provided `controller/hash_answer.py` when its "
        "SHA-256 is frozen in controller provenance; otherwise use "
        "`python -c \"from pathlib import Path; from "
        "argus_skill.core.repair_freshness import hash_project_files, "
        "load_freshness_expectation; e=load_freshness_expectation(Path('.')); "
        "print(hash_project_files(Path('.'), e.answer_paths))\"`; do not substitute "
        "a plain per-file SHA-256. Preflight "
        "and each regression evidence file must be structured JSON with status=pass "
        "and matching generation/iteration/repair_mission_id. The gate recomputes "
        "all hashes; declarations alone cannot pass. If the categorical signature is "
        "unchanged, also bind public_hypothesis_path/public_hypothesis_hash to a "
        "public-only changed hypothesis with matching generation/iteration/"
        "repair_mission_id, and mark a changed public-only regression. "
        "A no_execution signature must be infrastructure_only and is not an RTL verdict."
    )


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "REQUIRE_INDEPENDENT_REVIEW",
    "completion_gate",
    "prepare_repair_expectation",
    "role_banner",
    "validate_external_scoring_handoff",
]


if __name__ == "__main__":
    raise SystemExit(_main())
