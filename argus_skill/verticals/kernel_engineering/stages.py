"""General GPU-kernel engineering vertical.

This vertical is for real repository work: diagnose and improve CUDA/Triton/
TileLang/CUTLASS/PyTorch kernels, preserve the public API, validate numerical
behavior, benchmark on the target accelerator, and prepare an upstream-quality
change.  ``kernelbench`` remains the fixed SOL-ExecBench competition vertical;
this package owns production/library kernel work where environment provenance
and integration quality matter as much as a latency number.
"""

from __future__ import annotations

from ...skills.stage_checklists import ChecklistItem
from ..speedrun.stages import _PIPELINE_CHECK

STAGE_ORDER = [
    "scope",
    "environment",
    "baseline",
    "optimize",
    "validate",
    "report",
]

WORKFLOW_MODE = "staged"
completion_gate = "metric"

_AUDIT = (
    "${ARGUS_SKILL_PYTHON:-python} -m argus_skill.verticals.kernel_engineering.environment_audit"
)

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "scope": [
        _PIPELINE_CHECK,
        ("Kernel mission contract present", "test -s research/KERNEL_SCOPE.md"),
        ("Repository instructions inspected", "test -s research/PROJECT_NATIVE_SETUP.md"),
    ],
    "environment": [
        _PIPELINE_CHECK,
        ("Environment audit present", "test -s research/ENVIRONMENT_AUDIT.json"),
        ("Environment audit passes", f"{_AUDIT} check --project-root ."),
        (
            "Specialized tool shortlist present",
            "test -s research/TOOLCHAIN_CANDIDATES.md",
        ),
        ("Infrastructure reuse plan present", "test -s research/INFRASTRUCTURE_REUSE_PLAN.md"),
    ],
    "baseline": [
        _PIPELINE_CHECK,
        ("Baseline protocol pinned", "test -s research/BASELINE_PROTOCOL.md"),
        ("Correct baseline evidence present", "test -s research/BASELINE_RESULT.json"),
    ],
    "optimize": [
        _PIPELINE_CHECK,
        (
            "At least one hypothesis-driven attempt exists",
            "find attempts experiments -mindepth 2 -maxdepth 3 -type f "
            "2>/dev/null | head -1 | grep -q .",
        ),
    ],
    "validate": [
        _PIPELINE_CHECK,
        ("Validation matrix present", "test -s research/VALIDATION_MATRIX.md"),
        ("Candidate validation evidence present", "test -s research/VALIDATION_RESULT.json"),
    ],
    "report": [
        _PIPELINE_CHECK,
        ("Results report present", "test -s RESULTS.md"),
        ("Environment provenance retained", "test -s research/ENVIRONMENT_AUDIT.json"),
    ],
}

