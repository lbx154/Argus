"""Digital-circuit design and verification vertical.

This vertical covers synthesizable Verilog/SystemVerilog RTL, testbenches,
formal properties, FPGA/ASIC-oriented synthesis, fixed-harness benchmarks, and
sign-off evidence. It is not a software-only delivery lane: completion requires
an explicit hardware contract, independently checked RTL behavior, and
reproducible tool output.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ("specification", "rtl", "verification", "synthesis", "delivery")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "staged"
completion_gate = "none"

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


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    """Return deterministic structural blockers for the current hardware stage."""
    stage_name = (stage or "").strip().lower()
    root = Path(project_root)

    if stage_name == "specification":
        specifications = (
            root / "design" / "SPEC.md",
            root / "SPEC.md",
            root / "docs" / "SPEC.md",
        )
        if not any(path.is_file() and path.stat().st_size > 0 for path in specifications):
            return ("specification requires a non-empty design/SPEC.md, SPEC.md, or docs/SPEC.md",)
        return ()

    if stage_name == "rtl":
        from ..path_evidence import PathEvidenceError, validate_any_file

        try:
            validate_any_file(
                root,
                ["rtl/**/*.v", "rtl/**/*.sv", "src/**/*.v", "src/**/*.sv"],
            )
        except PathEvidenceError as exc:
            return (str(exc),)
        return ()

    if stage_name == "verification":
        from .evidence import EvidenceError, validate_verification_results

        try:
            validate_verification_results(root)
        except EvidenceError as exc:
            return (str(exc),)
        return ()

    if stage_name == "synthesis":
        from ..path_evidence import PathEvidenceError, validate_any_file

        try:
            validate_any_file(
                root,
                ["synthesis/REPORT.md", "synthesis/NOT_APPLICABLE.md"],
                case_insensitive_patterns=[
                    "reports/**/*synth*.log",
                    "reports/**/*timing*.rpt",
                    "reports/**/*utilization*.rpt",
                    "synthesis/**/*synth*.log",
                    "synthesis/**/*timing*.rpt",
                    "synthesis/**/*utilization*.rpt",
                ],
            )
        except PathEvidenceError as exc:
            return (str(exc),)
        return ()

    if stage_name == "delivery":
        issues: list[str] = []
        summaries = (root / "RESULTS.md", root / "DELIVERY.md")
        if not any(path.is_file() and path.stat().st_size > 0 for path in summaries):
            issues.append("delivery requires a non-empty RESULTS.md or DELIVERY.md")
        entry_points = (
            root / "Makefile",
            root / "justfile",
            root / "run.sh",
            root / "scripts" / "run.sh",
            root / "scripts" / "verify.sh",
        )
        if not any(path.is_file() for path in entry_points):
            issues.append("delivery requires a reproduction entry point")
        return tuple(issues)

    return ()


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
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
    "stage_completion_issues",
]
