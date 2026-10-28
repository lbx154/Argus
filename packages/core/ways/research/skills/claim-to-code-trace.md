# Claim-to-code trace

Use this when a consequential scientific claim rests on code: trace it through the
configuration and call path that produced its evidence, including formulas, comparison
controls and missing observations. Read the claim, the actual run command and configuration,
and the producing code; scope your conclusions to that revision.

Reading order: the project-root `METHOD.md` first (the method as the paper will claim it,
component by component), then the recorded runs and their outputs, then `tests/spec` itself
(run it where your tools allow and put the command and result in your letter), then the code,
and the Engineer's account last. Judge each `Components` row of the card against the code and
its knockout; a row whose knockout does not fail when the component is disabled, or that the
code contradicts, is a finding before any claim is.

1. Express the claim as inputs, transformation, output, timing and comparison.
2. Follow the producing entry point through executed branches, not just names, comments, dead
   helpers or isolated unit tests.
3. Compare relevant operands, signs, masks, reductions, normalization, sampling, gradient
   boundaries and where each term enters the loss or metric.
4. Check initialization, data and evaluator access, and required controls. Use existing logs
   or the smallest observation you can make read-only to establish that a branch executed.
5. Say for each claim or component whether the code MATCHES it, CONTRADICTS it, has
   NOT_IMPLEMENTED it, or leaves INSUFFICIENT_EVIDENCE. Missing telemetry is the last case
   unless other evidence establishes absence.

Your letter gives concrete code locations, the affected claims and the smallest next action.
A material mismatch calls for an implementation repair and new evidence, never a claim
reworded to fit what ran. Do not alter the reviewed work or the success definition yourself,
and do not write a separate trace report.
