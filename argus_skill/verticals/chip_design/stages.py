"""Evidence-gated chip and accelerator design vertical.

The vertical spans product/workload definition through RTL, verification,
PPA, prototyping, benchmark comparison, and final sign-off. It supports
synthesizable IP, FPGA prototypes, open-PDK GDS, and tapeout-readiness
missions without pretending those delivery levels are interchangeable.
"""

from __future__ import annotations

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = (
    "definition",
    "architecture",
    "environment",
    "rtl",
    "verification",
    "ppa",
    "prototype",
    "benchmark",
    "signoff",
)
CHECKLIST_STAGE_ORDER = STAGE_ORDER
# Stage order remains strict, but accepted upstream evidence is reusable. An
# operator-boundary uplift normally reopens RTL, not the unchanged product,
# architecture, and toolchain contracts.
WORKFLOW_MODE = "proportional"
completion_gate = "metric"
REQUIRE_INDEPENDENT_REVIEW = True

_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")
_EVIDENCE = "${ARGUS_SKILL_PYTHON:-python} -m argus_skill.verticals.chip_design.evidence"
_AUDIT = "${ARGUS_SKILL_PYTHON:-python} -m argus_skill.verticals.chip_design.environment_audit"

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "definition": [
        _PIPELINE_CHECK,
        ("Chip scope validates", f"{_EVIDENCE} scope --project-root ."),
        ("Workload contract present", "test -s design/WORKLOAD.md"),
        ("Functional specification present", "test -s design/SPEC.md"),
    ],
    "architecture": [
        _PIPELINE_CHECK,
        ("Architecture document present", "test -s design/ARCHITECTURE.md"),
        ("Memory and bandwidth model validates", f"{_EVIDENCE} architecture --project-root ."),
        ("Baseline comparison plan present", "test -s design/BASELINE_PLAN.md"),
    ],
    "environment": [
        _PIPELINE_CHECK,
        (
            "Environment audit validates",
            f"{_AUDIT} check --project-root . "
            "--target-python ${{ARGUS_SKILL_PROJECT_PYTHON:-.venv/bin/python}}",
        ),
        ("Specialized tool shortlist present", "test -s research/TOOLCHAIN_CANDIDATES.md"),
        ("IP and infrastructure reuse plan present", "test -s research/IP_REUSE_PLAN.md"),
        ("Target technology or FPGA manifest present", "test -s design/TARGET.json"),
    ],
    "rtl": [
        _PIPELINE_CHECK,
        (
            "Synthesizable RTL present",
            "{python} -m argus_skill.verticals.path_evidence --project-root . "
            "--glob 'rtl/**/*.v' --glob 'rtl/**/*.sv' --glob 'src/**/*.v' --glob 'src/**/*.sv'",
        ),
        ("RTL manifest validates", f"{_EVIDENCE} rtl --project-root ."),
    ],
    "verification": [
        _PIPELINE_CHECK,
        ("Verification plan present", "test -s verification/PLAN.md"),
        ("Verification evidence validates", f"{_EVIDENCE} verification --project-root ."),
    ],
    "ppa": [
        _PIPELINE_CHECK,
        ("PPA protocol present", "test -s ppa/PROTOCOL.md"),
        ("Synthesis or physical-design evidence validates", f"{_EVIDENCE} ppa --project-root ."),
    ],
    "prototype": [
        _PIPELINE_CHECK,
        ("Prototype evidence or scoped non-applicability validates", f"{_EVIDENCE} prototype --project-root ."),
    ],
    "benchmark": [
        _PIPELINE_CHECK,
        ("Benchmark protocol present", "test -s benchmark/PROTOCOL.md"),
        ("Benchmark comparison validates", f"{_EVIDENCE} benchmark --project-root ."),
    ],
    "signoff": [
        _PIPELINE_CHECK,
        ("Sign-off evidence validates", f"{_EVIDENCE} signoff --project-root ."),
        ("Final results present", "test -s RESULTS.md"),
        (
            "Reproduction entry point present",
            "test -f Makefile || test -f justfile || test -f scripts/reproduce.sh "
            "|| test -f scripts/verify.sh",
        ),
    ],
}

