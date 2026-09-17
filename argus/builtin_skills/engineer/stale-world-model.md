---
name: "Verify Decision-Critical External Facts"
description: "核实会影响当前决策的模型权限、版本、API 和基准信息。 Verify changing model and checkpoint access, library versions, API endpoints, benchmark protocols or prices only when the current task depends on them."
---

# Verify Decision-Critical External Facts

Use when a mutable external fact could change the task's implementation or claim.
Do not start every task with a survey. Local edits, mathematics and stable language
semantics usually need the existing repository and tests, not a web search.

## Cheapest sufficient evidence

1. State the fact that matters: model and checkpoint names/access, library and
   framework versions, benchmark names/protocols, or API endpoints/prices.
2. Inspect the configured environment, lockfile or recent task evidence first.
   Reuse evidence while its version and conditions still apply.
3. Query the authoritative source for the unresolved fact. For example,
   `pip index versions <package>` checks published versions; the installed
   interpreter checks the version actually running. Neither proves compatibility.
4. For a model, separate **identity, permission and execution**:
   - `https://huggingface.co/api/models/<id>` returning 200 proves metadata is
     visible. Inspect `gated`, license and revision; it does not grant downloads.
   - Probe a required small file at the pinned revision using the credentials
     already authorized for this task. A 401/403 is an access boundary; an
     unauthenticated rejection does not prove the configured account lacks access.
   - Download permission does not prove framework support or hardware capacity.
     Run a minimal load/inference only when execution readiness is required.
   Never print credentials or signed download URLs.
5. Fetch the claim-bearing documentation or released code before citing its
   behavior. Use search to locate it, not as proof of implementation.

## Unavailable dependencies

Find an alternative only if the task permits substitution. When identity is the
claim, a frozen evaluator requires a particular artifact, or the user selected
that exact model/version, retain the boundary and route the necessary decision.
Do not silently change the comparison, task objective, or access assumptions.

Record the source/revision, date and result beside the configuration or statement
that needs it. Report an unresolved fact as unknown. Stop once the evidence is
sufficient for the current decision; do not create a separate research report.
