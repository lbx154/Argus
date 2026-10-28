# From a research question to a reviewed paper: idea, experiments, paper, review

This is how an Argus team does research. It is advice for people and for the roles, not a
procedure Argus enforces: the Planner orders the work by it, the Engineer works by it, and the
Reviewer accepts work against its "done when". Beside this overview are `skills/`, one manual per
situation, opened when that situation comes up, and `checks/`, scripts that check what can be
checked mechanically.

## How the work goes

Research goes through four steps, in this order:

1. **Idea**: choose what to build, from sources alone.
2. **Experiment**: build it, and develop it through experiments until the evidence supports the claim.
3. **Paper**: write the paper that evidence supports.
4. **Review**: judge the paper as a reviewer at the venue would, and repair it until it holds.

Each step is one or more tasks the Planner hands out, and nothing else records which step a
project is in. The order only goes forward: a defect found later is repaired where it is found (an
experiment the review shows is missing is run during the review), and the chosen idea is not
reopened. A request for one step's work alone, such as an idea, a figure or a review of a draft,
is that step's work and ends after its review.

Two rules hold throughout.

- **The claim does not move to fit the results.** METHOD.md states it. A weak or negative result
  is first a defect to diagnose, one rung at a time (`skills/research-grind.md`). Who may change
  the claim depends on who chose the direction, which RESEARCH_NOTES.md records. If the operator
  named it, only the operator, asked with the evidence once every rung is worked. If the team
  chose it from a broad request, the Planner, in one reviewed task that records the refuting
  evidence under Dead ends in RESEARCH_NOTES.md, keeps the earlier results, and writes the new
  claim and its falsifier into METHOD.md before testing it. A narrowed claim, a "restricted case"
  or a negative result is never presented as meeting the operator's goal.
- **Nothing is reported that did not run.** Results come from the real system on the real data. A
  mock, a stand-in model, random inputs or synthetic data where the claim names a real one belong
  in tests, never in a result, and every number traces to raw output a run wrote.

## Files the steps share

| File | Holds |
|---|---|
| `RESEARCH_NOTES.md` | Where the research stands, rewritten (not appended) at the end of each step for whoever continues; each step below says what it holds |
| `METHOD.md` | The method as the paper will claim it: statement, components, protocol and falsifier (`skills/method-card.md`) |
| `tests/spec/` | What the method must do, in tests that run without a model |
| `third_party/<name>/` | The official code of each method compared against or extended, cloned at a pinned revision |
| `configs/` | Run configuration, each chosen value with a `# why:` comment |
| a folder per attempt | Each run's raw output, beside its command and configuration |
| `paper/main.tex`, `paper/*.bib`, `paper/main.pdf` | The manuscript, its bibliography, and the PDF built with `latexmk -cd -pdf -interaction=nonstopmode -halt-on-error paper/main.tex` |
| `paper/figures/<stem>.pdf`, `<stem>.source.json` | Each figure, and beside it what made it: `{"source": the script or editable drawing, "data": [the files or folders it plots]}`, paths relative to the workspace |
| `.argus/evidence/` | Logs and check output the next reader needs |

Keep the workspace in git and commit as the work goes: the history shows that the method was
stated before it was built.

## 1. Idea

Choose one technically nontrivial idea that would be a solid, publishable contribution at the
target venue: a clear delta from the closest work, and a decisive first experiment that fits this
machine. For a broad request, develop several materially different routes, review each, then
choose one; for a direction the operator named, examine and position it without replacing it.
Work from sources alone: papers, official documentation and code, no experiments. How:
`skills/idea.md`.

**Done when** the chosen idea has a thesis, its load-bearing mechanism, the closest work and the
delta from it, the strongest same-information baseline, resources that exist here, the strongest
argument against it stated plainly, and a decisive first experiment. Overlap with prior work and
open questions are listed as risks for Experiment, not reasons to reject. RESEARCH_NOTES.md holds
the idea, who chose the direction, and, for a broad request, one line on why each other route was
set aside. An idea-only request ends with the reviewed idea in the letter and writes no notes.

## 2. Experiment

Turn the idea into a real implementation and develop it until the evidence supports the claim.
Design and runs live together: the plan changes as evidence arrives; the claim does not. The first
task, by the Engineer who then implements, writes METHOD.md, clones the reference code and writes
tests/spec. How: `skills/experiment.md`.

**Done when**:

- METHOD.md was written from the chosen idea before the method code, and the code matches it:
  each component's entry point is named as `path:Symbol` and has a knockout test that fails
  without it;
- the method and real, strong, published baselines run through real entry points, built on the
  official code at a pinned revision, with no stand-in in any run that bears on the claim;