_ENGINEER_SKILL = "engineer/chip-design-environment-first.md"
_REVIEWER_SKILL = "reviewer/chip-design-signoff-review.md"

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "definition": (
        _ENGINEER_SKILL,
        "Freeze the product and workload contract before architecture work. Require a delivery level "
        "(RTL IP, FPGA, GDS, or tapeout readiness), supported models/operators, numerical formats, "
        "host/memory interfaces, power/performance/area targets, correctness oracle, commercial and "
        "open baselines, non-goals, and a scenario-level acceptance matrix. Reject marketing TOPS as "
        "the sole metric or an open-PDK proof presented as a production-node competitor.",
        ["design/CHIP_SCOPE.json", "design/WORKLOAD.md", "design/SPEC.md"],
    ),
    "architecture": (
        _ENGINEER_SKILL,
        "Review the microarchitecture against workload arithmetic intensity and memory traffic. Require "
        "roofline/Amdahl reasoning, compute/dataflow choice, SRAM banking and capacity, DMA/NoC behavior, "
        "host and external-memory assumptions, numerical accumulation, scheduling, clock/reset domains, "
        "backpressure, error handling, and a fair baseline plan. Reject architecture diagrams without "
        "cycle/data-movement consequences.",
        ["design/ARCHITECTURE.md", "design/MEMORY_MODEL.json", "design/BASELINE_PLAN.md"],
    ),
    "environment": (
        _ENGINEER_SKILL,
        "Treat the EDA environment as a hard gate. Independently inspect simulator, formal, synthesis, "
        "FPGA, physical-design, PDK, sign-off, compiler/runtime, license, container, and version evidence "
        "required by the declared delivery level. Prefer project-native and maintained open/vendor tools. "
        "A missing tool or PDK is an environment blocker, not proof that the architecture failed.",
        [
            "research/ENVIRONMENT_AUDIT.json",
            "research/ENVIRONMENT_AUDIT.md",
            "research/TOOLCHAIN_CANDIDATES.md",
            "research/IP_REUSE_PLAN.md",
            "design/TARGET.json",
        ],
    ),
    "rtl": (
        _ENGINEER_SKILL,
        "Audit synthesizable RTL against the architecture and frozen interfaces. Require reset/CDC/clock "
        "discipline, widths and signedness, parameter bounds, memory inference/macros, protocol timing, "
        "pipeline latency, deterministic arbitration, and explicit generated-source provenance. Reject "
        "simulation-only constructs, silent black boxes, copied IP without license/provenance, or RTL "
        "whose interface differs from the manifest.",
        ["design/RTL_MANIFEST.json", "rtl/", "src/", "design/ARCHITECTURE.md"],
    ),
    "verification": (
        _REVIEWER_SKILL,
        "Independently rerun verification. Require a separate executable reference/oracle, unit and "
        "integration tests, reset/boundary/backpressure/error cases, randomized seeds, assertions, X/Z "
        "checks, formal properties where tractable, coverage goals, numerical error analysis, and retained "
        "logs/waves for failures. Compile success or testing the RTL against a duplicated algorithm is not "
        "verification.",
        ["verification/PLAN.md", "verification/RESULTS.json", "verification/raw/", "formal/", "tb/"],
    ),
    "ppa": (
        _REVIEWER_SKILL,
        "Review fresh synthesis and implementation evidence under declared constraints. Require tool and "
        "library/PDK versions, clocks and I/O, memory macro accounting, area/resources, slack/Fmax, power "
        "method and activity assumptions, warnings/black boxes, congestion, and—when GDS is claimed—DRC, "
        "LVS, antenna and STA status. Compare baselines only in the same FPGA or technology flow.",
        ["ppa/PROTOCOL.md", "ppa/RESULTS.json", "ppa/raw/", "constraints/", "physical/"],
    ),
    "prototype": (
        _REVIEWER_SKILL,
        "Review the declared prototype level. FPGA claims need bitstream/tool/board identity, resource and "
        "clock reports, host/runtime integration, on-board correctness, power method, and reproducible "
        "logs. GDS/tapeout missions need layout/sign-off artifacts and explicit hard-IP/package/IO limits. "
        "Structured N/A is acceptable only when the frozen delivery level does not require a prototype.",
        ["prototype/RESULTS.json", "prototype/raw/", "fpga/", "physical/"],
    ),
    "benchmark": (
        _REVIEWER_SKILL,
        "Audit benchmark fairness and end-to-end relevance. Require identical workloads, quantization and "
        "quality floor, memory bandwidth, host responsibilities, warmup, repetitions, power measurement, "
        "and resource/area budgets. Report kernel and full-system metrics separately, including latency, "
        "throughput, energy, utilization, TTFT/TPOT/tokens-per-second where applicable, uncertainty, and "
        "all regressions. Commercial products may be market context, not same-node PPA baselines.",
        ["benchmark/PROTOCOL.md", "benchmark/RESULTS.json", "benchmark/raw/", "design/BASELINE_PLAN.md"],
    ),
    "signoff": (
        _REVIEWER_SKILL,
        "Perform final clean-room sign-off. Verify source and generated-artifact content, license/IP "
        "provenance, verification/PPA/prototype/benchmark bindings, reproducible commands, known "
        "limitations, unsupported levels, security/safety findings, intervention history, and claim scope. "
        "For tapeout readiness, require independent DRC/LVS/STA closure and foundry/package checklist "
        "evidence; for lower delivery levels, prohibit wording that implies fabricated silicon.",
        ["signoff/SIGNOFF.json", "signoff/ARTIFACT_MANIFEST.json", "RESULTS.md", "scripts/"],
    ),
}

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "definition": (
        ChecklistItem(
            id="definition.delivery-scope",
            statement=(
                "The delivery level, target workload, supported/non-supported operations, numerical "
                "formats, interfaces, target platform/technology, and non-goals are frozen."
            ),
            evidence_hint="design/CHIP_SCOPE.json and design/WORKLOAD.md",
        ),
        ChecklistItem(
            id="definition.acceptance-metrics",
            statement=(
                "Correctness, performance, power, area/resource, quality, and provenance acceptance "
                "criteria are measurable and distinguish IP, FPGA, GDS, tapeout, and market claims."
            ),
            evidence_hint="design/CHIP_SCOPE.json acceptance_metrics and design/SPEC.md",
        ),
        ChecklistItem(
            id="definition.baseline-fairness",
            statement=(
                "Open-hardware, software/system, and market-reference baselines are named with an "
                "explicit apples-to-apples comparison policy."
            ),
            evidence_hint="design/BASELINE_PLAN.md or a definition-stage baseline section",
        ),
    ),
    "architecture": (
        ChecklistItem(
            id="architecture.compute-memory-model",
            statement=(
                "Compute, dataflow, memory hierarchy, DMA/NoC, external bandwidth, arithmetic intensity, "
                "and expected utilization are quantified for representative workload shapes."
            ),
            evidence_hint="design/ARCHITECTURE.md and design/MEMORY_MODEL.json",
        ),
        ChecklistItem(
            id="architecture.interface-control",
            statement=(
                "Host commands, register map, address/stride rules, interrupts, errors, reset/clock/CDC, "
                "backpressure, latency, and completion semantics are explicit."
            ),
            evidence_hint="design/SPEC.md interface and cycle tables",
        ),
        ChecklistItem(
            id="architecture.leverage-risk",
            statement=(
                "Amdahl/roofline leverage, implementation risks, IP reuse, verification strategy, and "
                "fallback or de-scoping decisions are recorded before RTL."
            ),
            evidence_hint="design/ARCHITECTURE.md design-tradeoff table",
        ),
        ChecklistItem(
            id="architecture.area-reuse-plan",
            statement=(
                "Area reuse is planned explicitly: mutually exclusive operators share or fold "
                "MACs, accumulators, requantization, vector/SFU, divide/round/saturate, DMA, "
                "buffers, and control where measured mux/control overhead preserves the PPA goal."
            ),
            evidence_hint=(
                "design/ARCHITECTURE.md lifetime/resource-sharing table with dedicated-versus-"
                "shared alternatives and expected cycle/area costs"
            ),
        ),
    ),
    "environment": (
        ChecklistItem(
            id="environment.eda-capabilities",
            statement=(
                "A machine-readable audit proves every simulator, formal, synthesis, FPGA, physical-design, "
                "PDK, sign-off, and compiler/runtime capability required by the delivery level is ready."
            ),
            evidence_hint="research/ENVIRONMENT_AUDIT.json",
        ),
        ChecklistItem(
            id="environment.tool-ip-selection",
            statement=(
                "Maintained tools, reusable IP, licenses, PDKs, board support, and canonical flows were "
                "queried and selected before custom infrastructure was authored."
            ),
            evidence_hint="research/TOOLCHAIN_CANDIDATES.md and research/IP_REUSE_PLAN.md",
        ),
    ),
    "rtl": (
        ChecklistItem(
            id="rtl.contract-traceability",
            statement=(
                "Every synthesizable module and generated source traces to architecture/spec requirements "
                "and exactly matches the RTL manifest interfaces and parameters."
            ),
            evidence_hint="design/RTL_MANIFEST.json and RTL traceability notes",
        ),
        ChecklistItem(
            id="rtl.hardware-discipline",
            statement=(
                "RTL has intentional widths/signedness, complete combinational assignments, disciplined "
                "sequential logic, safe clock/reset/CDC behavior, bounded arrays/counters, and no "
                "unexplained latches, multiple drivers, or simulation-only design constructs."
            ),
            evidence_hint="lint/elaboration output and reviewer inspection",
        ),
        ChecklistItem(
            id="rtl.ip-provenance",
            statement=(
                "Third-party and generated IP have pinned source revisions, compatible licenses, wrappers, "
                "configuration, and regeneration commands."
            ),
            evidence_hint="design/RTL_MANIFEST.json provenance entries",
        ),
    ),
    "verification": (
        ChecklistItem(
            id="verification.independent-oracle",
            statement=(
                "RTL outputs and state transitions are checked against an independent executable reference "
                "or formally specified properties, including numerical tolerances and quality constraints."
            ),
            evidence_hint="verification/PLAN.md, reference/, formal/, and verification/RESULTS.json",
        ),
        ChecklistItem(
            id="verification.coverage-stress",
            statement=(
                "Unit/integration, reset, boundary, stalls/backpressure, illegal/error, randomized, CDC, "
                "X/Z, overflow, and representative workload cases meet declared coverage goals."
            ),
            evidence_hint="verification/RESULTS.json coverage and scenario summaries",
        ),
        ChecklistItem(
            id="verification.reproducible-green",
            statement=(
                "Fresh simulator/formal commands exit successfully, referenced raw artifacts exist, and "
                "passing summaries contain no contradictory failures."
            ),
            evidence_hint="verification/RESULTS.json and verification/raw/",
        ),
    ),
    "ppa": (
        ChecklistItem(
            id="ppa.constraints-and-provenance",
            statement=(
                "Target technology/device, tools, libraries/PDK, clocks, I/O constraints, memory treatment, "
                "parameters, corners, activity, and raw report paths are pinned."
            ),
            evidence_hint="ppa/PROTOCOL.md and ppa/RESULTS.json",
        ),
        ChecklistItem(
            id="ppa.timing-area-power",
            statement=(
                "Fresh timing/Fmax, area/resources including memory, power/energy methodology, utilization, "
                "warnings, and uncertainty are reported for the candidate and fair baselines."
            ),
            evidence_hint="ppa/RESULTS.json and ppa/raw/",
        ),
        ChecklistItem(
            id="ppa.physical-closure",
            statement=(
                "When physical design or GDS is claimed, placement/routing, congestion, STA, DRC, LVS, "
                "antenna, density, and generated-layout provenance satisfy the declared closure level."
            ),
            evidence_hint="ppa/RESULTS.json physical_closure and physical/raw/",
        ),
        ChecklistItem(
            id="ppa.incremental-area-reserve",
            statement=(
                "Every architecture frontier reports non-SRAM delta area/cells, Fmax, cycles, "
                "and remaining area reserve; repeated low-yield local tweaks stop and escalate "
                "to structural folding or an explicit Pareto/no-go result."
            ),
            evidence_hint="append-only PPA frontier ledger bound to RTL and constraint hashes",
        ),
    ),
    "prototype": (
        ChecklistItem(
            id="prototype.delivery-level",
            statement=(
                "The declared FPGA, emulator, GDS, silicon, or structured N/A prototype level is consistent "
                "with the frozen chip scope and contains no broader hardware claim."
            ),
            evidence_hint="prototype/RESULTS.json",
        ),
        ChecklistItem(
            id="prototype.hardware-evidence",
            statement=(
                "Applicable prototype evidence records tool/board/chip identity, build artifact, clocks and "
                "resources, host/runtime integration, on-hardware correctness, power, and raw commands/logs."
            ),
            evidence_hint="prototype/RESULTS.json and prototype/raw/",
        ),
    ),
    "benchmark": (
        ChecklistItem(
            id="benchmark.protocol-fairness",
            statement=(
                "Candidate and baselines use identical workloads, quantization/quality, memory/host budgets, "
                "warmup, repetitions, synchronization, power method, and platform/technology constraints."
            ),
            evidence_hint="benchmark/PROTOCOL.md",
        ),
        ChecklistItem(
            id="benchmark.kernel-system-metrics",
            statement=(
                "Kernel and end-to-end results separately report latency/throughput, effective bandwidth and "
                "utilization, energy, area/resources, uncertainty, and workload-specific metrics."
            ),
            evidence_hint="benchmark/RESULTS.json and benchmark/raw/",
        ),
        ChecklistItem(
            id="benchmark.claim-boundary",
            statement=(
                "All regressions, unsupported cases, quality deltas, and market-reference limitations are "
                "explicit; different PDK nodes or commercial products are not presented as direct PPA wins."
            ),
            evidence_hint="benchmark/RESULTS.json limitations and comparison_scope",
        ),
    ),
    "signoff": (
        ChecklistItem(
            id="signoff.artifact-integrity",
            statement=(
                "The final manifest links source, generated artifacts, verification, PPA, prototype, "
                "benchmark, tool versions, git identity, and reproduction entry points."
            ),
            evidence_hint="signoff/ARTIFACT_MANIFEST.json and signoff/SIGNOFF.json",
        ),
        ChecklistItem(
            id="signoff.provenance-autonomy",
            statement=(
                "IP/license provenance, Argus role trajectories, intervention history, failed attempts, and "
                "independent Reviewer decisions support the stated autonomy claim."
            ),
            evidence_hint="signoff/SIGNOFF.json provenance and intervention records",
        ),
        ChecklistItem(
            id="signoff.bounded-result",
            statement=(
                "RESULTS.md is reproducible and limits claims to the certified delivery level, hardware, "
                "technology, workloads, quality, PPA, and benchmark evidence."
            ),
            evidence_hint="RESULTS.md and signoff/SIGNOFF.json",
        ),
    ),
}


