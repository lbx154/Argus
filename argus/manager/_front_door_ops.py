"""argus.manager._front_door_ops — mixin for conversational/routing methods.

``_FrontDoorMixin`` holds the Manager's combined front-door classify, which
defers to ``life/router.py`` over a thin run_exec shim on ``self.runner``, and
the skill-placement judge (used by the Manager-as-janitor path).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..skills import vertical_select
from ._helpers import gateway_run_exec


class _FrontDoorMixin:
    """Mixin: the front-door classify and skill placement."""

    project_root: Path
    learned_vertical_root: Path

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
        operator_question_policy_sink: Any = None,
        authorization_sink: Any = None,
        failure_sink: Any = None,
        intake_sink: Any = None,
        domain_sink: Any = None,
        active_mission: bool = False,
    ) -> Any:
        """One fresh call classifying all cheap front-door decisions.

        Built FRESH on the raw backend (``self.runner``, NEVER
        ``self._session`` — no giant-session resume, no pollution),
        ``resume_thread_id=None``, on the light model and effort. Biases
        each axis to its own safe default on any error."""
        from ..life.router import classify_front_door

        domain_prompt = ""
        if domain_sink is not None:
            from ..verticals._data_domain import list_selectable_data_domain_summaries
            from .domain_intake import intake_prompt, read_intake

            catalog = {**vertical_select.available_vertical_purposes(),
                       **list_selectable_data_domain_summaries(self.project_root, learned_root=self.learned_vertical_root)}
            domain_prompt = intake_prompt(catalog, read_intake(self.project_root))
            if active_mission:
                domain_prompt += "\nAn active mission exists: leave domain.action out; set domain.vertical only.\n"
            original_domain_sink = domain_sink

            def domain_sink(decision: dict[str, str]) -> None:
                selected = decision.get("vertical")
                if selected is not None:
                    decision = {**decision, "vertical": selected if selected in catalog else ""}
                original_domain_sink(decision)

        if run_exec is None:
            if self.runner is None:
                return None, None, "complex"
            from ..core.knobs import resolve_light_effort, resolve_light_model
            from ..core.models import RunnerOptions
            from ..core.role_actions import role_actions

            _backend = self.runner
            from ..core.operator_context import build_operator_context_block

            operator_context, _operator_context_revision = build_operator_context_block(
                "manager", self.manager_session_root, consume_once=False
            )
            _effort = resolve_light_effort()
            _backend_name = str(
                getattr(_backend, "backend", "")
                or getattr(_backend, "_backend_name", "")
            ).strip().lower()
            _pi = _backend_name == "pi"

            def run_exec(prompt: str, actions: Any) -> Any:  # noqa: ANN401
                from ..core.operator_context import append_operator_context

                options = RunnerOptions(
                    model=resolve_light_model(backend=getattr(_backend, "backend", None)),
                    reasoning_effort=_effort,
                    skip_git_repo_check=True,
                    disable_tools=True,
                    extra_args=(
                        ["--system-prompt", "Classify the Argus operator message with hand_off only."]
                        if _pi
                        else (["--ephemeral"] if _backend_name == "codex" else None)
                    ),
                )
                with role_actions(_backend, options, actions) as bound:
                    return gateway_run_exec(
                        _backend,
                        prompt=append_operator_context(prompt, operator_context),
                        options=bound,
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
                operator_question_policy_sink=operator_question_policy_sink,
                authorization_sink=authorization_sink,
                failure_sink=failure_sink,
                intake_sink=intake_sink,
                active_mission=active_mission,
                domain_sink=domain_sink,
                domain_prompt=domain_prompt,
            )

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
