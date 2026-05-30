"""Prompt templates used by the scientist and the engineer.

Every string the models see passes through :class:`Prompts`. Keeping the
prompts in one module makes it easy to A/B-test wording changes across
the whole pipeline.
"""
from __future__ import annotations

from ..skills.role_context import format_role_context

_ENGINEER_ROLE_SKILL = "argus-engineer-role.md"
_SCIENTIST_ROLE_SKILL = "argus-scientist-role.md"
_ENGINEER_ROLE_FALLBACK = """# Argus Engineer Role

The Engineer is argus-skill's execution arm: follow the task and active skill,
modify files or answer inline as requested, run concrete verification, and
report evidence for the Reviewer.
"""
_SCIENTIST_ROLE_FALLBACK = """# Argus Scientist Role

The Scientist is argus-skill's skill-memory role: match skills conservatively,
distill reusable capability playbooks, and revise skills from evidence without
hard-coding one task's solution. Distilled skills are written for gpt-5.4-mini,
a relatively small engineer model, so they must be explicit and executable.
"""


def _engineer_role_context() -> str:
    return format_role_context(
        "Argus engineer role skill",
        _ENGINEER_ROLE_SKILL,
        _ENGINEER_ROLE_FALLBACK,
    )


def _scientist_role_context() -> str:
    return format_role_context(
        "Argus scientist role skill",
        _SCIENTIST_ROLE_SKILL,
        _SCIENTIST_ROLE_FALLBACK,
    )


