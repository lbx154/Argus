"""Defensive security: triage findings, remediate with regression evidence, verify the loop closes.

The remediation floor inherits software delivery's irreducible checks (the
build and the executed tests), namespaced to this vertical, and adds the
fail-before/pass-after regression requirement: a fix that cannot show a test
failing on the pre-patch code is not defense evidence. ``verify`` closes the
loop: replay each addressed finding's PoC and show it blocked, fire any
delivered detection rule on the recorded PoC, and leave a hardening summary a
reader can reconstruct the defense loop from.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem
from ..red_team.findings import findings_issues

STAGE_ORDER = ("triage", "remediate", "verify")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
CHECKLIST_OPTIONAL_STAGES: tuple[str, ...] = ()
WORKFLOW_MODE = "staged"
VERIFICATION_STAGE_PROFILES = {
    "triage": "explore",
    "remediate": "develop",
    "verify": "certify",
}
MISSION_KIND = "software"
GROUND_BEFORE_HANDOFF = True
REQUIRE_INDEPENDENT_REVIEW = True
completion_gate = "none"
COMPLETION_CONTRACT_VERSION = 1

# Defense inherits offense's playbooks: the red-team skills are seeded before
# blue_team's own (the built-in path of the VERTICAL_SKILL_PARENTS mechanism,
# resolved through ``verticals._base.builtin_skill_parents``).
VERTICAL_SKILL_PARENTS = ("red_team",)

HARDENING_SUMMARY_FILE = "HARDENING_SUMMARY.md"

STAGE_PRIMARY_DELIVERABLES = {
    "verify": (HARDENING_SUMMARY_FILE,),
}

# Irreducible floor: software's build/test semantics namespaced to this
# vertical, plus the defense-loop items. A mission may extend this checklist
# but may never delete or weaken these ids.
PROTECTED_ITEM_IDS = frozenset({
    "triage.findings-triaged",
    "remediate.builds",
    "remediate.tests-executed",
    "remediate.fail-before-pass-after",
    "verify.poc-blocked",
    "verify.detection-fires",
    "verify.hardening-summary",
})

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "triage": (
        ChecklistItem(
            id="triage.findings-triaged",
            statement=(
                "FINDINGS.json at the project root is consumed and triaged: "
                "duplicates (same id, or same target and title) collapse, findings "
                "rank by severity (P0 first), and any finding whose record is not "
                "locally reproducible — missing PoC artifact, missing raw output, "
                "broken sha256 binding, or no reproduction command — is dropped to "
                "unverified and never enters remediation. An external scanner export "
                "is mapped into the same FINDINGS.json shape before triage, and an "
                "unverified finding's drop reason is recorded, not silently lost."
            ),
            evidence_hint=(
                "the triage result: actionable findings ranked, unverified drops "
                "with reasons, collapsed duplicates"
            ),
        ),
    ),
    "remediate": (
        ChecklistItem(
            id="remediate.builds",
            statement=(
                "The changed code compiles / type-checks cleanly in the task's own "
                "toolchain, verified in THIS round by an independent build or "
                "type-check the Reviewer ran or saw run to completion. A build "
                "error anywhere the change is reachable — an undefined symbol, a "
                "wrong argument count, a mismatched type — is a hard failure "
                "regardless of how correct the fix reads."
            ),
            evidence_hint=(
                "the exact build/type-check command and its output "
                "(e.g. `go build ./...`, `mvn -q compile`, `python -m compileall`)"
            ),
        ),
        ChecklistItem(
            id="remediate.tests-executed",
            statement=(
                "The tests claimed to pass were actually executed this round and "
                "reported passing, not merely asserted. A test target that failed "
                "to build, collected zero tests, or was never run does not count "
                "as a passing test."
            ),
            evidence_hint=(
                "the test command and the result line showing tests ran "
                "(counts, PASS/FAIL, or the runner's summary)"
            ),
        ),
        ChecklistItem(
            id="remediate.fail-before-pass-after",
            statement=(
                "Every fix ships a regression test derived from the finding's "
                "recorded reproduction (the PoC or the recorded failing behavior), "
                "never from the patch's own behavior: run it against the pre-patch "
                "code and record the failure, then against the fix and record the "
                "pass. A test that cannot be shown to fail before the fix is not "
                "regression evidence, and a probe whose expected value was copied "
                "from the patch fails this item."
            ),
            evidence_hint=(
                "the regression test, its pre-patch failure output, and its "
                "post-patch pass output"
            ),
        ),
    ),
    "verify": (
        ChecklistItem(
            id="verify.poc-blocked",
            statement=(
                "Replay each addressed finding's recorded reproduction command "
                "against the fixed target and show it is now blocked: the exploit "
                "fails cleanly and the vulnerability class is closed. A finding "
                "claimed fixed without a replayed PoC fails, and findings left "
                "unaddressed are listed explicitly with their status — never "
                "silently dropped."
            ),
            evidence_hint=(
                "the replayed reproduction output per finding, before-fix and "
                "after-fix"
            ),
        ),
        ChecklistItem(
            id="verify.detection-fires",
            statement=(
                "If a detection rule (signature, alert, or log check) is delivered "
                "for a finding, run it against the recorded PoC evidence and show "
                "it fires on the pre-fix behavior. For findings that ship no "
                "detection rule, record that none was delivered — do not leave the "
                "case silent."
            ),
            evidence_hint=(
                "the detection rule output against the recorded PoC, or the "
                "recorded no-rule status per finding"
            ),
        ),
        ChecklistItem(
            id="verify.hardening-summary",
            statement=(
                "HARDENING_SUMMARY.md at the project root records the before/after "
                "comparison for the defense loop: each addressed finding, the "
                "pre-fix behavior, the change, the replayed PoC outcome after the "
                "fix, and the detection results — so a reader can reconstruct what "
                "was fixed and how it was proven without re-deriving it."
            ),
            evidence_hint=(
                "the hardening summary content with per-finding before/after results"
            ),
        ),
    ),
}


def _hardening_summary_issues(project_root: Path) -> tuple[str, ...]:
    path = Path(str(project_root)) / HARDENING_SUMMARY_FILE
    if not path.is_file():
        return (
            f"{HARDENING_SUMMARY_FILE} is missing at the project root: the verify "
            "stage's hardening summary (per-finding before/after comparison, PoC "
            "replay outcomes, detection results) cannot close the loop without it",
        )
    try:
        if not path.read_text(encoding="utf-8").strip():
            return (f"{HARDENING_SUMMARY_FILE} is empty",)
    except OSError as exc:
        return (f"{HARDENING_SUMMARY_FILE} is unreadable: {exc}",)
    return ()


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    """Deterministic pre-completion gate: triage needs a valid findings input,
    verify needs the hardening summary deliverable."""
    stage_name = (stage or "").strip().lower()
    if stage_name not in STAGE_ORDER:
        return (f"unknown blue_team stage {stage_name!r}",)
    if stage_name == "triage":
        return findings_issues(project_root)
    if stage_name == "verify":
        return _hardening_summary_issues(project_root)
    return ()


def role_banner(role: str) -> str:
    common = (
        "BLUE TEAM VERTICAL: defensive security. Triage FINDINGS.json — collapse "
        "duplicates, rank by severity, and drop anything without a locally "
        "reproducible record to unverified; it never enters remediation. Fix "
        "each actionable finding in the repository with a regression test that "
        "demonstrably fails pre-patch and passes post-patch, and verify by "
        "replaying the finding's PoC to show it is now blocked, firing any "
        "delivered detection rule on the recorded PoC, and writing the "
        "before/after hardening summary to HARDENING_SUMMARY.md."
    )
    if role == "reviewer":
        return (
            common
            + " Re-run the PoC replays and the regression tests yourself: a fix "
            "certified from the Engineer's self-report alone fails."
        )
    return common


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "COMPLETION_CONTRACT_VERSION",
    "HARDENING_SUMMARY_FILE",
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
