---
name: "Developing an idea through experiments"
description: "The guide that defines Experiment: implement the selected Idea faithfully and develop it through adaptive experiments until the evidence supports a strong paper."
---

# Developing an idea through experiments

## What Experiment should establish

Turn the selected idea into a real implementation and develop it until
representative evidence supports a scoped thesis with scientific value.
Building the method and running the experiments happen in this one stage:
the experimental design changes in place as evidence arrives; it is never
a frozen plan handed down from somewhere else.
Judge results the way a strong experimentalist does — against baselines,
effect sizes, and the question at hand, in context. Before each scientific
comparison, state the capability, a competing explanation, and the control
that separates them. Adapt the design using development evidence; freeze the
comparison before held-out confirmation. Do not impose universal success
thresholds or an immutable global plan. Report what the evidence supports.
A credible improvement in any meaningful dimension can carry the
contribution; the target is a result that a top reviewer would remember,
not a complete-looking experiment matrix.

When the thesis attributes an effect or interaction to particular components,
cross those components on the same experimental units. Match the input, model
state, layer/group, resource budget and evaluator conditions that could explain
the difference, and use those shared identities in the analysis. Two panels
with different selection rules or states do not isolate a component interaction.
Start with a decisive paired pilot, then confirm the claim's intended scope;
preserve adverse and null cells instead of selecting only the favorable pairs.

When exploration reveals a promising layer, group, task or resource regime,
record the selection rule and inspected data in the existing configuration or
research notes. A selected example establishes that a mechanism can occur.
Freeze the regime, intervention, strongest comparator, primary outcome and
sampling rule before testing independent units there. Reproducing the selected
cases and confirming the mechanism on new cases answer different questions;
a panel from another regime also cannot settle whether the discovered effect
generalizes within its proposed regime. Cases examined during selection are
development evidence even if they were not chosen. A split held out from model
fitting is not automatically held out from subsequent method or regime selection.
Retain the exploratory comparisons and all confirmation outcomes. Base uncertainty
on independent contexts, subjects or runs, rather than counting correlated
heads, layers or repeated deterministic treatments as independent samples.

Match the execution boundary and information timeline to the claim. For an
online protocol, run each decision with only the context and transmitted data
available by that point; complete-trace reconstruction does not establish that
the receiver could act on time. Keep validation-only future data out of the
decision path. Show how required side information reaches the actual receiver,
and include its cost unless the declared interface really provides it. Align
block lengths, framing, termination, latency and setup amortization with the
deployment being measured. For a whole-system claim, exercise the actual loop,
including its terminal or bonus decisions, and distinguish component timing
from full-loop performance. Start with a meaningful pilot of that interface.

## How to develop the evidence

1. Read the research notes in `RESEARCH_NOTES.md` and trace every load-bearing thesis element to concrete
   code, configuration, data, outputs, and information boundaries.
2. For every method the project compares against or extends, clone the
   official implementation — or the strongest public one when no official
   code exists — at a pinned revision into `third_party/` and run one of its
   shipped examples end to end (`engineer/delta-on-reference.md`). Build the
   new method as a delta on that code so the code diff is the idea diff;
   reuse maintained components instead of reimplementing them from a paper
   summary. Also survey the released code of recent papers in the same area
   — including ones the experiments will not compare against — and read the
   high-quality ones as reference implementations: how they structure the
   training and evaluation code, which libraries they build on, and how they
   handle the details a paper summary glosses over. Borrowing a proven
   pattern from a strong recent codebase beats inventing one.
   Training, post-training, distributed training and serving go through an
   established framework chosen by a current survey, never a hand-rolled
   loop or serving path: those are slower, subtly wrong in ways that
   contaminate every result built on them, and convince no reviewer. Write
   custom infrastructure only when that infrastructure is itself the
   contribution being studied. Choose the framework with
   `engineer/infrastructure-landscape-survey.md`, which replaces any
   remembered list of framework names with a dated, verified survey.
