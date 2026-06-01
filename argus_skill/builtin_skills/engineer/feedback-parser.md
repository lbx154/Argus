---
name: Feedback Parser
description: Signal C (user feedback) handler for the self-evolve loop. Reads operator messages from inbox.jsonl (written by `argus-skill --notify`), classifies each into directive / question / nudge / STOP / praise, extracts the structured intent for directives ("for X use Y" → patch target skill), and either edits an existing skill (L1 polish) or routes to mint-skill (L2 new). Records "feedback already absorbed" stamps so the planner doesn't keep re-applying the same operator message; validation is "operator did not repeat the same nudge in next mission".
category: self-evolve
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-01T00:00:00+00:00
---

# Feedback Parser (Signal C · auto-evolve)

> Argus self-evolve loop, **Signal C (user feedback)**. Pairs with
> Signal A (`missing_tool_detector`) and Signal B
> (`paper-ingestion`). The supervisor already drains `inbox.jsonl`
> at the start of every `_run_one` and appends raw messages to the
> engineer prompt; this skill adds **structured intent extraction
> + persistent skill changes** on top.

## When YOU are invoked

The planner schedules this skill when:

1. The inbox has unparsed messages (i.e. messages without a
   matching `feedback_absorbed` journal entry).
2. The previous round of inbox messages contained at least one
   `directive` intent that the engineer did NOT act on.
3. A `STOP` message appeared (highest priority).

The supervisor's existing drain still happens; this skill is for
**permanent absorption** rather than one-round prompt injection.

## Intent taxonomy

| Intent | Example | Action |
|---|---|---|
| **STOP** | "STOP", "停", "stop everything", "abort" | Enqueue immediate wrap-up; planner halts new exploratory work. No skill edit. |
| **directive** | "for image PDFs use the vision route", "next time use PPO not REINFORCE", "always include a test for X" | Extract `(target, change)`; patch existing skill or mint new |
| **question** | "why did you pick X?", "what's the budget?" | Answer in journal entry; no skill change |
| **nudge** | "please hurry", "this looks ok keep going" | One-round prompt injection only; do NOT modify skills (subjective + transient) |
| **praise** | "good job", "this approach is right" | Journal-only; never use praise as RL signal (would close the loop badly per skill 04) |

## Workflow

### Step 1 — read unabsorbed inbox messages

```bash
# inbox.jsonl is the canonical operator → daemon channel
INBOX=~/.argus-skill/projects/<project_hash>/inbox.jsonl
test -f "$INBOX" && wc -l "$INBOX"
```

For each message in inbox.jsonl, check `journal.jsonl` for a
matching entry of kind `feedback_absorbed` with `inbox_offset` ==
this message's offset. Skip already-absorbed messages.

### Step 2 — classify per message (zero shared context)

Reviewer agent (gpt-5.5 via `reviewer` route, fresh thread per
message — same anti-confirmation-bias as citation-audit):

```
You are classifying one operator message to argus, with no prior
context about the project or previous messages.

Message: <verbatim>

Return JSON:
{
  "intent": "STOP" | "directive" | "question" | "nudge" | "praise",
  "confidence": 0.0..1.0,
  "directive_target": "<skill name or 'project' if cross-cutting>"
                       | null,
  "directive_change": "<one-sentence imperative — 'in skill X, do Y
                        instead of Z' or 'next time always W'>" | null,
  "rationale": "<one sentence why this intent>"
}
```

If `intent == "STOP"` short-circuit: emit a `wrap_up` BacklogItem
at the highest priority and stop processing the rest of the inbox.

### Step 3 — apply directives

For each `directive` message:

1. **Resolve target**:
   - `directive_target == "<skill_name>"` → grep
     `argus_builtin_skills/` for the skill file
   - `directive_target == "project"` → modify AGENTS.md operator
     goal / non-goals
   - Unresolvable → record as question, ask operator to clarify

2. **Decide L1 polish vs L2 mint**:
   - Skill exists + change is a small prompt edit ("also use X"
     / "never do Y" / "include test for Z") → **L1 polish**: patch
     the skill md and journal the diff
   - Skill exists but change requires NEW behaviour the script
     can't do ("for image input route through vision capability"
     when the skill has no vision code) → **L2 mint**: enqueue a
     mint-skill mission with the operator's directive in objective
   - Skill doesn't exist → **L2 mint** directly