- a positive control recovered a known signal through the same path and evaluator before any null
  or negative result was read;
- every run can be reproduced from its code, configuration, command and raw output;
- the comparison was frozen before held-out confirmation, and held-out data chose nothing;
- the evidence supports the claim as METHOD.md states it, at the scale the claim needs (several
  model families and sizes for a claim about models, enough independent items that templates
  cannot explain it for a phenomenon, the matched ablation for a mechanism), and a claim of
  superiority beats the strongest same-information baseline;
- RESEARCH_NOTES.md holds the thesis, the comparisons that establish it, the strongest baseline,
  the losses and limits, the figures and data to use, and the commands that reproduce them.

An accepted experiment does not settle scale by itself: before Paper, the Planner compares the
accepted results' coverage with the operator's goal and, when it falls short, plans the expansion
first.

## 3. Paper

Write the paper that argues the claim as METHOD.md states it, led by the strongest result: an
argument, not an experiment report. How: `skills/paper.md`.

**Done when**:

- paper/main.tex is complete: every claim-bearing experiment, figure, table, citation and section
  the venue requires, with the body at the length planned for the venue;
- it compiles under the venue's current official author kit into paper/main.pdf;
- it has at least two distinct scientific figures, usually the method, the main comparison and an
  ablation or diagnostic, each with its source record;
- every citation was checked against its primary source;
- no private path, internal name, role or development narration appears in it;
- RESEARCH_NOTES.md holds where the manuscript is, the thesis, where each piece of evidence sits,
  the venue, and the length target beside the actual length.

## 4. Review

The Reviewer judges the complete paper as an independent reviewer at the selected venue: the
science, every page and figure at print size, the language, and the venue's rules. The Engineer
makes every repair it asks for, new experiments included, and the Reviewer judges again. How:
`skills/review.md`.

**Done when** the Reviewer recommends the paper at the venue at the operator's target level or
higher, with no reason to reject it left, and its last letter gives that recommendation, the
strongest case for acceptance, and what is still weak. A weak accept does not finish a goal of a
strong paper, and a rating is never raised to end the work.

## Checks

The scripts in `checks/` check what "done when" says that a script can check. Run them with Node:

    node <this folder>/checks/<script> <workspace>

Each exits 0 when everything holds, 1 when something does not (one line per problem), and 2 when
it could not run; its first lines say what it checks.

| Script | Checks | Run before accepting |
|---|---|---|
| `method-card.mjs` | METHOD.md has its parts and no template text left, every component has a test under tests/spec marked with its name, and METHOD.md was committed no later than the first method code | an Experiment task |
| `paper.mjs` | paper/main.pdf is newer than everything it shows and its log names no undefined reference; every citation is in the bibliography with a doi, url, eprint or isbn; every figure exists, has its source record, is newer than its source and data, and has no Type 3 font or matplotlib raster | a Paper or Review task |

The Engineer runs the checks for its step before handing the work to the Reviewer, and puts their
output in `.argus/evidence/`. The Reviewer runs them again before it accepts the work or, where its
sandbox cannot run them, reads that output. A failure is a repair to return, unless the letter
says why it does not apply to this work.

## Skills

Open one when its situation comes up, not all in advance.

| When | Open |
|---|---|
| A route's novelty or closest work is uncertain | `skills/novelty-check.md` |
| A route needs a sharper mechanism | `skills/ideation-patterns.md` |
| The method must be stated, a baseline has public code, or the spec tests are written | `skills/method-card.md` |
| A model is trained, or inference runs at a scale where the framework matters | `skills/training-infrastructure.md` |
| A policy-gradient run (PPO, GRPO, RLVR) is launched, misbehaves or underperforms | `skills/rl-training-health.md` |
| A number comes back wrong, the method trails its baseline, or the question is whether enough was done | `skills/research-grind.md` |
| A mechanism needs an ablation | `skills/ablations.md` |
| Judging what the experiments establish | `skills/experiment-review.md` |
| Checking that the executed code is the claimed method | `skills/claim-to-code-trace.md` |
| The venue is not chosen, or the draft must compile under its author kit | `skills/venue.md` |
| The paper is drafted or revised | `skills/paper-drafting.md`, and `skills/writing-craft.md` for each passage |
| A claim or citation must be checked against its source | `skills/sources-and-citations.md` |
| A data figure is drawn | `skills/data-figures.md` |
| A method, concept or Figure 1 drawing is needed | `skills/method-figures.md` |
| The paper's science is reviewed | `skills/peer-review.md`, and `skills/strongest-objection.md` for the decisive objection |
| Its argument and language are reviewed | `skills/language-review.md` |
