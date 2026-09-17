---
name: "Guiding the Engineer"
description: "Teach reviewer agents to turn what their checks found into concise, actionable next instructions for smaller engineer agents."
---

# Guiding the Engineer

Use this skill when a reviewer must turn what a check or a critique found into
the next prompt for an engineer agent.

## Terms

- Treat the raw output of your checks as reviewer-only evidence. The engineer
  should receive your distilled guidance, not a raw log dump.
- Follow the output schema attached to the current call exactly. Every Reviewer
  call is fresh; do not invent legacy fields that are absent from the schema.
- Read and directly edit the shared `CHECKPOINT.md` before returning your
  conclusion. The file, not the decision JSON, is the next Engineer's working
  context.
- Do not assume the engineer shares your context: write short, explicit,
  ordered instructions with no hidden context.
- If an in-scope repair remains, choose `continue`. Use `replan_requested` for a
  necessary scope/direction change and `blocked` only for a concrete missing
  decision or resource; follow the current call's available verdicts.
- If a short deterministic check can disambiguate missing evidence, the
  reviewer may run it locally. Do not run long builds, model reviews,
  experiments, or regeneration work while writing the guidance; give the
  engineer the exact command and the condition under which it passes.
- Preserve the important facts your checks turned up: the failed command, the
  exit code, issue codes, exact file paths, the paths of the files produced,
  and the checker's messages.
- Group related failures by root cause and name the outcome that must change
  first.
- Preserve implementation freedom: give constraints and the evidence gap, not a
  scripted sequence, unless a deterministic failed check already implies one.
- Include an exact command only when it is the real check the work must pass or
  the shortest way to disambiguate missing evidence.
- Do not tell the engineer merely to "look at the check output"; translate it
  into concrete work.
- If the same paper checks or reviews keep failing, write a coherent repair
  brief rather than a microtask. Ask the engineer to inspect the page map, the
  sufficiency of the evidence, how the source files feed one another, the
  freshness of any generated review, and where each figure and table comes
  from, then make the smallest complete root-cause repair.

## `next_action` shape

Default to a compact outcome brief:

1. Name the failed outcome or the evidence gap.
2. State hard constraints, relevant paths, and the expected proof.
3. Let the Engineer choose tools and implementation.

For a deterministic check or test failure, a short ordered repair brief with
the exact command is appropriate. Keep either form concise; avoid copying stack
traces or long output blocks unless one or two lines are essential for
diagnosis.

## Figures and the paper

When the question concerns an auto-research paper:

- Review the actual visible figure. Let it stand once it is readable, coherent,
  factually correct, and good-looking enough; minor stylistic preferences are
  no reason to send it back.
- Request at most one targeted visual repair for an aesthetic issue. Further
  regeneration requires a concrete remaining defect such as unreadable text,
  wrong content, broken rendering, or severe visual mismatch.
- Point the Engineer to the editable source and the visible defect.
