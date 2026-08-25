# Exploration Without Local Hill Climbing

Argus must not confuse engineering discipline with intellectual conservatism.
This document records a real failure mode, why it happened, the correction that
did not go far enough, and the policy now used by the `kernel_engineering`
vertical.

Chinese version: [exploration-without-local-hill-climbing.zh-CN.md](exploration-without-local-hill-climbing.zh-CN.md)

## The observed failure

During a long-running full-BF16 GLM-5.2 serving campaign on 8 AMD MI300X GPUs,
Argus began with broad online research. It inspected official model artifacts,
vLLM, SGLang, ROCm, AITER, public benchmarks, papers, issues, and pull
requests.

The execution log contained 1,851 GLM/ROCm-relevant web and GitHub tool calls
in the early research window. After the full-BF16 baseline was established,
only two relevant upstream source reads remained; after H2D was named as the
dominant bottleneck, the count was zero.

After the campaign found a dominant host-to-device expert-transfer bottleneck,
that curiosity disappeared. The loop generated a sequence of nearby mechanisms:

- packed and multi-stream copies;
- routed expert caches;
- explicit index maps;
- pointer tables;
- mapped-host fallbacks;
- recurrent prefetch variants;
- device-side and asynchronous SDMA controllers.

These were legitimate experiments, but the search became local. Argus did not
revisit the external frontier after the bottleneck was known. It missed systems
and ideas such as FluxMoE/PagedTensor, FineMoE, MoE-Infinity, Fiddler, and HIP
virtual-memory remapping, even though those mechanisms were directly relevant
to the observed failures.

The problem was not lack of tools. The problem was policy.

## Why the policy produced a local hill climber

Several individually reasonable rules combined into the wrong objective:

1. **Research was treated as a one-time phase.** Earlier grounding was reused
   after the precision target and bottleneck had changed.
2. **Implementation had a stronger reward than exploration.** Code, a benchmark,
   or even a concrete build failure counted as progress; a research-only report
   was easy to reject as "not executed."
3. **Anti-procrastination rules overcorrected.** Instructions such as "use the
   smallest relevant surface," "run the cheapest falsification check," and
   "produce a measurement every round" discouraged ambitious investigation.
4. **Live search depended on task classification.** Search was available for
   `algorithm_discovery`, while research-heavy work was often classified as
   `engineering_optimization`.
5. **Certification leaked into exploration.** Repeated runs, multiple seeds,
   reproducibility, confidence intervals, and safe fallbacks became default
   expectations before a mechanism had earned that cost.
6. **Cached Skills preserved old preferences.** Updating a top-level prompt was
   insufficient when project or shared Skills still said "smallest
   unimplemented mechanism" or "smallest fail-closed repair."

The rational behavior under those incentives was to keep modifying the nearest
known design. Argus was optimizing the probability of producing an accepted
increment, not the probability of discovering the best mechanism.

## A first correction that was still wrong

The first proposed repair introduced a "search reset" after repeated failures
or a material constraint change, and required research to finish with an
executable gate or patch.

That was better than never reopening research, but it still made curiosity a
permissioned exception:

- the agent had to fail enough times before it was allowed to search;
- a report was still considered incomplete without implementation;
- immediately verifiable ideas still had a structural advantage.

The operator rejected that design. Research must be allowed before failure, and
a good report can be the complete deliverable.

## Final policy: separate exploration from claims

The final design uses three distinct postures:

| Posture | Valid output | Default experiment cost | Risk posture |
| --- | --- | --- | --- |
| Explore | Sourced report, mechanism portfolio, hypotheses, open questions, optional prototype | No run required | Broad, radical, high-upside |
| Screen | One clean run or an inconclusive attempt | One run; no repeated controls or multi-seed campaign | Maximize information gain |
| Claim or retain | Correct implementation and comparable target-hardware evidence | Repetitions and variance only as needed for the claim | Strict evidence |

The core rule is:

> Exploration is allowed to be speculative, unimplemented, unverified, and not
> yet reproducible. A performance claim is not.

### Planner

The kernel Planner now:

- proactively uses current primary sources without waiting for failure;
- may schedule report-only `algorithm_discovery` tasks;
- keeps a portfolio of genuinely different mechanism families;
- may research independent families in parallel during long benchmarks;
- prefers expected upside and information gain over low execution risk;
- does not spend exploration slots repeating seeds, controls, or unchanged
  benchmarks;
- does not prefer the smallest patch or the easiest immediate validation.

### Engineer

The kernel Engineer now:

- follows surprising leads across papers, runtimes, kernels, memory systems,
  and adjacent stacks;
- may finish a research mission with a report only;
- may clearly label radical, unimplemented, or not-yet-reproducible ideas as
  hypotheses;
- uses one clean run for ordinary exploratory screening;
- reserves repeated runs and broader correctness work for candidates that may
  be claimed or retained.

### Reviewer

The kernel Reviewer now:

- judges a research report on source quality, factual accuracy, synthesis,
  breadth, and decision value;
- does not require code, a gate, multiple seeds, or immediate reproducibility
  for exploration;
- does not reject a high-risk idea merely because it is uncertain;
- still rejects an unsupported hypothesis presented as an established result;
- still requires rigorous correctness and measurement before accepting a
  performance claim.

## Runtime and Skill changes

The policy is implemented in:

- `argus_skill/verticals/kernel_engineering/stages.py`
- `argus_skill/verticals/kernel_engineering/skills/engineer/kernel-environment-first-engineering.md`
- `argus_skill/verticals/kernel_engineering/skills/reviewer/kernel-engineering-review.md`
- `argus_skill/verticals/kernel_engineering/references/frontier-search-protocol.md`
- `argus_skill/verticals/kernel_engineering/references/idgl-loop.md`
- `argus_skill/verticals/kernel_engineering/skills/engineer/kernel-benchmark-measurement-integrity.md`

Live search is available to both `algorithm_discovery` and
`engineering_optimization` kernel missions. Tests pin the role banners, search
availability, report-only acceptance, high-upside posture, single-run screening,
and the separation between exploration and certification.

The corresponding public implementation history is:

- `dd073f0d` - enable proactive kernel research;
- `c784f958` - favor broad kernel exploration;
- `173a2af0` - prefer high-upside kernel exploration.

The private mirror received equivalent changes.

## What remains non-negotiable

Removing exploration constraints does not permit dishonest claims.

Argus must still reject:

- fabricated execution or measurements;
- benchmark paths that did not exercise the changed code;
- corrupted or relabeled evidence;
- relaxed correctness thresholds presented as optimization;
- hypotheses described as measured facts.

Curiosity determines what Argus is willing to investigate. Evidence determines
what Argus is allowed to claim.
