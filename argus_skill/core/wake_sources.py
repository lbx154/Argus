"""The wake sources the host can actually observe.

Kept here rather than beside the validator so the Planner prompt can render the
same tuple the host checks against. The Planner has to name one and was never
shown the vocabulary: in four hours run-05 proposed operator_answer,
operator_message, artifact_change and project_state -- each a plausible synonym
for a real entry -- and had sixteen waiting contracts rejected for it.
"""

from __future__ import annotations

SUPPORTED_WAKE_SOURCES = (
    "authorization",
    "manager_stage",
    "artifact_revision",
    "subagent_terminal",
    "subagent_state",
)
