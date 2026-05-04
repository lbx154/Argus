"""SkillLoopRunner — main-agent shim that lets argus-skill plug
into ArgusBot's ``LoopEngine`` as the round driver.

Why this exists
---------------

ArgusBot's ``LoopEngine`` is the round-loop state machine that gives
us 7×24 unattended operation: rounds 1..N, reviewer judging each
round, planner proposing follow-ups when ``plan_mode == "auto"``,
persistent operator messages, stall watchdog, etc. The engine
delegates the *main agent* call to a single ``runner.run_exec(...)``
per round (see ``codex_autoloop/core/engine.py:130``).

argus-skill's value-add is the matcher → distiller → engineer skill
pipeline. We want LoopEngine to drive the rounds while argus-skill
does the per-round main-agent work — so this shim sits in the
``runner`` slot and, on each ``run_exec(run_label="main")`` call:

  1. Resolves a skill against a **stable mission objective**
     (cached after the first call — the engine prompt changes every
     round but the operator's objective does not, so we don't want
     to re-match against transient prompt boilerplate).
  2. If no skill matched and ``distill_on_miss`` is on, distills one
     and saves it to the skill store.
  3. Builds an engineer prompt: skill playbook + the LoopEngine prompt
     (which carries reviewer feedback / planner instructions / retry
     context).
  4. Calls the configured engineer runner with the **watchdog hooks
     forwarded** from LoopEngine's ``RunnerOptions`` (so ``/inject``
     and ``/stop`` from the operator can interrupt a long round).
  5. Maps the engineer result back into ArgusBot's ``CodexRunResult``
     shape so LoopEngine's reviewer can read ``last_agent_message``
     etc.

For non-``main`` run labels (``main-pptx-report``, ``main-final-report``)
the shim delegates straight through to a real ``CodexRunner`` — those
calls are LoopEngine's report-generation machinery and don't need
skill matching.

What this shim deliberately does NOT do
---------------------------------------

  * No inner per-round reviewer (LoopEngine's reviewer is the only gate).
  * No skill writeback from per-round trajectories (LoopEngine's
    reviewer is the final judge of "this work was good"; we don't
    want to update skill .md files based on rounds the outer reviewer
    later marks ``continue``/``blocked``).
  * No fake ``thread_id`` continuity. SkillLoop's pipeline is
    multi-stage and doesn't share a clean codex thread, so we return
    ``thread_id=None`` and let LoopEngine treat each round as fresh
    on the main side.

Provenance: new code. Depends on ArgusBot being importable
(``pip install -e ../ArgusBot``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.models import RunnerOptions, RunnerResult
from ..core.ports import RunnerBackend
from ..scientist.distiller import Distiller, DistillerConfig
from ..skills.store import Skill, SkillStore

log = logging.getLogger(__name__)


def _import_argusbot_models():
    try:
        from codex_autoloop.models import CodexRunResult  # type: ignore
    except ImportError as exc:  # pragma: no cover - environmental
        raise ImportError(
            "SkillLoopRunner requires ArgusBot to be importable. "
            "Install with `pip install -e /path/to/ArgusBot`."
        ) from exc
    return CodexRunResult


@dataclass
class EngineerCallConfig:
    """Knobs for the engineer subprocess invocation.

    Mirrors the slim subset of ``EngineerConfig`` argus-skill already
    uses for its ``SkillLoop``; kept separate so tests don't drag in
    SkillLoop construction just to wire the runner.
    """
    model: str = "gpt-5.4-mini"
    reasoning_effort: str | None = None
    extra_args: list[str] | None = None
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False


@dataclass
class SkillLoopRunnerConfig:
    """All runner-level knobs in one place.

    ``mission_objective`` is THE stable cache key. It MUST be the
    operator's original objective text, not the LoopEngine-formatted
    prompt that grows over rounds.
    """
    mission_objective: str
    workdir: Path
    engineer: EngineerCallConfig = field(default_factory=EngineerCallConfig)
    distiller: DistillerConfig | None = None
    distill_on_miss: bool = True


class SkillLoopRunner:
    """Adapter exposing a ``CodexRunner.run_exec`` shape.

    Constructed by ``MissionDaemon`` once per mission; held by
    ``LoopEngine.runner``. Each ``run_exec`` call corresponds to one
    LoopEngine round (or a one-off report generation).

    Args:
        config: stable per-mission runner knobs.
        skill_store: pre-constructed skill store. The shim resolves
            a skill against ``config.mission_objective`` lazily on
            first ``run_exec(run_label="main")``; subsequent rounds
            reuse the cached match.
        distiller: pre-constructed distiller. Used iff matcher returns
            no skill and ``config.distill_on_miss`` is True.
        engineer_runner: argus-skill ``RunnerBackend`` for the
            engineer subprocess (typically ``CodexRunnerBackend``).
        fallback_runner: real ArgusBot ``CodexRunner`` used for
            non-``main`` run labels (pptx / final-report). Pass the
            same instance LoopEngine's reviewer / planner use.
        on_event: optional event sink for matcher / distill /
            engineer telemetry.
    """

    def __init__(
        self,
        *,
        config: SkillLoopRunnerConfig,
        skill_store: SkillStore,
        distiller: Distiller,
        engineer_runner: RunnerBackend,
        fallback_runner: Any,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.config = config
        self.skill_store = skill_store
        self.distiller = distiller
        self.engineer_runner = engineer_runner
        self.fallback_runner = fallback_runner
        self.on_event = on_event

        self._codex_run_result_cls = _import_argusbot_models()
        # Lazy skill resolution — the matcher fires once per mission.
        self._cached_skill: Skill | None = None
        self._skill_resolved = False
        self._skill_distilled = False

    # ------------------------------------------------------------------
    # Public API — duck-typed CodexRunner.run_exec
    # ------------------------------------------------------------------

    def run_exec(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options: Any,  # ArgusBot RunnerOptions
        run_label: str | None = None,
    ):
        """Drive one LoopEngine round (or a report call).

        Returns ``CodexRunResult`` so LoopEngine's downstream code can
        read ``last_agent_message``, ``exit_code``, ``fatal_error``,
        ``thread_id``, ``turn_completed``, ``turn_failed`` unchanged.
        """
        if run_label != "main":
            # Reports etc. — LoopEngine builds custom prompts for these
            # (final report, pptx). They don't benefit from skill
            # matching; pass through to the real codex runner.
            return self.fallback_runner.run_exec(
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                options=options,
                run_label=run_label,
            )

        skill = self._resolve_skill_once()
        skill_text = self.skill_store.render_skill(skill) if skill else ""
        engineer_prompt = self._build_engineer_prompt(
            skill_text=skill_text,
            loop_engine_prompt=prompt,
            skill_name=skill.name if skill else None,
        )

        engineer_options = self._translate_options(options)
        engineer_result = self.engineer_runner.run_exec(
            prompt=engineer_prompt,
            options=engineer_options,
            run_label=run_label,
        )

        return self._to_codex_run_result(engineer_result)

    # ------------------------------------------------------------------
    # Skill resolution (mission-objective-keyed, cache once)
    # ------------------------------------------------------------------

    def _resolve_skill_once(self) -> Skill | None:
        if self._skill_resolved:
            return self._cached_skill

        mission = self.config.mission_objective
        try:
            matched, _ = self.skill_store.find_relevant(
                mission, on_event=self.on_event
            )
        except Exception as exc:  # noqa: BLE001 — never let matcher kill the round
            log.warning(
                "skill matcher raised (%s: %s); proceeding without skill",
                type(exc).__name__,
                exc,
            )
            matched = None

        skill: Skill | None = matched[0] if matched else None

        if skill is None and self.config.distill_on_miss and self.config.distiller:
            self._emit({
                "type": "scientist.start",
                "text": "no high-fit skill — distilling for mission objective",
            })
            try:
                distill_result = self.distiller.distill(
                    task_description=mission,
                    config=self.config.distiller,
                    on_event=self.on_event,
                )
                if distill_result.last_agent_message.strip():
                    skill = self.skill_store.save_distilled(
                        task_description=mission,
                        raw_distill_output=distill_result.last_agent_message,
                        scientist_model=self.config.distiller.model,
                    )
                    self._skill_distilled = True
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "scientist distill failed (%s: %s); proceeding without skill",
                    type(exc).__name__,
                    exc,
                )
                self._emit({
                    "type": "scientist.error",
                    "text": f"distill failed: {type(exc).__name__}",
                })

        self._cached_skill = skill
        self._skill_resolved = True
        return skill

    # ------------------------------------------------------------------
    # Prompt + options + result translation
    # ------------------------------------------------------------------

    @staticmethod
    def _build_engineer_prompt(
        *,
        skill_text: str,
        loop_engine_prompt: str,
        skill_name: str | None,
    ) -> str:
        sections: list[str] = []
        if skill_text:
            header = "## Skill playbook (read first)"
            if skill_name:
                header = f"## Skill playbook — {skill_name} (read first)"
            sections.append(header + "\n" + skill_text)
        sections.append("## Engine prompt\n" + loop_engine_prompt)
        sections.append(
            "## Required output\n"
            "Make concrete progress: read files, run commands, edit code\n"
            "as needed. End with a brief summary of what you did and what\n"
            "evidence proves it (commands run, tests passed, files changed)."
        )
        return "\n\n".join(sections)

    def _translate_options(self, argus_options: Any) -> RunnerOptions:
        """Translate ArgusBot RunnerOptions → argus-skill RunnerOptions.

        Watchdog hooks (``external_interrupt_reason_provider``,
        ``inactivity_callback``, ``watchdog_*_idle_seconds``) are
        forwarded so the codex subprocess can be interrupted by the
        outer supervisor.
        """
        return RunnerOptions(
            model=getattr(argus_options, "model", None) or self.config.engineer.model,
            reasoning_effort=getattr(argus_options, "reasoning_effort", None)
            or self.config.engineer.reasoning_effort,
            output_schema_path=getattr(argus_options, "output_schema_path", None),
            working_dir=getattr(argus_options, "working_dir", None) or str(self.config.workdir),
            extra_args=list(getattr(argus_options, "extra_args", None) or [])
            or list(self.config.engineer.extra_args or []),
            skip_git_repo_check=getattr(
                argus_options, "skip_git_repo_check", self.config.engineer.skip_git_repo_check
            ),
            full_auto=getattr(argus_options, "full_auto", self.config.engineer.full_auto),
            dangerous_yolo=getattr(
                argus_options, "dangerous_yolo", self.config.engineer.dangerous_yolo
            ),
            external_interrupt_reason_provider=getattr(
                argus_options, "external_interrupt_reason_provider", None
            ),
            inactivity_callback=getattr(argus_options, "inactivity_callback", None),
            watchdog_soft_idle_seconds=getattr(argus_options, "watchdog_soft_idle_seconds", 0),
            watchdog_hard_idle_seconds=getattr(argus_options, "watchdog_hard_idle_seconds", 0),
        )

    def _to_codex_run_result(self, engineer_result: RunnerResult):
        """Map argus-skill ``RunnerResult`` → ArgusBot ``CodexRunResult``.

        Transport-level failures (FileNotFoundError, generic exceptions
        from the runner) are surfaced as ``turn_failed=True`` +
        ``fatal_error=...`` so LoopEngine's reviewer sees a real error.

        An engineer with empty output but a clean exit is *not* a
        transport failure — it's a content failure. We still mark the
        turn as completed; LoopEngine's reviewer reads the empty
        ``last_agent_message`` and decides ``continue``.
        """
        cls = self._codex_run_result_cls

        agent_messages = list(engineer_result.agent_messages or [])
        exit_code = engineer_result.exit_code
        fatal_error = engineer_result.fatal_error

        # Was this a real transport failure?
        is_transport_failure = (
            (fatal_error is not None and not fatal_error.startswith("External interrupt:"))
            or exit_code not in (0,)
        )
        # External interrupts must be visible to LoopEngine without
        # being mistaken for a content/transport bug; preserve the
        # "External interrupt: ..." prefix so engine.py:166-168 can
        # take the interrupted-branch.
        if fatal_error and fatal_error.startswith("External interrupt:"):
            return cls(
                command=[],
                exit_code=exit_code if exit_code is not None else -1,
                thread_id=None,
                agent_messages=agent_messages,
                json_events=[],
                stdout_lines=list(engineer_result.stdout_lines or []),
                stderr_lines=list(engineer_result.stderr_lines or []),
                turn_completed=False,
                turn_failed=True,
                fatal_error=fatal_error,
            )

        return cls(
            command=[],
            exit_code=exit_code if exit_code is not None else 0,
            thread_id=None,
            agent_messages=agent_messages,
            json_events=[],
            stdout_lines=list(engineer_result.stdout_lines or []),
            stderr_lines=list(engineer_result.stderr_lines or []),
            turn_completed=not is_transport_failure,
            turn_failed=is_transport_failure,
            fatal_error=fatal_error,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001 — never let UI errors kill the round
            log.exception("on_event handler raised in SkillLoopRunner")


__all__ = [
    "EngineerCallConfig",
    "SkillLoopRunner",
    "SkillLoopRunnerConfig",
]
