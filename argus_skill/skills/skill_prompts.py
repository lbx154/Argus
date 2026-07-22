"""Prompt templates for skill matching, distillation and revision.

Every string the models see passes through :class:`Prompts`. Keeping the
prompts in one module makes it easy to A/B-test wording changes across
the whole skill-memory pipeline.
"""
from __future__ import annotations

_MATCHER_DESCRIPTION_CHARS = 240


def _matcher_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class Prompts:

    # -- Step 2: Skill matching (small model) --
    @staticmethod
    def skill_match(
        task_description: str,
        summaries: list[dict],
        *,
        requesting_role: str | None = None,
        primary_pool: frozenset[str] = frozenset(),
    ) -> str:
        def _role_tag(s: dict) -> str:
            if not requesting_role:
                return ""
            skill_role = s.get("role", "general")
            kind = "OWN" if skill_role in primary_pool else f"REFERENCE/{skill_role}"
            return f" [{kind}]"

        listing = "\n".join(
            (
                f"- ID `{s.get('candidate_id') or s.get('skill_id') or s['name']}` "
                f"— **{s['name']}**{_role_tag(s)}: "
                f"{_matcher_text(s['description'], _MATCHER_DESCRIPTION_CHARS)} "
                f"(category: {s['category'] or 'unspecified'})"
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
            "{\"matched\": [{\"id\": \"exact-candidate-ID\", "
            "\"name\": \"skill-name\", "
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

    # -- Periodic library housekeeping (LLM judge, batched clustering) --
    @staticmethod
    def skill_compaction_batch(summaries: list[dict]) -> str:
        """Ask a small model to find every GROUP of near-duplicate skills in
        ONE batch of the library, mirroring how ``skill_match`` scores a whole
        candidate batch in a single call rather than one call per pair (O(1)
        calls per batch instead of O(n^2) pairwise judge calls).

        Deliberately asks ONLY for the grouping (which names are the same
        capability), never which one to keep: the model only sees
        name/description/category here, not each skill's observed use
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
            reserved = {
                "skill name", "title", "name", "description", "category",
                "when to use", "when not to use", "how to solve", "examples",
                "step-by-step", "step by step", "playbook", "sources",
                "pitfalls", "task",
            }
            for m in re.finditer(r"^\s{0,3}#{1,6}\s*(.+?)\s*$", raw, re.MULTILINE):
                candidate = clean(m.group(1))
                low = candidate.casefold()
                if not candidate:
                    continue
                if low in reserved:
                    continue
                name = candidate
                break

        # Last-resort sanity: if we still only got a generic placeholder,
        # leave as unnamed so the store generates a numbered slug instead.
        if name and name.casefold() in ("title", "name", "skill name"):
            name = ""

        return name or "unnamed-skill", description, category, raw
