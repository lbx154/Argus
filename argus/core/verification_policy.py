"""Generic exploration and per-mission verification policy.

A project-level acceptance bar and the evidence needed for one bounded mission
are different decisions. Treating them as one makes every early increment face
the final project bar, while silently choosing a weaker bar would compromise
completion. This module separates those axes without owning any vertical's
stage names or evidence rules:

``ExplorationPosture``
    How much budget to spend on non-obvious, high-risk, high-upside routes.
    ``conservative`` | ``balanced`` | ``frontier``.

``VerificationProfile``
    What evidence the *current* mission needs to be complete.
    ``explore`` | ``develop`` | ``certify``, or ``adaptive`` to derive it from
    the stage.

A bolder posture must never mean a laxer conclusion, and a lighter profile must
never mean weaker facts. What a profile changes is *what has to be delivered*,
never *whether the evidence is real*. Concrete integrity checks and stage
mappings belong to the active vertical.

Resolution order is deliberate and fail-visible:

1. A final-submission scope forces ``certify``. Nothing overrides this.
2. An explicit operator profile wins over the stage default.
3. ``adaptive`` maps the current stage to a profile.
4. Anything unresolved says so, rather than silently picking the strictest or
   the loosest reading.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .pipeline_state import read_pipeline_state, write_pipeline_state

__all__ = [
    "DEFAULT_POSTURE",
    "DEFAULT_PROFILE",
    "EXPLORATION_POSTURES",
    "PROFILE_ORDER",
    "VERIFICATION_PROFILES",
    "EffectivePolicy",
    "lowers_the_bar",
    "normalize_posture",
    "normalize_profile",
    "policy_line",
    "profile_for_stage",
    "resolve_policy",
    "stored_policy",
]

EXPLORATION_POSTURES = ("conservative", "balanced", "frontier")
VERIFICATION_PROFILES = ("explore", "develop", "certify")
#: What an operator may configure; ``adaptive`` derives from the stage.
CONFIGURABLE_PROFILES = VERIFICATION_PROFILES + ("adaptive",)

DEFAULT_POSTURE = "balanced"
DEFAULT_PROFILE = "adaptive"

#: Increasing strictness. Used to detect when a change lowers the bar.
PROFILE_ORDER = {"explore": 0, "develop": 1, "certify": 2}

#: One line per profile, for the prompt. Deliberately terse: the reviewer
#: prompt has a hard character budget, and a rule the code enforces does not
#: need to be restated in prose.
_PROFILE_MEANING = {
    "explore": "is the premise real, testable, and worth the next probe",
    "develop": "does the implementation, comparison, and claim scope hold",
    "certify": "full acceptance-claim coverage with release-ready evidence",
}

_FINAL_SCOPES = frozenset({"final_submission"})


def normalize_posture(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in EXPLORATION_POSTURES else None


def normalize_profile(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in CONFIGURABLE_PROFILES else None


def profile_for_stage(
    stage: Any,
    stage_profiles: dict[str, str] | None = None,
) -> str | None:
    """Map a stage through a vertical-owned profile table."""
    stage_text = str(stage or "").strip().lower()
    if not stage_text:
        return None
    table = stage_profiles or {}
    profile = str(table.get(stage_text) or "").strip().lower()
    return profile if profile in VERIFICATION_PROFILES else None


def lowers_the_bar(current: str, proposed: str) -> bool:
    """Whether moving to *proposed* weakens what completion requires.

    Lowering the bar changes what "done" means for the project, so it belongs
    to the operator. Raising it does not need permission.
    """
    left = PROFILE_ORDER.get(current)
    right = PROFILE_ORDER.get(proposed)
    if left is None or right is None:
        return False
    return right < left


@dataclass(frozen=True)
class EffectivePolicy:
    """The resolved policy plus where each part came from."""

    posture: str
    profile: str
    configured_profile: str
    source: str
    stage: str | None = None
    vertical: str | None = None
    target_level: str | None = None
    resolved: bool = True
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "posture": self.posture,
            "profile": self.profile,
            "configured_profile": self.configured_profile,
            "source": self.source,
            "stage": self.stage,
            "vertical": self.vertical,
            "target_level": self.target_level,
            "resolved": self.resolved,
            "note": self.note,
        }


def stored_policy(project_root: object) -> dict[str, Any]:
    """Read the Manager-owned policy fields. Missing file → empty."""
    try:
        payload = read_pipeline_state(project_root)
    except (OSError, ValueError):
        return {}
    stored: dict[str, Any] = {}
    posture = normalize_posture(payload.get("exploration_posture"))
    profile = normalize_profile(payload.get("verification_profile"))
    if posture is not None:
        stored["exploration_posture"] = posture
    if profile is not None:
        stored["verification_profile"] = profile
    return stored


def resolve_policy(
    project_root: object,
    *,
    scope: Any = None,
    stage: Any = None,
    vertical: Any = None,
    target_level: Any = None,
    stage_profiles: dict[str, str] | None = None,
) -> EffectivePolicy:
    """Resolve the policy in force for the current mission."""
    stored = stored_policy(project_root)
    posture = stored.get("exploration_posture") or DEFAULT_POSTURE
    configured = stored.get("verification_profile") or DEFAULT_PROFILE
    stage_text = str(stage or "").strip().lower() or None
    vertical_text = str(vertical or "").strip().lower() or None
    target_text = str(target_level or "").strip().lower() or None

    common = {
        "posture": posture,
        "configured_profile": configured,
        "stage": stage_text,
        "vertical": vertical_text,
        "target_level": target_text,
    }

    # 1. A final submission is certified regardless of anything else. Bolder
    #    exploration earlier never buys a laxer final claim.
    if str(scope or "").strip().lower() in _FINAL_SCOPES:
        return EffectivePolicy(profile="certify", source="final_scope", **common)

    # 2. An explicit operator choice.
    if configured in VERIFICATION_PROFILES:
        return EffectivePolicy(profile=configured, source="operator", **common)

    # 3. adaptive → stage.
    mapped = profile_for_stage(stage_text, stage_profiles)
    if mapped is not None:
        return EffectivePolicy(profile=mapped, source="stage", **common)

    # 4. Unresolved. Say so instead of silently choosing; a silent strictest
    #    reading is the mis-kill this module exists to remove, and a silent
    #    loosest one would weaken certification.
    return EffectivePolicy(
        profile="develop",
        source="unresolved",
        resolved=False,
        note=(
            f"no profile for stage={stage_text!r} vertical={vertical_text!r}; "
            "using develop and reporting it unresolved"
        ),
        **common,
    )


def policy_line(policy: EffectivePolicy) -> str:
    """One-line policy statement for a role prompt.

    Kept short on purpose: the reviewer prompt is budgeted, and the integrity
    rules this line refers to are enforced in code.
    """
    meaning = _PROFILE_MEANING.get(policy.profile, "")
    suffix = " (unresolved)" if not policy.resolved else ""
    return f"`{policy.profile}`{suffix} — {meaning}" if meaning else f"`{policy.profile}`{suffix}"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

class PolicyConfirmationRequired(RuntimeError):
    """Raised when a change would weaken completion without operator consent."""


def set_policy(
    project_root: object,
    *,
    posture: Any = None,
    profile: Any = None,
    confirmed: bool = False,
    stage: Any = None,
    vertical: Any = None,
    stage_profiles: dict[str, str] | None = None,
) -> EffectivePolicy:
    """Persist policy changes into the Manager-owned pipeline state.

    Raising the bar applies immediately. Lowering it changes what "done" means
    for the project, so it needs the operator to say so — an Engineer or
    Reviewer must not be able to make its own completion easier.
    """
    try:
        payload = read_pipeline_state(project_root)
    except (OSError, ValueError):
        payload = {}

    if profile is not None:
        new_profile = normalize_profile(profile)
        if new_profile is None:
            raise ValueError(
                f"unknown verification profile {profile!r}; "
                f"expected one of {', '.join(CONFIGURABLE_PROFILES)}"
            )
        before = resolve_policy(
            project_root,
            stage=stage,
            vertical=vertical,
            stage_profiles=stage_profiles,
        )
        after_effective = (
            new_profile
            if new_profile in VERIFICATION_PROFILES
            else (profile_for_stage(stage, stage_profiles) or before.profile)
        )
        if not confirmed and lowers_the_bar(before.profile, after_effective):
            raise PolicyConfirmationRequired(
                f"moving verification from {before.profile!r} to {after_effective!r} "
                "lowers what project completion requires; this is an operator "
                "decision — re-issue with confirmation"
            )
        payload["verification_profile"] = new_profile

    if posture is not None:
        new_posture = normalize_posture(posture)
        if new_posture is None:
            raise ValueError(
                f"unknown exploration posture {posture!r}; "
                f"expected one of {', '.join(EXPLORATION_POSTURES)}"
            )
        payload["exploration_posture"] = new_posture

    write_pipeline_state(project_root, payload)
    return resolve_policy(
        project_root,
        stage=stage,
        vertical=vertical,
        stage_profiles=stage_profiles,
    )
