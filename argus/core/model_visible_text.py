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

# Agent CLIs and Argus mask credentials in what a role sees, so a file view can
# show ``f"******"`` where the source says ``f"Bearer {token}"``. Neither role
# can see past the mask; arguing over it from either side cannot converge (one
# task spent five rounds and most of its cost that way). Both roles keep their
# judgment; what changes is the evidence they reach for: an executable check
# whose outcome does not depend on reading the masked span.
#
# The Reviewer has the Engineer's tools, so it reruns the check itself; the
# Engineer's word alone is never the evidence.

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
REVIEW_EVIDENCE_RULE = (
    "You can run commands: rerun the decisive check yourself; " + _NOT_EVIDENCE
    + " If the sandbox blocks the rerun (write or network denied), judge from "
    "recorded runs and your own reading instead. " + _MASKED_DISPLAY
)


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
    (?:hash|checksum|digest|fingerprint)
    [a-z0-9_.-]*\b
    \s*(?::|=|\bis\b)?\s*
    [`\"']?(?:sha(?:-?256)?:)?{_HEX_VALUE}[`\"']?
    """
)
_BARE_LONG_HEX_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32,128}(?![0-9a-f])")


def sanitize_model_visible_text(value: object) -> str:
    """Redact opaque integrity values before text reaches a role model."""
    text = str(value or "")
    text = _LABELED_IDENTIFIER_RE.sub("<machine-integrity-metadata omitted>", text)
    text = _PREFIXED_DIGEST_RE.sub("<machine-integrity-metadata omitted>", text)
    return _BARE_LONG_HEX_RE.sub("<machine-integrity-metadata omitted>", text)


__all__ = [
    "FIXTURE_EVIDENCE_RULE",
    "LABELLED_FIXTURE_CARVE_OUT",
    "MASKED_DISPLAY_ENGINEER_RULE",
    "REVIEW_EVIDENCE_RULE",
    "sanitize_model_visible_text",
]