_ENGINEER_SKILL = "engineer/kernel-environment-first-engineering.md"
_REVIEWER_SKILL = "reviewer/kernel-engineering-review.md"

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "scope": (
        _ENGINEER_SKILL,
        "Evaluate the kernel scope before any implementation work. The target op, "
        "public API, editable/frozen files, target hardware, correctness oracle, "
        "benchmark command, supported shapes/dtypes, and intended upstream change "
        "must be explicit. The project-native install and benchmark instructions "
        "must have been read. Pass only when the task is narrow enough to measure.",
        ["research/KERNEL_SCOPE.md", "research/PROJECT_NATIVE_SETUP.md"],
    ),
    "environment": (
        _ENGINEER_SKILL,
        "Treat this as a hard gate. Independently inspect ENVIRONMENT_AUDIT.json, "
        "TOOLCHAIN_CANDIDATES.md, and INFRASTRUCTURE_REUSE_PLAN.md. The registry "
        "must have been queried for the relevant platform and bottleneck categories. "
        "The selected implementation path must name its "
        "required capability (for example tilelang, triton, cuda_cpp, cutlass_cute, "
        "profiling, or sanitizer), and every required capability must be ready in the "
        "same environment that will run tests and benchmarks. Project extras/lockfiles "
        "and mature upstream libraries must be preferred over hand-rolled substitutes. "
        "A missing compiler/package/profiler is an environment failure, not evidence "
        "that the kernel idea is bad. Do not advance while the audit is stale or red.",
        [
            "research/ENVIRONMENT_AUDIT.json",
            "research/ENVIRONMENT_AUDIT.md",
            "research/TOOLCHAIN_CANDIDATES.md",
            "research/INFRASTRUCTURE_REUSE_PLAN.md",
        ],
    ),
    "baseline": (
        _ENGINEER_SKILL,
        "Verify a clean unmodified baseline in the audited environment. Correctness "
        "must pass before timing; warmup/autotune/JIT policy, synchronization, input "
        "matrix, hardware/software versions, and isolation policy must be pinned. A "
        "baseline that differs from project/official reference expectations must be "
        "diagnosed before optimization starts.",
        ["research/BASELINE_PROTOCOL.md", "research/BASELINE_RESULT.json"],
    ),
    "optimize": (
        _ENGINEER_SKILL,
        "Evaluate the latest attempt as an engineering experiment: measured bottleneck, "
        "mechanistic hypothesis, minimal implementation, correctness result, timing, "
        "and verdict. Reject blind parameter sweeps and reinvention of an available "
        "project/vendor primitive. Compile/runtime failures must be attributed to code "
        "versus environment before abandoning the mechanism.",
        ["attempts/", "research/ENVIRONMENT_AUDIT.json", "research/BASELINE_RESULT.json"],
    ),
    "validate": (
        _REVIEWER_SKILL,
        "Audit the retained candidate against the full supported contract: forward and "
        "backward/reference parity, dtypes, aligned and ragged shapes, optional/varlen "
        "paths, determinism where relevant, fallback behavior, memory, and repeated "
        "isolated benchmarks. Require environment provenance and no benchmark/scorer "
        "weakening. Hardware-specific dispatch is acceptable when unsupported devices "
        "fall back safely.",
        ["research/VALIDATION_MATRIX.md", "research/VALIDATION_RESULT.json", "attempts/"],
    ),
    "report": (
        _REVIEWER_SKILL,
        "Require an upstream-ready report: exact baseline/candidate commands, target "
        "hardware and stack, correctness matrix, latency quantiles and uncertainty, "
        "memory, regressions/fallbacks, limitations, and a claim no broader than the "
        "evidence. If the result is intended for a PR, the diff must stay narrow and "
        "the report must explain why the chosen mature infrastructure was reused.",
        ["RESULTS.md", "research/ENVIRONMENT_AUDIT.json", "research/VALIDATION_RESULT.json"],
    ),
}

