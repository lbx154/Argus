---
name: "Claim-to-Code Trace"
description: "Audit whether a manuscript's named mechanisms, formulas, losses, and algorithm steps are the code paths that actually produced its results. Follow training/eval entry points to executed file:line anchors and classify each load-bearing claim as MATCHES, CONTRADICTS, or NOT-IMPLEMENTED."
---

# Claim-to-Code Trace

Use this before method prose is certified and whenever a paper makes a
mechanistic claim about a repository. The question is not whether suitable code
exists somewhere. The question is whether the training or evaluation path that
produced the paper's evidence actually executes the claimed computation.

## Inputs and output

Inputs are the manuscript, repository, claim-bearing run manifests/configs, and
the commands or entry points that produced the cited results. Write the audit to
`paper/CLAIM_TO_CODE_TRACE.md` so it remains in the paper's evidence chain.

For each claim, report exactly one verdict:

- `MATCHES`: the executed path computes the claimed quantity with matching
  operands, scope, and placement.
- `CONTRADICTS`: an executed anchor exists, but what it computes differs from
  the claim.
- `NOT-IMPLEMENTED`: no executed anchor for the claimed mechanism can be found.

`CONTRADICTS` and `NOT-IMPLEMENTED` are top-severity for a central claim. They
have exactly two exits: implement the claimed mechanism and regenerate affected
evidence, or rewrite the paper to describe what the code actually does.

## Trace method

1. **Extract claims before reading identifiers.** Read the Method, equations,
   algorithm boxes, captions, abstract contributions, and experimental setup.
   Start with the two or three claims whose removal would collapse the paper,
   then enumerate every named mechanism, loss term, equation, and algorithm step.
   Rewrite each as a testable computation: inputs, transformation, output,
   training/eval phase, and claimed effect.

2. **Locate the producing entry point.** Use the result manifest, job command,
   trainer/evaluator config, or experiment log to identify the command that made
   the cited artifact. Record its `file:line`, arguments, configuration, and
   checkpoint. Do not infer an entry point from a likely filename.

3. **Follow the actual call chain.** Trace imports, constructors, dispatch,
   callbacks, data transforms, forward/loss calls, and evaluator invocations from
   that entry point. At every dynamic choice, resolve the run's real config and
   branch. Record the chain as ordered `file:line -> file:line` anchors. A
   definition with no call path from the producing entry point is dead evidence.

4. **Compare formulas symbol by symbol.** Make a small mapping for every claimed
   formula: manuscript symbol, semantic meaning, code expression, shape/scope,
   and anchor. Check operands, signs, masks, normalization, detach/gradient flow,
   reduction axes, prefix/suffix boundaries, sampling distribution, and where the
   quantity enters the optimized loss or reported metric. Similar variable names
   do not establish equivalence.

5. **Verify execution, not reachability alone.** Use existing logs, manifests,
   traces, tests, or a minimal instrumentation run when authorized to show that
   the relevant branch executed under the claim-bearing configuration. A call
   graph proves the route; runtime evidence proves that the route was taken. Do
   not substitute repeated full experiments for this check.

6. **Propagate the impact.** Name every result, table, figure, abstract sentence,
   and conclusion that depends on a contradicted or absent mechanism. Repeated
   seeds do not reduce this severity: repeats detect sampling noise, not wrong
   implementations.

## Canonical name-lies example

A paper claims a semantic branch-point objective: common reasoning prefix `h`,
fork masks `m_t+`/`m_t-`, and suffix-only DPO. The repository contains a variable
named `branch_prefix_hash`, but its executed assignment only hashes
`prompt_text`; stored winner/loser "suffixes" are whole completions; training
calls `suffix_logprob(model, prompt, completion)` on each full response. The
identifier sounds right, but the executed quantities contain neither the common
reasoning prefix nor suffix-only likelihoods. Verdict: `CONTRADICTS`, with the
assignment, storage, caller, and loss call cited by `file:line`. Names lie; call
graphs and operands do not.

## Report format

For each claim include:

```text
Claim: <manuscript wording/equation and location>
Verdict: MATCHES | CONTRADICTS | NOT-IMPLEMENTED
Producing entry point: <file:line + run command/config>
Executed call chain: <file:line -> file:line -> ...>
Symbol/step comparison: <claimed symbol => computed expression + file:line>
Runtime evidence: <log/trace/test artifact proving this branch executed>
Impact: <dependent results and prose>
Required exit: <none | implement claim | rewrite paper to actual code>
```

End with a matrix of every audited claim and verdict, followed by unresolved
dynamic dispatch or missing runtime evidence. Never upgrade uncertainty to
`MATCHES`; missing proof of an executed anchor is `NOT-IMPLEMENTED` until traced.

## Boundaries

- Do not judge whether the claimed idea is good; judge whether it is the method
  that ran.
- Do not accept function names, comments, type signatures, unit tests of an
  isolated helper, or code search hits as execution evidence.
- Do not demand a rerun when existing call-chain and runtime artifacts answer the
  question. If instrumentation is needed, use the smallest faithful execution.
- Keep exact anchors and claim wording. An integrity adjective, assurance memo,
  or reviewer confidence statement cannot replace them.
