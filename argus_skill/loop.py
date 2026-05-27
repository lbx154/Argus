"""SkillLoop — the integrated matcher → distiller → supervised-engineer flow.

This is the new code that argus-skill exists to deliver. It composes:

  * ``SkillStore`` (vendored from skill-agent): horizontal skill cache.
  * ``Distiller``  (vendored from skill-agent): scientist's playbook authoring.
  * ``SupervisedEngineer`` (new, with ``Reviewer`` vendored from ArgusBot):
    vertical round-loop that supervises the engineer until the reviewer
    is satisfied.

End-to-end shape:

    task → matcher
        ├── high-fit hit  → engineer round-loop with skill block injected
        └── miss          → distill new skill → engineer round-loop with new skill
    engineer round-loop:
        engineer turn → checks → reviewer
            done    → write skill back, return success
            continue → inject next_action, next round
            blocked → stop with reason
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core.models import LoopOutcome, RoundRecord
from .core.ports import RunnerBackend
from .engineer.reviewer import Reviewer, ReviewerConfig
from .engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from .scientist.distiller import Distiller, DistillerConfig
from .skills.store import Skill, SkillStore

log = logging.getLogger(__name__)

_PAPER_OBJECTIVE_SUBSTRINGS = (
    "academic paper",
    "camera-ready",
    "citation",
    "latex",
    "long paper",
    "main.pdf",
    "main.tex",
    "make_paper",
    "manuscript",
    "paper/",
    "paper draft",
    "publication-ready",
    "submission-ready",
    "validate-full-emnlp",
    "正文",
    "论文",
    "投稿",
)
_PAPER_OBJECTIVE_WORDS = {"acl", "emnlp"}


@dataclass
class SkillLoopConfig:
    """All knobs for one SkillLoop.run invocation, in one place."""
    scientist_model: str = "gpt-5.4"
    engineer_model: str = "gpt-5.4-mini"
    reviewer_model: str | None = None  # default: same as engineer (cheap)
    matcher_model: str | None = None   # default: same as engineer
    scientist_reasoning_effort: str = "high"
    engineer_reasoning_effort: str | None = "high"
    reviewer_reasoning_effort: str = "high"
    matcher_reasoning_effort: str | None = None
    max_rounds: int = 500
    check_commands: list[str] = field(default_factory=list)
    check_timeout_seconds: int = 600
    no_progress_threshold: int = 2
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0
    skill_writeback: bool = True
    distill_on_miss: bool = True
    # When True, the writeback also calls the scientist to revise the
    # playbook based on the successful trajectory (bumps version).
    skill_revise_on_writeback: bool = False
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False
    extra_args: list[str] | None = None
    session_id: str | None = None

    def resolved_reviewer_model(self) -> str:
        return self.reviewer_model or self.engineer_model

    def resolved_matcher_model(self) -> str:
        return self.matcher_model or self.engineer_model


class SkillLoop:
    """High-level entry point: ``loop.run("task description")``.

    Three injectable backends — typically all the same in production
    (one codex CLI), but separable so tests can mock individually:

      * ``scientist_runner`` — for distillation (big model).
      * ``engineer_runner``  — for execution (small model).
      * ``reviewer_runner``  — for the per-round verdict (small / medium model).

    Pass the same backend three times if you only have one.
    """

    def __init__(
        self,
        *,
        skills_dir: Path,
        scientist_runner: RunnerBackend,
        engineer_runner: RunnerBackend,
        reviewer_runner: RunnerBackend | None = None,
        config: SkillLoopConfig | None = None,
        skill_store: SkillStore | None = None,
        on_event: Callable[[dict], None] | None = None,
        extra_guidance_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self.config = config or SkillLoopConfig()
        self.skills_dir = Path(skills_dir)
        self.scientist_runner = scientist_runner
        self.engineer_runner = engineer_runner
        self.reviewer_runner = reviewer_runner or engineer_runner
        self.on_event = on_event
        # Optional callable consulted at the start of each engineer round.
        # Returns a list of additional guidance strings to append to the
        # prompt (used by the daemon to honour /inject between rounds).
        self.extra_guidance_provider = extra_guidance_provider

        self.skill_store = skill_store or SkillStore(
            self.skills_dir,
            runner=engineer_runner,
            matcher_model=self.config.resolved_matcher_model(),
            matcher_reasoning_effort=self.config.matcher_reasoning_effort,
        )
        self.distiller = Distiller(scientist_runner)
        self.reviewer = Reviewer(self.reviewer_runner)
        self.supervised = SupervisedEngineer(
            engineer_runner=engineer_runner,
            reviewer=self.reviewer,
            engineer_config=EngineerConfig(
                model=self.config.engineer_model,
                reasoning_effort=self.config.engineer_reasoning_effort,
                extra_args=self.config.extra_args,
                full_auto=self.config.full_auto,
                skip_git_repo_check=self.config.skip_git_repo_check,
                dangerous_yolo=self.config.dangerous_yolo,
            ),
            reviewer_config=ReviewerConfig(
                model=self.config.resolved_reviewer_model(),
                reasoning_effort=self.config.reviewer_reasoning_effort,
                extra_args=self.config.extra_args or [],
                full_auto=self.config.full_auto,
                skip_git_repo_check=self.config.skip_git_repo_check,
                dangerous_yolo=self.config.dangerous_yolo,
            ),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, task: str, *, workdir: Path | None = None, seed_thread_id: str | None = None,
            failed_tool_ledger: Any | None = None,
            objective_for_skill: str | None = None) -> LoopOutcome:
        """Run one mission end-to-end.

        ``task`` is the *full* prompt the engineer sees (typically a long
        string with prelude, identity card, and live objective). It is
        the right thing to feed to the engineer because round prompts are
        meant to carry full context.

        ``objective_for_skill`` is the *clean* operator objective, with
        no prelude / boilerplate / identity-card prefix. It is what the
        skill matcher, the distiller, and ``task_history`` should see —
        otherwise we end up indexing skills under "### Memory context"
        boilerplate (literally happened, see commit history).
        Falls back to ``task`` when not supplied for back-compat.
        """
        workdir = Path(workdir) if workdir else Path.cwd()
        skill_task = (objective_for_skill or task).strip() or task
        self._emit({"type": "loop.start", "text": f"task: {skill_task[:120]}"})

        # Step 1: matcher
        matched, matcher_tokens = self.skill_store.find_relevant(
            skill_task, on_event=self.on_event,
        )
        matcher_input_tokens = int(
            getattr(self.skill_store, "last_match_input_tokens", 0) or 0
        )
        matcher_cached_input_tokens = int(
            getattr(self.skill_store, "last_match_cached_input_tokens", 0) or 0
        )
        matcher_output_tokens = int(
            getattr(self.skill_store, "last_match_output_tokens", 0) or 0
        )
        skill: Skill | None = matched[0] if matched else None
        skill_distilled = False
        distill_result = None

        # Step 2: distill on miss
        if skill is None and self.config.distill_on_miss:
            self._emit({"type": "scientist.start", "text": "no high-fit skill — distilling"})
            try:
                distill_result = self.distiller.distill(
                    task_description=skill_task,
                    config=DistillerConfig(
                        model=self.config.scientist_model,
                        reasoning_effort=self.config.scientist_reasoning_effort,
                        extra_args=self.config.extra_args,
                        skip_git_repo_check=self.config.skip_git_repo_check,
                        full_auto=self.config.full_auto,
                    ),
                    on_event=self.on_event,
                )
                if distill_result.last_agent_message.strip():
                    skill = self.skill_store.save_distilled(
                        task_description=skill_task,
                        raw_distill_output=distill_result.last_agent_message,
                        scientist_model=self.config.scientist_model,
                        on_event=self.on_event,
                    )
                    skill_distilled = skill is not None
            except Exception as exc:
                log.warning("scientist distill failed (%s: %s); proceeding without skill",
                            type(exc).__name__, exc)
                self._emit({"type": "scientist.error",
                            "text": f"distill failed: {type(exc).__name__}"})

        skill_text = self.skill_store.render_skill(skill) if skill else ""
        skill_name = skill.name if skill else None

        # Step 3: supervised round-loop
        def build_prompt(next_action: str | None) -> str:
            extra = self._collect_extra_guidance()
            return self._build_engineer_prompt(
                task=task,
                skill_text=skill_text,
                next_action=next_action,
                extra_guidance=extra,
            )

        status, rounds, final_message, reason, last_thread_id = self.supervised.run(
            objective=task,
            engineer_prompt_builder=build_prompt,
            supervised_config=SupervisedConfig(
                max_rounds=self.config.max_rounds,
                check_commands=list(self.config.check_commands),
                check_timeout_seconds=self.config.check_timeout_seconds,
                no_progress_threshold=self.config.no_progress_threshold,
                backend_failure_threshold=self.config.backend_failure_threshold,
                backend_failure_backoff_seconds=self.config.backend_failure_backoff_seconds,
                session_id=self.config.session_id,
            ),
            workdir=workdir,
            on_event=self.on_event,
            seed_thread_id=seed_thread_id,
            failed_tool_ledger=failed_tool_ledger,
        )

        # Step 4: skill writeback on success
        if status == "done" and skill is not None and self.config.skill_writeback:
            try:
                trajectory = self._summarize_trajectory(rounds)
                self.skill_store.writeback_from_trajectory(
                    skill=skill,
                    task_description=skill_task,
                    successful_trajectory=trajectory,
                    distiller=self.distiller if self.config.skill_revise_on_writeback else None,
                    scientist_model=self.config.scientist_model,
                    revise=self.config.skill_revise_on_writeback,
                    on_event=self.on_event,
                )
                self._emit({"type": "skill.writeback",
                            "text": f"wrote {skill.name} v{skill.version} back to store"})
            except Exception as exc:
                log.warning("skill writeback failed (%s: %s)", type(exc).__name__, exc)
                self._emit({"type": "skill.writeback.error",
                            "text": f"writeback failed: {type(exc).__name__}"})

        outcome = LoopOutcome(
            status=status,
            rounds=rounds,
            skill_used=skill_name,
            skill_distilled=skill_distilled,
            final_message=final_message,
            reason=reason,
            workdir=str(workdir),
            last_thread_id=last_thread_id,
        )
        # Effectiveness telemetry — one structured event per mission so
        # operators can compute hit-rate, mean-rounds-with-skill, and
        # mean-rounds-without-skill from events.jsonl alone.
        try:
            self._emit({
                "type": "skill.outcome",
                "skill_name": skill_name or "",
                "skill_hit": bool(skill_name) and not skill_distilled,
                "skill_distilled": bool(skill_distilled),
                "matcher_tokens": int(matcher_tokens or 0),
                "matcher_input_tokens": matcher_input_tokens,
                "matcher_cached_input_tokens": matcher_cached_input_tokens,
                "matcher_output_tokens": matcher_output_tokens,
                "distiller_tokens": int(
                    (getattr(distill_result, "input_tokens", 0) or 0)
                    + (getattr(distill_result, "output_tokens", 0) or 0)
                ),
                "distiller_input_tokens": int(getattr(distill_result, "input_tokens", 0) or 0),
                "distiller_cached_input_tokens": int(
                    getattr(distill_result, "cached_input_tokens", 0) or 0
                ),
                "distiller_output_tokens": int(
                    getattr(distill_result, "output_tokens", 0) or 0
                ),
                "rounds": int(len(rounds)),
                "status": str(status),
                "success": bool(status == "done"),
            })
        except Exception:  # noqa: BLE001
            log.debug("skill.outcome emit failed", exc_info=True)
        self._emit({
            "type": "loop.done",
            "text": f"status={status} rounds={len(rounds)} reason={reason[:80]}",
        })
        return outcome

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:  # never let UI errors kill the loop
            log.exception("on_event handler raised")

    @staticmethod
    def _build_engineer_prompt(
        *,
        task: str,
        skill_text: str,
        next_action: str | None,
        extra_guidance: list[str] | None = None,
    ) -> str:
        sections: list[str] = []
        if skill_text:
            sections.append("## Skill playbook (read first)\n" + skill_text)
        sections.append("## Task\n" + task)
        if SkillLoop._looks_like_paper_objective(task):
            sections.append(
                "## Long-horizon paper execution contract\n"
                "This is not a one-file bounded patch. Treat the engineer as the\n"
                "owner of the paper trajectory for this mission.\n\n"
                "- Read `AGENTS.md` and the relevant built-in paper skills before\n"
                "  choosing the scope of work.\n"
                "- Run or inspect `python -m argus_skill.skills.pipeline_contracts\n"
                "  validate-full-emnlp --project-root .` early when feasible; use\n"
                "  the failures as the roadmap. If it is too expensive, run the\n"
                "  narrower reported validators and explain why.\n"
                "- Fix multiple adjacent blockers in one mission when budget allows:\n"
                "  evidence, `paper/main.tex`, body/page flow, citations, figures,\n"
                "  tables, reviews, assurance, manifest freshness, and submission\n"
                "  state.\n"
                "- Do not stop after one narrow check passes if obvious paper-quality\n"
                "  blockers remain and are addressable in this mission.\n"
                "- For underfilled papers, improve reader-facing prose, evidence\n"
                "  integration, and figure/table placement toward 7.5-8 main-content\n"
                "  pages; keep main/body content within 8 pages, start references\n"
                "  and appendices on page 9 or later, and do not impose a total-page\n"
                "  maximum after references begin.\n"
                "- If the full gate still fails, end with the exact remaining blockers\n"
                "  and the next concrete command."
            )
        if next_action:
            sections.append(
                "## Reviewer guidance from prior round\n"
                "The previous round was judged incomplete. Address the\n"
                "following before declaring done:\n\n"
                + next_action
            )
        if extra_guidance:
            sections.append(
                "## Operator guidance (injected since last round)\n"
                + "\n\n".join(extra_guidance)
            )
        from .tools.validator_toolbelt import format_validator_toolbelt_for_role

        validator_toolbelt = format_validator_toolbelt_for_role("engineer")
        if validator_toolbelt:
            sections.append(validator_toolbelt)
        sections.append(
            "## Required output\n"
            "Make concrete progress: read files, run commands, edit code as\n"
            "needed.\n\n"
            "End your response with a fenced markdown section titled\n"
            "**`## Verification (verbatim)`** containing the *literal stdout*\n"
            "of every acceptance command you ran this round — pytest summary\n"
            "line, ruff result, mypy result, coverage table, `ls` output,\n"
            "etc. Quote the actual lines, not paraphrases. Use a fenced\n"
            "code block. The reviewer is text-only and must see the real\n"
            "command output to judge completion; without it the round will\n"
            "be marked `continue` and burn another cycle.\n\n"
            "Below the verification block, add a short `## Summary`\n"
            "section (≤8 bullets) describing what you changed."
        )
        return "\n\n".join(sections)

    @staticmethod
    def _looks_like_paper_objective(text: str) -> bool:
        normalized = str(text or "").casefold()
        if any(marker in normalized for marker in _PAPER_OBJECTIVE_SUBSTRINGS):
            return True
        tokens = set(re.findall(r"[a-z0-9]+", normalized))
        return bool(tokens & _PAPER_OBJECTIVE_WORDS)

    def _collect_extra_guidance(self) -> list[str]:
        if self.extra_guidance_provider is None:
            return []
        try:
            collected = self.extra_guidance_provider() or []
        except Exception:  # never let a hook raise into the loop
            log.exception("extra_guidance_provider raised")
            return []
        return [str(item).strip() for item in collected if str(item).strip()]

    @staticmethod
    def _summarize_trajectory(rounds: list[RoundRecord]) -> str:
        lines: list[str] = []
        for r in rounds:
            lines.append(f"### Round {r.round_index} (review={r.review.status}, conf={r.review.confidence:.2f})")
            if r.engineer_message:
                snippet = r.engineer_message.strip()
                if len(snippet) > 800:
                    snippet = snippet[:800] + "…"
                lines.append(snippet)
            for c in r.checks:
                tag = "PASS" if c.passed else "FAIL"
                lines.append(f"- [{tag}] `{c.command}` (exit={c.exit_code})")
            if r.review.round_summary_markdown:
                lines.append(r.review.round_summary_markdown.strip())
            lines.append("")
        return "\n".join(lines).strip()


__all__ = ["SkillLoop", "SkillLoopConfig"]
