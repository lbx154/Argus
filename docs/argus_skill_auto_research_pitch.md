# Argus-Skill Auto-Research Pitch

## Slide 1: Argus-Skill Auto-Research
- Goal: turn a long-horizon coding agent into a research pipeline that can discover, test, write, and revise EMNLP-style papers.
- Current status: not "one click accepted paper"; it is a supervised autonomous research factory that now has explicit gates for evidence, claims, figures, citations, layout, and submission readiness.

## Slide 2: Why This Matters
- Most research agents reach the first 70 percent: draft an idea, run a toy experiment, produce a plausible PDF.
- The hard 30 percent is what reviewers actually reject: weak grounding, thin experiments, unsupported claims, bad citations, fake-looking figures, page-budget failure, stale artifacts, and no reproducible trail.
- Argus-Skill targets that hard 30 percent with persistent execution plus validator-enforced paper craft.

## Slide 3: What We Built
- 7x24 daemon: continuous planner, engineer, reviewer, critic loop with resumable work.
- 34 built-in research/paper skills: domain routing, ideation, literature grounding, benchmark execution, ablations, result-to-claim, paper drafting, image-2 figures, format preflight, review revision, and submission assurance.
- Domain expansion: EMNLP/ACL remains the flagship path, with new CV, multimodal, AI-infrastructure, and training-methodology playbooks for follow-on research programs.
- Official project entrypoint: initializes AGENTS.md, built-in skills, and reusable code helpers such as LLM/image wrappers.
- CI-backed implementation: pytest, ruff, and mypy currently pass after the latest merge.

## Slide 4: Evidence Pipeline
- Research brief -> literature/source discovery -> idea provenance -> code reuse plan.
- Benchmark/task design -> full-scale runs -> ablations/failure analysis -> canonical result tables.
- Claim graph -> result-to-claim audit -> figure/table generation -> LaTeX paper.
- Format preflight -> academic-language review -> visual layout review -> submission assurance -> final L2 reviewer full-pipeline checklist certification.

## Slide 5: Concrete Capabilities Today
- Full-scale evidence gate: blocks pilot PDFs and requires completed scored rows for all required methods/baselines.
- Claim/evidence gate: every headline numerical claim must trace to raw artifacts.
- Image-2 figure contract: requires real generated raster, prompt/output hashes, inspection, review, and provenance.
- Citation/reference gate: catches citation dumping, placeholder authors, broken refs, weak bibliography depth.
- Layout/page gate: checks ACL/EMNLP page flow, visual anchors, overfull boxes, references/appendix order.

## Slide 6: Lessons From v7-v20
- The agent was not "lazy"; the system rewarded local validator repair over full-paper completion.
- Root cause 1: References on page 8 were treated as acceptable, so the agent stopped at roughly seven body pages.
- Root cause 2: bounded paper tasks said "not a broad rewrite", which pushed the engineer toward tiny float/prose edits.
- Root cause 3: method clarity was underspecified and accidentally encouraged writing internal Argus/Codex execution details into the paper.
- Latest fix: references must start after the eight-page body, underlength is routed to evidence/content expansion, and Method must describe the evaluated paper system, not our paper-generation infrastructure.

## Slide 7: Why a Mentor Should Care
- This changes advising leverage: mentor feedback can target thesis quality and evaluation standards, not formatting and artifact bookkeeping.
- It creates a reproducible paper trail: idea provenance, benchmark provenance, raw runs, claims, figures, reviews, and final readiness are inspectable.
- It makes failure useful: a rejected direction leaves reusable skills, validators, benchmark code, and audit artifacts for the next run.
- It is a platform bet: each failed paper teaches the skill library and gates, improving the next autonomous research attempt.

## Slide 8: Near-Term Roadmap
- Produce one flagship auto-research paper that passes the full-pipeline reviewer certification (research -> submission) end to end.
- Add a live dashboard for daemon state, evidence gaps, page budget, and review blockers.
- Expand benchmark sourcing beyond synthetic tasks into public/frontier suites where feasible.
- Build mentor-in-the-loop checkpoints: idea shortlist, experiment design, claim calibration, final paper go/no-go.
- Package repeatable project entrypoints across domains, with training-free vs. training-based methodology selected independently.

## Slide 9: Ask
- Mentor time: 30 minutes to judge whether the current research direction is worth turning into the flagship demo paper.
- Scientific bar: help define what counts as a nontrivial contribution and acceptable evidence for EMNLP/ACL.
- Resources: compute/API budget for long benchmark runs and model-backed review/figure generation.
- Outcome target: a credible submission package plus a reusable autonomous research system, even if the first paper is not accepted.
