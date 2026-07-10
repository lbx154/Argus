---
name: keep-files-small
description: Don't append substantial logic to existing large files. Default to a new module. Files are read-points for future maintainers and review tools — a 2000-line file is unreviewable. Soft cap ~500 lines per file; past that, splitting is the burden of proof, keeping isn't.
when_to_invoke: Before appending more than ~50 lines to an existing source file, or before adding a class/function to a file that's already >500 lines.
---

# Keep-Files-Small

## The rule

Source files in `argus_skill/` should usually stay under **~500
lines**. When you're about to add 50+ lines of new logic, default to
a new module unless there's a clear reason the new logic belongs in
the existing file.

## Why this matters in this repo

Argus has several files that are already over the line — `supervisor.py`
~1800 lines, `runner.py` ~1500 lines, `paper_calibration.py` was 2324
lines (we deleted it in `c142bf8`). Every one of those started small
and grew because each contributor thought "just one more method, it
belongs in this class anyway".

Costs of monolithic files:

- **Reviewers can't form a mental model**. A 2000-line file forces
  jumping between sections; reviewers settle for "this PR diff looks
  reasonable" and miss cross-cutting bugs.
- **Tools struggle**. Codex / Claude / IDE plugins read in chunks;
  past ~1500 lines the assistant starts losing context within the
  file.
- **Tests bloat**. Test files for monolithic modules become 1500+
  lines themselves; fixtures get reused across unrelated paths.
- **Refactoring becomes expensive**. The longer a file, the higher
  the friction to extract a class — so it doesn't happen, and the
  next contributor faces the same wall.
- **Cross-file `grep` beats in-file `grep`**. When logic lives in
  its own module, `grep -l` finds it instantly. When buried in a
  huge file, you have to skim or remember line numbers.

## The mechanical rule

| File size | What to do when adding code |
|---|---|
| < 300 lines | Append freely |
| 300 – 500 | Append if cohesive; consider extraction if topic-new |
| 500 – 1000 | Default to a new module; extract before adding cousin logic |
| > 1000 | Adding here requires a justification in the commit message ("X belongs here because Y") |
| > 1500 | New code goes in a new file. Period. |

These are soft numbers — context matters. A 600-line file made of
five clearly-related dataclasses is fine; a 600-line file with three
unrelated control flows is not. The size is a smell, not a verdict.

## What an extraction looks like

If you have a 1800-line `supervisor.py` and you want to add ~150
lines of experiment-health advisory logic:

**Wrong**:
```python
# in supervisor.py — appended at line 1700
def _maybe_journal_run_health(self, item, result): ...
def _recent_run_health_tools(self, limit): ...
def _tail_events_for_item(self, item): ...
```
→ file is now 1950 lines; run-health analysis is intermingled with budget,
lifecycle, missions, journal, ...

**Right**:
```python
# argus_skill/life/run_health_advisor.py (new file, ~150 lines)
class RunHealthAdvisor:
    def __init__(self, memory, on_cost): ...
    def maybe_journal_advisory(self, item, result): ...
    def _recent_advisory_tools(self, limit): ...
    @staticmethod
    def _tail_events_for_item(memory_root, item): ...

# argus_skill/life/supervisor.py — 4-line delegate
def _maybe_journal_run_health(self, item, result):
    from .run_health_advisor import RunHealthAdvisor
    return RunHealthAdvisor(
        memory=self.memory,
        on_cost=self._inject_cumulative_cost,
    ).maybe_journal_advisory(item, result)
```
→ supervisor.py grew 8 lines (a thin delegate), run-health logic has its
own discoverable module + its own test file.

The delegate exists so existing tests calling
`supervisor._maybe_journal_run_health` still work; the
real logic moved.

## Reference incidents

| Date | File | Anti-pattern | Fix commit |
|---|---|---|---|
| 2026-06-01 | `argus_skill/skills/paper_calibration.py` (2324 lines, mixed quality + anti-fab logic) | grew by accretion, never split, became unwired and dead | `c142bf8` (deleted entirely) |
| 2026-06-01 | `argus_skill/life/supervisor.py` (+130 lines of an optional subsystem) | appended a feature into an already-1800-line file | (refactor in same commit as this skill) |

## When NOT to split

A few cases where keeping it in one file is correct:

- **A class and its data**: a dataclass + the one function that
  validates it stay together
- **Tightly coupled state machine**: state enum + transition table
  + apply function go together
- **A small helper that's only called from one place**: extracting
  it to its own module is overengineering

The 500-line cap is for THE FILE, not for THE CLASS. A 480-line
file with one cohesive class is healthy; a 600-line file with five
unrelated helpers is a tease away from 1500.

## Anti-patterns

- ❌ "I'll split later when this gets too big" — by then the cost
  of splitting outweighs the cost of the file. Split before you
  cross the line.
- ❌ Extracting a single 5-line function to its own file just to
  satisfy the rule — extraction has a cost (one more import,
  one more place to grep). Only extract when you're moving
  cohesive logic.
- ❌ Putting an entire optional subsystem into one giant module — that's just
  deferring the same problem. A large subsystem has at least three sub-concerns (detector,
  advisor/router, validator); each gets its own file.
- ❌ Bundling cross-stage logic into a "utils.py" — that's a
  graveyard. Pick a real module name that names the responsibility.

## Quick checklist before a commit

1. `wc -l <file_you_edited>` — is any file you edited >1000 lines?
2. Does your added logic share a meaningful boundary with the
   rest of the file, or is it a new responsibility?
3. Could a future reader find your new code via `grep -rn "<key
   phrase>"` if it lived in its own file? (Usually yes →
   extract.)
4. If you DO append to a big file, mention in the commit message
   why (or this skill's rule is the answer to "why didn't you
   extract?").
