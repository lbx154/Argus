"""argus.manager._front_door_ops — mixin for conversational/routing methods.

``_FrontDoorMixin`` holds every Manager method that handles front-door
classification: conversational-intent detection, config-intent recognition,
the combined front-door classify, route, and persistence-lifetime decision.
Also carries the skill-placement judge (used by the Manager-as-janitor path).

All calls either defer to ``life/router.py`` helpers (which do the actual model
calls) or wire a thin run_exec shim over ``self.runner`` / ``self._session``.
"""
from __future__ import annotations

from typing import Any

from ..skills import vertical_select
from ._helpers import (
    _manager_reasoning_effort,
    gateway_run_exec,
)


class _FrontDoorMixin:
    """Mixin: is_conversational, classify_*, route, and skill placement."""

    def classify_front_door(
        self,
        text: str,
        *,
        run_exec: Any = None,
        root_task_id: str | None = None,
        name_sink: Any = None,
        lifetime_sink: Any = None,
        self_mode_sink: Any = None,
        reply_sink: Any = None,
        greeting_sink: Any = None,
        steering_sink: Any = None,
        authorization_sink: Any = None,
        failure_sink: Any = None,
        active_mission: bool = False,
    ) -> Any:
        """One fresh call classifying all cheap front-door decisions.

        Built FRESH on the raw backend (``self.runner``, NEVER
        ``self._session`` — no giant-session resume, no pollution),
        ``resume_thread_id=None``. Effort comes from
        ``ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT`` (default ``low``). Biases
        each axis to its own safe default on any error."""
        from ..life.router import classify_front_door

        if run_exec is None:
            if self.runner is None:
                return None, None, "complex"
            from ..core.knobs import resolve_knob, resolve_manager_classify_model
            from ..core.models import RunnerOptions

            _backend = self.runner
            _effort = resolve_knob(
                "ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT",
                "low",
            ).value.strip() or "low"
            _pi = str(getattr(_backend, "backend", "")) == "pi"

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        model=resolve_manager_classify_model(
                            backend=getattr(_backend, "backend", None),
                        ),
                        reasoning_effort=_effort,
                        skip_git_repo_check=True,
                        disable_tools=True,
                        extra_args=(
                            [
                                "--system-prompt",
                                "Return only the requested Argus Manager "
                                "classification decision.",
                            ]
                            if _pi
                            else None
                        ),
                    ),
                    run_label="manager-frontdoor-classify",
                    resume_thread_id=None,
                )

        with self._task_usage_scope(root_task_id):
            return classify_front_door(
                text,
                run_exec=run_exec,
                name_sink=name_sink,
                lifetime_sink=lifetime_sink,
                self_mode_sink=self_mode_sink,
                reply_sink=reply_sink,
                greeting_sink=greeting_sink,
                steering_sink=steering_sink,
                authorization_sink=authorization_sink,
                failure_sink=failure_sink,
                active_mission=active_mission,
            )

    def route(
        self,
        text: str,
        *,
        run_exec: Any = None,
        root_task_id: str | None = None,
    ) -> str:
        """The Manager's lego-block router: pick the SMALLEST block that fits the
        operator's input — ``"chat"`` (one codex reply), ``"simple"`` (one bounded
        codex turn, no reviewer/planner), or ``"complex"`` (the full mission
        pipeline). The Manager owns this call. Reuses
        ``life/router.classify_route`` (biases hard to ``"complex"`` so real work
        never silently skips the reviewer). With no backend, returns ``"complex"``
        — the safe default that never drops work to a bad classify."""
        from ..life.router import classify_route

        if run_exec is None:
            if self.runner is None:
                return "complex"
            from ..core.models import RunnerOptions

            _backend = self._session or self.runner

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_manager_reasoning_effort(),
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-route",
                )

        with self._task_usage_scope(root_task_id):
            return classify_route(text, run_exec=run_exec)


    # ---- skill-library tidy-up (the Manager is the "janitor") ----
    def classify_skill_placement(self, *, content: str, task: str) -> Any:
        """Decide where a project-distilled skill belongs: global / a vertical /
        stay. Runs the placement judge on THIS Manager's runner with the known
        verticals as candidates. Returns a ``PlacementVerdict``."""
        from ..domains import BUILTIN_DOMAINS
        from .skill_review import classify_skill_placement as _classify

        return _classify(
            content=content,
            task=task,
            candidate_verticals=[*vertical_select.available_verticals(), *BUILTIN_DOMAINS],
            runner=(self._session or self.runner),
        )

    def classify_skill_placements(self, skills: list[dict[str, str]]) -> Any:
        """Batch variant used by shared runtime propagation."""
        from ..domains import BUILTIN_DOMAINS
        from .skill_review import classify_skill_placements as _classify_batch

        return _classify_batch(
            skills=skills,
            candidate_verticals=[*vertical_select.available_verticals(), *BUILTIN_DOMAINS],
            runner=(self._session or self.runner),
        )
