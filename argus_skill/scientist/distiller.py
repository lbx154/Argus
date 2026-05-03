"""The scientist's distill step.

Calls the configured runner with ``Prompts.distill(task)`` and returns
the raw markdown. Persistence happens in ``SkillStore.save_distilled``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.models import RunnerOptions, RunnerResult
from ..core.ports import RunnerBackend
from .prompts import Prompts


@dataclass
class DistillerConfig:
    model: str
    reasoning_effort: str | None = "high"
    extra_args: list[str] | None = None
    skip_git_repo_check: bool = True
    full_auto: bool = True


class Distiller:
    def __init__(self, runner: RunnerBackend) -> None:
        self.runner = runner

    def distill(
        self,
        *,
        task_description: str,
        config: DistillerConfig,
        workdir_context: str = "",
        on_event: Callable[[dict], None] | None = None,
    ) -> RunnerResult:
        prompt = Prompts.distill(task_description, workdir_context)
        if on_event:
            on_event({"type": "distill.start",
                      "text": f"distilling skill via {config.model}"})
        result = self.runner.run_exec(
            prompt=prompt,
            options=RunnerOptions(
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                extra_args=config.extra_args,
                skip_git_repo_check=config.skip_git_repo_check,
                full_auto=config.full_auto,
            ),
            run_label="distiller",
        )
        if on_event:
            on_event({
                "type": "distill.done",
                "text": (
                    f"distilled ({len(result.last_agent_message)} chars, "
                    f"{result.input_tokens + result.output_tokens:,} tok)"
                ),
            })
        return result
