---
name: Mint Skill
description: Auto-evolve loop — when a mission's trajectory shows the agent tried to use a tool that didn't exist, the supervisor enqueues a mint-skill mission targeted at that missing tool. The engineer (this skill) writes a candidate skill markdown + executable script + ≥3 held-out fixtures, runs the validator, and iterates until the held-out gate passes. Never mints judgment skills (reviewer / kill-argument / novelty-check etc. are blacklisted because SkillLens says LLM judges of skill text are 46.4% worse than chance — only execution-measurable skills can be safely auto-minted).
category: self-evolve
version: 1
author_model: gpt-5.5
created_at: 2026-06-01T00:00:00+00:00
---

# Mint Skill (Signal A · auto-evolve)

> Argus self-evolve loop, layer L2 ("mint a new skill the agent lacked").
>
> **Trigger path (post skill-04 redesign):** the supervisor's
> trajectory detector writes
> ``self_evolve.missing_tool_advisory`` journal entries when it
> detects missing-tool patterns (structural regex grep — pure
> plumbing). The **reviewer or planner agent** reads recent
> advisories and decides whether to request a mint-skill mission.
> The harness no longer auto-enqueues; the judgment "is this worth
> minting / was it a typo / did we work around it" belongs to the
> agent, not the harness.
>
> Sits inside the standard backlog → mission → reviewer flow once
> the agent enqueues; respects all gates (lifecycle, budget, F4
> evidence_chain, D1 gate veto).

## How a mint-skill mission gets started

Two paths, both agent-driven:

