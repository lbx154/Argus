# Notes, 6 September 2026: how Argus speaks, and two new habits

This batch does three things: it gives Argus a voice, a second reading of the
evidence when doubt has accumulated, and the habit of writing home.

## How Argus speaks

`docs/how-argus-speaks.md` is the standard. Every playbook, skill, stage
description, and role prompt in the research vertical was rewritten against
it, and the shared paragraph `RESEARCHER_VOICE` (`argus_skill/roles/prompts/voice.py`)
is included wherever a role is told how to write for a person. The machine's
tokens (`ACTION=`, `STATUS=`, `TASK_*`, `RETIRE_TASK=`, and so on) are unchanged;
what changed is every sentence around them.

Names that changed:

- `HANDOFF.md` is now `RESEARCH_NOTES.md`, "the research notes", with the
  heading `# Research notes — Experiment stage` (or Idea, or Paper). The
  reader in `argus_skill/verticals/research/notes.py` moves a project's old
  `HANDOFF.md` to the new name the first time it is read, so the two running
  campaigns continue without interruption.
- `reviewer/experiment-audit.md` → `reviewer/reading-the-evidence.md`;
  `reviewer/kill-argument.md` → `reviewer/strongest-argument-against.md`;
  `engineer/claims-evidence-audit.md` → `engineer/claims-against-evidence.md`;
  `engineer/citation-audit.md` → `engineer/citation-check.md`;
  `builtin_skills/engineer/environment-readiness-gate.md` → `environment-readiness.md`.
  The seeding table retires the old names from installed libraries.
- Stage items `experiment.handoff` and `paper.handoff` are `experiment.notes`
  and `paper.notes`.

## A second reading of the evidence

The Reviewer has long been able to say "reconsider", but the signal only
reached the Planner when the Reviewer also asked for a full replan; a
"continue, but reconsider" went nowhere. In the ACL and CoT campaigns roughly
half of all reviews carried that signal.

Now, in the research vertical's Experiment stage, the supervisor counts those
signals (`.argus/SECOND_READING.json` under the project state). After two of
them since the last reading, and at least six hours since the previous one,
the Manager is asked to read the evidence afresh, read-only in the project
directory, with one question: what claim does the evidence collected so far
support, what has it refuted, and what is the single most decisive next
experiment? The answer is placed at the top of `RESEARCH_NOTES.md` under
"A second reading of the evidence", recorded as `life.research.second_reading`,
and handed to the Planner as a revision request, so that the remaining plan
is rebuilt around what the evidence shows and the tasks of the refuted line
are retired with `RETIRE_TASK`.

Code: `argus_skill/verticals/research/second_reading.py`,
`argus_skill/life/supervisor/_second_reading.py`, the hook in
`argus_skill/life/supervisor/_core.py` beside the replan branch, and
`Manager.write_prose` in `argus_skill/manager/_stage_ops.py`.

## The letter to the operator

Argus never wrote to the person who started a campaign between its first
message and its last. Now it does: at a mission boundary or while waiting on a
long run, once every `ARGUS_SKILL_LETTER_INTERVAL_HOURS` (default 8; 0
switches it off), the Manager writes a letter in the operator's language from
facts the host gathers (missions finished, what the Reviewer concluded,
decisions taken, what is running and planned, questions waiting, cost, the
machine). The letter is published to the conversation transcript, which every
cockpit shows, appended to `LETTERS.md` in the project directory, and
recorded as `life.letter.written`.

Code: `argus_skill/roles/prompts/letter.py`,
`argus_skill/life/supervisor/_letters.py`.

## Time and presence

The Planner's current-reality note now begins with the local time and how long
the operator has been silent (`argus_skill/core/operator_presence.py`, read
from the transcript and the inbox). After three hours of silence it adds one
sentence: no one is likely to read a question soon, so prefer the work that has
results ready by the time someone does.

## Deployment

Prompt and playbook changes take effect for a running daemon only after its
runtime checkout is updated and it is restarted (see
`docs/HANDOFF-2026-09-05-NEXT.md` for the drain-and-relaunch procedure). The
notes-file migration is safe to deploy onto a live project.

## Where the handover landed

The batch above is finished, not merely started. The rewrite went through all
three layers — the skill and playbook markdown, the Python that assembles
prompts and stage text, and the four role prompts themselves — and nothing was
left speaking the old dialect.

Two prompt budgets had to give. The shared `RESEARCHER_VOICE` paragraph
(~725 characters) now rides along on the front-door classifier and the
Engineer's fixed prompt, so their character bars moved from 3,000 to 3,800 and
from 2,800 to 3,600. The comments beside both assertions say the same thing:
the body underneath still fits the old discipline, and the next person who
wants more room should trim the prompt before raising the number
(`tests/life/test_front_door_classify.py`,
`tests/test_bounded_turn_discipline.py`).

Loose ends tied off along the way: `reviewer/reviewer-engineer-handoff.md` is
now `reviewer/guiding-the-engineer.md`, which is what the skill actually does;
`ARGUS_SKILL_LETTER_INTERVAL_HOURS` is a registered knob in
`argus_skill/core/knobs.py`, so `argus config` can see it; all three frontends
(core, TUI, web) render `life.research.second_reading` and
`life.letter.written` — the letter gets an envelope glyph and "wrote you a
letter" in both languages; and `argus_skill/verticals/research_bridge.py` is
the one framework-side door into the research vertical, so the architecture
test can keep insisting that framework packages never reach into
`verticals/research/` directly.

The full suite — 6,852 tests — passes, and ruff is clean.
