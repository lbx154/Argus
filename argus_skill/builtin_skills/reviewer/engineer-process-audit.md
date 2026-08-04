---
name: "Engineer Process Audit"
description: "Audit the engineer's EXECUTION LOG (events.jsonl) by grep to verify PROCESS correctness, not just whether the final artifact matches the checklist. Catches hardcoded answers, skipped steps, cheat methods (use_attach, fabricated metrics, bypassed evaluators), and commands that contradict the claimed method. Use when the result is suspicious, surprisingly good, or a checklist item can't be independently verified from the produced files."
---

# Engineer Process Audit

You normally judge a round from the engineer's final summary and produced
artifacts. Both describe the OUTCOME. Neither tells you HOW the outcome was
produced — whether the engineer reached it honestly or faked its way past the
checklist. This skill closes that gap by reading the engineer's **execution log**
directly.

## Why this exists

An optimizing agent with no reward signal is usually honest, but the failure
modes that DO occur are invisible to result-traceability:

1. **Hardcoded answer** — the engineer writes the expected value straight into
   the output (or `return 0.9`, `assert True`) instead of computing it. The
   artifact then "matches" the checklist perfectly.
2. **Skipped step** — the checklist says "run X, then measure"; the engineer
   skips X and writes the result directly, so the file exists but was never
   earned.
3. **Cheat method** — a physics/sim override (`use_attach`, forced pose,
   teleport), a fabricated metric, or a bypassed/replaced real evaluator makes a
   FAILING task look passed.
4. **Method contradiction** — the prose claims approach A; the commands in the
   log show approach B. The summary hides the discrepancy.

Result-traceability cannot see any of these. The execution log can.

## When to use

The reviewer prompt injects an "Engineer execution-log audit" section with the
absolute path to this mission's log whenever the supervisor wires it. Reach for
this skill when:

- The result is suspicious, implausible, or **surprisingly good**.
- A checklist item **cannot be independently verified** from the produced files
  alone (you would have to trust the engineer's word).
- The summary is **thin on HOW** the work was done.
- The score **jumped** in a way that doesn't match the described change.
- You suspect a sim/physics task was passed via an override rather than real
  control.

## When NOT to use

- The engineer's own summary already shows the verification output (scorer
  RESULT line, pytest output, file listing) AND it is internally consistent — a
  quick skim is enough; do not burn the round re-deriving an honest result.
- **MEASURED-BENCHMARK mode**: you TRUST the frozen scorer. Grep the log ONLY as
  a red-flag check (no RESULT line pasted, implausible number, suspicious jump)
  — never to re-confirm an honest, internally-consistent number.
- There is no concrete suspicion and the result is mundane — do not manufacture
  a process objection where none exists.

## How to audit (grep the log)

The log is the per-project `<life_dir>/events.jsonl` (NOT in the git work-tree).
Each `engineer.progress` event's `text` field is what the engineer actually did
that round — a shell command, a tool call, or a reasoning beat. Substitute
`<path>` with the absolute path from the prompt.

1. **What did the engineer run this round?** (newest last)
   ```
   grep '"type": "engineer.progress"' <path> | tail -60
   ```
   Read the command sequence. Does it match the method the summary/checklist
   claims? Are the steps the checklist requires actually present?

2. **Hunt for cheats / shortcuts that mask a real failure:**
   ```
   grep -nE 'use_attach|set_pose|teleport|hardcod|HARDCODE|TODO|FIXME|mock|monkeypatch|fake|dummy|placeholder|return 0\.9|assert True|--skip|xfail' <path>
   ```
   A hit is not automatically damning (a legitimate mock in a unit test is
   fine), but each hit must be explainable by the claimed method.

3. **Was the claimed evaluator/scorer actually invoked** (not bypassed or
   replaced by an inline constant)?
   ```
   grep -nE 'pytest|check_success|scorer|evaluate|benchmark|metric' <path>
   ```
   If the checklist claims "passed the official scorer" but the scorer command
   never appears, the result is unearned.

4. **Cross-check the written output against the computation.** If the artifact
   contains a number, find the command that produced it in the log. No producing
   command + a matching number = likely hardcoded.

## Red flags → escalate

If you find any of these, return `continue` (or `blocked` when the operator must
act) EVEN IF the artifact traces to the checklist. Name the process defect
explicitly in `reason`:

- (a) **Hardcoded** the expected value/answer instead of computing it.
- (b) **Skipped** a required step and wrote the result directly.
- (c) Used a **wrong/cheating method** (`use_attach`, forced pose, fabricated
  metric, bypassed real evaluator) to make a failing task look passed.
- (d) Ran commands that **contradict** the method the checklist/summary claims.

In `next_action`, tell the engineer exactly which step was skipped or which
cheat to remove, and the command that would prove the result honestly.

## Boundaries

- This audit **supplements** result-traceability; it never replaces it.
- You may **never** change the frozen outcome / metric / verifier / validity
  test — you only judge whether the engineer's PROCESS honestly satisfied them.
- A clean log that matches the claim is a positive signal: say so briefly and
  judge on the result as usual.
- Do not wrap this in a timer/loop — it is a per-round, suspicion-driven check.
