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

    # ---- conversational-intent decision (the Manager owns this) ----
    def is_conversational(self, text: str, *, run_exec: Any = None) -> bool:
        """The Manager's top-level dialogue call: is this free text a conversation
        (greeting / capability question / ack) rather than a real task?

        The Manager — not the runner — owns this decision. Reuses
        ``life/router.classify_is_conversational`` (conservative: biases hard
        toward TASK, so work is never silently skipped). ``run_exec`` is the LLM
        caller; when omitted one is built from ``self.runner``. With no backend at
        all, treat as a task (safe default — never drop work to a bad classify).
        """
        from ..life.router import classify_is_conversational

        if run_exec is None:
            if self.runner is None:
                return False
            from ..core.models import RunnerOptions

            # Route the internal classify call through the shared persistent
            # session when available, so this turn continues the one Manager
            # conversation; otherwise fall back to a plain runner call.
            _backend = self._session or self.runner

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_manager_reasoning_effort(),
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-converse",
                )

        return classify_is_conversational(text, run_exec=run_exec)

    def classify_config_intent(
        self,
        text: str,
        *,
        run_exec: Any = None,
        root_task_id: str | None = None,
    ) -> Any:
        """Does this free text ask to change one of Argus's OWN runtime knobs
        (a role's backend/model/effort, a budget cap, or a safe_mode/
        show_reasoning/telegram toggle)? Returns a ``life.router.ConfigIntent``
        or ``None``.

        Intent recognition via one low-reasoning LLM call — never keyword/regex
        matching. The Manager owns this decision; ``run_exec`` is the LLM caller,
        built from ``self.runner`` when omitted. Biases hard toward ``None`` so a
        real task that merely mentions a model/backend is never swallowed.
        """
        from ..life.router import classify_config_intent

        if run_exec is None:
            if self.runner is None:
                return None
            from ..core.models import RunnerOptions

            # Config-intent is a STATELESS yes/no check on the CURRENT message
            # ("does this ask to change a knob?") — it needs no prior turns. Run it
            # FRESH on the raw backend (``self.runner``), NOT through ``self._session``:
            # the persistent Manager session reloads its FULL history on every
            # resume (tens of seconds on a long-lived copilot session — it was
            # adding ~30s to EVERY operator message at the cockpit front door), and
            # continuing it here would also pollute that conversation with throwaway
            # classify prompts. ``route`` is already run fresh at the front door for
            # exactly this reason (see ``apps/_runtime.py``'s ``_classify_run_exec``);
            # this makes config-intent match instead of resuming the big session.
            _backend = self.runner

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_manager_reasoning_effort(),
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-config-intent",
                    resume_thread_id=None,
                )

        with self._task_usage_scope(root_task_id):
            return classify_config_intent(text, run_exec=run_exec)

    def classify_front_door(
        self,
        text: str,
        *,
        run_exec: Any = None,
        root_task_id: str | None = None,
        name_sink: Any = None,
        lifetime_sink: Any = None,
        greeting_sink: Any = None,
        steering_sink: Any = None,
        authorization_sink: Any = None,
        active_mission: bool = False,
    ) -> Any:
        """One fresh call classifying all cheap front-door decisions.

        Same discipline as ``classify_config_intent``: built FRESH on the raw
        backend (``self.runner``, NEVER ``self._session`` — no giant-session
        resume, no pollution), ``resume_thread_id=None``. Effort comes from
        ``ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT`` (default ``low``): an eight-axis
        classification needs no heavy reasoning, and ``low`` is what makes
        this cheap. Biases each axis to its own safe default on any error."""
        from ..life.router import classify_front_door

        if run_exec is None:
            if self.runner is None:
                return None, None, "complex"
            import os

            from ..core.knobs import resolve_manager_classify_model
            from ..core.models import RunnerOptions

            _backend = self.runner
            _effort = os.environ.get(
                "ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", "low"
            ).strip() or "low"

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        model=resolve_manager_classify_model(),
                        reasoning_effort=_effort,
                        skip_git_repo_check=True,
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
                greeting_sink=greeting_sink,
                steering_sink=steering_sink,
                authorization_sink=authorization_sink,
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
            candidate_verticals=[*vertical_select.VERTICALS, *BUILTIN_DOMAINS],
            runner=(self._session or self.runner),
        )

    def classify_skill_placements(self, skills: list[dict[str, str]]) -> Any:
        """Batch variant used by shared runtime propagation."""
        from ..domains import BUILTIN_DOMAINS
        from .skill_review import classify_skill_placements as _classify_batch

        return _classify_batch(
            skills=skills,
            candidate_verticals=[*vertical_select.VERTICALS, *BUILTIN_DOMAINS],
            runner=(self._session or self.runner),
        )