3. Set up a clean project-local environment before writing method code: the
   project gets its own virtual environment on the system interpreter, with
   dependencies installed and pinned there — never in the framework
   environment, and never inherited from another project's leftovers. Install
   the complete toolchain the method actually needs and confirm each piece
   runs: compilers, the CUDA toolkit and driver-compatible libraries,
   profilers, and domain toolkits — kernel-optimization work in particular
   depends on many of these (nvcc, Triton, CUTLASS, Nsight and friends), and a
   missing or mismatched one quietly invalidates every measurement built on
   top of it.
   GPU inventory indices are physical, while `cuda:0` means the first device
   exposed to the launched process. Honor any allocation or inherited
   `CUDA_VISIBLE_DEVICES` mask. When choosing an authorized free GPU yourself,
   include the intended mask in the actual launch command and match the
   configuration's logical index to it. Merely writing that environment prefix
   in a saved command or example does not apply it. Confirm and retain the
   actual device mapping with the run command before a long execution.
   A prompt's machine inventory is a snapshot, not a reservation. Launch long
   GPU jobs through the durable runner with its accelerator/count/peak-memory/
   duration/intent flags so other cooperating projects share the same resource
   queue. Honor the assigned visibility mask inside the command. Inspect
   unmanaged device processes too: available memory and momentary zero GPU
   utilization do not establish an uncontended measurement window.
4. Implement against `tests/spec`. Write the method card and the executable
   spec first (sections below), then implement the method and baseline
   through real entry points under comparable data, compute, information,
   and evaluator access until the differential, knockout and parity tests
   pass without loosened tolerances or skips. Build the strongest faithful
   version of the idea, not the easiest version that can pass a local check;
   where the implementation must simplify the route, say so in the card's
   Notes column and mark the entry point `# @simplified` before running
   anything that bears a claim. Each implementation task arrives as a brief
   (`engineer/implementation-brief.md`): claim verbatim from `METHOD.md`,
   the components this task builds with their entry points, interfaces,
   the `tests/spec` ids that must pass, data and scale as the route states
   them, commands, environment prerequisites and an executable definition
   of done. A brief missing a field is completed from `METHOD.md` and the
   route before code is written, and the completion is named in the round
   summary; the brief is never narrowed to fit what is convenient. Write
   the code for the Reviewer that reads it without you present
   (`engineer/write-for-review.md`): `# @component <name>` above every
   component entry point, `# @simplified`, `# @reuses` and `# why:` where
   they apply; the host builds the review packet from these anchors.
5. Beyond `tests/spec`, run only the smallest engineering checks needed to
   establish imports, shapes, branches, numerical behavior, and end-to-end
   wiring, then run a known detectable positive control through the same
   evaluator path.
   Exercise the actual selected method and configuration, including its full
   update sequence, before a large panel. For an iterative bound, cache, or
   refinement algorithm, check the terminal limit the method promises as well
   as intermediate validity: for example, full materialization should recover
   the declared dense result or accuracy within the numerical contract. An
   interval can contain the reference while becoming uselessly wide; zero
   containment misses alone do not establish the requested accuracy. Include
   removal/replacement updates when those occur in the real path, and ensure
   the positive control executes this variant rather than a legacy default.
   Retain a failing real input as the first regression for a repair; a toy
   convergence check does not establish behavior at the real input's scale or
   conditioning. Confirm that case and representative affected regimes before
   launching the replacement panel.
   When changing measured fields or output metadata, exercise the public
   consumer and final export on a small complete case as well: result
   serialization, aggregation, and any consumed table or LaTeX-macro output.
   Check native runtime types, not only hand-written JSON fixtures. Convert
   opaque identifiers to suitable scalar metadata at the boundary; do not
   silently stringify scientific arrays or numbers to suppress a write error.
6. Develop the method with real models or systems and the strongest
   same-information baselines required by the claim. Prefer existing public or
   official benchmarks with their released tasks, splits, protocols, and scorers.
   Small custom benchmarks are allowed under the cost-aware benchmark policy
   below, including as scientific evidence for a scoped mechanism claim.
   Choose models for task competence and claim scope, not release date.
