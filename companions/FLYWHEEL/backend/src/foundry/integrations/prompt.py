"""Compatibility facade for the single authoritative Prompt Factory.

New code should import :class:`foundry.services.PromptCompiler` directly. This
module deliberately delegates instead of maintaining a second prompt template;
that prevents safety, resource and scientific-integrity rules from drifting.
"""

from __future__ import annotations

from typing import Any

from ..services.prompt_compiler import PromptCompiler


class StructuredPromptCompiler:
    """Deprecated legacy-shaped adapter backed by PromptCompiler v2+."""

    def compile(self, venue: dict[str, Any], idea: dict[str, Any], context: dict[str, Any]) -> str:
        deadline = context.get("deadline") or {}
        resource = context.get("resource") or {}
        capacity = resource.get("capacity") or {}
        gpu_count = capacity.get("gpu_count")
        if gpu_count is None:
            gpu_count = len(capacity.get("devices") or [])

        evidence = deadline.get("evidence_status") or "unconfirmed"
        if evidence == "official_confirmed":
            deadline_contract = (
                f"{deadline.get('deadline_date', 'TBA')} {deadline.get('timezone', '')} "
                "[official_confirmed]"
            ).strip()
        elif deadline:
            lower = deadline.get("forecast_window_start") or deadline.get("deadline_date", "TBA")
            upper = deadline.get("forecast_window_end") or deadline.get("deadline_date", "TBA")
            deadline_contract = (
                f"point estimate {deadline.get('deadline_date', 'TBA')} [forecast, not fact]; "
                f"planning interval {lower}..{upper}; schedule against {lower}"
            )
        else:
            deadline_contract = "rolling/TBA; operator-supplied internal cutoff"

        domain = context.get("domain") or {
            "name": venue.get("category_zh") or venue.get("category_id") or "Research domain",
            "evidence_requirements": [
                "Freeze a decisive falsifier and preserve every run, including negative results",
                "Use only traceable, license-compatible public evidence and real baselines",
            ],
        }
        normalized_idea: dict[str, Any] = {
            "title": idea.get("title_zh"),
            "problem_gap": idea.get("problem_gap"),
            "mechanism_hypothesis": idea.get("core_hypothesis"),
            "method_seed": idea.get("method"),
            "public_data_or_tasks": idea.get("public_data_or_tasks"),
            "baseline_candidates": [idea.get("strongest_baselines")],
            "decisive_experiment": idea.get("decisive_experiments"),
            "predicted_observation": (
                "Must be preregistered after the portfolio scan; this seed claims no result direction"
            ),
            "kill_criterion": idea.get("kill_criterion"),
            "oral_aspiration": True,
        }
        for field in PromptCompiler.LOCKED_REQUIRED:
            if field in context:
                normalized_idea[field] = context[field]
            elif field in idea:
                normalized_idea[field] = idea[field]

        compiled = PromptCompiler().compile(
            venue={
                "name": venue.get("display_name") or venue.get("official_name"),
                "edition": str(deadline.get("conference_year") or "rolling"),
                "track": deadline.get("round_note") or "Full/Regular Paper",
                "deadline": deadline_contract,
                "scope": idea.get("venue_fit_reason") or "Must be verified against the official CFP",
                "policies": [
                    f"Deadline evidence is {evidence}; verify the official source before launch",
                    "Re-check anonymity, AI-use, ethics, page-limit and artifact rules",
                ],
            },
            domain=domain,
            idea=normalized_idea,
            resources={
                "gpu_count": gpu_count,
                "gpu_model": capacity.get("gpu_model") or ("API-only" if gpu_count == 0 else None),
                "gpu_hours": capacity.get("gpu_hours"),
                "wall_clock_deadline": capacity.get("wall_clock_deadline"),
                "max_parallel_jobs": capacity.get("max_parallel_jobs", 1),
                "api_budget": capacity.get("api_budget"),
            },
            phase=str(context.get("phase") or "portfolio"),
        )
        return compiled.prompt
