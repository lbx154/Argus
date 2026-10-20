"""What evidence the current mission needs, mapped from its stage.

A project-level acceptance bar and the evidence needed for one bounded mission
are different decisions. Treating them as one makes every early increment face
the final project bar. The active vertical maps each of its stages to a
profile — ``explore`` | ``develop`` | ``certify`` — and a final-submission
scope is always ``certify``. A lighter profile changes *what has to be
delivered*, never *whether the evidence is real*. A stage the vertical did not
map is reported unresolved rather than silently read as the strictest or the
loosest bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "VERIFICATION_PROFILES",
    "EffectivePolicy",
    "policy_line",
    "profile_for_stage",
    "resolve_policy",
]

VERIFICATION_PROFILES = ("explore", "develop", "certify")

#: One line per profile, for the prompt. Deliberately terse: the reviewer
#: prompt has a hard character budget.
_PROFILE_MEANING = {
    "explore": "is the premise real, testable, and worth the next probe",
    "develop": "does the implementation, comparison, and claim scope hold",
    "certify": "full acceptance-claim coverage with release-ready evidence",
}

_FINAL_SCOPES = frozenset({"final_submission"})


def profile_for_stage(
    stage: Any,
    stage_profiles: dict[str, str] | None = None,
) -> str | None:
    """Map a stage through a vertical-owned profile table."""
    stage_text = str(stage or "").strip().lower()
    if not stage_text:
        return None
    profile = str((stage_profiles or {}).get(stage_text) or "").strip().lower()
    return profile if profile in VERIFICATION_PROFILES else None


@dataclass(frozen=True)
class EffectivePolicy:
    profile: str
    resolved: bool = True


def resolve_policy(
    *,
    scope: Any = None,
    stage: Any = None,
    stage_profiles: dict[str, str] | None = None,
) -> EffectivePolicy:
    """Resolve the profile in force for the current mission."""
    if str(scope or "").strip().lower() in _FINAL_SCOPES:
        return EffectivePolicy("certify")
    mapped = profile_for_stage(stage, stage_profiles)
    if mapped is not None:
        return EffectivePolicy(mapped)
    return EffectivePolicy("develop", resolved=False)


def policy_line(policy: EffectivePolicy) -> str:
    """One-line policy statement for a budgeted role prompt."""
    meaning = _PROFILE_MEANING[policy.profile]
    suffix = " (unresolved)" if not policy.resolved else ""
    return f"`{policy.profile}`{suffix} — {meaning}"
