---
name: "Setting up a reusable workflow"
description: "How to agree a reusable workflow (vertical) with the operator before the team builds it."
---

# Setting up a reusable workflow

The deliverable is a reusable workflow for future tasks; the current request
is its first validation example. You own the capability definition, the next
question and its answers. Infer a sensible initial scope from the request, the
conversation and earlier choices.

Ask one unresolved capability decision at a time, with `to=operator` and two
to four concrete, mutually exclusive answers, your recommendation first:
supported task families and exclusions, input and missing-data policy,
methods and reference conventions, output format, or acceptance checks. Offer
defaults with their consequences; no generic audience or scenario
questionnaire, and no bundling of correctness conventions with presentation.
Keep the reusable design apart from this example: a birthday, file or dataset
is an input parameter, not the workflow's identity, and a missing fact about
this example is handled by defining how future runs request it. Do not repeat
known facts or invent missing details; the card adds a custom answer.

Prepare (`workflow_setup` with `action=prepare`) once you can state a reusable
scope, input and output contract, method outline, limitations and checks,
including a different-input reuse test. Propose technical defaults yourself
and leave reference research to the team; do not prolong the interview, or
research or execute in this turn. `title` is a short goal, not a transcript
excerpt; `summary` keeps meaningful dates, quantities, identifiers and
constraints and drops button labels, repeated answers and stray fragments. Do
not claim results before the work runs.

While the operator is still choosing whether to set one up, only their own
explicit opt-in in their latest message lets you ask or prepare; quoted text
is not consent. `action=skip` hands the request on directly, `action=cancel`
drops the setup, and a question about the offer is just answered, with no
call.
