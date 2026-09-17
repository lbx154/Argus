"""A standalone foundation document for one explicitly submitted reader question.

Task IDs, attempts and research-event material belong to provenance/application
context in the caller. They are deliberately absent from this input contract.
"""

from __future__ import annotations

import json

from .map_teaching_review import MARKDOWN_TEACHING_CORE, _object, _string

QUESTION_MARKER = "\nReader's actual question (JSON):\n"
MARKDOWN_LIMIT = 32_000
MARKDOWN_OUTPUT_CONTRACT = f"""Return only the complete Markdown document. Its first line must be a single H1 heading written as '# Title', with a plain-text title of at most 160 characters. Follow it with a blank line and a nonempty answer. The complete document, including the heading, may contain at most {MARKDOWN_LIMIT} characters. Do not wrap the document in a code fence or a JSON object. Write mathematical backslashes literally in the Markdown; there is no JSON-string escaping layer."""


def foundation_request(question: str, locale: str) -> tuple[str, dict]:
    """Prepare text/schema only; the caller owns the single model invocation."""
    schema = _object({"title": _string(160), "markdown": _string(MARKDOWN_LIMIT)})
    language = "简体中文" if locale == "zh-CN" else "English"
    prompt = f"""Write a durable foundation explanation answering this reader's actual question, in {language}. The result is a standalone document the reader can return to as research progresses. It is not a summary of a current task, a proof attempt, or a report of work performed. Use no tools.

{MARKDOWN_TEACHING_CORE}

Start with what the reader wants to understand. If the question names a conjecture, criterion or technical claim, explain the relation it asserts; the name itself is not an explanation. Keep distinct the meaning of a claim and a method that could prove it.

Teach the necessary ideas in a connected order, explaining prerequisites where they are needed rather than sending the reader away to learn them. Use as many short sections as the question needs; there is no fixed section count. Explain what a record or representative keeps and ignores, and what equality or improvement means. State unfamiliar operation rules explicitly; ordinary arithmetic must not be silently assumed to apply to a new object.

Work through a specific example of the main relation, with concrete eligible inputs, the rules used, intermediate operations and the resulting comparison or representation. Then give another input or change a defining restriction and work through what changes, including an informative boundary or non-example. A reader should be able to repeat these operations using the explanations already given. Merely restating an existence claim with new letters is not a worked example. Checking one candidate is different from proving that every eligible target has a candidate; make that limit clear.

Small accurate textbook examples and explicitly identified teaching models are allowed. State exactly what a simplified model illustrates and what it does not establish. A purported instance of a theorem must meet its hypotheses. Do not invent measurements, experiments, citations, inspected files, or new research results. If the question depends on a specific record that has not been supplied, say what remains unknown instead of inventing that record.

Read the complete explanation as a beginner would: every essential object, operation and restriction in the answer must have acquired a usable meaning. Recalculate the examples and changed-input answer using only rules supplied in the document. Fill missing prerequisites rather than claiming the reader has understood. Keep any discussion of proof methods after the main relation is understandable, and distinguish a conjectured relationship from an established result or a teaching illustration.

Return a title and the complete document as ordinary Markdown. Use paragraphs and helpful headings. Do not use HTML or <details> blocks. A practice question may be followed by a plainly labeled worked answer; do not add scores, mastery labels, a course plan, implementation details, or progress/status narration. The length limit is room for necessary explanation, not a target to fill.

{MARKDOWN_OUTPUT_CONTRACT}"""
    prompt += QUESTION_MARKER + json.dumps(question, ensure_ascii=False)
    return prompt, schema
