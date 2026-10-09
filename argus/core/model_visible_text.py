"""Keep opaque machine identifiers out of model-facing semantic judgment.

Checksums and content digests are useful to host code for cache keys, atomic
identity, corruption detection, and deduplication.  Their values are not useful
semantic evidence for an LLM: comparing two opaque strings cannot establish
correctness, freshness, provenance, or task completion.

This module therefore owns the boundary between machine-only integrity metadata
and text shown to or produced by a role agent.
"""

from __future__ import annotations

import re

MODEL_INTEGRITY_BOUNDARY = """## Opaque integrity IDs
Checksums, digests, fingerprints, and commit IDs are host-only. Never inspect,
quote, compare, or use their values as evidence. Differences cannot prove
freshness, correctness, provenance, completion, contradiction, or justify
`continue`, `blocked`, or `replan_requested`. Use content, timestamps, tests,
metrics, and readable provenance; ignore lower-level identifier adjudication.
"""

# Agent CLIs and Argus mask credentials in what a role sees, so a file view can
# show ``f"******"`` where the source says ``f"Bearer {token}"``. Neither role
# can see past the mask; arguing over it from either side cannot converge (one
# task spent five rounds and most of its cost that way). Both roles keep their
# judgment; what changes is the evidence they reach for: an executable check
# whose outcome does not depend on reading the masked span.
#
# Which check the Reviewer can reach depends on its tools and on what the host
# records. A Reviewer that can run commands reruns it. Most Reviewer surfaces
# only read and search; told to rerun anyway, one A/B arm's Reviewer refused
# the work in 13 verdicts and asked for an execution surface no role could
# grant. A read-only Reviewer weighs what the host recorded of a run when the
# Engineer's backend reports exit codes. That record is what the agent CLI's
# stream reported, not proof, so the Reviewer still reads what the command ran. Where the
# backend reports none, the Reviewer asks for evidence in a form it can read.
# No form accepts the Engineer's word alone.
EVIDENCE_EXECUTE = "execute"
EVIDENCE_RECORDED = "recorded"
EVIDENCE_READ = "read"

# A check can also be impossible in this environment rather than missing: the
# resource it needs exists only when the work is graded or deployed (one task's
# event-feed token was supplied only during the grader's own commands). Asked
# for anyway, a Reviewer that agreed the token was unavailable still made
# completion depend on that run: 15 rounds over 5 missions. "Impossible" has to
# be grounded, though: only a statement in the task, its packet or the
# environment makes it so, and the Reviewer quotes it (the host checks the
# quote). Leaving the check unverified changes the acceptance standard, so it
# is the operator's call, or the Manager's in a run with no operator; the
# Reviewer approves only on that acceptance and names the residual risk.
#
# A local fixture of the unreachable interface is the usual best evidence, and
# also the easiest to fake: a fixture that stubs the code under test, or
# invents a payload shape, proves only that the stub agrees with itself.
_FIXTURE_CONDITIONS = (
    "follows the documented interface or schema, runs the real entry point "
    "(not a stub of the code under test) and is labelled a test fixture"
)
FIXTURE_EVIDENCE_RULE = f"A test fixture counts only if it {_FIXTURE_CONDITIONS}."
# The same faithfulness bar wherever a role is told a fixture may stand in for
# an interface it cannot reach: the no-operator assumption rule (Engineer,
# Planner, Manager) and the OperatorContext no-operator line.
LABELLED_FIXTURE_CARVE_OUT = (
    "A test fixture of an interface this environment cannot reach (the task, "
    "packet or environment says its access exists only at grading or deploy "
    f"time) is test evidence, not a substitute, and counts only if it "
    f"{_FIXTURE_CONDITIONS}; it never ships in what is delivered."
)
_MASKED_DISPLAY = (
    "`******` or `<REDACTED:…>` is display masking that proves neither a defect "
    "nor a fix; settle it the same way, with a rerunnable assertion on a "
    "placeholder value. A check is impossible, not missing, only if the task, "
    "packet or environment says it needs what exists only at grading or deploy "
    "time; see unverifiable."
)
_NOT_EVIDENCE = "the Engineer's cited claim alone is not evidence."
REVIEW_EVIDENCE_RULE_EXECUTING = (
    "You can run commands: rerun the decisive check yourself; " + _NOT_EVIDENCE
    + " If the sandbox blocks the rerun (write or network denied) or your "
    "command tool is unavailable, judge from recorded runs and your own reading "
    "instead. " + _MASKED_DISPLAY
)
REVIEW_EVIDENCE_RULE_READ_ONLY = (
    "You can read and search, not run commands; never ask for an execution tool. "
    "Judge from host-recorded runs and your own reading of code and tests; "
    + _NOT_EVIDENCE + " A record shows what the agent CLI reported, not proof: "
    "read the test or script it ran, and weigh pipes, `|| true`, test selection, "
    "checks edited this round, and conflicting or unverified results. "
    "An Engineer-written check counts only once you have read it. If that cannot "
    "settle it, ask the Engineer for one named check whose run the host records. "
    + _MASKED_DISPLAY
)
REVIEW_EVIDENCE_RULE_UNRECORDED = (
    "You can read and search, not run commands; never ask for an execution tool. "
    "Judge from your own reading of code and tests and the Engineer's evidence; "
    + _NOT_EVIDENCE + " If that cannot settle it, ask for the decisive evidence "
    "in a form you can read, such as an output file in the workspace, and read "
    "what produced it. " + _MASKED_DISPLAY
)
_RULES = {
    EVIDENCE_EXECUTE: REVIEW_EVIDENCE_RULE_EXECUTING,
    EVIDENCE_RECORDED: REVIEW_EVIDENCE_RULE_READ_ONLY,
    EVIDENCE_READ: REVIEW_EVIDENCE_RULE_UNRECORDED,
}


