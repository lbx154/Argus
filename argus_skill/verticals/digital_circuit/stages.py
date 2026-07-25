"""Digital-circuit design and verification vertical.

This vertical covers synthesizable Verilog/SystemVerilog RTL, testbenches,
formal properties, FPGA/ASIC-oriented synthesis, fixed-harness benchmarks, and
sign-off evidence. It is not a software-only delivery lane: completion requires
an explicit hardware contract, independently checked RTL behavior, and
reproducible tool output.
"""
from __future__ import annotations

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ("specification", "rtl", "verification", "synthesis", "delivery")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "staged"
completion_gate = "none"

_PIPELINE_CHECK = (
    "Pipeline state present",
    "test -f research/PIPELINE_STATE.json",
)

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "specification": [
        _PIPELINE_CHECK,
        (
            "Digital design specification present",
            "test -s design/SPEC.md || test -s SPEC.md || test -s docs/SPEC.md",
        ),
    ],
    "rtl": [
        _PIPELINE_CHECK,
        (
            "Verilog or SystemVerilog RTL present",
            "{python} -m argus_skill.verticals.path_evidence --project-root . "
            "--glob 'rtl/**/*.v' --glob 'rtl/**/*.sv' "
            "--glob 'src/**/*.v' --glob 'src/**/*.sv'",
        ),
    ],
    "verification": [
        _PIPELINE_CHECK,
        (
            "Verification source present",
            "{python} -m argus_skill.verticals.path_evidence --project-root . "
            "--glob 'tb/**/*.v' --glob 'tb/**/*.sv' --glob 'tb/**/*.py' --glob 'tb/**/*.sby' "
            "--glob 'testbench/**/*.v' --glob 'testbench/**/*.sv' "
            "--glob 'testbench/**/*.py' --glob 'testbench/**/*.sby' "
            "--glob 'verification/**/*.v' --glob 'verification/**/*.sv' "
            "--glob 'verification/**/*.py' --glob 'verification/**/*.sby' "
            "--glob 'formal/**/*.v' --glob 'formal/**/*.sv' "
            "--glob 'formal/**/*.py' --glob 'formal/**/*.sby'",
        ),
        (
            "Verification results present",
            "{python} -m argus_skill.verticals.digital_circuit.evidence "
            "verification --project-root .",
        ),
    ],
    "synthesis": [
        _PIPELINE_CHECK,
        (
            "Synthesis evidence or justified non-applicability present",
            "test -s synthesis/REPORT.md || test -s synthesis/NOT_APPLICABLE.md "
            "|| {python} -m argus_skill.verticals.path_evidence --project-root . "
            "--iglob 'reports/**/*synth*.log' --iglob 'reports/**/*timing*.rpt' "
            "--iglob 'reports/**/*utilization*.rpt' --iglob 'synthesis/**/*synth*.log' "
            "--iglob 'synthesis/**/*timing*.rpt' --iglob 'synthesis/**/*utilization*.rpt'",
        ),
    ],
    "delivery": [
        _PIPELINE_CHECK,
        (
            "Delivery summary present",
            "test -s RESULTS.md || test -s DELIVERY.md",
        ),
        (
            "Reproduction entry point present",
            "test -f Makefile || test -f justfile || test -f run.sh "
            "|| test -f scripts/run.sh || test -f scripts/verify.sh",
        ),
    ],
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "specification": (
        "engineer/digital-circuit-rtl-verification.md",
        "Review the hardware contract before RTL work. Require explicit cycle-level "
        "behavior, ports and widths, signedness, clock/reset semantics, protocol "
        "timing, parameter constraints, illegal-input behavior, latency/throughput "
        "expectations, and a checkable acceptance matrix. Reject ambiguous prose that "
        "would let multiple incompatible circuits all appear correct.",
        ["design/SPEC.md", "SPEC.md", "docs/SPEC.md"],
    ),
    "rtl": (
        "engineer/digital-circuit-rtl-verification.md",
        "Review the RTL against the frozen specification. Check synthesizability, "
        "combinational completeness, sequential assignment discipline, reset values, "
        "width/signedness conversions, parameter bounds, clock-domain assumptions, "
        "and protocol timing. Reject simulation-only constructs in synthesizable RTL, "
        "unintended latches, multiple drivers, unsafe CDC, and changes that weaken the "
        "specification or testbench to make the design pass.",
        ["rtl/", "src/", "design/SPEC.md"],
    ),
    "verification": (
        "reviewer/digital-circuit-signoff-review.md",
        "Independently rerun the declared verification flow. Require directed reset "
        "and boundary tests, randomized or exhaustive cases appropriate to the state "
        "space, protocol assertions, X/Z detection, and waveform/log evidence for "
        "failures. Formal proof may replace simulation only for properties actually "
        "covered. A compile-only result or a self-checking testbench with no observed "
        "PASS/FAIL evidence is not completion.",
        ["verification/RESULTS.md", "tb/", "testbench/", "formal/", "reports/"],
    ),
    "synthesis": (
        "reviewer/digital-circuit-signoff-review.md",
        "Review synthesis and implementation evidence for synthesizable designs: "
        "tool/version, target device or library, clock and I/O constraints, warnings, "
        "latches/black boxes, timing, and area/resource utilization. For a verification-"
        "only or fixed functional-benchmark mission, accept synthesis/NOT_APPLICABLE.md "
        "only when it names the exact reason synthesis/PPA is outside the frozen scorer "
        "contract and the RTL claim is not overstated. Missing host-PATH tools alone are "
        "not a blocker until declared project-local and local container toolchains have "
        "also been inspected.",
        ["synthesis/REPORT.md", "synthesis/NOT_APPLICABLE.md", "reports/"],
    ),
    "delivery": (
        "reviewer/digital-circuit-signoff-review.md",
        "Perform final hardware sign-off from a clean reproduction command. Confirm "
        "the delivered RTL matches the reviewed source, all required tests pass, "
        "synthesis/formal claims trace to raw tool output, generated artifacts are not "
        "mistaken for source, known limitations are explicit, and no passing claim "
        "depends on stale caches or an edited reference/testbench. For an external "
        "benchmark claim, require frozen selection/scorer provenance, a non-empty patch, "
        "immutable first-attempt evidence, separately appended repair attempts, and "
        "explicit golden/hidden-harness non-exposure. Require every prompt-referenced "
        "pre-existing public file to be present before generation; missing referenced "
        "context is a benchmark packaging defect, not an interface to infer from oracle "
        "failures.",
        [
            "RESULTS.md",
            "DELIVERY.md",
            "Makefile",
            "scripts/",
            "selection.json",
            "controller.json",
            "results.jsonl",
        ],
    ),
}

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "specification": (
        ChecklistItem(
            id="spec.behavior-interface",
            statement=(
                "The cycle-level functional behavior and complete module interface are explicit, "
                "including port direction, width, signedness, parameters, and legal value ranges. "
                "A public benchmark interface is preserved exactly unless the prompt explicitly "
                "requests its repair; every unresolved ambiguity is recorded."
            ),
            evidence_hint="design/SPEC.md with behavior tables and a port/parameter table",
        ),
        ChecklistItem(
            id="spec.clock-reset-protocol",
            statement=(
                "Clock domains, reset polarity and sync/async behavior, reset values, handshake "
                "timing, backpressure, latency, and throughput expectations are explicit."
            ),
            evidence_hint="timing diagrams or cycle tables plus reset and protocol rules",
        ),
        ChecklistItem(
            id="spec.acceptance-matrix",
            statement=(
                "Normal, boundary, illegal, reset, stall, and recovery scenarios are mapped to "
                "observable acceptance criteria before implementation begins."
            ),
            evidence_hint="a scenario-to-expected-result verification matrix",
        ),
        ChecklistItem(
            id="spec.benchmark-interface-closure",
            statement=(
                "For an external RTL benchmark, exact output path, top module, ports, "
                "parameters, reset/clock semantics, control interpretation, and cycle "
                "latency are frozen before generation; otherwise the artifact explicitly "
                "states that no external benchmark contract applies."
            ),
            evidence_hint=(
                "design/BENCHMARK_INTERFACE.json derived only from public prompt/context "
                "and output schema, or an explicit non-benchmark statement"
            ),
        ),
    ),
    "rtl": (
        ChecklistItem(
            id="rtl.spec-traceability",
            statement=(
                "Every state element, datapath operation, and interface response traces to the "
                "frozen specification; benchmark output filenames and module/port/parameter "
                "identifiers exactly match the interface manifest, and implementation does "
                "not silently redefine behavior."
            ),
            evidence_hint="RTL review notes mapping modules and state transitions to spec clauses",
        ),
        ChecklistItem(
            id="rtl.synthesizable-discipline",
            statement=(
                "Synthesizable RTL uses complete combinational assignments and disciplined "
                "sequential logic, with no unintended latches, multiple drivers, unsafe CDC, "
                "or simulation-only constructs in the design path."
            ),
            evidence_hint="lint output and reviewer inspection of rtl/**/*.sv or src/**/*.v",
        ),
        ChecklistItem(
            id="rtl.width-reset-parameters",
            statement=(
                "Width extension/truncation, signed arithmetic, reset state, counter overflow, "
                "array bounds, and parameter edge cases are intentional and verified."
            ),
            evidence_hint="lint findings plus targeted elaboration or compile configurations",
        ),
    ),
    "verification": (
        ChecklistItem(
            id="verify.independent-oracle",
            statement=(
                "The testbench or formal model checks behavior against an independent oracle, "
                "assertion set, or reference model rather than reproducing the RTL algorithm."
            ),
            evidence_hint="self-checking scoreboard/reference model or named formal properties",
        ),
        ChecklistItem(
            id="verify.reset-boundary-random",
            statement=(
                "Verification covers reset entry/exit, boundaries, protocol stalls, illegal inputs "
                "where defined, exact width/signing, combinational versus prior-state sequential "
                "semantics, reset polarity/synchronicity, latency, initialization uncertainty, "
                "and randomized, exhaustive, or metamorphic cases appropriate to the public design."
            ),
            evidence_hint="verification plan and fresh run log with scenario counts/seeds",
        ),
        ChecklistItem(
            id="verify.no-xz-and-properties",
            statement=(
                "Assertions detect X/Z leakage, protocol violations, overflow/underflow hazards, "
                "and safety/liveness properties relevant to the module."
            ),
            evidence_hint="SVA/assertion sources and simulator or formal-engine results",
        ),
        ChecklistItem(
            id="verify.reproducible-pass",
            statement=(
                "A clean, documented command reproduces compile/elaboration and all claimed PASS "
                "results; failures retain actionable logs or waveforms."
            ),
            evidence_hint="verification/RESULTS.md plus raw logs and the exact command",
        ),
    ),
    "synthesis": (
        ChecklistItem(
            id="synth.target-constraints",
            statement=(
                "The synthesis target, tool/version, clock definitions, I/O delays, generated "
                "clocks, and other timing constraints are explicit and appropriate."
            ),
            evidence_hint="synthesis script/constraints and synthesis/REPORT.md",
        ),
        ChecklistItem(
            id="synth.structural-sanity",
            statement=(
                "Reports show no unexplained latches, combinational loops, undriven nets, multiple "
                "drivers, unresolved black boxes, or critical warnings."
            ),
            evidence_hint="lint/synthesis warning summary with every waiver justified",
        ),
        ChecklistItem(
            id="synth.timing-area",
            statement=(
                "Timing and area/resource claims cite fresh reports, including worst slack, clock "
                "target, utilization/cell area, and the tested parameter configuration."
            ),
            evidence_hint="timing and utilization/area report excerpts with source paths",
        ),
    ),
    "delivery": (
        ChecklistItem(
            id="delivery.clean-reproduction",
            statement=(
                "A clean checkout can run the documented lint, simulation/formal, and synthesis "
                "commands without relying on private caches or undeclared files."
            ),
            evidence_hint="Makefile/justfile/scripts plus a clean reproduction log",
        ),
        ChecklistItem(
            id="delivery.traceable-results",
            statement=(
                "RESULTS.md or DELIVERY.md traces every correctness, coverage, timing, and area "
                "claim to raw tool output and names all unsupported or unverified behavior."
            ),
            evidence_hint="delivery summary with links to verification and synthesis artifacts",
        ),
        ChecklistItem(
            id="delivery.source-artifact-boundary",
            statement=(
                "Source RTL, testbench/formal sources, constraints, generated netlists, waveforms, "
                "and reports are clearly separated so stale generated output cannot masquerade as source."
            ),
            evidence_hint="final artifact manifest and repository status",
        ),
        ChecklistItem(
            id="delivery.benchmark-integrity",
            statement=(
                "When an external benchmark is claimed, task selection and scorer provenance "
                "are frozen, the delivered patch is non-empty, first-attempt evidence is "
                "immutable, repairs append separate records, and golden outputs or hidden "
                "harness sources were not exposed; otherwise delivery explicitly records that "
                "no external benchmark claim is being made. Every pre-existing public file "
                "referenced by the prompt was present before generation; missing context "
                "blocked the run rather than being reconstructed from evaluator feedback."
            ),
            evidence_hint=(
                "selection.json/controller.json plus append-only results.jsonl and per-attempt "
                "raw score directories, or an explicit non-benchmark statement in DELIVERY.md"
            ),
        ),
    ),
}


