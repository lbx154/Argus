---
name: "Claim-to-Code Trace"
description: "核查科研声明与实际执行代码是否一致。 Trace a paper claim through the configuration and call path that produced its evidence, including formulas, comparison controls and missing observations."
---

# Claim-to-Code Trace

Use for a consequential scientific implementation claim. Read the claim, actual
run command/configuration and producing code; scope conclusions to that revision.

Reading order: the project-root `METHOD.md` first (the method as the paper
will claim it, component by component), then the Raw verification evidence
in the round context — the host runs the project's `tests/spec` after every
Engineer round and delivers the output there, so it is evidence nobody wrote
for you — then `tests/spec` itself, then the code, and the Engineer's account
last. Judge each `Components` row of the card against the code and its
knockout; a row whose knockout does not fail when the component is disabled,
or whose `Status` the code contradicts, is a finding before any claim is.

1. Express the claim as inputs, transformation, output, timing and comparison.
2. Follow the producing entry point through executed branches, not just names,
   comments, dead helpers or isolated unit tests.
3. Compare relevant operands, signs, masks, reductions, normalization, sampling,
   gradient boundaries and where each term enters the loss or metric.
4. Check initialization, data/evaluator access and required controls. Use existing
   receipts or the smallest authorized observation to establish branch execution.
5. Report `MATCHES`, `CONTRADICTS`, `NOT_IMPLEMENTED`, or `INSUFFICIENT_EVIDENCE`.
   Missing telemetry is the last case unless other evidence establishes absence.

Return concrete code locations, affected claims and the smallest next action in
this call's Reviewer schema. A material mismatch calls for implementation repair
and new evidence, or a claim narrowed to what ran. Do not alter the reviewed work
or success definition yourself, and do not create a separate trace report.