7. Keep every run reproducible from its code, explicit configuration, command,
   and raw output. Preserve the actual executed source and configuration in the
   attempt's existing records or snapshot; a Git revision alone is insufficient
   when the working files have changed. Name the method/oracle variant explicitly
   when its semantics differ from earlier results. Give each attempt its own
   output directory. If an existing directory must be reused, invalidate the earlier completion marker before
   opening or truncating any raw file; never leave a prior success beside a
   new partial run. Publish completion only after the workers finish producing
   data and the raw configuration/repeat identities, counts, and scientific invariants
   agree with the declared run. Preserve interrupted attempts separately.
   A failed process may already have emitted useful diagnostics or timing rows.
   Retain those rows and the failure context before retrying in a fresh attempt;
   incompleteness prevents promotion but is not a reason to erase observations.
   Persist raw measurements as cases finish, independently of final summary
   assembly. A metadata or exporter failure should be repairable from saved
   measurements rather than forcing the scientific computation to run again.
8. Treat weak results as optimization signals against a fixed claim. Work
   the diagnosis ladder below in order, one diagnosed change per attempt,
   and record each rung's evidence — the design and the runs live together
   here precisely so this iteration is cheap. The claim itself does not
   move.
9. Separate small engineering diagnostics from claim-bearing experiments.
   Stop repeating micro-benchmarks once they no longer change the next decision.
10. Use held-out confirmation after method and evaluation choices stabilize.

Choose sample counts from coverage of independent items and the precision
needed for the claim; explain the choice in the research notes. Repeat stochastic
runs when seed variation could change the conclusion. Rerunning deterministic
comparisons adds no independent evidence.

For latency, throughput or bottleneck claims, preserve the actual device
identity, process occupancy and relevant load over the measurement window.
Run on an uncontended accelerator, or explicitly define and match the competing
load when sharing is the experiment. A shared-device smoke test can establish
correctness without establishing isolated performance. Independent repetitions
may run sequentially on the same released device; three processes do not
require three simultaneously available cards. If another workload overlaps a
timing run, keep the observations and overlap record, investigate the affected
comparisons, and repeat only the measurements whose interpretation needs it.
Do not silently pool uncontrolled shared-device and isolated timings, or stop
another project's work to obtain a preferred result.

On resume, check the actual recorded observations and the code/configuration
that produced them before deciding whether work is complete. A copied
`completed` flag or an old checkpoint is not that evidence. Reuse a complete,
valid attempt; rerun only work made incomplete or invalid by a concrete defect.
Check actual compute processes as well as any shell or launcher PID. An exited
wrapper does not prove its children have stopped. Before replacing a run,
confirm that every producer for its output has exited; use a separate attempt
directory and preserve the old observations. Low GPU utilization while the CPU
is working, or output buffered until completion, does not establish failure.
Let healthy attempts finish before applying optional performance optimizations.
Launch long CPU analyses as well as GPU experiments through the available Argus
durable job interface, with an actual task record, project environment, explicit
command and separate attempt output. A provider's native background shell can
be owned by that CLI invocation; its promise of a completion notification does
not ensure survival after the model call ends. While the durable job runs,
continue independent scientific work and preserve its task identity in the
existing checkpoint. Check the real producer and completed outputs at the next
decision point. A wait response or an empty model answer does not make the
scientific batch ready for review.
When promoting a new result, validate the whole attempt first and switch the
canonical reference together, rather than mixing raw rows, summaries, and
completion records from different attempts. Use the existing run records and
research notes; this does not require a new reporting document.

When configuration or a public wrapper changes, exercise the same command path
advertised to readers with its current configuration and an empty output
directory. Use a supported small run first when the full study is costly, then
run the intended scale after that path works. An import test, a private helper,
or an old saved result cannot substitute for the public entry point. A changed
oracle or evaluator also requires updating every affected headline comparison,
not just the smallest positive case; reuse evidence whose execution contract
did not change.

If the paper retains an earlier method or certificate alongside a new one,
keep both reproducible through an explicit variant choice or the preserved
executed source and configuration. Changing the default must not silently make
the published command run a different method from its cited panel. A paper
build may consume validated results or emit a reproducible runner, but it must
not silently replace the scientific implementation or its configuration.