class Prompts:

    # -- Step 2: Skill matching (small model) --
    @staticmethod
    def skill_match(task_description: str, summaries: list[dict]) -> str:
        listing = "\n".join(
            (
                f"- **{s['name']}**: {s['description']} "
                f"(category: {s['category'] or 'unspecified'})"
                + ((" | past tasks: " + ", ".join(s["task_history"][:3])) if s.get("task_history") else "")
            )
            for s in summaries
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
            "all of them and let the engineer make the final relevance call.\n\n"
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
    def distill(task_description: str, workdir_context: str = "") -> str:
        """Unified distill prompt.

        The playbook is intent-agnostic and CAPABILITY-level. It must
        describe how to handle a FAMILY of tasks, not the single example
        in front of you, because every distilled skill is cached and
        consulted by future task matching.
        """
        return (
            _scientist_role_context()
            +
            "You are a senior engineer compiling a CAPABILITY playbook for "
            "`gpt-5.4-mini`, a relatively small engineer model. The playbook "
            "must be explicit enough for that smaller model to execute without "
            "guessing: include ordering, anti-conditions, exact artifacts, "
            "validation commands, and common failure modes. It will be CACHED and "
            "REUSED for many future tasks of the same kind, so you must "
            "resist every instinct toward task-specific hardcoding.\n\n"
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
            "You are a senior engineer revising a CAPABILITY playbook. "
            "The target reader is `gpt-5.4-mini`, a relatively small engineer "
            "model, so revisions must make the playbook more explicit and "
            "operational rather than relying on senior-model inference. "
            "The playbook is CACHED and REUSED for many future tasks of "
            "the same kind, so any edit must broaden capability, not "
            "narrow it to the specific incoming example.\n\n"
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

    # -- Step 4: Task execution (small model, with skill) --
    @staticmethod
    def execute(task_description: str, *, in_container: bool = False,
                in_git_repo: bool = True,
                workdir_has_files: bool = True,
                workspace_inventory: dict | None = None,
                history: list[tuple[str, str]] | None = None) -> str:
        """Unified execute prompt.

        The engineer is told the WORKSPACE SHAPE (git / non-git-with-files /
        empty) as an objective fact, and given a single rule for choosing
        the output form based on the task text itself. No pre-classification
        of the task happens on the Python side.
        """
        history = history or []
        history_block = ""
        if history:
            lines = ["## Conversation so far"]
            for i, (u, a) in enumerate(history, 1):
                lines.append(f"### Turn {i} — user\n{u.strip()}")
                lines.append(f"### Turn {i} — assistant\n{a.strip()}")
            history_block = "\n".join(lines) + "\n\n"

        # In-container (SWE-bench-style eval) keeps its strict
        # reproducer-then-patch workflow; benchmark success depends on it.
        if in_container:
            env_note = (
                "You are running INSIDE the project's official Docker image. "
                "The repository at /app is checked out at the base commit "
                "with all build/test dependencies installed.\n\n"
            )
            return (
                _engineer_role_context()
                +
                "A distilled skill guide has been installed at AGENTS.md in "
                "the repository root. Read it first as a reference playbook, "
                "then complete the task below.\n\n"
                f"{env_note}"
                f"{history_block}"
                f"## Task\n{task_description}\n\n"
                "## Skill applicability gate (run BEFORE step 1)\n"
                "Open AGENTS.md and locate its `## When NOT to use` section "
                "(it is mandatory in the new skill format; older skills may "
                "lack it — that's fine). For each anti-condition listed, "
                "ask: does it match the task above? If ANY anti-condition "
                "matches, **abandon the playbook**: ignore the rest of "
                "AGENTS.md and solve the task from first principles using "
                "the workflow below. State which anti-condition matched in "
                "your first message before doing anything else. If no "
                "anti-condition matches, proceed with the playbook as the "
                "primary reference.\n\n"
                "## Required workflow (follow in order)\n"
                "1. **Read relevant code.** Start from AGENTS.md (unless "
                "the applicability gate told you to abandon it), then "
                "locate the functions/classes/files this task touches. "
                "Read them fully before editing so you understand the "
                "data flow.\n"
                "2. **Write a minimal reproducer** as a standalone script "
                "(e.g. `reproducer.py`, `reproducer.js`) that triggers the "
                "bug or demonstrates the missing behaviour. Run it and "
                "confirm it fails/prints the wrong output on the unpatched "
                "code.\n"
                "3. **Implement the fix.** Make the minimal change needed. "
                "Do not refactor unrelated code. Do not modify existing "
                "tests unless the task explicitly requires it.\n"
                "4. **Re-run the reproducer** and confirm the new behaviour "
                "is correct. If the repository has a fast-running test "
                "module related to this area (unit tests only — not the "
                "full suite), run it to catch regressions.\n"
                "5. **Think about edge cases.** Consider boundary inputs, "
                "nil/None, empty collections, concurrent access, etc. "
                "Extend the fix if a realistic edge case would still be "
                "broken.\n\n"
                "Before finishing, delete the reproducer script so it does "
                "not end up in the final diff. Your final diff should "
                "contain ONLY the fix."
            )

        # Generic path: tell the engineer the shape of the workspace, then
        # let it pick the output form from the task text.
        if in_git_repo:
            workspace_desc = (
                "The current working directory is a **git repository**. If "
                "you modify files, a patch will be produced from the diff."
            )
        elif workdir_has_files:
            workspace_desc = (
                "The current working directory is a regular (non-git) "
                "directory with existing files. If you create or modify "
                "files, the deliverable is the files themselves."
            )
        else:
            workspace_desc = (
                "The current working directory is empty / a fresh scratch "
                "space. If you create files, the deliverable is the files "
                "themselves."
            )

        inventory_block = ""
        if workspace_inventory:
            lines = []
            ctx = workspace_inventory.get("context") or []
            rel = workspace_inventory.get("relevant") or []
            stl = workspace_inventory.get("stale") or []
            overflow = workspace_inventory.get("stale_overflow") or 0
            if ctx:
                lines.append(
                    "- **Project context** (read if helpful): "
                    + ", ".join(f"`{n}`" for n in ctx)
                )
            if rel:
                lines.append(
                    "- **Likely relevant to this task** "
                    "(name matches task text): "
                    + ", ".join(f"`{n}`" for n in rel)
                )
            if stl:
                tail = f" (+{overflow} more)" if overflow else ""
                lines.append(
                    "- **Pre-existing scratch files, likely unrelated to "
                    "this task — do NOT read them unless the task "
                    "explicitly references them**: "
                    + ", ".join(f"`{n}`" for n in stl) + tail
                )
            if lines:
                inventory_block = (
                    "## Workspace inventory\n"
                    "A quick classification of the files already in the "
                    "working directory. Treat the \"scratch\" bucket as "
                    "leftovers from unrelated prior runs — skip reading "
                    "them to save tokens and avoid being misled.\n"
                    + "\n".join(lines) + "\n\n"
                )

        return (
            _engineer_role_context()
            +
            "You are an interactive coding agent (think codex / "
            "claude-code). A skill guide has been installed at AGENTS.md "
            "in the current working directory — read it first as a "
            "reference playbook, then complete the task.\n\n"
            f"{history_block}"
            f"## Task\n{task_description}\n\n"
            f"## Workspace\n{workspace_desc}\n\n"
            f"{inventory_block}"
            "## How to decide the output shape\n"
            "Look at what the task is actually asking for and pick "
            "exactly ONE of:\n\n"
            "**(A) Reply inline** — when the task is analysis, code review, "
            "debugging commentary, explanation, comparison, Q&A, or "
            "planning. Clearest signal: the user **pasted code** and asks "
            "\"what's wrong\" / \"帮我找找bug\" / \"review this\" / "
            "\"哪里错了\" / \"解释一下\". Put your full answer directly in "
            "the chat reply. **Do NOT create a notes/explanation file on "
            "disk** in this case — the user wants to read your answer in "
            "the terminal, not open a markdown file afterwards.\n\n"
            "**(B) Write or modify files** — when the task asks you to "
            "implement / fix / refactor / scaffold / create / modify real "
            "source files in the workspace. Put the code in real files "
            "with clear filenames. Keep your final reply short (a brief "
            "summary of what you did).\n\n"
            "**(C) Ask a clarifying question** — ONLY when the task "
            "genuinely cannot be answered as-is: it refers to a project / "
            "file / symbol that is not in the workspace and has multiple "
            "plausible meanings, or the user's intent is truly ambiguous "
            "(e.g. \"fix it\" with no referent). Ask one short, specific "
            "question and stop. The user will answer in the next turn "
            "and you'll see the full conversation. **Do not use (C) to "
            "ask for code the user already pasted, or to punt on a task "
            "you could reasonably attempt.**\n\n"
            "Never do both of (A)/(B) for the same task. If unsure between "
            "(A) and (B), prefer (A) when code was pasted in the task, "
            "(B) when the task names a concrete artifact to produce.\n\n"
            "## General rules\n"
            "- Read whatever files you need and run shell commands (tests, "
            "linters, quick smoke scripts) whenever it helps.\n"
            "- If you produce code, run it once to confirm it works "
            "before reporting back when that's cheap.\n"
            "- For **long-running processes** (training loops, eval suites, "
            "servers that need observation) you can hand the command off "
            "to `skill-agent watch \"<cmd>\" --for <budget>`; it launches "
            "the process and wakes another engineer periodically to check "
            "on the log and decide continue/abort. Use this instead of "
            "blocking the current turn on a 30-minute train.\n"
            "- Use the user's language in your reply.\n"
            "- If the user refers to earlier turns (\"上一条\", \"what did "
            "I just ask\"), use the Conversation so far section as the "
            "source of truth.\n"
            "- Do NOT mention \"AGENTS.md\" or \"skill guide\" to the user."
        )

    # -- Step 6: Skill repair (big model) --
    @staticmethod
    def repair(
        task_description: str,
        skill_content: str,
        error_output: str,
        patch: str,
    ) -> str:
        return (
            _scientist_role_context()
            +
            "You are an expert software engineer. A small model tried to solve a task "
            "using a skill guide but failed. That small model is usually "
            "`gpt-5.4-mini`; analyze why the guidance was not explicit enough "
            "for that target and produce an improved skill.\n\n"
            f"## Task\n{task_description}\n\n"
            f"## Skill that was used\n{skill_content}\n\n"
            f"## Small model's output/patch\n```\n{patch[:3000]}\n```\n\n"
            f"## Error / failure details\n{error_output[:2000]}\n\n"
            "## Instructions\n"
            "1. Diagnose: What did the small model get wrong? Was the skill guide "
            "misleading, incomplete, or did the model misinterpret it?\n"
            "2. Fix: Produce an IMPROVED version of the skill. Be more specific where "
            "the model went wrong. Add warnings about the pitfall encountered.\n"
            "3. Output ONLY the improved skill markdown, nothing else."
        )

    # -- Refinement from user feedback (no-code / chat tasks) --
    @staticmethod
    def refine_from_feedback(
        task_description: str,
        skill_content: str,
        previous_reply: str,
        user_feedback: str,
    ) -> str:
        return (
            _scientist_role_context()
            +
            "You are the scientist model in a skill-driven agent. A small engineer "
            "model (usually `gpt-5.4-mini`) answered a user's task using the skill guide below, but the USER "
            "was not satisfied with the answer. Your job is to produce an IMPROVED "
            "version of the skill so future runs on similar tasks do better.\n\n"
            f"## Task\n{task_description}\n\n"
            f"## Skill that was used\n{skill_content}\n\n"
            f"## Engineer's previous reply\n{previous_reply[:3000]}\n\n"
            f"## User's feedback (why they were unsatisfied)\n{user_feedback}\n\n"
            "## Instructions\n"
            "1. Diagnose: What did the previous reply miss, get wrong, or do poorly "
            "according to the user's feedback? Is the skill guide vague, incomplete, "
            "or missing concrete steps/examples that would have led to a better answer?\n"
            "2. Fix: Produce an IMPROVED version of the skill markdown. Make the "
            "guidance MORE specific where the engineer went wrong. Add explicit rules, "
            "checklists, or examples that directly address the user's feedback. Keep "
            "what already works; rewrite or expand what failed.\n"
            "3. The skill must remain a general reusable playbook for this CATEGORY "
            "of task — do not hard-code this specific task's answer.\n"
            "4. Output ONLY the improved skill markdown, nothing else."
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
