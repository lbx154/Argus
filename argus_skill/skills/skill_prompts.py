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