Scale the evidence to the claim, not to the first configuration that ran. A
thesis about language models in general is tested across families and sizes;
a thesis about a phenomenon needs enough independent items, worlds, or tasks
that a reviewer cannot attribute it to a handful of templates; a thesis about a
mechanism needs the matched ablation that isolates it. Use available hardware
and cached models for the comparisons that resolve the question. A
development panel sized for quick iteration is not the claim-bearing
evaluation; once method and evaluation settle, run the comparison at the scale
the claim needs and say in the research notes why that scale is enough.

### Cost-aware benchmark policy

Use established benchmarks by default for broad task-performance claims and
large-scale evaluation. Small custom benchmarks are allowed when they consume
no API calls or only a small amount within the existing authorized budget.
Examples include locally generated factorial controls for token identity,
position, layer, or activation amplitude, evaluated on a local model.
They may support the mechanism they actually test, not just implementation
smoke checks.

Before dispatch, account for the total API calls and tokens across generation,
labeling, evaluation, judging, and planned repetitions, as well as local compute.
A small item count alone does not establish low cost. Expanding such a benchmark
requires reassessing the total workload; the small-experiment exception does not
authorize a large API-backed synthetic campaign. Existing project-specific
restrictions, including a ban on paid APIs or custom tasks, still apply.

Make custom task construction, label derivation, splits, controls, and scoring
explicit and reproducible. Before scoring any model on a custom benchmark,
check a constant-answer baseline and verify that the intended targets are
separable on its items. Resolve a trivial shortcut or indistinguishable labels
in the task before interpreting model scores. Execute the experiments and
preserve their actual results. A custom mechanism test must not masquerade as
official benchmark coverage or replace established evaluation needed for a broad
performance claim.

### Planner scale assessment after a passing experiment

Reviewer acceptance establishes that the current experiment meets its
requirements; it does not by itself establish adequate research scale.
Before advancing to Paper, Planner inspects the accepted results and actual
configuration in its next normal planning turn. Compare training coverage,
independent evaluation items, task or template families, model or agent scales,
repetitions, and uncertainty with the operator's research objective. Identify
which dimensions matter for that objective rather than imposing universal
sample counts or requiring every possible benchmark setting.

If coverage or precision is insufficient, stay in Experiment and assign an
incremental expansion of the existing implementation. Reuse valid completed
results and the evaluator; prefer relevant released benchmark splits, task
settings, training data, models, or repetitions. Apply the cost-aware benchmark
policy to any small custom experiment and its expansion. Do not count repeated
identical runs as new independent evidence. Make sample counts configurable and validate against the
selected configuration, not a hard-coded pilot count. A minimum count is not
an exact-count requirement.

Keep held-out data out of training and method selection. Expanding training
creates a new checkpoint and comparison with fresh held-out confirmation;
preserve the earlier results separately rather than silently pooling them.
Use measured throughput and available resources to size the next run. Surface
an actual access or budget blocker instead of quietly shrinking the objective.

If scale is sufficient, state the evidence-based rationale in the existing
research plan and Planner REASON, and request `ADVANCE_TO_STAGE=paper` with the
writing task. Manager applies the transition. If insufficient, leave that
field unset and explain the expansion in the task. Repeat this judgment after
the expanded experiment is reviewed, not by rerunning an unchanged inspection.
Do not replace this check with a narrower paper claim or a standalone
validation-only mission.

### Fixed claim: iterate until it works

The claim in `METHOD.md` is fixed at Idea selection. Only the operator may
change it; no role rewrites it to fit the code, the data that happened to be
at hand, or the first result. A negative or weak result is an optimization
signal, the way a failing test is for infrastructure: the implementation,
the setup or the recipe is wrong until diagnosed otherwise. Work this ladder
in order, one rung per attempt, and record the evidence for each rung in the
run records before moving to the next:

1. **Implementation fidelity** against the method card: every component
   anchored and reached on the executed path, knockouts that fail in the
   component's absence, differential tests against the oracle and the
   pinned reference (`engineer/hypothesis-implementation-contract.md`).
