"""Prompt templates used by the scientist and the engineer.

Every string the models see passes through :class:`Prompts`. Keeping the
prompts in one module makes it easy to A/B-test wording changes across
the whole pipeline.
"""
from __future__ import annotations

from ..skills.role_context import format_role_context

_SCIENTIST_ROLE_SKILL = "argus-scientist-role.md"
_SCIENTIST_ROLE_FALLBACK = """# Argus Scientist Role

The Scientist is argus-skill's skill-memory role: match skills conservatively,
distill reusable capability playbooks, and revise skills from evidence without
hard-coding one task's solution. Distilled skills are written for gpt-5.4-mini,
a relatively small engineer model, so they must be explicit and executable.
"""


def _scientist_role_context() -> str:
    return format_role_context(
        "Argus scientist role skill",
        _SCIENTIST_ROLE_SKILL,
        _SCIENTIST_ROLE_FALLBACK,
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
        from ..skills.store import ROLE_SKILL_POOLS

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
            _scientist_role_context()
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

    # -- Step 3: Skill distillation (big model) --
    @staticmethod
    def distill(task_description: str, workdir_context: str = "", guidance: str = "") -> str:
        """Unified distill prompt.

        The playbook is intent-agnostic and CAPABILITY-level. It must
        describe how to handle a FAMILY of tasks, not the single example
        in front of you, because every distilled skill is cached and
        consulted by future task matching.

        ``guidance`` is the operator's skill-authoring meta-skill, injected
        verbatim so the HOW (what a good skill is, generalize/don't-bloat,
        method-not-answer, you-are-judged-by-effect) lives in a human-written
        skill rather than hardcoded here.
        """
        return (
            _scientist_role_context()
            +
            (f"## Skill-authoring guidance (read first)\n{guidance}\n\n" if guidance else "")
            +
            "You are a senior engineer compiling a CAPABILITY playbook for "
            "`gpt-5.4-mini`, a relatively small engineer model. The playbook "
            "must be explicit enough for that smaller model to execute without "
            "guessing: include ordering, anti-conditions, exact artifacts, "
            "validation commands, and common failure modes. It will be CACHED and "
            "REUSED for many future tasks of the same kind, so you must "
            "resist every instinct toward task-specific hardcoding.\n\n"
            "## Mandatory external grounding before writing\n"
            "Before drafting the playbook, use Codex's web/search capability to "
            "look for how experienced practitioners solve this class of problem "
            "(docs, issue threads, papers, postmortems, or production guides). "
            "Synthesize those external patterns with the local task evidence and "
            "your own reasoning; do not merely copy snippets. If search is "
            "temporarily unavailable, proceed from local evidence but make the "
            "playbook conservative and avoid claiming external consensus. Fold "
            "the useful findings into `How to solve`, `Common pitfalls`, and "
            "`When NOT to use` rather than adding a new top-level section.\n\n"
            f"## Incoming task (one example of the category)\n{task_description}\n\n"
            f"{f'## Repository context{chr(10)}{workdir_context}{chr(10)}{chr(10)}' if workdir_context else ''}"
            "## Output format (STRICT)\n"
            "Return ONLY plain markdown with EXACTLY these headings, in this "
            "order. Do NOT wrap in code fences, do NOT consult any external "
            "skill-creator template — write the playbook yourself.\n\n"
            "## Title\n"
            "<3-6 words naming a CAPABILITY (NOT a single task). Good: "
            "'Crack archive password', 'Bring up qemu Alpine VM', "
            "'Build C extension via Cython'. Bad: 'Solve crack-7z-hash', "
            "'fix-ocaml-gc'.>\n\n"
            "## Description\n"
            "<one sentence describing the FAMILY of tasks. Do NOT include "
            "the specific archive name, file path, or numeric value from "
            "the incoming example.>\n\n"
            "## Category\n"
            "<short slug like 'archive-crack', 'qemu-bringup', "
            "'c-binary-recreation'>\n\n"
            "## When to use\n"
            "<3-5 bullets describing FAMILIES of tasks this applies to. "
            "Each bullet should match many possible tasks, not only the "
            "incoming example.>\n\n"
            "## When NOT to use\n"
            "<3-5 bullets describing tasks that LOOK similar (share a "
            "keyword, tool, or sub-domain) but are NOT covered. Be "
            "concrete: 'this skill is for X, not for Y'. Examples of "
            "good anti-conditions: 'configuring nginx access_log format "
            "(use a logging-pipeline skill instead)', 'rewriting git "
            "history (use a repo-hygiene skill instead)'. The engineer "
            "MUST abandon this skill if any anti-condition matches the "
            "incoming task.>\n\n"
            "## How to solve\n"
            "<concrete steps that work for the WHOLE family. Use angle-"
            "bracket placeholders like <archive_path>, <output_file>, "
            "<password_list> for inputs that vary task-to-task. Include a "
            "'Common pitfalls' subsection listing 2-4 traps you've seen "
            "(missing tools, format quirks, environment differences).>\n\n"
            "## Examples\n"
            "<1-2 short sketches of DIFFERENT concrete instances of the "
            "family — not paraphrases of the incoming task.>\n\n"
            "## Response shape\n"
            "A single bullet: for this class of task, does the small "
            "engineer usually need to (a) reply inline with an "
            "explanation/review/diagnosis, or (b) write or modify files on "
            "disk? Pick exactly one.\n\n"
            "## Generality check (MANDATORY before you reply)\n"
            "Mentally re-read your playbook with a different task in the "
            "same family substituted in. If your steps reference any "
            "concrete value (path, integer, archive name) from the "
            "incoming example, replace it with a placeholder. If your "
            "title or description names the example task, rewrite it as a "
            "capability.\n\n"
            "## Coverage check (MANDATORY — counter-pull to Generality)\n"
            "Now read the playbook BACK against the incoming task. Could "
            "an engineer who knew nothing about the task except your "
            "playbook actually solve it? Specifically:\n"
            "- Does your `When to use` list a family that *contains* the "
            "incoming task (not just one of its siblings)?\n"
            "- Does your `How to solve` produce the artefact / change / "
            "answer the incoming task asks for, with at most placeholder "
            "substitution?\n"
            "- Does your `When NOT to use` accidentally exclude the "
            "incoming task? If yes, fix it.\n"
            "If ANY of these checks fail, NARROW the capability "
            "(retitle, retighten `When to use`, rewrite steps) until the "
            "playbook unambiguously covers the incoming task. The "
            "Generality and Coverage checks together force a capability "
            "that is BROADER than the task example but still STRICTLY "
            "ENCLOSES it.\n\n"
            "Keep the whole document under 1500 words. Be specific to the "
            "FAMILY but not to this single task. Do NOT hardcode "
            "reproducer-then-delete gymnastics unless the family truly "
            "requires them."
        )

    # -- Step 3b: Skill revision (big model) --
    @staticmethod
    def revise(
        *,
        old_skill_md: str,
        task_description: str,
        change_kind: str,
        evidence: str,
        guidance: str = "",
    ) -> str:
        """Produce a revised playbook that integrates new evidence.

        ``change_kind`` is one of:
        - ``"success_trajectory"``: a new task in the same family was just
          solved using this playbook. The trajectory may reveal a hidden
          step, pitfall, or anti-condition that should be promoted into
          the playbook for next time.
        - ``"failure_lesson"``: the reviewer flagged ``failure_cause==
          skill_gap`` and emitted a one-paragraph lesson the engineer
          needed but the playbook did not give. Integrate the lesson
          (typically as a new ``Common pitfalls`` bullet, a tightened
          ``How to solve`` step, or a new ``When NOT to use`` entry).

        The output MUST stay strictly within the same heading structure
        as the input and remain CAPABILITY-level. Narrowing the family
        scope to fit only the new example is a regression — the matcher
        relies on broad ``When to use`` / ``Description`` to recall the
        skill across future tasks.
        """
        kind_directive = {
            "success_trajectory": (
                "A new task in this skill's family was just solved using "
                "the playbook. Read the trajectory: did the engineer have "
                "to discover anything the playbook did not say? If yes, "
                "fold that knowledge in (a sharper step, a new pitfall, a "
                "tighter anti-condition). If the playbook already covered "
                "everything the engineer did, output the playbook UNCHANGED "
                "(byte-for-byte the same markdown body) — do not rewrite "
                "for the sake of rewriting."
            ),
            "failure_lesson": (
                "The reviewer judged that this playbook had a gap that "
                "caused the engineer to fail, and emitted a lesson. "
                "Integrate the lesson into the playbook so a future "
                "engineer with only the playbook (not the lesson) would "
                "not hit the same gap. Prefer adding to ``Common pitfalls`` "
                "or sharpening a ``How to solve`` step over inventing new "
                "headings."
            ),
        }.get(change_kind, (
            "Integrate the evidence below into the playbook with the "
            "minimum edit that prevents a future engineer from repeating "
            "the same gap."
        ))

        return (
            _scientist_role_context()
            +
            (f"## Skill-authoring guidance (read first)\n{guidance}\n\n" if guidance else "")
            +
            "You are a senior engineer revising a CAPABILITY playbook. "
            "The target reader is `gpt-5.4-mini`, a relatively small engineer "
            "model, so revisions must make the playbook more explicit and "
            "operational rather than relying on senior-model inference. "
            "The playbook is CACHED and REUSED for many future tasks of "
            "the same kind, so any edit must broaden capability, not "
            "narrow it to the specific incoming example.\n\n"
            "## Mandatory external grounding before revising\n"
            "Before revising, use Codex's web/search capability to look for how "
            "others diagnose and fix this failure class (docs, issue threads, "
            "papers, postmortems, production guides). Combine those findings "
            "with the reviewer lesson and the existing playbook. Do not copy a "
            "single external recipe blindly; generalize the consensus into the "
            "existing `How to solve`, `Common pitfalls`, or `When NOT to use` "
            "sections. If search is unavailable, still revise from evidence but "
            "keep the edit narrow and explicitly conservative inside the existing "
            "sections.\n\n"
            f"## Existing playbook (current version)\n{old_skill_md}\n\n"
            f"## Incoming task that just exercised the playbook\n{task_description}\n\n"
            f"## New evidence ({change_kind})\n{evidence}\n\n"
            f"## What to do\n{kind_directive}\n\n"
            "## Hard rules\n"
            "1. Keep the SAME heading structure as the existing playbook: "
            "``Title``, ``Description``, ``Category``, ``When to use``, "
            "``When NOT to use``, ``How to solve`` (with ``Common "
            "pitfalls``), ``Examples``, ``Response shape``, ``Generality "
            "check``, ``Coverage check``. Do not introduce or drop "
            "top-level sections.\n"
            "2. PRESERVE ``Title``, ``Description``, and ``Category`` "
            "verbatim unless the family scope itself genuinely changed. "
            "If you cannot justify a rename in one sentence in the "
            "Generality check, leave them alone.\n"
            "3. DO NOT hardcode the specific task: no concrete paths, "
            "issue numbers, repository names, function signatures, or "
            "literal values from the incoming example. Use angle-bracket "
            "placeholders like ``<token_store>`` instead.\n"
            "4. Re-run the embedded Generality check and Coverage check. "
            "If the revised playbook now excludes the incoming task or a "
            "previous family member, undo that change.\n"
            "5. Output ONLY the revised markdown playbook (with the same "
            "headings, no frontmatter). No code fences, no commentary."
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
