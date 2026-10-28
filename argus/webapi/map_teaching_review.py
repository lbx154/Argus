"""Teaching rules and JSON-schema helpers shared by the map reader prompts."""

from __future__ import annotations

# Shared by the reading-answer and foundation prompts.
MARKDOWN_TEACHING_CORE = r"""Teach for a reader who knows everyday language and basic arithmetic; technical words in the question or source do not imply prior knowledge. Give necessary meanings before names and notation, within the requested output's scope.

Supplied records are evidence of this run's choices, work and reported results. Accurate standard definitions, relations and conventions may go beyond those records; label them as background. Missing run details do not prevent teaching that background, and background does not establish what this run did or proved. Keep teaching examples separate from recorded data.

Before teaching a comparison, define both actual objects or quantities, what they measure, how they are constructed, their domains, assumptions and conventions. Explain any normalization and its factors before applying an operation; state why the normalized objects qualify for that operation. Work through the requested relation, not just an auxiliary test or schematic equality. State specifically what cannot be supplied accurately.

Preserve quantifiers, assumptions and implication directions. Distinguish inclusion from equality and an unproved converse from a refutation. 'Not all' does not mean 'none'; an unproved claim is not thereby false. A counterexample meets the hypotheses and violates the conclusion; failure of a sufficient condition alone is not a refutation.

Use readable words for titles, labels and summary fields. In mathematical Markdown bodies use \(...\) for inline and \[...\] for display math, never backticks or code blocks around formulas."""


def _object(properties: dict) -> dict:
    return {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False,
    }


def _string(limit: int) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": limit}
