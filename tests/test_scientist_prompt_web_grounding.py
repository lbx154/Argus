from __future__ import annotations

from argus_skill.skills.skill_prompts import Prompts


def test_distill_prompt_requires_external_grounding() -> None:
    prompt = Prompts.distill("debug recurring CUDA OOM in a supervised run")

    assert "Mandatory external grounding before writing" in prompt
    assert "use Codex's web/search capability" in prompt
    assert "Synthesize those external patterns" in prompt
    assert "Fold the useful findings into `How to solve`" in prompt


def test_revise_prompt_requires_external_grounding() -> None:
    prompt = Prompts.revise(
        old_skill_md="## Title\nDebug Runs\n",
        task_description="debug recurring CUDA OOM",
        change_kind="failure_lesson",
        evidence="OOM recurred because batch sizing advice was missing.",
    )

    assert "Mandatory external grounding before revising" in prompt
    assert "use Codex's web/search capability" in prompt
    assert "others diagnose and fix this failure class" in prompt
    assert "Combine those findings with the reviewer lesson" in prompt
