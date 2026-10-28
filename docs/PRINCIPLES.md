# Argus Principles

This is not a checklist or a list of fixes for one incident. A principle must
remain useful across models, domains, tasks, and implementations, and it must
derive concrete design choices. A rule about one field, gate, or file is a
policy or mechanism, not a principle.

Argus keeps nine governing principles. A new mechanism must say which principle
requires it. If it cannot, it should not enter Core.

Chinese version: [PRINCIPLES.zh-CN.md](PRINCIPLES.zh-CN.md)

## 1. Agents judge meaning; the runtime guarantees mechanics

Value, strategy, priority, completion quality, and the meaning of evidence
require context and belong to agents. Permission, budget, irreversibility,
ownership of a path, and the truth of records are boundaries an agent cannot
vouch for itself; the runtime guarantees them.

The runtime surfaces facts an agent cannot observe directly. It does not make
research judgments through regexes, counters, fixed thresholds, or schemas.
Agents cannot use prose to bypass permission, budget, concurrency, or truth.
A capability cannot depend on a development machine that somebody prepared by
hand. The runtime discovers host capabilities and either creates a
project-local, reproducible environment or reports that the capability is
unavailable. Preinstalled virtual environments, caches, paths, credentials,
and one person's shell configuration are not product capabilities.

The runtime never speaks for an agent. A judgment is recorded once, by the
actor that made it, and nobody else rewrites it. When the runtime intervenes
with a budget stop, a pause, or a policy, it records its own fact beside the
judgment instead of editing or imitating it. Whether work is progressing is a
judgment too: the runtime enforces the budgets a principal set, not its own
estimate of progress. Summaries, checkpoints, and "latest" views are
projections rebuilt from these records, never a second source of truth.

The runtime is not a parent. What an agent backend (Copilot, Codex, and the
like) can do itself, the runtime does not do for it with built-in tools or
mechanisms: starting and waiting on long jobs, deciding what to wait for and
for how long, polling, judging whether it is stuck, deciding whether to look
again. Nor do prompts forbid the backend from doing these its own way. The
runtime does only what a backend cannot: hand work between roles, deliver new
messages, keep the records, and hold the permission and irreversibility
boundaries. A mechanism that only does for an agent what the agent could do
itself is deleted; the mistakes it meant to prevent are pointed out by an
observer watching the work, not prevented by more rules.

## 2. Core provides capability; Verticals provide domain policy

Core is a small stable kernel for execution, persistence, scheduling,
permission, communication, observability, and versioned extension interfaces.
Verticals own stages, Skills, tools, evidence meaning, review standards, and
completion semantics.

Core does not import named Verticals or know a venue, dataset, theorem, or
experimental threshold. Built-in and external Verticals load through the same
interface. Adding or replacing domain policy must not require changing Core.
Verticals declare requirements; Core environment adapters discover, prepare,
or reject those capabilities on the current host. A Vertical does not embed
paths or preinstalled environments from one machine.

## 3. Thought is prose; only side effects need a minimal protocol

Roles reason and hand off in natural language, sharing goals, strategy,
evidence, and open questions. Models are not required to compress thought into
JSON, fixed tables, or large key-value schemas for parser convenience.

Typed fields exist only where the runtime must perform an unambiguous side
effect, such as selecting an owner, assigning path ownership, changing
lifecycle state, or authorizing an irreversible action. The protocol stays
small, and a formatting mistake cannot erase understood work. Legacy formats
may remain readable without governing new prompts.

## 4. Every token must buy information or action

A model call must change a real artifact, change strategy, reduce a material
uncertainty, or provide an independent judgment that existing evidence cannot
derive. Repeating context, filling schemas, rerunning unchanged checks,
re-adjudicating for UI, and repeatedly reviewing an unchanged artifact are not
work.

Tools obtain deterministic facts. Context is shared by reference and delta.
The default is the shortest ownership chain that can complete the task. More
agents are justified only when parallel search or independent judgment can
change the outcome. Token efficiency is measured with success, quality, wall
time, model calls, tool calls, and questions, not prompt length or cache rate
alone.

## 5. Exploration optimizes upside; claims accept evidence constraints

Exploration searches actively, combines distinct mechanisms, and pursues high
information gain and high upside. Speculation, report-only research,
unimplemented ideas, and failed prototypes are legitimate. The integrity floor
always applies, but final-delivery standards do not decide whether an idea
deserves exploration.

Only claims require implementation fidelity, faithful reference standards,
direct evidence, and independent review proportional to their scope. Anything
presented to a user as settled, written into a final artifact, or used to
authorize a downstream decision is already a claim and cannot retain an
"exploration" exemption. Review happens once on a frozen candidate artifact.
Ceremonial gates, repeated certification, and artifact counts cannot replace
direct evidence.

The runtime, not the producer, freezes the candidate: everything that changed
since the last accepted state, with the evidence that produced it. The reviewer
reads that change and evidence directly and can read everything the producer
could change; the producer's account guides the reading but is not the
evidence. The standard a claim must meet is set by its owner before the
candidate exists. Review judges the candidate against that standard and says
what would change the verdict. A concern outside the standard is recorded for
its owner instead of moving the standard, unless it breaches the integrity
floor, which always blocks.

