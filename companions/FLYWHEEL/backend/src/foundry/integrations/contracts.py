from __future__ import annotations

from typing import Any, Protocol


class PromptCompiler(Protocol):
    """Extension point for the versioned Prompt Factory service."""

    def compile(self, venue: dict[str, Any], idea: dict[str, Any], context: dict[str, Any]) -> str: ...


class ResearchViewer(Protocol):
    """Extension point for an independently deployed reviewer/viewer service."""

    async def request_review(
        self, campaign: dict[str, Any], rubric: dict[str, Any]
    ) -> dict[str, Any]: ...


class ViewerNotConfigured:
    async def request_review(
        self, campaign: dict[str, Any], rubric: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "accepted": True,
            "state": "queued",
            "detail": "Review is queued; configure an external viewer worker to consume it.",
        }
