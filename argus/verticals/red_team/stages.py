"""Authorized offensive security: engagement-gated exploitation, evidence-bound findings.

The authorization gate is a contract-level requirement, not a Manager
discretion call: ``authorize`` cannot close without a machine-readable
``SECURITY_ENGAGEMENT.json`` (see :mod:`.engagement`), the deterministic stage
gate re-checks it on every completion attempt, and the protected checklist ids
below cannot be edited away by a mission that finds them inconvenient.

Evidence-by-canary is the default expectation carried by the checklists and
skills: proving access means a harmless demonstrated action — reading a
planted marker, listing a directory — never a destructive one, unless the
operator has explicitly enabled the matching impact flag.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem
from .engagement import engagement_issues
from .findings import FINDINGS_FILE, findings_issues

STAGE_ORDER = ("authorize", "attack", "report")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
CHECKLIST_OPTIONAL_STAGES: tuple[str, ...] = ()
WORKFLOW_MODE = "staged"
VERIFICATION_STAGE_PROFILES = {
    "authorize": "explore",
    "attack": "develop",
    "report": "certify",
}
MISSION_KIND = "software"
REQUIRE_INDEPENDENT_REVIEW = True
completion_gate = "none"
COMPLETION_CONTRACT_VERSION = 1

STAGE_PRIMARY_DELIVERABLES = {
    "report": (FINDINGS_FILE,),
}

# Irreducible floor, modeled on software's build gate: a mission may extend
# this checklist but may never delete or weaken these ids.
PROTECTED_ITEM_IDS = frozenset({
    "authorize.declaration-valid",
    "attack.evidence-per-finding",
    "report.reproduced",
})

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "authorize": (
        ChecklistItem(
            id="authorize.declaration-valid",
            statement=(
                "The engagement declaration at SECURITY_ENGAGEMENT.json parses and "
                "is current: the operator is named, the window covers today, every "
                "target this mission will touch is covered by a declared entry "
                "(kind source_audit or host, with ref and authorization), every "
                "declared method is a known category, and every impact flag those "
                "methods require is explicitly enabled by the operator. A missing "
                "key, an expired window, an uncovered mission target, or an unset "
                "impact flag blocks this stage — the Manager cannot fold the gate "
                "away. Demonstrating access in this stage means a harmless canary "
                "action (read a planted marker, list a directory), never a "
                "destructive one."
            ),
            evidence_hint=(
                "the engagement gate output for SECURITY_ENGAGEMENT.json and the "
                "operator's authorization references"
            ),
        ),
    ),
    "attack": (
        ChecklistItem(
            id="attack.evidence-per-finding",
            statement=(
                "Every vulnerability claim carries a reproduction artifact: a PoC "
                "script plus the raw tool output that demonstrates it, stored under "
                "the project's evidence/ directory and hash-bound to the finding in "
                "FINDINGS.json. A recall-only suspicion or a static scanner hit "
                "without a local reproduction ships as unverified, never as a "
                "finding. Stay inside the declared window, targets, methods, and "
                "impact flags; prove access by canary unless the operator has "
                "explicitly enabled the matching impact flag."
            ),
            evidence_hint=(
                "the evidence/ artifacts and their sha256 bindings for every finding"
            ),
        ),
    ),
    "report": (
        ChecklistItem(
            id="report.reproduced",
            statement=(
                "The Reviewer independently re-runs at least the reproduction of "
                "every shipped finding against the declared target and records the "
                "outcome; a finding certified from the Engineer's self-report alone "
                "fails. Severity is calibrated honestly (P0: unauthenticated RCE or "
                "auth bypass on a default deployment; P1: authenticated RCE, "
                "unauthenticated memory-safety violation, DoS-by-default, or default "
                "sensitive-data exposure, within the declared impact flags) and a "
                "null result is reported as such rather than inflated."
            ),
            evidence_hint=(
                "the recorded reproduction outputs for each finding and the "
                "reviewer's independent re-run results"
            ),
        ),
    ),
}


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    """Deterministic pre-completion gate: authorize needs the declaration,
    report needs a valid FINDINGS.json deliverable."""
    stage_name = (stage or "").strip().lower()
    if stage_name not in STAGE_ORDER:
        return (f"unknown red_team stage {stage_name!r}",)
    if stage_name == "authorize":
        return engagement_issues(project_root)
    if stage_name == "report":
        return findings_issues(project_root)
    return ()


def role_banner(role: str) -> str:
    common = (
        "RED TEAM VERTICAL: authorized offensive security only. The authorize "
        "stage cannot close without a valid SECURITY_ENGAGEMENT.json at the "
        "project root naming the operator, a current window, every target with "
        "its authorization, the allowed methods, and the impact flags the "
        "operator has explicitly enabled. Attack only declared targets with "
        "declared methods, prove access by canary — a harmless demonstrated "
        "action — never by destruction, and bind every finding to reproduced "
        "evidence (PoC script plus raw tool output, hash-bound in FINDINGS.json). "
        "An undemonstrated suspicion is unverified, not a finding."
    )
    if role == "reviewer":
        return (
            common
            + " Independently re-run each shipped finding's reproduction before "
            "certifying report: self-certified findings fail, and severity must "
            "match what the reproduction actually demonstrates."
        )
    return common


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "COMPLETION_CONTRACT_VERSION",
    "PROTECTED_ITEM_IDS",
    "REQUIRE_INDEPENDENT_REVIEW",
    "STAGE_ORDER",
    "STAGE_PRIMARY_DELIVERABLES",
    "VERIFICATION_STAGE_PROFILES",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
    "stage_completion_issues",
]