## 6. Failure updates strategy; it is not automatically the product

A negative result first distinguishes setup, implementation, optimization,
data, scale, evaluator, and method failure. While valuable repairs or
alternative routes remain, failure drives the next engineering or strategy
move instead of being packaged immediately as the final deliverable.

Negative evidence is never hidden. It becomes a final contribution only when
the negative question is consequential and prespecified, the experiment could
detect the target effect, and the strongest alternative explanations have been
excluded. Otherwise it is honest intermediate feedback.

## 7. Optimize the programme, not the task queue

The objective is the capability, discovery, or paper the user asked for, not a
green backlog. Tasks, plans, method names, and stages are disposable means. If
local progress no longer changes the final conclusion, change the route rather
than hill-climbing its neighborhood.

A living daemon, a longer paper, more figures, a passing gate, or a completed
mission is not progress by itself. Progress improves the final outcome, reduces
a key uncertainty, or changes programme strategy. Managers maintain this
programme view; Planners create only work that advances it.

## 8. Autonomy follows reversibility

Local, reversible, budgeted actions are autonomous by default: reading,
research, implementation, tests, isolated dependencies, and reversible
experiments. Credentials, payment, external publication, destructive deletion,
global infrastructure, and other irreversible authority stay human-owned.
Environment preparation stays project-local or inside an isolated container by
default. Argus does not assume administrator access or mutate a host's global
toolchain merely to make one task runnable.

Questions are a last resort, not a way to transfer risk. Independent Reviewers
serve material claims and work that genuinely needs a second judgment; they are
not a permission layer for every local action. Work in progress is not gated
turn by turn; review happens when a candidate is frozen.

Authority and obstacles go to whoever can act on them. A role asked for a
judgment receives the access that judgment needs. An obstacle such as a missing
permission or a conflicting contract goes to the actor that can remove it,
never back to a role that cannot.

Human-owned authority is the operator's, and the operator may hand it over.
When the operator says not to be asked, the Manager holds every authority the
operator holds, including the human-owned kinds above; a run with no operator
at all is the same case. Only the operator's own words set that policy, and the
operator can withdraw it at any time. The Manager decides what the operator
would have decided, records each such decision as its own with the evidence it
cited, and the delivery report lists them so the operator can review or reverse
them. A decision therefore never waits on someone who will not be asked. What
no policy can hand over, such as credentials the Manager does not hold, stays
out of reach; the Manager decides how the work proceeds without it.

## 9. Evolution is proven only by end-to-end outcomes

Changes to Argus are evaluated like product experiments on real user tasks with
matched controls. Gate pass rate, event volume, task count, and self-assessment
cannot show that the system improved.

Measure success, final quality, wall time, tokens, model calls, tool calls,
questions, recovery, and cost together. Production feedback informs the next
design, but the system under evaluation does not choose a favorable scoring
rule for itself. Environment-dependent capabilities must also reproduce from a
clean install or equivalent cold start; success on one hand-maintained server
is not portability evidence.

## Policies derived from the principles

These are currently sensible policies, not eternal principles:

- splitting Core and Verticals follows principle 2;
- prose handoffs ending in one `hand_off` form follow principles 1 and 3;
- direct short paths, on-demand Skills, and a shared mission view follow
  principle 4;
- explore/develop/certify follows principle 5;
- using negative results to repair or reroute follows principles 6 and 7;
- one independent review of a frozen final candidate follows principle 5;
- the runtime snapshots the workspace and gives the Reviewer the change since
  the last accepted state follows principles 1 and 5;
- acceptance criteria written before execution, verdicts given per criterion,
  and out-of-scope concerns kept as follow-ups follow principles 5 and 7;
- one append-only record per actor, with runtime interventions recorded as the
  runtime's own and every summary file rebuilt from those records, follows
  principle 1;
- round limits expressed as principal-set budgets, not inferred progress,
  follow principles 1 and 8;
- asking fewer questions and autonomously installing project-local
  dependencies follows principle 8;
- discovering CUDA, drivers, disk, and schedulers, creating a version-locked
  project environment, and running a one-batch smoke follows principles 1, 2,
  8, and 9;
- matched Argus-versus-direct-agent trials follow principle 9.

Implementations may change while the principles remain. When an implementation
conflicts with a principle, change the implementation instead of adding an
exception.

## Seven design questions

1. Is this a semantic judgment or a mechanical fact only the runtime can know?
2. Is this domain policy or a capability every Vertical needs?
3. Does this format control a real machine side effect, or only help a parser?
4. Will this model call change an artifact, strategy, material uncertainty, or
   independent judgment?
5. Will this work advance the programme's final outcome?
6. Who is the only author of this record, and does the judgment that uses it
   read the evidence itself rather than someone's account of it?
7. Could the agent backend do this itself? If so, do not do it for it.

If a mechanism repeatedly cannot answer these questions, delete it rather than
adding another layer to protect it.