3. **Apply L1 polish** (when chosen):
   ```bash
   # Use Edit to insert / replace a section in the target skill md
   # Always preserve frontmatter, never modify validation logic
   # without going through the full mint-skill flow
   ```
   Then bump the skill's frontmatter `version` by 1 and append a
   line in the skill's body:
   ```markdown
   > Operator directive on <date>: <directive_change>
   ```
   The frontmatter `created_at` stays the original mint date;
   `version` tracks operator-driven edits.

4. **Apply L2 mint** (when chosen): create a BacklogItem with
   `tags=["mint-skill", "feedback-derived"]` and objective:
   ```
   Mint or extend a skill per operator directive: "<directive_change>"
   Source inbox message offset: <offset>
   Target (if known): <skill_name>
   Follow mint-skill.md flow. The operator-supplied directive IS
   the I/O contract — write fixtures that test the directive's
   condition (e.g. "given image PDF input, output uses vision route
   path").
   ```

### Step 4 — journal absorption

For each processed message, append to journal:

```json
{
  "kind": "feedback_absorbed",
  "summary": "<intent>: <directive_change or message excerpt>",
  "tags": ["feedback", "<intent>"],
  "extra": {
    "inbox_offset": <byte offset of this message in inbox.jsonl>,
    "intent": "...",
    "applied_as": "polish" | "mint-skill" | "wrap_up" | "noop"
  }
}
```

This is the dedup signal — Step 1 reads journal `feedback_absorbed`
entries to skip messages already processed.

### Step 5 — validate (next mission)

The reviewer at the END of the next mission compares:
- Did the operator repeat the same directive (same intent +
  same `directive_change` text or paraphrase)?
- If YES → the absorption failed; flag it and the planner
  re-enqueues a `feedback-parser` mission with a hint to escalate
  (e.g. "previous polish at skill X didn't take, consider mint
  instead")
- If NO → absorption confirmed; nothing more to do

The reviewer rules on this; the harness doesn't enforce
"successful internalization" — that would be a quality verdict
on operator-feedback handling, exactly the kind of harness call
forbidden by skill 04.

## Anti-patterns

- ❌ Use praise / "good job" as a reward signal that adjusts
  reviewer scoring or skill ranking — closes a corrupt loop (per
  SkillLens: judges of skill quality from text are 46.4% worse
  than chance; praise from a single operator is not a measurable
  signal of skill quality)
- ❌ Apply directives across many skills "while we're at it" —
  one directive, one target. Operator should send another
  message if they want a broader change.
- ❌ Re-apply a directive on every tick — that's why journal
  `feedback_absorbed` exists. Read it first.
- ❌ Strip the operator's exact words from the polish text —
  always quote the operator verbatim in the skill's body so the
  next reader knows what was changed and why
- ❌ Use STOP as a soft hint — STOP means stop NOW (wrap-up
  highest priority). Anything weaker is `nudge`.
- ❌ Modify reviewer / kill-argument / novelty-check skills —
  even via operator directive. Those are on the mint-skill
  blacklist for the same reason (judgment skills can't be
  text-judged by LLM; SkillLens). If the operator directly
  edits a judgment skill, journal it as a manual operator edit
  with no automated absorption.

## Integration with the rest of argus

- Supervisor's existing `_drain_user_inbox` still runs every
  `_run_one` and appends raw messages to the engineer prompt for
  immediate context. This skill **adds persistent absorption** on
  top — without it, every notify is a one-round nudge that
  evaporates after the mission ends.
- L1 polish: edits a skill .md in-place. Frontmatter `version`
  bump signals to skill matcher / cache that the skill changed.
- L2 mint: routes through the same BacklogItem → supervisor →
  engineer flow as Signal A trigger; deduplication against
  in-flight mint-skill missions is automatic.
- This is Signal C in the 3-signal architecture:
  - Signal A · trajectory: `missing_tool_detector`
  - Signal B · literature: `paper-ingestion`
  - Signal C · user feedback: **this skill**
  All three feed the same downstream loop; this skill is the
  inbox-message adapter.