2. **Setup and evaluator**: the positive control recovers a known signal
   through the same path, the data pipeline yields what the route describes,
   no leakage across splits, and the pilot is large enough to resolve the
   promised margin (`engineer/suspect-the-setup.md`).
3. **Hyperparameters and recipe**: start from the framework's recipe at the
   pinned revision, then change one factor at a time with the reason beside
   the value (`engineer/recipe-anchored-tuning.md`).
4. **Scale and data**: the route's datasets and sizes, not a shrunken
   pilot; an effect absent at toy scale has not been tested.
5. **Baseline fairness**: matched information, compute and tuning; a baseline
   run at an advantage, or below its published number, measures nothing.
6. **Method variants that still satisfy the claim as stated**: a different
   realization of the same components, never a different claim.

At least three distinct, diagnosed attempts, each naming its rung and its
evidence, precede any escalation. Escalation is an operator question carrying
that evidence — a blocking question or `operator_options` with the diagnosed
rungs, the numbers and the candidate changes to the claim — never a narrowed
claim, never a "restricted case" or "negative result" paper, never a variant
scheduled under a new title. Repeated development on the same panels is not
confirmation, however many cycles it took; a further attempt needs a concrete,
diagnosed cause from the ladder.

Plan only on resources that are actually in hand. Human participants, ethics
approval, paid annotation, credentials, external services, or compute the
operator has not supplied cannot be scheduled as work in an autonomous
campaign; raise the need once, then design the claim around what can be
executed here. Never stand in for missing evidence with fabricated,
placeholder, or projected results: not in the runs, not in the research notes, and
never in a manuscript.

Choose benchmarks that expose the method's mechanism and real advantage rather
than convenient saturated tasks. Inspect the existing benchmark's label
provenance, shortcut opportunities, and ability to distinguish the claimed
mechanism; if unsuitable, select another established benchmark or a small
custom experiment permitted by the cost-aware benchmark policy. Follow surprising positive evidence when it
reveals a stronger contribution, then confirm it on untouched data. Keep
relevant losses visible internally, but do not let defensive edge-case coverage
replace the main result.

Check real external, numerical, persistence, and security boundaries. Inside
the controlled implementation, trust established invariants. Do not add
redundant guards, fallback chains, reports, wrappers, or abstractions merely to
make the project look robust.

Do not freeze a global experiment plan, reopen Idea selection, hide relevant
losses, or present unfinished development as a negative result. The paper
argues the claim as stated in `METHOD.md`; a negative or boundary paper exists
only after the operator has changed that claim.

## Method card

`METHOD.md` at the project root is the authoritative statement of the
method as the paper will claim it, written once by the Engineer who
implements it, in the first Experiment round, from the selected route,
before method code. It holds the one-paragraph statement, a `Components`
table (`Component | The idea prescribes | Notes`: the route quoted, and a
note only for a deliberate simplification), the `Protocol` copied from the
route with every deviation named, and what would falsify the claim. It is
touched again only when the method itself changes. Implementation status
per component, the tests that prove it, reused code with pinned revisions,
hyperparameters with their `# why:` reasons, and the change history are not
written by hand: the host derives them from the code, the
`@pytest.mark.component` markers on `tests/spec`, the config files and git,
and shows them beside the card in Atlas and to the Reviewer. It is a work
product shown to human readers, the one named exception to the rule against
extra files; the Reviewer reads it before the code and before the Engineer's
account. `engineer/method-card.md` and `engineer/method_card_template.md`
give the procedure and the body.

## Executable spec

`tests/spec` is what the method must do, in code the host runs after every
round without a model and shows to the Reviewer and the Engineer as Raw
verification evidence. It is written before the first claim-bearing run:
an oracle transcribed from the route's equations (slow, `float64`, one
function per equation), differential tests of the implementation against
the oracle and against the `third_party/` reference, one knockout per
component in the card (disable it; on a case built to exercise it the
output must change), and claim-shaped tests for complexity, memory and
throughput claims, each tagged with the component it exercises.
Outcome-shaped tests that assert the headline number prove nothing and do
not belong here. Nothing blocks on the result; a
failing test, an unexplained skip, or a test that was collected last round
and is missing now is a repair the Reviewer names. Templates and the
procedure are in `engineer/executable-spec.md`.

