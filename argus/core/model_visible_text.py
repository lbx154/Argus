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
a handoff. Use content, timestamps, tests,
metrics, and readable provenance; ignore lower-level identifier adjudication.
"""

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


def sanitize_model_visible_text(value: object) -> str:
    """Redact opaque integrity values before text reaches a role model."""
    text = str(value or "")
    text = _LABELED_IDENTIFIER_RE.sub("<machine-integrity-metadata omitted>", text)
    text = _PREFIXED_DIGEST_RE.sub("<machine-integrity-metadata omitted>", text)
    return _BARE_LONG_HEX_RE.sub("<machine-integrity-metadata omitted>", text)


__all__ = [
    "FIXTURE_EVIDENCE_RULE",
    "LABELLED_FIXTURE_CARVE_OUT",
    "MODEL_INTEGRITY_BOUNDARY",
    "sanitize_model_visible_text",
]