def role_banner(role: str) -> str:
    """Frame roles around executable digital-hardware evidence."""
    common = (
        "MISSION TYPE: DIGITAL CIRCUIT / RTL ENGINEERING. Work on Verilog, "
        "SystemVerilog, testbenches, assertions/formal properties, FPGA/ASIC "
        "synthesis, timing, and hardware delivery. This is NOT a paper pipeline "
        "and NOT ordinary software testing. Hardware behavior is cycle-accurate; "
        "clock/reset/protocol semantics, widths, signedness, X/Z behavior, and "
        "synthesizability are first-class correctness conditions. Never claim PASS "
        "from compile success alone or invent simulator, formal, timing, coverage, "
        "or area results.\n"
        "For a fixed external benchmark, freeze the prompt, evaluator, official "
        "test inputs, tool versions, and score policy before editing. Keep the first "
        "official attempt immutable, record repair attempts separately, and never "
        "inspect or expose golden outputs or hidden harness sources. Preserve the "
        "exact public module/port contract; record ambiguity rather than silently "
        "correcting an interface. Classify evaluator no-execution separately and "
        "draw no RTL correctness conclusion from it.\n"
    )
    role_norm = (role or "").strip().lower()
    if role_norm == "planner":
        return common + (
            "Plan from the hardware contract and highest-risk unknowns: interface "
            "ambiguity, reset/CDC/protocol behavior, tool availability, verification "
            "oracle, and synthesis constraints. Keep the reference/testbench frozen "
            "once the acceptance contract is established. When a fixed functional "
            "benchmark does not score synthesis/PPA, use the shortest auditable path "
            "and document synthesis as outside scorer scope instead of manufacturing "
            "unscored implementation work. Select only visible-evidence-supported "
            "spec-guidance detectors, and route repair work only after the Engineer or "
            "Reviewer records an evidence-backed failure-taxonomy class. Require exact "
            "benchmark interface closure before any RTL generation."
        )
    if role_norm == "engineer":
        return common + (
            "Implement minimal synthesizable RTL, then run the real available tools "
            "(for example Verilator/iverilog, cocotb, Yosys, or SymbiYosys) through "
            "project-local commands. Inspect declared local containers after host PATH "
            "before declaring a tool unavailable, and serialize shared container "
            "runtimes. Preserve failing seeds, logs, and waveforms; fix RTL rather than "
            "weakening assertions or expected values. For each failed attempt, name one "
            "failure-taxonomy class, one root-cause hypothesis, and one regression. Before "
            "the first attempt, freeze and compile the exact public top/module/port/parameter "
            "contract instead of guessing compatibility aliases."
        )
    if role_norm == "reviewer":
        return common + (
            "Act as an independent hardware sign-off reviewer. Inspect the RTL and "
            "specification, rerun the declared commands, challenge the oracle, audit "
            "reset/clock/width/CDC/X behavior, and trace synthesis/timing claims to "
            "fresh raw reports. For benchmarks, audit workspace isolation, patch "
            "non-emptiness, hidden-input non-exposure, and separate first-attempt and "
            "post-repair records. Promote guidance only from cross-task evidence, never "
            "from task-specific hidden-oracle behavior. Do not trust a summary or a "
            "single happy-path test. Reject first-attempt readiness when the benchmark "
            "interface manifest is absent or does not match the RTL exactly."
        )
    return common


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