CHECKLIST_STAGE_ORDER: tuple[str, ...] = tuple(STAGE_ORDER)

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "scope": (
        ChecklistItem(
            id="scope.kernel_contract",
            statement=(
                "The target kernel/op, public API, allowed edits, target accelerator, "
                "correctness oracle, benchmark entry point, and supported shape/dtype "
                "surface are pinned before implementation."
            ),
            evidence_hint="research/KERNEL_SCOPE.md",
        ),
        ChecklistItem(
            id="scope.project_native_setup",
            statement=(
                "Repository-native installation, extras, lockfiles, CI, benchmark tools, "
                "and existing backend abstractions were inspected and recorded."
            ),
            evidence_hint="research/PROJECT_NATIVE_SETUP.md",
        ),
    ),
    "environment": (
        ChecklistItem(
            id="environment.capability_audit",
            statement=(
                "A fresh machine-readable audit proves that every capability selected "
                "for this implementation path is available in the actual test/benchmark "
                "environment. Missing professional tooling is resolved or explicitly "
                "blocks the stage; it is never papered over with a homemade substitute."
            ),
            evidence_hint=(
                "research/ENVIRONMENT_AUDIT.json generated by "
                "`python -m argus_skill.verticals.kernel_engineering.environment_audit collect`"
            ),
        ),
        ChecklistItem(
            id="environment.specialized_catalog",
            statement=(
                "The professional tool registry was queried by relevant platform and "
                "bottleneck category; the shortlist records maintained specialist "
                "packages considered, legacy/archived options rejected, and why the "
                "selected stack is preferable to custom infrastructure."
            ),
            evidence_hint="research/TOOLCHAIN_CANDIDATES.md",
        ),
        ChecklistItem(
            id="environment.infrastructure_reuse",
            statement=(
                "The reuse plan identifies project-native and mature upstream libraries, "
                "profilers, build tools, and reference implementations considered; it "
                "justifies any custom infrastructure that remains necessary."
            ),
            evidence_hint="research/INFRASTRUCTURE_REUSE_PLAN.md",
        ),
    ),
    "baseline": (
        ChecklistItem(
            id="baseline.correct_reproducible",
            statement=(
                "The unmodified baseline passes the real correctness oracle and has a "
                "reproducible isolated timing record with exact commands, versions, "
                "warmup/autotune policy, shapes, and synchronization."
            ),
            evidence_hint="research/BASELINE_PROTOCOL.md and research/BASELINE_RESULT.json",
        ),
    ),
    "optimize": (
        ChecklistItem(
            id="optimize.mechanistic_attempt",
            statement=(
                "Each attempt starts from a measured bottleneck, states a physical or "
                "compiler-level hypothesis, changes the smallest relevant surface, and "
                "records correctness, timing, environment attribution, and keep/reject."
            ),
            evidence_hint="attempts/<id>/CHANGES.md plus raw test/benchmark artifacts",
        ),
    ),
    "validate": (
        ChecklistItem(
            id="validate.full_contract",
            statement=(
                "The retained candidate is validated across the supported numerical, "
                "shape, dtype, gradient, determinism, fallback, memory, and performance "
                "matrix without weakening the harness."
            ),
            evidence_hint="research/VALIDATION_MATRIX.md and research/VALIDATION_RESULT.json",
        ),
    ),
    "report": (
        ChecklistItem(
            id="report.evidence_bounded_claim",
            statement=(
                "RESULTS.md reports exact environment and commands, raw correctness and "
                "performance evidence, uncertainty, regressions, dispatch boundaries, "
                "limitations, and only the speedup claim actually demonstrated."
            ),
            evidence_hint="RESULTS.md",
        ),
    ),
}


def role_banner(role: str) -> str:
    common = (
        "MISSION — production GPU kernel engineering. Improve a real repository's "
        "kernel while preserving its API and correctness. ENVIRONMENT IS PART OF THE "
        "ALGORITHM: before writing code, inspect project-native setup and prove the "
        "chosen professional toolchain is installed and compatible. Do not recreate "
        "Triton/TileLang/CUTLASS/CuTe/vendor libraries, benchmark harnesses, profilers, "
        "or training/RL infrastructure that the project already depends on. A missing "
        "package/compiler/configuration is an environment blocker, not a failed kernel "
        "idea. Correctness precedes timing; only isolated real-hardware measurements "
        "support a speed claim. This is not a paper pipeline and not SOL-ExecBench.\n"
    )
    if role == "planner":
        return common + (
            "Plan environment and baseline work before implementation. Require a reuse "
            "decision and capability audit; schedule custom infrastructure only after "
            "the canonical project/vendor path is shown insufficient.\n"
        )
    if role == "reviewer":
        return common + (
            "Fail closed on stale/red environment audits, missing project extras, "
            "unexplained fallbacks, mixed benchmark environments, or compile failures "
            "misreported as algorithm failures.\n"
        )
    if role == "engineer":
        return common + (
            "Run the environment audit first, repair the audited environment in an "
            "isolated/pinned way, reproduce baseline, then profile and optimize.\n"
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