## When the evidence is ready for Paper

Enter Paper after Reviewer accepts the experiment and Planner's post-result
scale assessment finds its coverage and precision sufficient for the objective.
The evidence must support the claim as stated in `METHOD.md` and improve at
least one scientifically meaningful dimension. Do not require a hard numeric
margin, wins on every headline metric, or dominance over every strong
baseline. Keep uncertainty, relevant losses, and tradeoffs visible; state the
claim's scope as the card states it, not narrowed to the cells that won.
Manager alone advances the stage.

## Research notes

When the entry bar is met, replace the research notes at project-root
`RESEARCH_NOTES.md`, beginning with `# Research notes — Experiment stage`.
Include the thesis, winning comparisons, strongest
baseline, relevant limitations, confirmed figures/data, and minimum
reproducibility pointers needed by Paper. Organize it around the claim
and its evidence, not around the order in which experiments ran.

## When another skill would help

Start with this guide. Open one specialist skill only for the current
decision, then return here. Do not read all the sources in advance.

| When needed | Open | Use it for |
|---|---|---|
| The method must be stated before it is built, or has changed | `engineer/method-card.md` | Write and update the project-root `METHOD.md` from the selected route |
| A baseline or extended method has public code | `engineer/delta-on-reference.md` | Clone it at a pinned revision under `third_party/`, run an example, build the delta and the parity test |
| The spec suite must be written or extended | `engineer/executable-spec.md` | Oracle, differential, knockout and claim-shaped tests under `tests/spec` from the templates |
| An implementation task must be handed over or checked for completeness | `engineer/implementation-brief.md` | The brief a Planner writes and an Engineer verifies before code: claim, components, interfaces, tests, data, commands, environment, definition of done |
| Code must be readable by a Reviewer who was not there | `engineer/write-for-review.md` | `# @component`, `# @simplified`, `# @reuses` and `# why:` anchors the host turns into the review packet |
| The thesis may have drifted from code | `engineer/hypothesis-implementation-contract.md` | Map the selected mechanism to the executed path |
| A fresh Reviewer must verify execution fidelity | `reviewer/claim-to-code-trace.md` | Trace claim-critical calls and formulas |
| Training or large inference infrastructure is required | `engineer/infrastructure-landscape-survey.md` | Choose the framework from a current, verified survey; `engineer/training-infrastructure-guide.md` covers standing it up |
| A project environment needs setup or repair | `project-venv-package-management.md` in the global library | Reuse the configured environment and install only required dependencies |
| A concrete dependency or resource may block execution | `engineer/environment-readiness.md` | Check only the resources this implementation uses |
| An experiment changes a requested time estimate or misses a milestone | `engineer/research-timeline.md` | Recompute the forecast and explain the deviation with evidence |
| The method is below its baseline | `engineer/research-grind.md` | Work the diagnosis ladder against the fixed claim over many rounds |
| The run may be misconfigured | `engineer/suspect-the-setup.md` | Separate setup failure from method evidence |
| A mechanism needs one decisive ablation | `engineer/ablation-planner.md` | Choose only claim-changing ablations |
| Raw evidence or evaluator behavior is disputed | `reviewer/reading-the-evidence.md` | Inspect code, configuration, evaluator, and rows |
| The next experiment or Paper decision is unclear | `reviewer/experiment-results-review.md` | Independently judge what the evidence supports and what remains to learn |
| Results must become a precise claim | `engineer/result-to-claim.md` | Relate direct evidence to the strongest supported thesis |
| Confirmed results need tables or figures | `engineer/research-results-analysis-and-figures.md` | Produce claim-bearing paper visuals |
| Data figures need publication styling | `engineer/paper-chart-styling.md` | Shared style helper: vector PDF, TrueType fonts, colorblind palette, error bars |

Specialist Skills answer one implementation or experiment question. They do not
define a global plan, stage transition, or parallel report.
