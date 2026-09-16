---
name: "Infrastructure Landscape Survey"
description: "在项目开始时从实时来源调查训练/推理框架现状,不依赖记忆中的名字。 Survey the current training and inference framework landscape from live, cached sources at project time; produce dated evidence cards and a shortlist of 2-3 candidates with exclusion reasons."
---

# Infrastructure Landscape Survey

Use when the selected method trains a model or runs substantial inference and no
project decision record for this task class and hardware is still inside its
horizon. Remembered framework, engine and model names are dated hypotheses: this
skill produces the current answer from live sources and records it so the
Reviewer and later projects can re-verify it. Never let a name from memory skip
the fetch that dates it.

## Sources (fetch everything through the source cache)

Fetch every page with `python -m argus.tools.web_source <URL>`; it stores the
text under `.argus/sources/` with an access date. Cite the returned path next to
every fact you use. Sources, in order:

1. Repository search sorted by recent push with an explicit date window
   (record the query string and the window).
2. For each candidate: latest release/tag date and last-commit date from the
   repository itself, not from a blog or a README badge.
3. Recent papers on the task class via `engineer/semantic-scholar-search.md`,
   and the code each paper releases (the stack a paper actually ran on is
   stronger evidence than the stack it mentions).
4. Official documentation of every shortlisted candidate for the capabilities
   in the evidence card below.
5. The released baseline's own stack: if the comparison baseline ships code,
   its framework is a candidate by default.

## Successor discovery

For every framework name you remember for this task class, fetch its release and
last-commit dates first. Then search READMEs, issue trackers and recent papers
for projects that benchmark against it, fork it, or state that they migrated
away from it; each such project becomes a candidate card. Repeat once for the
newly found names. A remembered name with no fetch is not a candidate.

## Activity windows

Record activity in three windows: 90, 180 and 365 days before the survey date
(commits, releases, closed issues). A candidate with no commit in 180 days needs
an explicit justification to stay on the shortlist.

## Evidence card (one per candidate, rejected ones included)

- official URL and docs URL, each with its access date and cached path
- latest release date; last-commit date; 90-day activity count
- supported algorithms for this task class
- native multi-turn/agentic rollout support versus a custom loop you would write
- integrated inference engines and whether they colocate with the trainer
- target architecture, PEFT and quantization support for the frozen model
- observation-token loss masking (tool outputs excluded from the policy loss)
- turn-level versus token-level advantage assignment
- CUDA/torch pins compared with the local driver and the allocated hardware
- license
- nearest official example to this task (path in the repository at a SHA)

## Shortlist and record

Shortlist 2-3 candidates. Every rejected card carries one exclusion reason that
points at a card field (for example a pin that the local driver cannot satisfy,
or no loss masking for tool observations). Write the survey into the project
decision record described by the round prompt (`engineer/<task-class>-infrastructure-decision.md`),
with the survey date, hardware, every source path, every card, and the queries
you ran. Then continue with `engineer/framework-stand-up-pilot.md`.

"No material update found" is a valid outcome only when the record lists the
queries, windows and dates that were checked. An offline survey (fetches
failed) is recorded as unverified with the failed URLs; work continues and the
Reviewer is told.
