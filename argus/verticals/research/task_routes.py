"""Which model route a research task belongs to.

Figure work is the one kind of Engineer task whose result is judged by
looking at a picture: the method figure through PPT Master and the data
figures through the paper_charts helper. An operator may point that work at
an image-capable model with the ``ARGUS_SKILL_FIGURE_MODEL`` knob while every
other task keeps the engineer model. The host recognises a figure task from
its text (the sources, exports and tools such a task names), so the Planner
needs no new field, and a writing task that merely includes figures is not
routed.
"""
from __future__ import annotations

import re

FIGURE_ROUTE = "figure"
# Named only by figure tasks: the editable source, the exporter, the data-figure helper, its record.
_FIGURE_SOURCES = re.compile(r"\.pptx\b|pptx_export|paper_charts|figures/src/[^\s]*facts\.json", re.IGNORECASE)
# Figure work in words: a drawing verb close to a figure noun.
_FIGURE_WORK = re.compile(
    r"\b(?:draw|redraw|author|compose|design|export|render|produce|make|create|fix|repair)\w*\b"
    r"[^.\n]{0,60}?\b(?:method|architecture|framework|overview|mechanism|teaser|data|results?|main|ablation)\s+figures?\b",
    re.IGNORECASE,
)


def model_route_for_task(text: str) -> str:
    """``"figure"`` when the task is figure work, else ``""``."""
    body = str(text or "")
    if _FIGURE_SOURCES.search(body) or _FIGURE_WORK.search(body):
        return FIGURE_ROUTE
    return ""


__all__ = ["FIGURE_ROUTE", "model_route_for_task"]
