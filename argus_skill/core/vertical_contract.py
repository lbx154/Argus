"""Framework-owned interface implemented by every vertical.

Core defines the contract but never imports a concrete vertical.  The vertical
loader resolves a provider and converts it once; consumers use this immutable
view instead of probing module attributes or branching on vertical names.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

VERTICAL_CONTRACT_VERSION = 1
_COMPLETION_GATES = frozenset({"none", "metric", "certified"})
_WORKFLOW_MODES = frozenset({"staged", "direct", "proportional"})
_MISSION_KINDS = frozenset({"custom", "optimize", "research", "software"})
_VERIFICATION_PROFILES = frozenset({"explore", "develop", "certify"})


class VerticalContractError(ValueError):
    """A vertical is present but does not implement the framework contract."""


def _normalize_live_search_stages(
    name: str,
    raw_stages: object,
    stage_order: tuple[str, ...],
) -> frozenset[str]:
    if isinstance(raw_stages, str) or not isinstance(
        raw_stages, (list, tuple, set, frozenset)
    ):
        raise VerticalContractError(
            f"vertical {name!r} live search stages are not a collection of stages"
        )
    declared: set[str] = set()
    for stage in raw_stages:
        if not isinstance(stage, str):
            raise VerticalContractError(
                f"vertical {name!r} live search stage {stage!r} is not a string"
            )
        normalized = stage.strip().lower()
        if not normalized:
            raise VerticalContractError(
                f"vertical {name!r} declares a blank live search stage"
            )
        declared.add(normalized)
    stages = frozenset(declared)
    unknown = sorted(stages - set(stage_order))
    if unknown:
        raise VerticalContractError(
            f"vertical {name!r} declares live search for unknown stages: "
            f"{', '.join(unknown)}"
        )
    return stages


class MissionPrelude(Protocol):
    """What a vertical's ``prepare_mission`` has to accept.

    Written as a Protocol rather than a ``Callable[...]`` alias because the
    argument *names* are the contract now: this hook is forwarded by keyword
    (see ``VerticalContract.prepare_mission``), so a provider that renames a
    parameter is a broken provider, and a bare ``Callable[..., str]`` would say
    nothing about which names it must use.

    ``mission`` is the backlog item this prelude is being built for --
    ``life.memory.BacklogItem``, annotated loosely because ``core`` is the layer
    underneath ``life`` and must not acquire an upward import for a value it
    only forwards. Verticals sit above both and are free to import the real
    type; ``verticals/math/context_projection.py`` does.
    """

    def __call__(
        self,
        *,
        stage: str,
        project_root: Path,
        state_root: Path,
        mission: Any,
    ) -> str: ...


class RolePromptFragment(Protocol):
    """Optional vertical-owned prompt text selected from structured context."""

    def __call__(
        self,
        *,
        role: str,
        operation: str,
        stage: str,
        scope: str,
        project_root: Path | None,
    ) -> str: ...


class IterationAssessmentHook(Protocol):
    """Optional vertical-owned decision at a would-be successful settlement."""

    def __call__(
        self,
        *,
        stage: str,
        scope: str,
        project_root: Path,
        state_root: Path,
        mission: Any,
        outcome: Any,
    ) -> "IterationAssessment | None": ...


@dataclass(frozen=True)
class IterationAssessment:
    """A vertical's domain-specific reason to continue or stop iteration.

    ``objective`` is non-empty only when the current result is a trusted
    optimization signal and the same backlog item should be re-armed.
    ``blocking_issues`` names integrity defects that make such optimization
    unsafe; settlement records them but never turns them into an iteration.
    ``None`` from the provider means the chartered result did not fall short.
    """

    shortfall: str
    objective: str = ""
    blocking_issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannerReviewPurchaseDecision:
    """A vertical-owned decision about a proposed review purchase."""

    defer_reason: str = ""
    discard_semantic_duplicate: bool = False
    release_stage_closing_blocker: bool = False


@dataclass(frozen=True)
class VerticalLibraryContext:
    """Core-owned inputs for optional provider-owned Skill preparation."""

    workdir: Path
    state_root: Path
    stage: str
    objective: str
    direction: str
    workflow_mode: str
    paper_mission: bool
    team_task_id: str | None
    runner: Any
    model: str | None
    emit: Callable[[dict], None]
    required_skill_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VerticalContract:
    name: str
    stage_order: tuple[str, ...]
    checklist_items: dict[str, Any]
    completion_gate: str
    mission_kind: str = "custom"
    paper_mission: bool = False
    ground_before_handoff: bool = False
    role_guidance: Callable[[str], str] | None = None
    role_prompt_fragment: RolePromptFragment | None = None
    evidence_schema: Any = None
    requires_independent_review: bool = False
    completion_contract_version: int = 0
    research_target_levels: tuple[str, ...] = ()
    workflow_mode: str = "staged"
    verification_stage_profiles: dict[str, str] | None = None
    checklist_optional_stages: frozenset[str] = frozenset()
    stage_aliases: dict[str, str] | None = None
    search_altitude: Callable[[object], str] | None = None
    mission_prelude: MissionPrelude | None = None
    library_preparer: Callable[[VerticalLibraryContext], None] | None = None
    stage_completion_validator: Callable[..., object] | None = None
    planner_task_validator: Callable[[str, Path, Any], object] | None = None
    review_purchase_policy: Callable[..., PlannerReviewPurchaseDecision] | None = None
    iteration_assessor: IterationAssessmentHook | None = None
    # Optional: records the operator's stated objective at project setup, for a
    # vertical that cannot pick a completion bar on its own. See
    # ``adopt_operator_objective``.
    operator_objective_adopter: Callable[[Path, str], object] | None = None
    stage_checks: dict[str, tuple[tuple[str, str], ...]] | None = None
    stage_primary_deliverables: dict[str, tuple[str, ...]] | None = None
    # Stages whose Engineer round runs with live web search enabled. ``None``
    # means "this vertical declares nothing", which is NOT the same as an
    # explicitly declared empty set ("never search"): the former keeps the
    # framework default, the latter overrides it off.
    engineer_live_search_stages: frozenset[str] | None = None

    @property
    def assurance_level(self) -> str:
        if self.stage_checks or self.stage_completion_validator is not None:
            return "hybrid"
        if self.checklist_optional_stages == frozenset(self.stage_order):
            return "runtime-authored"
        return "reviewer"

    def banner(self, role: str) -> str:
        if self.role_guidance is None:
            return ""
        value = self.role_guidance(role)
        return value if isinstance(value, str) else ""

    def prompt_fragment(
        self,
        *,
        role: str,
        operation: str,
        stage: str,
        scope: str,
        project_root: Path | None,
    ) -> str:
        if self.role_prompt_fragment is None:
            return ""
        value = self.role_prompt_fragment(
            role=role,
            operation=operation,
            stage=stage,
            scope=scope,
            project_root=project_root,
        )
        return value if isinstance(value, str) else ""

    def altitude(self, project_root: object) -> str:
        if self.search_altitude is None:
            return ""
        value = self.search_altitude(project_root)
        return value if isinstance(value, str) else ""

    def prepare_libraries(self, context: VerticalLibraryContext) -> None:
        if self.library_preparer is not None:
            self.library_preparer(context)

    def primary_deliverables(self, stage: str) -> tuple[str, ...]:
        return tuple((self.stage_primary_deliverables or {}).get(stage, ()))

    def live_search_stages(
        self,
        default: frozenset[str],
        *,
        preserve_configured: bool = False,
    ) -> frozenset[str]:
        """Stages in which THIS vertical's Engineer runs with live web search.

        Core owns ``default`` and never enumerates vertical stage names: a
        vertical whose pipeline has no research stage would otherwise never
        reach a live-search stage at all. Stage policy is vertical-local, so two
        verticals sharing a stage name (``review``) never leak into each other.
        An existing all-mission stage declaration retains its historical precedence.
        """
        if self.engineer_live_search_stages is not None:
            return self.engineer_live_search_stages
        if preserve_configured:
            return default
        # Current literature, official implementations, provider behaviour and
        # hardware facts can change in every kind of work and at every stage.
        # Restricting the fallback to a stage literally named `research` left
        # math, kernel and software verticals without live search at all, and
        # froze research campaigns to what they knew at idea selection. A
        # vertical can still declare an explicit empty set to turn search off.
        return frozenset(self.stage_order) or default

    def completion_issues(
        self,
        stage: str,
        project_root: Path,
        *,
        state_root: Path | None = None,
    ) -> tuple[str, ...]:
        if self.stage_completion_validator is None:
            return ()
        validator = self.stage_completion_validator
        try:
            parameters = inspect.signature(validator).parameters
        except (TypeError, ValueError):
            parameters = {}
        kwargs: dict[str, object] = {}
        if state_root is not None and "state_root" in parameters:
            kwargs["state_root"] = state_root
        if "verification_profile" in parameters:
            from .verification_policy import resolve_policy

            final_scope = (
                "final_submission"
                if self.completion_gate == "certified"
                and self.stage_order
                and stage == self.stage_order[-1]
                else None
            )
            policy = resolve_policy(
                state_root if state_root is not None else project_root,
                scope=final_scope,
                stage=stage,
                vertical=self.name,
                stage_profiles=self.verification_stage_profiles,
            )
            kwargs["verification_profile"] = policy.profile
        value = validator(
            stage,
            project_root,
            **kwargs,
        )
        if value is None:
            return ()
        if isinstance(value, str):
            raise VerticalContractError(
                f"vertical {self.name!r} completion validator returned a string"
            )
        try:
            return tuple(
                text
                for issue in value
                if (text := str(issue or "").strip())
            )
        except TypeError as exc:
            raise VerticalContractError(
                f"vertical {self.name!r} completion validator returned a non-iterable"
            ) from exc

    def planner_task_issues(self, stage: str, project_root: Path, task: Any) -> tuple[str, ...]:
        if self.planner_task_validator is None:
            return ()
        return tuple(
            str(issue).strip()
            for issue in self.planner_task_validator(stage, project_root, task)
            if str(issue).strip()
        )

    def review_purchase(
        self,
        *,
        project_root: Path,
        task: Any,
        existing_items: Iterable[Any],
        semantic_duplicate: Any | None,
        stage_reviewed_at: float | None,
    ) -> PlannerReviewPurchaseDecision | None:
        if self.review_purchase_policy is None:
            return None
        value = self.review_purchase_policy(
            project_root=project_root,
            task=task,
            existing_items=existing_items,
            semantic_duplicate=semantic_duplicate,
            stage_reviewed_at=stage_reviewed_at,
        )
        if isinstance(value, PlannerReviewPurchaseDecision):
            return value
        raise VerticalContractError(
            f"vertical {self.name!r} review purchase policy returned "
            f"{type(value).__name__}, expected PlannerReviewPurchaseDecision"
        )

    def assess_iteration(
        self,
        *,
        stage: str,
        scope: str,
        project_root: Path,
        state_root: Path,
        mission: Any,
        outcome: Any,
    ) -> IterationAssessment | None:
        """Ask the active vertical whether a trusted result missed its charter."""
        if self.iteration_assessor is None:
            return None
        value = self.iteration_assessor(
            stage=stage,
            scope=scope,
            project_root=project_root,
            state_root=state_root,
            mission=mission,
            outcome=outcome,
        )
        if value is None or isinstance(value, IterationAssessment):
            return value
        raise VerticalContractError(
            f"vertical {self.name!r} iteration assessor returned "
            f"{type(value).__name__}, expected IterationAssessment or None"
        )

    def adopt_operator_objective(self, project_root: Path, request: str) -> bool:
        """Let the vertical record the operator's stated objective, if it wants one.

        Most verticals declare no adopter and this is a no-op. It exists for a
        vertical whose completion rule depends on a choice that cannot be
        guessed from the request alone — ``math`` has two opposite bars
        (``targeted`` vs ``exploratory``) and refuses every stage until one is
        selected. Without a hook the only way to select it was a host CLI, so
        the vertical was unusable through the product: a math project created
        through the real front door could not close a single stage.

        Core deliberately does not learn what the choice *is*. It hands the
        vertical the operator's own request text at the one moment that text is
        known and the project is being set up, and the vertical decides whether
        the text says anything it can use. Returns whether an adopter ran at
        all, not whether it wrote anything — an adopter that correctly declines
        to overwrite an existing choice is not a failure.
        """
        if self.operator_objective_adopter is None:
            return False
        self.operator_objective_adopter(project_root, str(request or ""))
        return True

    def prepare_mission(
        self,
        *,
        stage: str,
        project_root: Path,
        state_root: Path,
        mission: Any,
    ) -> str:
        """Text a vertical wants prepended to *this* mission's instruction.

        ``mission`` is the claimed backlog item. Without it every mission in a
        stage receives a byte-identical block, so a vertical can only say
        things about the stage -- which is the same as saying them once in a
        role banner. It is what lets a vertical answer "what does this
        particular task need to know".

        Forwarded by keyword, not positionally, which makes the parameter
        *names* part of the contract. The alternative -- appending ``mission``
        positionally, so a three-argument provider fails on arity instead --
        was considered and rejected: positional forwarding lets a provider that
        merely *reorders* its parameters take ``stage`` where it meant
        ``project_root``, silently, both being plausible strings, and that is a
        wrong answer rather than a failure. Keyword forwarding closes it.

        Be clear about what the price of that is, because it is steeper than
        "an error message". This hook is called unguarded from
        ``life/supervisor/_mission_execution_runtime.py``; a ``TypeError``
        here propagates through ``_run_one`` and ``tick`` to ``run``, which
        fails the item, emits ``life.supervisor.error``, sets ``stopped_by =
        "supervisor_error"`` and **breaks the run loop**. So a stale
        out-of-tree provider does not degrade a project, it halts it, on every
        restart, from the first mission. That is still the better trade than
        the alternatives -- the failure is immediate, deterministic, and its
        message names the argument to add, whereas a vertical quietly demoted
        to stage-blind emits nothing at all and stays wrong for the life of the
        project -- but a reader weighing a change here should weigh the real
        cost, not a rhetorical one.

        This is deliberately *not* softened by inspecting the provider's
        signature and only passing ``mission`` when it is accepted. That would
        keep stale providers running at the price of making them permanently
        and invisibly stage-blind, which is the failure mode with no error
        message and no end.

        One inconsistency to know about, pre-existing and left alone: a
        provider that returns a non-``str`` is dropped silently by the last
        line here, while one that raises takes the run down. Two malformed
        providers, two opposite blast radii.
        """
        if self.mission_prelude is None:
            return ""
        value = self.mission_prelude(
            stage=stage,
            project_root=project_root,
            state_root=state_root,
            mission=mission,
        )
        return value if isinstance(value, str) else ""


def vertical_contract(name: str, provider: Any) -> VerticalContract:
    """Validate one provider and return its immutable framework view."""
    stage_order = tuple(
        str(stage).strip()
        for stage in (getattr(provider, "CHECKLIST_STAGE_ORDER", ()) or ())
        if str(stage).strip()
    )
    checklist_items = getattr(provider, "CHECKLIST_ITEMS", None)
    gate = str(getattr(provider, "completion_gate", "") or "").strip().lower()
    if not stage_order:
        raise VerticalContractError(f"vertical {name!r} declares no stage order")
    if len(set(stage_order)) != len(stage_order):
        raise VerticalContractError(f"vertical {name!r} declares duplicate stages")
    if not isinstance(checklist_items, dict):
        raise VerticalContractError(f"vertical {name!r} declares no checklist items")
    if gate not in _COMPLETION_GATES:
        raise VerticalContractError(
            f"vertical {name!r} has unsupported completion gate {gate!r}"
        )
    optional_stages = frozenset(
        str(stage).strip().lower()
        for stage in (getattr(provider, "CHECKLIST_OPTIONAL_STAGES", ()) or ())
        if str(stage).strip()
    )
    unknown_optional = sorted(optional_stages - set(stage_order))
    if unknown_optional:
        raise VerticalContractError(
            f"vertical {name!r} has unknown optional stages: {', '.join(unknown_optional)}"
        )
    unknown_checklists = sorted(set(checklist_items) - set(stage_order))
    if unknown_checklists:
        raise VerticalContractError(
            f"vertical {name!r} has checklists for unknown stages: "
            f"{', '.join(unknown_checklists)}"
        )
    missing = [
        stage
        for stage in stage_order
        if stage not in checklist_items and stage not in optional_stages
    ]
    if missing:
        raise VerticalContractError(
            f"vertical {name!r} has no checklist for: {', '.join(missing)}"
        )
    empty_required = [
        stage
        for stage in stage_order
        if stage not in optional_stages and not checklist_items.get(stage)
    ]
    if empty_required:
        raise VerticalContractError(
            f"vertical {name!r} has empty required checklists for: "
            f"{', '.join(empty_required)}"
        )
    for stage, items in checklist_items.items():
        if not isinstance(items, (list, tuple)):
            raise VerticalContractError(
                f"vertical {name!r} checklist {stage!r} is not a sequence"
            )
        seen_ids: set[str] = set()
        for item in items:
            item_id = str(getattr(item, "id", "") or "").strip()
            statement = str(getattr(item, "statement", "") or "").strip()
            if not item_id or not statement:
                raise VerticalContractError(
                    f"vertical {name!r} checklist {stage!r} has a malformed item"
                )
            if item_id in seen_ids:
                raise VerticalContractError(
                    f"vertical {name!r} checklist {stage!r} repeats item {item_id!r}"
                )
            seen_ids.add(item_id)
    mode = str(getattr(provider, "WORKFLOW_MODE", "staged") or "staged").strip().lower()
    if mode not in _WORKFLOW_MODES:
        raise VerticalContractError(
            f"vertical {name!r} has unsupported workflow mode {mode!r}"
        )
    mission_kind = str(
        getattr(provider, "MISSION_KIND", "custom") or "custom"
    ).strip().lower()
    if mission_kind not in _MISSION_KINDS:
        raise VerticalContractError(
            f"vertical {name!r} has unsupported mission kind {mission_kind!r}"
        )
    aliases = getattr(provider, "STAGE_ALIASES", {})
    aliases = {
        str(source).strip().lower(): str(target).strip().lower()
        for source, target in aliases.items()
        if str(source).strip() and str(target).strip()
    } if isinstance(aliases, dict) else {}
    stage_completion_validator = getattr(provider, "stage_completion_issues", None)
    if stage_completion_validator is not None and not callable(stage_completion_validator):
        raise VerticalContractError(
            f"vertical {name!r} has a non-callable stage completion validator"
        )
    planner_task_validator = getattr(provider, "planner_task_issues", None)
    if planner_task_validator is not None and not callable(planner_task_validator):
        raise VerticalContractError(
            f"vertical {name!r} has a non-callable planner task validator"
        )
    review_purchase_policy = getattr(provider, "review_purchase_policy", None)
    if review_purchase_policy is not None and not callable(review_purchase_policy):
        raise VerticalContractError(
            f"vertical {name!r} has a non-callable review purchase policy"
        )
    iteration_assessor = getattr(provider, "iteration_assessment", None)
    if iteration_assessor is not None and not callable(iteration_assessor):
        raise VerticalContractError(
            f"vertical {name!r} has a non-callable iteration assessor"
        )
    operator_objective_adopter = getattr(provider, "adopt_operator_objective", None)
    if operator_objective_adopter is not None and not callable(
        operator_objective_adopter
    ):
        raise VerticalContractError(
            f"vertical {name!r} has a non-callable operator objective adopter"
        )
    raw_stage_checks = getattr(provider, "STAGE_CHECKS", {}) or {}
    if not isinstance(raw_stage_checks, dict):
        raise VerticalContractError(f"vertical {name!r} stage checks are not a mapping")
    unknown_stage_checks = sorted(set(raw_stage_checks) - set(stage_order))
    if unknown_stage_checks:
        raise VerticalContractError(
            f"vertical {name!r} has checks for unknown stages: "
            f"{', '.join(unknown_stage_checks)}"
        )
    stage_checks: dict[str, tuple[tuple[str, str], ...]] = {}
    for stage, checks in raw_stage_checks.items():
        if not isinstance(checks, (list, tuple)):
            raise VerticalContractError(
                f"vertical {name!r} checks for {stage!r} are not a sequence"
            )
        normalized_checks: list[tuple[str, str]] = []
        for check in checks:
            if not isinstance(check, (list, tuple)) or len(check) != 2:
                raise VerticalContractError(
                    f"vertical {name!r} check for {stage!r} is not a label-command pair"
                )
            label, command = check
            if not isinstance(label, str) or not label.strip():
                raise VerticalContractError(
                    f"vertical {name!r} check for {stage!r} has an empty label"
                )
            if not isinstance(command, str) or not command.strip():
                raise VerticalContractError(
                    f"vertical {name!r} check for {stage!r} has an empty command"
                )
            normalized_checks.append((label.strip(), command.strip()))
        stage_checks[stage] = tuple(normalized_checks)
    raw_primary_deliverables = (
        getattr(provider, "STAGE_PRIMARY_DELIVERABLES", {}) or {}
    )
    if not isinstance(raw_primary_deliverables, dict):
        raise VerticalContractError(
            f"vertical {name!r} primary deliverables are not a mapping"
        )
    unknown_primary_stages = sorted(
        set(raw_primary_deliverables) - set(stage_order)
    )
    if unknown_primary_stages:
        raise VerticalContractError(
            f"vertical {name!r} has primary deliverables for unknown stages: "
            f"{', '.join(unknown_primary_stages)}"
        )
    stage_primary_deliverables = {
        str(stage): tuple(
            path
            for value in values
            if (path := str(value or "").strip())
        )
        for stage, values in raw_primary_deliverables.items()
    }
    raw_live_search_stages = getattr(provider, "ENGINEER_LIVE_SEARCH_STAGES", None)
    engineer_live_search_stages: frozenset[str] | None = None
    if raw_live_search_stages is not None:
        # Declared-empty ("never search") and absent ("use the caller's
        # baseline") are different answers, so nothing here may silently DROP an
        # element: a stray blank string would otherwise turn a typo into a
        # permanent, unreported "live search off".
        engineer_live_search_stages = _normalize_live_search_stages(
            name, raw_live_search_stages, stage_order
        )
    raw_verification_profiles = (
        getattr(provider, "VERIFICATION_STAGE_PROFILES", {}) or {}
    )
    if not isinstance(raw_verification_profiles, dict):
        raise VerticalContractError(
            f"vertical {name!r} verification profiles are not a mapping"
        )
    verification_stage_profiles = {
        str(stage).strip().lower(): str(profile).strip().lower()
        for stage, profile in raw_verification_profiles.items()
        if str(stage).strip()
    }
    unknown_profile_stages = sorted(
        set(verification_stage_profiles) - set(stage_order)
    )
    if unknown_profile_stages:
        raise VerticalContractError(
            f"vertical {name!r} has verification profiles for unknown stages: "
            f"{', '.join(unknown_profile_stages)}"
        )
    invalid_profiles = sorted(
        {
            profile
            for profile in verification_stage_profiles.values()
            if profile not in _VERIFICATION_PROFILES
        }
    )
    if invalid_profiles:
        raise VerticalContractError(
            f"vertical {name!r} has invalid verification profiles: "
            f"{', '.join(invalid_profiles)}"
        )
    return VerticalContract(
        name=str(name or "").strip().lower(),
        stage_order=stage_order,
        checklist_items=checklist_items,
        completion_gate=gate,
        mission_kind=mission_kind,
        paper_mission=bool(getattr(provider, "PAPER_MISSION", False)),
        ground_before_handoff=bool(
            getattr(provider, "GROUND_BEFORE_HANDOFF", False)
        ),
        role_guidance=(
            getattr(provider, "role_banner")
            if callable(getattr(provider, "role_banner", None))
            else None
        ),
        role_prompt_fragment=(
            getattr(provider, "render_role_prompt_fragment")
            if callable(getattr(provider, "render_role_prompt_fragment", None))
            else None
        ),
        evidence_schema=getattr(provider, "EVIDENCE_SCHEMA", None),
        requires_independent_review=bool(
            getattr(provider, "REQUIRE_INDEPENDENT_REVIEW", True)
        ),
        completion_contract_version=max(
            0, int(getattr(provider, "COMPLETION_CONTRACT_VERSION", 0) or 0)
        ),
        research_target_levels=tuple(
            str(level).strip().lower()
            for level in (getattr(provider, "RESEARCH_TARGET_LEVELS", ()) or ())
            if str(level).strip()
        ),
        workflow_mode=mode,
        verification_stage_profiles=verification_stage_profiles,
        checklist_optional_stages=optional_stages,
        stage_aliases=aliases,
        search_altitude=(
            getattr(provider, "search_altitude_context")
            if callable(getattr(provider, "search_altitude_context", None))
            else None
        ),
        mission_prelude=(
            getattr(provider, "prepare_mission")
            if callable(getattr(provider, "prepare_mission", None))
            else None
        ),
        library_preparer=(
            getattr(provider, "LIBRARY_PREPARER")
            if callable(getattr(provider, "LIBRARY_PREPARER", None))
            else None
        ),
        stage_completion_validator=stage_completion_validator,
        planner_task_validator=planner_task_validator,
        review_purchase_policy=review_purchase_policy,
        iteration_assessor=iteration_assessor,
        operator_objective_adopter=operator_objective_adopter,
        stage_checks=stage_checks,
        stage_primary_deliverables=stage_primary_deliverables,
        engineer_live_search_stages=engineer_live_search_stages,
    )


__all__ = [
    "VERTICAL_CONTRACT_VERSION",
    "MissionPrelude",
    "IterationAssessment",
    "IterationAssessmentHook",
    "PlannerReviewPurchaseDecision",
    "RolePromptFragment",
    "VerticalContract",
    "VerticalContractError",
    "VerticalLibraryContext",
    "vertical_contract",
]
