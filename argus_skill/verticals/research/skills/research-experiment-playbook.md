---
name: "Research Experiment Playbook"
description: "The single authoritative playbook for turning the selected Idea into a faithful implementation and developing it through adaptive experiments until the evidence supports a strong paper."
---

# Research Experiment Playbook

## Outcome

Turn the selected idea into a real implementation and develop it until
representative evidence supports a scoped thesis with scientific value.
Building the method and running the experiments happen in this one stage:
the experimental design is a living object that gets revised in place as
evidence arrives, never a frozen plan handed down from somewhere else.
Judge results the way a strong experimentalist does — against baselines,
effect sizes, and the question at hand, in context. Do not manufacture
preregistration ceremony: no success thresholds declared before the data
exists, no decision procedures or extension criteria fixed ahead of the
first run, no design tables treated as immutable. When evidence arrives,
interpret it honestly and report what it does and does not support.
A credible improvement in any meaningful dimension can carry the
contribution; the target is a result that a top reviewer would remember,
not a complete-looking experiment matrix.

## Work

1. Read `HANDOFF.md` and trace every load-bearing thesis element to concrete
   code, configuration, data, outputs, and information boundaries.
2. Inspect the strongest relevant official implementations. Clone and run a
   fixed public revision when compiling, adapting, or comparing its code; reuse
   maintained components instead of reimplementing them from a paper summary.
   Also survey the released code of recent papers in the same area — including
   ones the experiments will not compare against — and read the high-quality
   ones as reference implementations: how they structure the training and
   evaluation code, which libraries they build on, and how they handle the
   details a paper summary glosses over. Borrowing a proven pattern from a
   strong recent codebase beats inventing one.
   For training and inference infrastructure this is the rule, not a
   preference: RL post-training, preference optimization such as DPO,
   distributed training, and serving all go through an established framework
   (veRL, OpenRLHF, TRL, LLaMA-Factory, vLLM, or the released baseline's own
   stack). A hand-rolled training loop or serving path is slower, subtly
   wrong in ways that contaminate every result built on it, and convinces no
   reviewer — write custom infrastructure only when that infrastructure is
   itself the contribution being studied.
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
4. Implement the method and baseline through real entry points under comparable
   data, compute, information, and evaluator access. Build the strongest
   faithful version of the idea, not the easiest version that can pass a local
   check.
5. Run only the smallest engineering checks needed to establish imports,
   shapes, branches, numerical behavior, and end-to-end wiring, then run a
   known detectable positive control through the same evaluator path. Do not
   build a ladder of toy scientific experiments.
6. Develop the method with real models or systems, public or official
   benchmarks, authentic evaluators, and the strongest same-information
   published baselines required by the claim.
7. Keep every run reproducible from its code, explicit configuration, command,
   and raw output.
8. Treat weak results as optimization signals. Change the method,
   implementation, benchmark, baseline, controls, or scale when development
   evidence identifies a concrete reason — the design and the runs live
   together here precisely so this revision is cheap.
9. Separate small engineering diagnostics from claim-bearing experiments.
   Stop repeating micro-benchmarks once they no longer change the next decision.
10. Use held-out confirmation after method and evaluation choices stabilize.

Design experiments as single well-configured runs per condition: do not
prescribe repeated runs across random seeds. Spend the compute on the
comparisons that decide the claim — stronger baselines, more tasks, larger
scale — rather than on repeating the same run.

Choose benchmarks that expose the method's mechanism and real advantage rather
than convenient saturated tasks. Follow surprising positive evidence when it
reveals a stronger contribution, then confirm it on untouched data. Keep
relevant losses visible internally, but do not let defensive edge-case coverage
replace the main result.

Validate real external, numerical, persistence, and security boundaries. Inside
the controlled implementation, trust established invariants. Do not add
redundant guards, fallback chains, reports, wrappers, or abstractions merely to
make the project look robust.

Do not freeze a global experiment plan, reopen Idea selection, hide relevant
losses, or convert an unfinished campaign into a negative-result paper.

## Paper entry

Enter Paper when Reviewer judges that credible evidence improves at least one
scientifically meaningful dimension. Do not require a hard numeric margin,
wins on every headline metric, or dominance over every strong baseline. Keep
uncertainty, relevant losses, and tradeoffs visible, and scope the thesis to
what improved. Manager alone advances the stage.

## Handoff

When the entry bar is met, replace project-root `HANDOFF.md` with
`# HANDOFF — EXPERIMENT`. Include the thesis, winning comparisons, strongest
baseline, relevant limitations, confirmed figures/data, and minimum
reproducibility pointers needed by Paper. Organize the handoff around the claim
and its evidence, not around the order in which experiments ran.

## Progressive disclosure

Start with this Playbook. Open one specialist Skill only for the current
decision, then return here. Do not preload the table.

| When needed | Open | Use it for |
|---|---|---|
| The thesis may have drifted from code | `engineer/hypothesis-implementation-contract.md` | Map the selected mechanism to the executed path |
| A fresh Reviewer must verify execution fidelity | `reviewer/claim-to-code-trace.md` | Trace claim-critical calls and formulas |
| Training or large inference infrastructure is required | `engineer/training-infrastructure-guide.md` | Select and reuse maintained frameworks |
| A fresh project environment must be set up | `engineer/project-environment-management.md` | Create the project venv and install the ML stack cleanly |
| A concrete dependency or resource may block execution | `engineer/environment-readiness-gate.md` | Check only the resources this implementation uses |
| The method is below its baseline | `engineer/research-grind.md` | Diagnose and improve the largest live gap |
| The run may be misconfigured | `engineer/suspect-the-setup.md` | Separate setup failure from method evidence |
| A mechanism needs one decisive ablation | `engineer/ablation-planner.md` | Choose only claim-changing ablations |
| Raw evidence or evaluator behavior is disputed | `reviewer/experiment-audit.md` | Inspect code, configuration, evaluator, and rows |
| The next experiment or Paper decision is unclear | `reviewer/experiment-results-review.md` | Independently judge the evidence frontier |
| Results must become a precise claim | `engineer/result-to-claim.md` | Bind direct evidence to the strongest supported thesis |
| Confirmed results need tables or figures | `engineer/research-results-analysis-and-figures.md` | Produce claim-bearing paper visuals |

Specialist Skills answer one implementation or experiment question. They do not
define a global plan, stage transition, or parallel report.
