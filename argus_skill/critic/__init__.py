"""Critic sub-agent — proposes concrete improvements after a `done` mission.

This is what enables true 7×24 iteration: when a mission's reviewer
verdict is ``done``, the supervisor hands the produced artefacts to a
:class:`Critic`. The critic returns a (possibly empty) list of
:class:`Improvement` records. If the list is non-empty AND the item's
iteration budget / cycle ceiling has not yet been reached, the
supervisor requeues the item with a polished objective derived from
those improvements. Low-impact or unevidenced suggestions are filtered
out before they can burn another round. If the list is empty, the item
is finalized as truly done and continuous mode hands control back to the
planner for the next valuable mission.

The :meth:`Critic.plan_next` method extends the critic into a
*planner* role for continuous 24/7 project improvement.
"""

from .critic import (
    Critic,
    CriticConfig,
    CriticVerdict,
    Improvement,
    PlannerVerdict,
    TaskSpec,
    parse_critic_text,
    parse_planner_text,
    render_iteration_objective,
)

__all__ = [
    "Critic",
    "CriticConfig",
    "CriticVerdict",
    "Improvement",
    "PlannerVerdict",
    "TaskSpec",
    "parse_critic_text",
    "parse_planner_text",
    "render_iteration_objective",
]
