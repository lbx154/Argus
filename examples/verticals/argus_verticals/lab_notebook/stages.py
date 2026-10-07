"""Stages and checklists of the ``lab_notebook`` example vertical.

Everything Argus needs to know about a vertical is declared at module level in
this file; there is no base class to inherit from.  The framework reads the
attributes below through ``argus.core.vertical_contract.vertical_contract`` and
``argus.verticals._registry._validated_plugin``.
"""

from __future__ import annotations

from argus.skills.stage_machine import ChecklistItem

# Required for every vertical that is not built in.  Argus refuses any other
# API version, and an empty purpose hides the vertical from the Manager.
ARGUS_VERTICAL_API_VERSION = 1
VERTICAL_PURPOSE = (
    "small measurement tasks on this machine: run the measurement, record how "
    "it was run, and write a notebook entry another person can reproduce"
)

# The stage order is the tuple order.  Every stage that is not optional needs a
# non-empty checklist below.
CHECKLIST_STAGE_ORDER: tuple[str, ...] = ("measure", "report")
CHECKLIST_OPTIONAL_STAGES: tuple[str, ...] = ()
# Names the Manager may use for a stage; they are canonicalized to the real one.
STAGE_ALIASES = {"experiment": "measure", "writeup": "report"}

# "none": the project is complete when the last stage's checklist is met.
# "metric" and "certified" are the other two values the contract accepts.
completion_gate = "none"
WORKFLOW_MODE = "staged"      # staged | direct | proportional
MISSION_KIND = "custom"       # custom | optimize | research | software
REQUIRE_INDEPENDENT_REVIEW = True

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "measure": (
        ChecklistItem(
            id="measure.ran-here",
            statement=(
                "The measurement was executed on this machine in this project, "
                "and its raw output is saved in the work directory."
            ),
            evidence_hint="the command that was run and the path of its raw output",
        ),
        ChecklistItem(
            id="measure.repeated",
            statement=(
                "The measurement was repeated, and the reported number is a "
                "median or mean with its spread; a single run is not a result."
            ),
            evidence_hint="number of repeats, the aggregate and the spread",
        ),
    ),
    "report": (
        ChecklistItem(
            id="report.notebook-entry",
            statement=(
                "NOTEBOOK.md in the work directory states what was measured, "
                "how, the result with its spread, and the exact command to rerun it."
            ),
            evidence_hint="the NOTEBOOK.md section and the rerun command",
        ),
        ChecklistItem(
            id="report.numbers-traceable",
            statement=(
                "Every number in NOTEBOOK.md can be traced to a saved raw output file."
            ),
            evidence_hint="raw file paths next to each number",
        ),
    ),
}


def role_banner(role: str) -> str:
    """One paragraph every role reads before its task; keep it about the field."""
    return (
        "LAB NOTEBOOK VERTICAL: measure first, then write. A number without a "
        "saved raw output and a rerun command is not a result. The Reviewer "
        "checks the notebook entry against the raw files, not against the "
        "Engineer's summary."
    )
