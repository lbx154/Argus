"""Prompt templates for skill matching, distillation and revision.

Every string the models see passes through :class:`Prompts`. Keeping the
prompts in one module makes it easy to A/B-test wording changes across
the whole skill-memory pipeline.
"""
from __future__ import annotations

from .role_context import format_role_context

_AUTHOR_ROLE_SKILL = "argus-author-role.md"
_AUTHOR_ROLE_FALLBACK = """# Skill-memory authoring

Skill-memory work in argus-skill: match skills conservatively, distill reusable
capability playbooks, and revise skills from evidence without hard-coding one
task's solution. Distilled skills are written for the engineer model that will
execute them, so they must be explicit and executable.
"""


def _author_role_context() -> str:
    return format_role_context(
        "Argus author role skill",
        _AUTHOR_ROLE_SKILL,
        _AUTHOR_ROLE_FALLBACK,
    )


class Prompts:

    # -- Step 2: Skill matching (small model) --
    @staticmethod
    def skill_match(
        task_description: str,
        summaries: list[dict],
        *,
        requesting_role: str | None = None,
    ) -> str:
        from .store import ROLE_SKILL_POOLS

        primary_pool = (
            ROLE_SKILL_POOLS.get(requesting_role, frozenset())
            if requesting_role else frozenset()
        )

        def _role_tag(s: dict) -> str:
            if not requesting_role:
                return ""
            skill_role = s.get("role", "general")
            kind = "OWN" if skill_role in primary_pool else f"REFERENCE/{skill_role}"
            return f" [{kind}]"

        listing = "\n".join(
            (
                f"- **{s['name']}**{_role_tag(s)}: {s['description']} "
                f"(category: {s['category'] or 'unspecified'})"
                + ((" | past tasks: " + ", ".join(s["task_history"][:3])) if s.get("task_history") else "")
            )
            for s in summaries
        )
        role_note = ""
        if requesting_role and primary_pool:
            role_note = (
                f"\n\nYou are matching skills for the **{requesting_role}** role. "
                "Skills tagged `[OWN]` are this role's own playbooks. Skills "
                "tagged `[REFERENCE/<role>]` belong to a *different* role and "
                "are only useful as context (e.g. anticipating that role's "
                "standards) — match one ONLY when genuinely high-fit, and never "
                "as a substitute for an OWN skill.\n"
            )
        return (
            _author_role_context()
            +
            "You are a skill matcher. Given a task and a list of available "
            "skills, decide which (if any) actually fit. A WRONG skill is "
            "worse than NO skill — it will steer the engineer down the "
            "wrong sub-domain. Be strict: a borderline match is worse than "
            "none. Most tasks have one matching skill or none — but when "
            "several skills EACH independently clear the `high` bar, return "
            "all of them and let the engineer make the final relevance call."
            + role_note
            + "\n"
            f"## Task\n{task_description}\n\n"
            f"## Available Skills\n{listing}\n\n"
            "## Instructions\n"
            "Reply with ONLY a JSON object of the shape:\n"
            "{\"matched\": [{\"name\": \"skill-name\", "
            "\"fit\": \"high|medium|low\", \"why\": \"<one short clause>\"}, ...]}\n"
            "\n"
            "Grading rubric (anchor each judgement explicitly):\n"
            "- `high`  — the skill's capability *subsumes* this task: "
            "following its 'How to solve' would solve THIS task with at "
            "most parameter substitution. Same sub-domain, same tools, "
            "same goal.\n"
            "- `medium` — the skill addresses an adjacent capability or "
            "covers part of this task's workflow but not the goal "
            "(e.g. shares a tool, language, or input format but the "
            "central activity differs).\n"
            "- `low`   — only superficial keyword overlap (e.g. both "
            "mention 'sanitize' but in unrelated domains). Do NOT include "
            "these.\n"
            "\n"
            "Rules:\n"
            "1. Return `high` matches only when the rubric is genuinely "
            "satisfied. If unsure between high and medium, pick medium.\n"
            "2. List EVERY skill that genuinely clears the `high` bar, "
            "ordered most-relevant first. Do not cap the count artificially, "
            "but never pad with speculative entries — most tasks yield zero "
            "or one.\n"
            "3. Empty list `{\"matched\": []}` is a perfectly fine answer "
            "and is preferable to a `low` or speculative `medium` match.\n"
            "4. Backward-compat: bare strings in `matched` are still "
            "accepted and treated as `high`."
        )

    # -- Skill-library independence check (LLM judge, progressive disclosure) --
    @staticmethod
    def skill_duplicate_check(
        *,
        name: str,
        description: str,
        category: str,
        summaries: list[dict],
    ) -> str:
        """Ask a small model whether a NEW skill proposal duplicates an
        EXISTING one — semantic judgment over compact summaries (name +
        description + category), never full skill bodies, so this stays
        cheap even against a large library (the same progressive-disclosure
        shape ``skill_match`` already uses: summaries first, full content
        only for whatever the caller decides to load next).

        Catches paraphrased duplicates a pure lexical/cosine comparison
        misses (e.g. "Debug CUDA OOM" vs "Fix GPU memory overflow" — same
        capability, near-disjoint title vocabulary)."""
        listing = "\n".join(
            f"- **{s['name']}**: {s['description']} "
            f"(category: {s['category'] or 'unspecified'})"
            for s in summaries
        )
        return (
            "You are the skill-library independence judge. A NEW skill "
            "playbook has been proposed for a shared, reusable capability "
            "library. Decide whether it is a near-duplicate of an EXISTING "
            "skill — i.e. an engineer who already had the existing skill "
            "would gain NOTHING new from also having this one, because they "
            "teach the SAME underlying capability (even if the wording, "
            "title, or framing differs).\n\n"
            f"## New skill proposal\n"
            f"- **{name}**: {description} (category: {category or 'unspecified'})\n\n"
            f"## Existing skills in the library\n{listing or '(library is empty)'}\n\n"
            "## Instructions\n"
            "Reply with ONLY a JSON object: "
            "{\"duplicate\": true|false, \"of\": \"<existing skill name or "
            "empty string>\", \"why\": \"<one short clause>\"}.\n"
            "- `duplicate: true` ONLY when the new proposal teaches the SAME "
            "underlying capability as one existing skill (same sub-domain, "
            "same tools, same goal) — different title/description wording "
            "alone does NOT make two skills distinct.\n"
            "- Two skills that merely share a category, or overlap on a "
            "generic keyword, are NOT duplicates — `duplicate: false`.\n"
            "- When genuinely unsure, prefer `duplicate: false` (a missed "
            "near-duplicate is cheaply caught later; a wrongly-rejected "
            "distinct skill is a real capability lost)."
        )

    # -- Periodic library housekeeping (LLM judge, batched clustering) --
    @staticmethod
    def skill_compaction_batch(summaries: list[dict]) -> str:
        """Ask a small model to find every GROUP of near-duplicate skills in
        ONE batch of the library — the batched-clustering counterpart to
        ``skill_duplicate_check`` above, mirroring how ``skill_match`` scores
        a whole candidate batch in a single call rather than one call per
        pair (O(1) calls per batch instead of O(n^2) pairwise judge calls).

        Deliberately asks ONLY for the grouping (which names are the same
        capability), never which one to keep: the model only sees
        name/description/category here, not each skill's PROVEN reuse
        history (``version`` / ``task_history``) — a real, factual maturity
        signal the harness has and the model does not. The harness picks the
        representative from that data (see ``compaction._representative``);
        the model's job is purely "are these the same thing?"."""
        listing = "\n".join(
            f"- **{s['name']}**: {s['description']} "
            f"(category: {s['category'] or 'unspecified'})"
            for s in summaries
        )
        return (
            "You are the skill-library compaction judge, doing periodic "
            "housekeeping on a shared, reusable capability library. Below is "
            "a batch of skills currently in it (name + description + "
            "category only). Find every GROUP of 2+ skills that teach the "
            "SAME underlying capability (paraphrases of each other, even "
            "with disjoint title/description wording) — these are "
            "near-duplicates that should be merged down to ONE. You decide "
            "WHICH skills group together; the harness decides which one in "
            "each group to keep (from proven reuse history you cannot see) "
            "— do not try to rank them.\n\n"
            f"## Skills in this batch\n{listing}\n\n"
            "## Instructions\n"
            "Reply with ONLY a JSON object: "
            "{\"clusters\": [[\"<name>\", \"<name>\", ...], ...]} — a list "
            "of groups, each group a list of 2+ exact skill names.\n"
            "- Only emit a group when its skills are genuinely the SAME "
            "capability — same sub-domain, same tools, same goal. Sharing a "
            "category or a generic keyword is NOT enough.\n"
            "- Every name MUST be copied EXACTLY from the list above — "
            "never invent or paraphrase a name.\n"
            "- A skill may appear in AT MOST one group.\n"
            "- Most batches have NO groups at all — "
            "`{\"clusters\": []}` is the common, correct answer. When "
            "unsure whether two skills truly duplicate, leave them out "
            "(a missed near-duplicate is cheap to catch on a later pass; a "
            "wrongly-merged distinct skill is a real capability lost)."
        )

    # -- Parsing helpers --
    @staticmethod
    def parse_skill_output(raw: str) -> tuple[str, str, str, str]:
        """Extract name, description, category, content from distilled skill output."""
        import re
        def clean(text: str) -> str:
            text = text.strip()
            text = text.strip("*`# ").strip()
            return text

        def section_value(title: str) -> str:
            heading_re = re.compile(
                rf"^\s{{0,3}}#{{1,6}}\s*(?:\d+\.\s*)?{re.escape(title)}\s*:?\s*(?P<inline>.*)$",
                re.IGNORECASE | re.MULTILINE,
            )
            matches = list(heading_re.finditer(raw))
            if not matches:
                return ""

            match = matches[0]
            inline = clean(match.group("inline"))
            if inline and inline.casefold() != title.casefold():
                return inline

            next_heading = re.search(r"^\s{0,3}#{1,6}\s+", raw[match.end():], re.MULTILINE)
            body_end = match.end() + next_heading.start() if next_heading else len(raw)
            body = raw[match.end():body_end]
            for line in body.splitlines():
                value = clean(line)
                if value:
                    return value
            return ""

        # Accept multiple heading styles: new "Title" / "Name", old "Skill Name".
        name = (section_value("Title")
                or section_value("Name")
                or section_value("Skill Name")
                or section_value("Playbook"))
        description = section_value("Description")
        category = section_value("Category").strip("`'\"")

        if not name:
            # Fall back to the first non-meta markdown heading.
            for m in re.finditer(r"^\s{0,3}#{1,6}\s*(.+?)\s*$", raw, re.MULTILINE):
                candidate = clean(m.group(1))
                low = candidate.casefold()
                if not candidate:
                    continue
                if any(k in low for k in (
                    "skill name", "title", "description", "category",
                    "when to use", "how to", "examples", "step-by-step",
                    "step by step", "playbook",
                )):
                    continue
                name = candidate
                break

        # Last-resort sanity: if we still only got a generic placeholder,
        # leave as unnamed so the store generates a numbered slug instead.
        if name and name.casefold() in ("title", "name", "skill name"):
            name = ""

        return name or "unnamed-skill", description, category, raw