def review_evidence_rule(mode: str) -> str:
    """The Reviewer's evidence rule for its tools and what the host records.

    ``mode`` is :data:`EVIDENCE_EXECUTE`, :data:`EVIDENCE_RECORDED` or
    :data:`EVIDENCE_READ`; anything else gets the most cautious read-only form.
    """
    return _RULES.get(mode, REVIEW_EVIDENCE_RULE_UNRECORDED)


MASKED_DISPLAY_ENGINEER_RULE = (
    "If the review disputes text a display masks (`******`, `<REDACTED:…>`), "
    "restating the fix cannot help: add a rerunnable test asserting the behavior "
    "with a harmless placeholder value, run it, and cite its command and result."
)

_HEX_VALUE = r"[0-9a-f]{7,128}"
_PREFIXED_DIGEST_RE = re.compile(rf"(?i)\b(?:sha(?:-?1|-?256|-?512)?|md5):{_HEX_VALUE}\b")
_LABELED_IDENTIFIER_RE = re.compile(
    rf"""(?ix)
    \b[a-z0-9_.-]*
    (?:sha(?:-?256)?|hash|checksum|digest|fingerprint|commit(?:[_ -]?id)?|revision)
    [a-z0-9_.-]*\b
    \s*(?::|=|\bis\b)?\s*
    [`\"']?(?:sha(?:-?256)?:)?{_HEX_VALUE}[`\"']?
    """
)
_BARE_LONG_HEX_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32,128}(?![0-9a-f])")
_INTEGRITY_TERM_RE = re.compile(
    r"(?i)\b(?:sha(?:-?256)?|hash(?:es|ed|ing)?|checksum|digest|fingerprint)\b"
    r"|哈希|校验和|摘要值"
)
_INTEGRITY_JUDGMENT_RE = re.compile(
    r"(?i)\b(?:match(?:es|ed|ing)?|mismatch(?:es|ed)?|stale|fresh|same|different|"
    r"changed?|drift(?:ed|ing)?|contradict(?:s|ed|ory)?|invalid|missing|verify|"
    r"verified|passes?|passed|fails?|failed|refresh(?:ed)?)\b"
    r"|一致|不一致|陈旧|过期|匹配|不同|变化|漂移|校验|验证|冲突|失效"
)
_MATERIAL_BLOCKER_RE = re.compile(
    r"(?i)\b(?:incomplete|missing|required|must|need(?:s|ed)?|fix|repair|reject|"
    r"fail(?:s|ed|ure)?|wrong|unsatisfied|unresolved|cannot|can't|blocker|"
    r"problem|issue|gap|lacks?|unable)\b"
    r"|\b(?:could\s+not|was\s+not\s+able)\b"
    r"|not\s+(?:done|complete|completed|satisfied|verified)"
    r"|does\s+not\s+(?:meet|pass)"
    r"|未完成|缺失|必须|需要|修复|失败|未满足|阻塞|问题"
)


def sanitize_model_visible_text(value: object) -> str:
    """Redact opaque integrity values before text reaches a role model."""
    text = str(value or "")
    text = _LABELED_IDENTIFIER_RE.sub("<machine-integrity-metadata omitted>", text)
    text = _PREFIXED_DIGEST_RE.sub("<machine-integrity-metadata omitted>", text)
    return _BARE_LONG_HEX_RE.sub("<machine-integrity-metadata omitted>", text)


def contains_integrity_judgment(value: object) -> bool:
    """Return whether text asks a semantic verdict from opaque identifiers."""
    text = str(value or "")
    return bool(_INTEGRITY_TERM_RE.search(text) and _INTEGRITY_JUDGMENT_RE.search(text))


def sanitize_model_judgment_text(value: object) -> str:
    """Remove identifier-based verdict clauses and redact remaining values."""
    text = str(value or "").strip()
    if not text:
        return ""
    units = re.split(r"(?<=[.!?。！？;；])\s+|\n+", text)
    kept: list[str] = []
    for unit in units:
        cleaned = unit.strip()
        if not cleaned:
            continue
        if _INTEGRITY_TERM_RE.search(cleaned) and _INTEGRITY_JUDGMENT_RE.search(cleaned):
            continue
        kept.append(sanitize_model_visible_text(cleaned))
    return " ".join(kept).strip()


def has_material_blocker(value: object) -> bool:
    """Return whether sanitized prose still names a non-integrity blocker."""
    return bool(_MATERIAL_BLOCKER_RE.search(str(value or "")))


__all__ = [
    "EVIDENCE_EXECUTE",
    "EVIDENCE_READ",
    "EVIDENCE_RECORDED",
    "FIXTURE_EVIDENCE_RULE",
    "LABELLED_FIXTURE_CARVE_OUT",
    "MASKED_DISPLAY_ENGINEER_RULE",
    "MODEL_INTEGRITY_BOUNDARY",
    "REVIEW_EVIDENCE_RULE_EXECUTING",
    "REVIEW_EVIDENCE_RULE_READ_ONLY",
    "REVIEW_EVIDENCE_RULE_UNRECORDED",
    "contains_integrity_judgment",
    "has_material_blocker",
    "review_evidence_rule",
    "sanitize_model_judgment_text",
    "sanitize_model_visible_text",
]
