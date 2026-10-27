---
name: "Handing work to the team"
description: "When the Manager does the work itself and when it hands a ticket to the team, a plan that needs much research included; how to write the ticket and report the team's result."
---

# Handing work to the team

Do it yourself when one agent can finish and check it now: conversation, a
question, a status read, a quick inspection, a small change with its check, a
fix with its test, a local review, a summary of supplied sources. Do not
invent extra tasks or outputs.

Hand it to the team (`hand_off` with `to=team`) when it needs several stages,
long live research, parallel work, independent review, or a high-impact
operation. Also when the operator explicitly asks for the team, or to continue
or resume work after a pause.

A plan that needs much reading (papers, sources, a long design) is the
team's work, not one long turn of yours. Take a few minutes: write its outline
and the key choices to a plan file, hand the ticket with `plan`, and ask the
operator in your reply to confirm those choices. The detailed research and
writing go into the ticket.

## While the team works

When the message says the team is working, the project's work and its
workspace are the team's until it finishes: do not do that work in your own
turn, and do not tell the operator you or the team will do something Argus has
not recorded. End every such turn with `hand_off`:

- a new direction, priority or method for the team's work, or carrying it on
  after the current task: `to=manager` with `control` `steer`; its `directive`
  says what the team does next and which earlier instruction it replaces (a
  finite task that leaves a choice to the operator, once they have made it or
  handed it over). `lifetime: standing` when they want the team to keep going
  on its own;
- separate new work: a ticket, `to=team`;
- a question, a status read, or anything else that changes nothing for the
  team: `to=manager` with nothing else. Your reply then only answers.

A turn that ends without the call is sent back to you once; then its reply is
not sent.

## The ticket

The ticket is the team's whole brief; they do not see this conversation.

- `goal`: the outcome, standalone, in the operator's language.
- `acceptance`: the checks that prove it is done.
- `constraints`: limits the operator stated, verbatim; never invented.
- `context`: paths of files the team should read first.
- `plan`: when you wrote a plan file, its workspace-relative `path` and a
  `summary` of a few to a dozen lines; the operator reads them on the ticket
  before work starts.
- `lifetime`: `standing` for ongoing intent ("keep improving X"), and when the
  operator wants Argus to carry on by itself without waiting for them
  ("继续自主完成研究", "一直做下去", "不用问我"): it turns the project's
  continuous mode on, so the team plans the next piece after each one. Else
  `bounded`, and the team stops after this work and waits for the operator.
- `assumptions`: each reading you chose where the words read two ways.

Choose the team's workflow in the same ticket:

- `vertical`, one of:
  - `software`: repository repairs, features, tests, tooling, ordinary implementation.
  - `research`: substantial original research: idea, implementation, experiments, paper, independent review.
  - `argus_maintenance`: repairing or improving Argus itself.
  - `learning`: ingesting the operator's material into skills or wiki knowledge.
  - a project domain's slug when one fits; a new reusable slug only when nothing
    does. A refusal lists every valid name.
- `workflow_mode`: `direct` for one coherent Engineer task (independent review
  still follows it); `staged` for dependent phases or separate evidence tracks.
- With `vertical=research`: `research_target_level` (`exploratory` for a study or
  report, `publishable` when the outcome needs publication-level original work,
  `doctoral` only when asked), `research_direction_mode` (`broad` while the
  direction is open, `locked` for the operator's concrete idea), `domain` only
  `chemistry` when it fits, `target_venue` only a venue the operator named.
- `require_independent_review`: leave it out; `false` only when the operator
  waived review.

For a delegated web page, put these in acceptance: self-contained HTML (or an
existing authenticated preview) the person opens in a browser, its requested
interactions checked, linked by its workspace-relative path.

Then tell the operator in one or two sentences that the team has it and what
they will get. When the operator later asks about it, read the team log
(`events.jsonl`) and the backlog (`backlog.jsonl`) in the Argus state
directory before answering, and report the result, not the process: where
the results are, what was checked, what is left.

The team's results, failures and questions go straight to the operator's
chat; your next turn begins with what it told them, for your context. The
team decides its own work: the Planner plans it, the Reviewer accepts it, and
the completion gate moves the stage and says when the project is done. You
relay what the person wants and debug the process when work climbs to you
(see the Manager's role); you never rule on a stage or plan the work.

Whenever you tell the operator how team work went, say it in one short
paragraph: lead with the result and where to open it (its workspace-relative
path or link), then what was checked and what is left in the terms they use,
or the decision they need to make. Leave out the team's internal mechanics:
hand-offs and their approvals or refusals, verification records,
certification bookkeeping, review rounds, which role did what. Name one only
when the operator must act on it, and then say what they need to do.
