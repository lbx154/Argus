"""Single-stage vertical for Agents' Last Exam's hardest workflows.

ALE evaluates a completed professional deliverable against a hidden reference
after the agent exits.  A multi-stage research or optimization pipeline is the
wrong shape: it spends the episode budget producing internal scaffolding while
the required workbook, model, simulation, report, video, or program remains
unfinished.  This vertical therefore has one ``execute`` stage.  The normal
Engineer/Reviewer round loop supplies iteration inside that stage.

The reviewer cannot access the hidden reference or run the final grader.  It
instead verifies every observable precondition that commonly causes a hard
zero: exact output paths, complete artifact bundles, parseability, native-tool
state, measured rather than guessed results, and evidence that long-running
jobs actually finished.
"""
from __future__ import annotations

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["execute"]
CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)

# ALE success is the benchmark's post-run artifact score.  It is neither a
# paper-submission gate nor a metric-search campaign.
completion_gate = "none"

# The required output locations are task-specific and live in the task prompt.
# A generic shell command cannot validate them without re-parsing prose, so the
# stage checklist carries the substantive review against the actual instruction
# and artifacts.

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "execute": (
        ChecklistItem(
            id="contract-complete",
            statement=(
                "Every explicit task requirement has been converted into a concrete "
                "acceptance item: all required files, exact paths and names, schemas, "
                "content constraints, screenshots, application state, and stated hard gates."
            ),
            evidence_hint=(
                "the original task instruction cross-checked item-by-item against the final outputs"
            ),
        ),
        ChecklistItem(
            id="bundle-present",
            statement=(
                "The complete required artifact bundle exists at the exact task-specified "
                "locations with correct case, extensions, and non-empty contents; no mandatory "
                "companion file is missing."
            ),
            evidence_hint="fresh directory listing and file metadata for every required output",
        ),
        ChecklistItem(
            id="artifacts-parse",
            statement=(
                "Every deliverable reopens or parses successfully with the native application "
                "or an independent format-aware checker, and its internal structure/schema is "
                "valid rather than merely having the right filename."
            ),
            evidence_hint=(
                "native reopen/export, parser output, workbook sheet/cell inspection, media probe, "
                "mesh/program/test execution, or another task-appropriate validation"
            ),
        ),
        ChecklistItem(
            id="workflow-finished",
            statement=(
                "Any required simulation, render, export, analysis, build, or replay actually "
                "finished. Completion is established from result artifacts or application state, "
                "not inferred from elapsed time, a click, or a still-running progress window."
            ),
            evidence_hint="completed job state plus generated result/log/export files",
        ),
        ChecklistItem(
            id="values-measured",
            statement=(
                "Reported values and conclusions were extracted from real tool outputs or computed "
                "from supplied data. Nothing is guessed, estimated to resemble a reference, "
                "fabricated, left null, or represented by placeholder/demo content."
            ),
            evidence_hint="trace from each reported value to solver, source data, or executable result",
        ),
        ChecklistItem(
            id="native-evidence-valid",
            statement=(
                "When the task requires professional GUI software or visual proof, the required "
                "native application/project state is genuine and the submitted screenshots or "
                "previews visibly show the produced work. A CLI substitute is used only when it "
                "preserves every required output and provenance condition."
            ),
            evidence_hint="fresh screenshot/native project inspection tied to the submitted artifact",
        ),
        ChecklistItem(
            id="cross-artifact-consistent",
            statement=(
                "All submitted files agree with one another: reports match structured data, "
                "screenshots show the submitted project, previews match source files, and no stale "
                "or earlier attempt was exported by mistake."
            ),
            evidence_hint="cross-check identifiers, timestamps, dimensions, values, and visible content",
        ),
        ChecklistItem(
            id="final-hard-gate-audit",
            statement=(
                "A final hard-gate audit found no missing, malformed, all-null, unsafe, colliding, "
                "or otherwise automatically disqualifying output. Partial progress has been saved, "
                "but done is declared only when every observable gate passes."
            ),
            evidence_hint="reviewer-run final audit over the exact task contract and final artifact set",
        ),
    ),
}


def role_banner(role: str) -> str:
    """Frame every role around one hidden-reference artifact-delivery episode."""
    common = (
        "MISSION TYPE: AGENTS' LAST EXAM (ALE), hardest-tier professional workflow. "
        "Complete ONE bounded real-computer task whose final artifacts will be scored "
        "against a HIDDEN reference only after exit. This is NOT a paper pipeline and "
        "NOT an open-ended metric search. The user is unavailable during the run. "
        "Never seek, infer, or access hidden reference assets. Success means a complete, "
        "valid deliverable bundle at the exact paths in the task instruction.\n"
    )
    role_norm = (role or "").strip().lower()
    if role_norm == "planner":
        return common + (
            "Plan backward from the hard gates. Identify every mandatory output and exact "
            "path, start the highest-latency or highest-risk native-software step early, "
            "preserve a minimally valid bundle as soon as possible, then spend remaining "
            "budget on task-specific quality. Keep all work inside the single execute stage."
        )
    if role_norm == "engineer":
        return common + (
            "Before acting, extract a private itemized delivery contract from the prompt. "
            "Use both terminal and GUI/CUA tools as the workflow demands. Save early and "
            "checkpoint long jobs; inspect real progress and wait for completion. Prefer a "
            "complete valid bundle over polishing one component while another is missing. "
            "Before yielding, reopen/parse/replay every output and quote fresh verification "
            "evidence. Never guess solver values or submit placeholders."
        )
    if role_norm == "reviewer":
        return common + (
            "Act as an independent preflight auditor. Read the original instruction and "
            "inspect the actual filesystem, application state, and submitted artifacts; do "
            "not trust the engineer's summary. Check hard gates first. Missing companion "
            "files, wrong paths, malformed schemas, unfinished jobs, guessed values, stale "
            "exports, or screenshots unrelated to the deliverable require continue. Give "
            "one prioritized, executable repair plan. Mark done only when every observable "
            "requirement is independently verified."
        )
    return common


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "STAGE_ORDER",
    "completion_gate",
    "role_banner",
]