def role_banner(role: str) -> str:
    """Frame roles around auditable chip-design evidence."""
    common = (
        "MISSION TYPE: CHIP / ACCELERATOR DESIGN. Execute an evidence-gated hardware "
        "flow from workload definition through architecture, RTL, verification, PPA, "
        "prototype, benchmark, and sign-off. This is NOT ordinary software work, NOT "
        "a paper pipeline, and NOT merely RTL generation. Delivery level matters: "
        "synthesizable IP, FPGA, open-PDK GDS, computer-verified pre-tapeout readiness, "
        "actual tapeout readiness, and fabricated silicon "
        "are different claims. Freeze interfaces, numerical behavior, target technology, "
        "resource/power budgets, baselines, and acceptance metrics before implementation. "
        "Numeric area, frequency, power, memory, and quality targets are operator-owned "
        "contracts. An Agent-authored plan, ledger, review packet, or no-go report may "
        "recommend a change but cannot authorize one; relaxing a target requires explicit "
        "operator approval recorded as such. "
        "Use maintained EDA flows and IP before authoring replacements. Never invent tool "
        "runs, coverage, PPA, power, DRC/LVS/STA, FPGA, silicon, or market-comparison results.\n"
    )
    normalized = (role or "").strip().lower()
    if normalized == "manager":
        return common + (
            "Reuse Reviewer-certified upstream stages and roll back only to the earliest "
            "stage whose contract actually changed. Advancing the first unsupported "
            "operator within an unchanged workload, delivery level, numerical contract, "
            "host/memory interface, resource budget, architecture, target technology, and "
            "toolchain is an RTL delta: roll back directly to rtl. Roll back to definition "
            "only for a real workload/delivery/interface/budget contract change; architecture "
            "only for a changed dataflow, memory hierarchy, compute organization, or control "
            "interface; environment only for changed target/tool/IP/license requirements. "
            "Never treat a Planner or Reviewer budget-reallocation packet as operator "
            "authorization to relax a numeric target. "
            "Do not rewrite, rehash, or recertify stable definition/architecture/environment "
            "artifacts merely to replace the name of the next unsupported operator. When several "
            "remaining operators share one descriptor family, numerical contract, memory model, "
            "and compute organization, freeze that family once instead of reopening one contract "
            "per operator. After that family contract is certified, keep resource folding, "
            "retiming, and area/timing repair in the RTL loop; do not reopen earlier stages. Use a fast "
            "capability loop for non-milestone operator uplifts: rtl -> verification -> fresh "
            "Sky130 PPA inside one bounded RTL mission whose Planner task has "
            "`stage_closing=false`; Reviewer acceptance completes that task but does not advance "
            "the pipeline out of rtl. Then schedule the next operator directly. Run prototype, "
            "full benchmark, multi-node PPA, signoff, and a local milestone commit only when "
            "the complete target hardware workload or complete model/system demonstration is "
            "Reviewer-certified, or the operator explicitly requests a release. Intermediate "
            "operator groups such as QKV, RoPE+KV, Attention, or MLP are checkpoints, not release "
            "milestones. Every reused result must still "
            "bind the current design/RTL_MANIFEST.json source revision; stale bindings are never reusable."
        )
    if normalized == "planner":
        return common + (
            "Plan from highest-risk unknowns and the declared delivery level. Close workload/"
            "interface/quality ambiguity before architecture; close EDA/PDK/IP/license readiness "
            "before RTL; require independent verification before PPA; and require PPA/prototype "
            "evidence before benchmark claims. Rank architecture changes by roofline/Amdahl "
            "leverage and end-to-end workload impact. Separate fair same-flow open baselines from "
            "commercial market context. Replan on toolchain, correctness, timing, area, power, or "
            "memory-bandwidth blockers rather than polishing downstream reports."
            " Batch the remaining operators of one already-understood hardware family into one "
            "definition/architecture contract; never schedule per-operator environment refreshes "
            "when target, tools, IP, licenses, and delivery level are unchanged."
            " For a non-milestone operator uplift, plan the fast rtl -> verification -> Sky130 "
            "PPA loop as exactly one bounded RTL task with `stage_closing=false`; include "
            "implementation, full regression, canonical PPA, and evidence binding in that task. "
            "Do not emit separate verification-stage or PPA-stage closeout tasks and do not ask "
            "the Manager to advance out of rtl. Leave prototype/full benchmark/signoff for complete-workload or "
            "model/system release milestones. Keep an unmet operator-owned target unmet "
            "until the operator explicitly approves a replacement; do not route "
            "implementation through a proposed cap."
        )
    if normalized == "engineer":
        return common + (
            "Read the project-native contract and audit the exact runtime first. Build the smallest "
            "traceable architecture/RTL increment, maintain an independent executable model, and "
            "run real lint, simulation, formal, synthesis, implementation, and benchmark commands "
            "appropriate to scope. Preserve failing seeds, waveforms, reports, candidate diffs, "
            "tool versions, constraints, and PDK/board identity. Fix design/source rather than "
            "weakening tests or constraints. Keep generated outputs separate from authored source."
        )
    if normalized == "reviewer":
        return common + (
            "Act as an independent architecture, verification, implementation, benchmark, and "
            "tapeout-readiness reviewer. Rerun decisive commands; challenge workload and memory "
            "assumptions, reference independence, CDC/reset/protocol behavior, timing/area/power "
            "constraints, baseline fairness, quality floors, IP licensing, raw artifact hashes, "
            "and intervention claims. Reject different-node PPA comparisons, simulation presented "
            "as silicon, or commercial-product claims without same-workload measured evidence."
            " Reject any claimed target relaxation that lacks explicit operator authorization; "
            "Reviewer acceptance alone cannot change an operator-owned numeric contract. "
            " Reuse upstream evidence only when its recorded RTL-manifest binding is current. "
            "Treat one successful canonical, hash-bound Yosys/ABC result as the decisive PPA "
            "run: inspect its raw evidence and do not launch a second full Yosys/ABC PPA for "
            "the same RTL, verification, constraints, library, and toolchain hashes unless "
            "the packet is incomplete or materially suspect. "
            "For intermediate operator and operator-group uplifts, certify the "
            "rtl/verification/PPA delta without demanding ceremonial prototype N/A, full "
            "benchmark, multi-node PPA, or signoff regeneration. Treat `done` on such a "
            "`stage_closing=false` task as mission completion, not permission to advance the "
            "pipeline out of rtl."
        )
    return common