1. **Reviewer-requested**: the reviewer at end of a mission reads
   the advisory in journal, decides it's a real capability gap, and
   includes a recommendation in its ``next_action`` (e.g.
   "enqueue mint-skill: pdftotext — used 3 times, blocking PDF
   ingestion"). The planner picks this up in its next cycle and
   enqueues a BacklogItem.

2. **Planner-batched**: in continuous mode, the planner scans
   recent ``self_evolve.missing_tool_advisory`` journal entries on
   each cycle, decides which ones are worth minting (based on
   recurrence count, alignment with project goal, blacklist
   filtering), and enqueues mint-skill BacklogItems in batch.

Either way, by the time YOU (the engineer minting) are invoked,
there's a BacklogItem with ``tags=["mint-skill", ...]`` whose
objective references the missing tool's slug + context.

## Blacklist — DO NOT mint these skill kinds

Per SkillLens (arXiv 2605.23899): LLM judges are **46.4% worse than
chance** at telling effective skills from ineffective ones by reading
them. The harness has no measurable signal for "is this judgment
skill good", so the auto-evolve loop is forbidden from minting:

- Anything in `argus_builtin_skills/reviewer/*` (reviewer playbooks)
- `kill-argument`, `novelty-check`, `idea-creator`, `idea-discovery`
- `*-review.md`, `*-audit.md` (the audit skills already exist; new
  audit skills need a measurable ground-truth fixture, see below)
- Anything named `*-judge.md`, `*-rate.md`, `*-score.md`

If the missing-tool signal points at one of these, **STOP** — write a
brief journal note ("blacklisted skill kind, escalating to operator")
and finalize the mission as `done` without minting. The operator
decides whether to write the judgment skill by hand.

## Whitelist — what CAN be minted

Skills with **objectively measurable I/O contracts**:

- File-format converters (PDF → text, CSV → JSON, HTML → markdown)
- Wrappers around CLI tools (ffmpeg, imagemagick, pandoc, jq)
- Parsers / extractors with known correct output for known input
- API clients with deterministic request/response shape
- Data transformers (clean / pivot / dedupe / normalise)

The validator runs `python <skill>_scripts/main.py < input > stdout`
and diffs vs `expected`. **If your skill cannot be expressed as that
contract, it does not belong in the auto-evolve loop.**

## Workflow

### Step 1 — read context, dedup, blacklist-check

1. Read the mission objective: `tool_name`, `kind`, `context`,
   `evidence` (from the `MissingToolSignal` the detector produced).
2. Apply the blacklist above. If hit → finalize and exit.
3. Dedup vs existing skills:
   ```bash
   ls argus_builtin_skills/engineer/ | grep -i "<tool_name>"
   grep -ri "<tool_name>" argus_builtin_skills/ | head
   ```
   If an existing skill already covers this tool, finalize and
   exit ("already minted as `<existing.md>`").

### Step 2 — sketch the I/O contract

Before writing code: what is the SHORTEST sentence describing
"input → output"? Write it in a one-line skill description.

Example:
- input: PDF file via stdin (bytes)
- output: extracted plain text to stdout (utf-8)
- failure mode: encrypted / scanned-only PDFs → nonzero exit + error
  to stderr

If you cannot write this contract in one sentence, the skill is
either too vague (blacklist) or needs to be split into smaller
skills.

### Step 3 — write candidate

Write to project-local paths (NEVER directly into the installed
`argus_skill` package — the committed package is the source of truth
and only operator promotion ships skills there):

```
argus_builtin_skills/engineer/
  ├── <slug>.md                  ← agent prompt for using the skill
  └── <slug>_scripts/
      └── main.py                ← stdin → stdout transformer
```

Required `main.py` shape:

```python
"""<slug> — <one-line contract>."""
from __future__ import annotations
import sys
# (optional) import 3rd-party deps; install instructions go in the md

def main() -> int:
    data = sys.stdin.buffer.read()
    # ... transform ...
    sys.stdout.write(result)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

Required `<slug>.md` shape:

```yaml
---
name: <Human Skill Name>
description: <one-line contract from Step 2>
category: self-evolve-minted
version: 1
author_model: gpt-5.5
created_at: <iso ts>
---

# <Human Skill Name>

> Auto-minted by the self-evolve loop on <date>. Triggered by missing
> tool `<tool_name>` observed in mission `<context>`.

## I/O contract

- Input: <description>
- Output: <description>
- Errors: <how failure is signalled>

## How to invoke

`python argus_builtin_skills/engineer/<slug>_scripts/main.py < input > output`

## Dependencies (if any)

- `pip install <pkg>==<version>` — pinned at mint time
- Any system binaries: <list>
```

### Step 4 — write held-out fixtures

In `.argus-fixtures/<slug>/`, write **at least 3** cases. Each case
is a directory with `input.<ext>` and `expected.<ext>`. The validator
fails structurally if fewer than 3 — this is a minimum-evidence guard,
not a quality threshold, so don't try to argue around it.

```
.argus-fixtures/<slug>/
  ├── case_001/
  │   ├── input.pdf            # real test input
  │   └── expected.txt         # exact expected stdout
  ├── case_002/
  │   ├── input.pdf
  │   └── expected.txt
  └── case_003/
      ├── input.pdf
      └── expected.txt
```

Pick cases that cover:
1. The "obvious" happy path (the kind of input that motivated the
   mint in the first place)
2. An edge case (empty input / malformed / minimum size)
3. A regression case (something the FIRST attempt of your script
   would likely break on)

Fixtures live OUTSIDE the skill directory because they're test
assets, not part of the deployed skill.

### Step 5 — install deps + run validator

```bash
# install deps user-local (no global pollution)
pip install --user <deps>

# run the validator
python -m argus_skill.skills.mint_skill_validator \
    --skill argus_builtin_skills/engineer/<slug>.md \
    --fixtures .argus-fixtures/<slug>
```

Validator exit code:
- `0` — all fixtures pass → DONE; commit
- `1` — at least one fail → iterate

Iteration loop:
- Read the per-case `detail` line to see WHICH case failed and how
- Edit `main.py` to fix
- Re-run validator
- Cap at 5 iterations (the reviewer round budget will hit you anyway)
- If still failing after 5 iterations → finalize as `blocked` with a
  written reason in the journal; the operator decides whether to
  hand-fix or remove the candidate

### Step 6 — commit

When validator passes:
1. Update the skill md `description` to be specific enough that the
   skill matcher will surface it for relevant tasks
2. Stage: `git add argus_builtin_skills/engineer/<slug>.md
   argus_builtin_skills/engineer/<slug>_scripts/`
3. NOTE: do NOT `git add .argus-fixtures/` — those are test
   assets, kept project-local but not part of the deployable skill
4. Write a commit message: `Mint skill: <slug> (auto-evolve from
   missing tool <tool_name>)` with `Co-Authored-By: nssmd
   <nssmd@noreply.local>`
5. The reviewer will read the validator's pass report from the
   CheckResult tail and mark the mission `done`

## Anti-patterns (will fail validation)

- ❌ Writing the skill md to explain HOW the script works — the md
  is for **how to invoke the skill**, not how it's implemented
- ❌ Fixtures where input + expected are the same string (tautology;
  no evidence the script does anything)
- ❌ Using mocks / `unittest.mock` in main.py — the validator runs
  the script for real
- ❌ Skipping the dedup check — minting `pdftotext-extract` when
  `pdf-to-text` already exists pollutes the matcher
- ❌ "I'll write tests later" — no, the validator IS the test; the
  fixtures ARE the test cases
- ❌ Pulling unpinned latest of a 3rd-party package — pin versions
  in the md so future runs are reproducible

## Integration with the rest of argus

- The mission you're in goes through the SAME backlog → engineer →
  reviewer flow as a research mission; you don't get special
  treatment
- `stage_check.py` still runs every round (F4 evidence_chain is a
  no-op here, F3 mediocrity_finding ditto, but the structural
  shell checks still verify PIPELINE_STATE.json etc.)
- D1 gate veto: if the validator reports FAIL, the CheckResult
  passes that signal up to `_coerce_review_for_failed_checks`,
  which forces reviewer `status="continue"` even if you tried to
  claim done
- F5 lifecycle: this mission counts against the project's budget
  like any other; if the project hits 80% budget cap and you
  haven't shipped, lifecycle quarantines you

## Why this skill is whitelist-eligible itself

This skill writes new code and tests it against measurable fixtures.
It does NOT make a quality judgment about what other code is "good";
that's the reviewer's call. The skill is essentially mechanical:
read a missing-tool signal → write candidate → run validator → iterate.
The judgment that DOES happen ("is this skill ready to commit") is
the reviewer's via the standard D1 gate veto path; the harness just
surfaces the validator's exit code.
