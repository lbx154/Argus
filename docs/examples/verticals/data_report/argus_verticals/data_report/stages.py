"""Stages, checklist and skills for the data_report vertical."""
from pathlib import Path

from argus.skills.stage_machine import ChecklistItem

ARGUS_VERTICAL_API_VERSION = 1
VERTICAL_PURPOSE = (
    "Turn one tabular dataset (CSV, Excel, Parquet) into a short analysis report "
    "whose every number can be recomputed from a script in the project."
)
VERTICAL_ROUTING_PATH = ("analytics", "data", "report")
VERTICAL_SKILL_PARENTS = ()
VERTICAL_SKILLS = Path(__file__).resolve().parent / "skills"

CHECKLIST_STAGE_ORDER = ("understand", "analyze", "report")
CHECKLIST_ITEMS = {
    "understand": (
        ChecklistItem(
            "understand.data_profile",
            "The dataset's rows, columns, types, missing values and obvious errors are described from the real file.",
            "a profiling script and its saved output",
        ),
    ),
    "analyze": (
        ChecklistItem(
            "analyze.reproducible_numbers",
            "Every number the report will quote is produced by a script that runs end to end on the original file.",
            "the analysis script, the exact command, and its saved output",
        ),
    ),
    "report": (
        ChecklistItem(
            "report.claims_traced",
            "Each claim in the report names the number, the script output it comes from, and its limits.",
            "report.md with a reproduction section",
        ),
    ),
}
completion_gate = "none"
# One dataset and one report is a bounded analyze-check-revise task, so it keeps
# the framework defaults, written out: the progress window opens after round 12,
# an explicit progress judgement is required from round 24, and four consecutive
# no-progress verdicts end the mission.
ROUND_POLICY = {
    "stall_threshold": 4,
    "no_progress_threshold": 2,
    "soft_round_limit": 12,
    "hard_escalate_rounds": 24,
}


def role_banner(role: str) -> str:
    common = "DATA REPORT: every number must be recomputable from a script in this project."
    if role == "reviewer":
        return common + " Rerun the scripts and compare their output with the report before approving."
    return common
