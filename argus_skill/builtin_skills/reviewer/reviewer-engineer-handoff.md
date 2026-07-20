---
name: Reviewer Engineer Handoff
description: Teach reviewer agents to translate validation failures into concise, actionable prompts for smaller engineer agents.
category: review-loop
version: 2
created_at: 2026-05-25T00:00:00+00:00
---

# Reviewer-to-engineer handoff

Use this skill when a reviewer must turn validation or critique into the next prompt for an engineer agent.

## Contract

- Treat validation output as reviewer-only evidence. The engineer should receive your distilled handoff, not a raw log dump.
- Follow the output schema attached to the current call exactly. Every Reviewer
  call is fresh; do not invent legacy fields that are absent from the schema.
- Read and directly edit the shared `CHECKPOINT.md` before returning the verdict.
  The file, not the verdict JSON, is the next Engineer's working context.
- Do not assume the engineer shares your context: write short, explicit, ordered instructions with no hidden context.
- If verification fails, choose `continue` unless user input is strictly required.
- If a short deterministic check can disambiguate missing evidence, the reviewer may run it locally. Do not run long builds, model reviews, experiments, or regeneration work inside the handoff step; give the engineer the exact command and expected pass condition.
- Preserve the important facts from validation: failed command, exit code, issue codes, exact file paths, artifact paths, and validator messages.
- Group related failures by root cause and name the outcome that must change first.
- Preserve implementation freedom: give constraints and the evidence gap, not a
  scripted sequence, unless a deterministic failed check already implies one.
- Include an exact command only when it is the real acceptance check or the
  shortest way to disambiguate missing evidence.
- Do not tell the engineer merely to "look at the validation output"; translate it into concrete work.
- If repeated paper validators or reviews are failing, write a coherent repair brief rather than a microtask. Ask the engineer to inspect the page map, evidence sufficiency, source artifact graph, generated review freshness, and figure/table provenance, then make the smallest complete root-cause repair.

## `next_action` shape

Default to a compact outcome brief:

1. Name the failed outcome or evidence gap.
2. State hard constraints, relevant paths, and the expected proof.
3. Let the Engineer choose tools and implementation.

For a deterministic validator/test failure, a short ordered repair brief with
the exact command is appropriate. Keep either form concise; avoid copying stack
traces or long output blocks unless one or two lines are essential for diagnosis.

## Figure and paper validation handoff

When validation concerns an auto-research paper:

- Review the actual visible figure. Pass it once it is readable, coherent,
  factually correct, and good-looking enough; minor stylistic preferences are
  not blockers.
- Request at most one targeted visual repair for an aesthetic issue. Further
  regeneration requires a concrete remaining defect such as unreadable text,
  wrong content, broken rendering, or severe visual mismatch.
- Use optional renderer metadata only to help the Engineer find the source. Its
  absence or hash drift is not itself a Reviewer blocker.
