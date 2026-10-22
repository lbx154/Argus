# Argus roadmap: from 0.1.8 to 1.0

Chinese version: [ROADMAP.zh-CN.md](ROADMAP.zh-CN.md)

## In one sentence

**Give Argus a real research or engineering goal, and it finishes the job on its own. When it says "done", the work
is actually done. It can tell you what the work cost in money and time, you can watch the process at any moment, and it
gets better the more you use it.** When that is true, we have 1.0.

This roadmap lists all the work it takes to get there from today (0.1.8). Every item states the problem today, what to
do, and what done looks like, so you can claim it directly. When everything here is done, all eight 1.0 standards below
are met.

This document describes plans and status on the `dev` branch only. It does not mean any version has been released or
deployed. Milestones are listed in order, with no promised dates. Most tasks within the same milestone can run in
parallel.

## The 1.0 standards

1.0 is not a date. It is these eight things being true at the same time. Numbers marked "initial" wait until the
matching metrics tool has measured today's baseline (mostly done in 0.2), and are then calibrated in this document.

| # | Standard | How we measure it | 1.0 target | Main tasks |
| --- | --- | --- | --- | --- |
| 1 | **Dependable**: it finishes with nobody watching; after a crash, restart, or network loss, nothing is lost or done twice; when something goes wrong, you can see it | Unattended completion rate of long tasks in the standard task set; fault-injection tests; places on the critical path that swallow errors | Completion rate initial ≥ 90%; all fault-injection tests pass; zero swallowed errors on the critical path | CO, HO, CI |
| 2 | **"Done" means done** | On an evaluation set with injected known defects, how many the Reviewer lets through and how many it wrongly rejects; whether every "done" can be traced | Wrongly passed and wrongly rejected initial ≤ 10% each; every "done" traces back to the reviewed change, who judged it, and the evidence | HO, SC |
| 3 | **No waste** | The same task done with the same coding Agent used directly | Simple tasks take ≤ 1.2 times as long as using the Agent directly; success rate no lower than direct use; the cost of every task is visible on the web page | CE, EV, HO |
| 4 | **Gets better with use** | The same batch of unseen tasks, run once with accumulated experience and once with experience cleared | In at least one domain, success rate or cost improves with experience, and the improvement holds up statistically | LE, EV |
| 5 | **Anyone can get started** | A brand-new machine, from install to the first result | Done within 10 minutes on Linux, macOS, and Windows, without reading the docs; the Mac installer is signed; Windows passes the core tests natively | UX, CI |
| 6 | **Safe for many users** | Isolation tests and a security review | Accounts and projects are isolated from each other, with tests; one user's Agent cannot read anyone else's files; the security review finds no high-severity issues | SE |
| 7 | **Stable interfaces, upgrades don't break** | The domain plugin interface and upgrading old data | The domain SDK has a public, versioned v1; community domains pass contract tests against the new version; 0.x project data upgrades directly | EC, CO, RL |
| 8 | **Every change can be shown not to make things worse** | Continuous integration and public evaluation | Core tests run on all three platforms on every commit; zero new type-check issues, and zero in the core packages; every release updates the public comparison table | CI, EV |

## Where we are now

To be honest: Argus can already do a lot, but it is still a good distance from the standards above.

- **Size**: about 250,000 lines of Python and 77,000 lines of TypeScript, 857 test files, about 12,700 tests all passing
  on Linux; 2,569 commits, about 20 active contributors; 7 built-in domains, plus 24 in the community repository.
- **Handoffs and review are where things break most often**: across the full development history, including work before this repository, 774 commit messages mention the Reviewer. Of the 59
  "continue" verdicts on the two most recent instances, 30 were a second review when nothing had changed, and 17 were the
  Reviewer unable to read files or missing a tool, so it could only send the work back
  (see [the handoff design](role-handoff-design.md)). On the bright side: on the trial instance, 22 of 25 tasks passed,
  13 of them in one round.
- **Result credibility rests on the Reviewer reading a narrative**: we have seen a perplexity computed on a single
  2048-token segment written up as the whole dataset's number, a tie written up as "matches or exceeds", and an 11-page
  paper written in 13 minutes that passed all 6 reviews. Nobody has ever measured how often the Reviewer lets errors through.
- **Slower and more expensive than using an Agent directly**: on a task that fixes 6 tests, Copilot used directly takes
  28 seconds, Argus 53 seconds, and 100 seconds with an independent Reviewer. On the trial instance, 409 model calls cost
  $88.85, and the Planner made twice as many calls as the Engineer.
- **"Self-evolution" has no evidence yet**: the learning process runs, but two real measurements showed no improvement in
  task quality.
- **Errors get swallowed easily**: the code has 3,268 `try:` and 842 `except Exception`; type checking has 1,433 existing
  issues (counted 2026-09-12); each commit runs backend tests on Linux only.
- **Getting started is hard**: you first need a coding Agent command-line tool installed and logged in; the Mac installer
  is not signed; on the web version everyone shares one key and there are no accounts; during the trial, an Engineer
  once read another tenant's files.
- **Community domains depend on framework internals**: about 15 private modules are imported directly by the community
  repository, so any framework refactor can break them.

## Version milestones

| Version | Theme | Done means | Tasks included |
| --- | --- | --- | --- |
| **0.2** | Stop making mistakes first | We have a baseline to measure everything; the most wasteful reviews and the most dangerous hole are closed first; CI no longer lets type and frontend problems through | HO-1–6, HO-15, SE-1–2, CI-1–2, CO-1, CO-3, CE-1–2, EV-4, LE-5, RL-4, SC-14 |
| **0.3** | Results you can trust | The Reviewer looks at the real change and judges against acceptance criteria; who said what is on record; research numbers are checked by the host; we can measure how many errors the Reviewer lets through | HO-7–11, SC-1–5, SC-7–9, SC-11, EV-3 |
| **0.4** | Fast, cheap, and provably so | A public comparison table against using an Agent directly; cost is visible and budgets can be set; review happens only when work is handed in | HO-12–13, HO-16–17, CE-3–8, EV-1–2, EV-5–6, CI-8 |
| **0.5** | Anyone can get started | A new machine on any of the three platforms gets a result in 10 minutes; Windows works natively; interrupted uploads resume | UX-1–4, UX-6, CI-4–7, CI-10 |
| **0.6** | Safe for many users | Accounts, project isolation, a fixed address, data that can be exported and deleted; nothing is lost when you switch devices; passes the security review | SE-3–6, SE-8, UX-5, UX-7–9, UX-11, UX-13 |
| **0.7** | An ecosystem you can rely on | The code structure makes sense; the domain SDK has a public version; old data upgrades | CO-4–13, EC-1–2, EC-4–6, EC-10–11, RA-10–11, RL-1–2, HO-14 |
| **0.8** | Gets better with use | We can say which piece of experience helped which task; the first public "gets better with use" curve | LE-1–4, LE-6–8, RA-1–2, RA-5–8, SC-6, SC-10, SC-12–13, UX-10 |
| **0.9** | Release candidate | The long stability run passes; swallowed errors and type debt are down to target; the release process has been rehearsed | CO-2, CO-14–19, CI-3, CI-9, EC-12–13, RL-3, RL-5–7, EV-2 running continuously |
| **1.0** | 1.0 | All eight standards are met | — |

## Themes and tasks

Each task has: an ID and title; its version, size (S is under a day, M is one to three days, L needs to be split into
several PRs), and dependencies. Tasks suited to a first contribution are marked "good first issue".

---

### Theme HO: Handoffs and review — let the reviewer see the real thing

[Principles: 1, 5, 8](PRINCIPLES.md)

**The problem today**: the one doing the work (Engineer) and the one checking it (Reviewer) cooperate by passing
messages. The checker mostly sees the worker's summary rather than the work itself, and often cannot read the files it
should read. The result is either endless send-backs or mistakes that slip through. The system itself sometimes rewrites
the checker's verdict, or records something in its name, so afterwards nobody can tell who said what.

**What we want**: like code review reading a diff, the Reviewer looks directly at "what actually changed this time", and
at the whole task's changes. Every record says who said it, and nobody edits anyone else's words. No change, no review.
Problems the Reviewer cannot judge go to whoever can solve them. The system only enforces budgets and hard rules; it does
not guess on the model's behalf whether there is progress. The full design is in
[role-handoff-design.md](role-handoff-design.md).

#### HO-1 Handoff metrics report
`0.2` · `S` · Depends on: none · good first issue
- Today: no tool can tell us "how many times each passed task was reviewed, and how many of those reviews were wasted".
  Every later improvement needs this number.
- Do: in `argus/tools/`, write a read-only report that reads a project's `events.jsonl` and
  round handoff files and outputs, for each passed task: Reviewer calls, how many of them saw an unchanged candidate,
  "continue" reasons by category, time taken, tokens, and model calls.
- Done when: given a fixed event log, a test gets the expected numbers; this document shows the command to run it.
- Split into: HO-1a count calls and time; HO-1b spot "reviewed again with no change"; HO-1c categorize reasons (for the
  report, a rough split is fine); HO-1d command line and instructions.
- Run: `python -m argus.tools.efficiency_report <project-state-dir> [--json]` (read-only; it reads only `events.jsonl`,
  since the round events carry every verdict the handoff files hold). It also lists calls, tokens, cache reads and
  writes, and cost share by role; the calls that brought no new information (a Planner turn that only kept waiting, a
  Manager supervision that was not applied or kept the course, a review while background work was still running) and
  the calls skipped because nothing changed; the context-length spread and how it grows with session turns; and what
  failures and retries cost.
- Status: done (`7a2432b5`).

#### HO-2 Don't review again when nothing changed
`0.2` · `M` · Depends on: none
- Today: a background job is still running and nothing has changed, yet the Reviewer is called in to review, and says
  "wait a bit more". This was 30 of the 59 "continue" verdicts.
- Do: before calling the Reviewer (`_invoke_reviewer_with_retry` in `engineer/round_reviewer.py`), check whether
  anything changed since the last review: workspace files, newly arrived external results, a new instruction from the
  operator. If nothing did, record a system event and keep waiting or keep working, without calling the Reviewer.
- Done when: in tests, 0 Reviewer calls while the background job is unfinished, and exactly 1 once the result arrives;
  replaying old logs through HO-1 shows the share of wasted reviews going down.
- Split into: HO-2a define "change"; HO-2b check before the call and record the event; HO-2c the web page shows "waiting
  for results, not reviewing again"; HO-2d tests.
- Status: dropped. The Engineer now waits for its own jobs inside its turn and hands over only after they finish, so a
  review in the middle of a running job no longer happens; the change-facts ledger that skipped the Reviewer is gone.
  Old `holders.json` files under mission lineages and old skip events are ignored.

#### HO-3 Hand problems the Reviewer can't judge to whoever can solve them
`0.2` · `M` · Depends on: none
- Today: when the Reviewer cannot read files or has no command line, all it can do is send the work back to an Engineer
  who is just as stuck, and the two spin in circles (17 of the 59).
- Do: let the Reviewer report an obstacle to the system (missing permission, missing tool) or to the owner of the
  contract (requirements that contradict each other), handing it on with `→ manager`; in that case, do not
  rerun the Engineer. The Reviewer now has the Engineer's tools, so it runs code itself wherever review needs it.
- Done when: in tests, after the Reviewer reports it cannot read files, the task goes to escalation with no further
  Engineer round.
- Split into: HO-3a the report action; HO-3b cleaning up the prompts.

#### HO-4 The Reviewer wrote a paragraph but didn't press "submit"
`0.2` · `S` · Depends on: none · good first issue
- Today: when the Reviewer gives its verdict in prose without calling a review action, this counts as a backend
  failure, and two in a row end the task with an error.
- Do: on retry, tell it plainly "you did not submit a verdict"; delete the prose-parsing rescue code in
  `reviewer/_parsing.py` that nothing calls anymore.
- Done when: in tests, prose only the first time and a normal submission the second time lets the task continue instead
  of failing.
- Status: the prose-parsing code is deleted with HO-16e (`reviewer/_parsing.py` is gone); the retry message is not done.

#### HO-5 A Manager "hold on" must not throw away a result that already passed
`0.2` · `M` · Depends on: none
- Today: the Reviewer has already passed the work, then a Manager wait instruction turns the task into a pause, and on
  resume the Engineer starts over from round 1.
- Do: in `engineer/round_settlement.py`, settle the passing verdict first, or resume from it.
- Done when: in tests, "pass, then wait right away, then resume" does not redo work that already passed.

#### HO-6 One answer to "can we ask the operator", and the Manager decides when we can't
`0.2` · `M` · Depends on: none · in progress
- Today: the Reviewer's tool description reads an environment variable, while settlement reads the project policy; when
  they disagree, the Reviewer is steered into asking a question that settlement will reject. After the operator says
  "don't ask me", decisions about credentials, spending, irreversible actions, releases, and broken model configuration
  still wait for the operator or end as "needs operator".
- Do: have every place read the same effective policy. Per principle 8, when the operator cannot be asked (they said
  "don't ask me", or the run has no operator), the Manager holds the operator's whole authority: it answers in a typed
  field, the answer is applied through the same path as an operator's, and every decision it takes in the operator's
  place is recorded and listed in the delivery report. Credentials are the exception: the Manager cannot supply ones it
  does not hold, so it decides whether to route around them or end the item.
- Done when: when the policy is "no questions", the Reviewer's tool description no longer says "ask the operator"; no
  code path waits for the operator or ends as "needs operator"; the Manager can answer only the cards handed to it;
  the delivery report lists each decision it took.
- Status: the single policy and the Manager taking over the operator's authority are both in `dev`. The four ways to
  say it (`AUTONOMY_MODE`, `--no-operator`, the front door's question policy, `--operator-wait-exit` with its 30-minute
  timer) are now one switch, `ARGUS_SKILL_OPERATOR_AVAILABLE`; the text-matching boundary regex is gone.

#### HO-7 Show the Reviewer a real diff
`0.3` · `L` · Depends on: none
- Today: the Reviewer gets at most 20 "recently changed file names", with no content; run results under `.argus` are
  invisible; the clock restarts after every review, so a "pass" covers only the last round's changes. The existing private
  snapshot feature is off by default, cannot be triggered, and nobody reads it.
- Do:
  - HO-7a Snapshot store: put a private repository in the project state directory and snapshot the working directory;
    skip generated files, virtual environments, and nested repositories; for large files record only path, size, and
    modification time; never touch the user's own repository. Measured on the largest live project (220 GB): 3.7 seconds
    the first time, 0.03 seconds each time after.
  - HO-7b Review input: take a "baseline" when the task starts and a "candidate" at each review request; show the
    Reviewer the changes from baseline to candidate and the command record first, then the Engineer's account; replace
    `_changed_artifact_block` in `roles/prompts/reviewer.py`.
  - HO-7c Binding: the baseline moves forward only on a pass; the verdict records which candidate it reviewed, and stage
    certificates and replays are valid only for that candidate.
  - HO-7d Delete the old `skills/round_checkpoint.py`, the switch `ARGUS_SKILL_ROUND_CHECKPOINT`, and the window based on
    modification time.
- Done when: non-git directories, git directories, nested `.git`, large files, and excluded directories all have tests;
  a "pass" covers the whole task's changes.
- Status: HO-7a and HO-7b are done (`247b432c`), and the window based on modification time went with them; HO-7d is
  done (the round checkpoint and its switch are deleted); HO-7c remains.

#### HO-8 Record what someone said under their own name
`0.3` · `L` · Depends on: none
- Today: 4 places in system code change the Reviewer's verdict directly; the verdict's source defaults to "reviewer", so
  when it is unclear, it counts as something the Reviewer said; 5 places write the Engineer's next instruction.
- Do:
  - HO-8a Add a "system intervention" record that references the verdict it affects; settlement computes the final
    result from "verdict + intervention" and records both inputs. This step does not change behavior.
  - HO-8b–8d Turn the three rewrites — venue acceptance, waiting on a background job, and the operator question
    policy — into intervention records, one at a time.
  - HO-8e Remove the "defaults to reviewer" pattern, and fail clearly when the author is unknown; merge the overlapping
    source fields.
  - HO-8f The next instruction has a single writer.
- Done when: an architecture test forbids system code from changing fields of a Reviewer verdict; sealed records keep the
  Reviewer's own words and system interventions stored separately.

#### HO-9 Judge item by item against the acceptance criteria
`0.3` · `M` · Depends on: none
- Today: acceptance criteria are scattered across the task objective, the original request, the goal contract, and
  stage guidance; the Reviewer's verdict is free text with no link to the criteria.
- Do: for work that will produce a claim, the Planner must write acceptance criteria; both pass and send-back mark the
  status of every criterion; new problems outside the criteria are recorded as "follow-ups" for the Planner and do not
  block (except anything that touches the integrity floor); the Reviewer cites the task contract directly instead of
  reading a stitched-together objective; merge back `fix/completion-objective-status` (whether the objective was
  reached), which never made it into `dev`.
- Done when: the tool rejects a "pass" that skips any criterion; follow-ups neither block nor get lost.

#### HO-10 Say the same thing about checkpoints everywhere
`0.3` · `S` · Depends on: none · good first issue
- Today: the Engineer is told to write evidence paths into `CHECKPOINT.md`, the Reviewer is told not to read it, and the
  session-rotation prompt tells the Reviewer to rely on approvals recorded there.
- Do: evidence belongs in the workspace (and in the diff after HO-7); make the prompts consistent; delete the unused
  Reviewer branch in `shared_checkpoint_instructions`.

#### HO-11 Don't show the Engineer's decision wrong
`0.3` · `S` · Depends on: none · good first issue
- Today: the display is fixed (fix/liveness-adopt): "stuck" no longer shows as "continue", and the default hand-off to
  the Reviewer shows as such. What remains is a misleading name: by contract the first valid decision wins and later
  prose is not parsed (5d912d70), yet the function is called `latest_role_decision`.
- Do: rename it to say first valid decision; behaviour unchanged.

#### HO-12 Budgets instead of "guessing whether there is progress"
`0.4` · `M` · Depends on: HO-1, HO-2, HO-7, CE-6
- Today: the system makes judgments for the model with rules like "a non-empty message counts as progress", "two
  passages sharing 60% of their words means stuck on the same problem", and "after round 12, stop if progress wasn't
  claimed twice". The code that maintains these rules and the related rewrites is about 2,000 to 2,500 lines.
- Do: turn rounds, time, and spending into a task budget set by the Manager or the operator; when it runs out, record it
  and let the Manager decide what's next, never killing live work; delete the rules above (including "hand back to itself at most 2
  times in a row", added in the 2026-10-16 fixes); stop explaining system mechanics in the Reviewer prompt. No timer
  re-asks a waiting Planner any more: unchanged inputs keep it quiet until they change, and the operator-wait and
  spare-slot paths also ask only on new facts.
- Done when: an architecture test lists which system guards are allowed; HO-1 shows success rate and cost did not get
  worse.
- Status: the first step is done. The guards above no longer stop a task themselves: while the facts are unchanged
  they wait, and on new facts they hand the task to the Manager once; the Manager may choose to stop, which parks the
  task and keeps its workspace. The hard timeouts that killed processes are gone: a turn is stuck only when there is
  neither model output nor CPU use in its process tree, and then it is reported to the Manager once. Background jobs
  follow the same rule: `subagent submit` takes no `--timeout`, and a job with neither output nor CPU use for 30 min is
  reported once, never killed. The budgets and deleting the guards remain.

#### HO-13 Review only when work is handed in
`0.4` · `M` · Depends on: HO-2, HO-7, HO-12
- Today: the Engineer is reviewed after every round.
- Do: the Engineer works through reversible steps in a row, and requests review only when it declares "the candidate is
  ready" or reaches a checkpoint budget.
- Done when: the number of reviews per passed task goes down, and the share of wrong "done" verdicts does not go up.

#### HO-14 Every summary file can be rebuilt from the records
`0.7` · `M` · Depends on: HO-8
- Today: `latest.json`, the task brief, and the Backlog result fields are each written directly, and can
  disagree with the facts.
- Do: derive all of them from the records, continuing the `MISSION.md` plan in
  [architecture-simplification-plan.md](architecture-simplification-plan.md).
- Done when: in tests, deleting these files and rebuilding them gives identical content.


#### HO-15 Every item ends: close the places where work stops forever
`0.2` · `M` · Depends on: none · in progress
- Today: in several places work stops forever because nobody picks it up: a daemon hot handoff can leave the project
  with no daemon; an open failure circuit has no one to close it; the descendants of a parked item wait forever; one
  operator question blocks the whole conversation; a Manager "wait" freezes planning for the whole project; a subagent
  whose process is alive but whose heartbeat stopped still counts as running; answering a question after a pause
  reopens the campaign.
- Do: close each one according to the four invariants in the "baton" section of the
  [handoff design](role-handoff-design.md): one holder per item; waiting happens in the holder's own turn and the
  backend handles it; an unchanged return climbs; authority never vanishes.
- Done when: each case above has a test (`tests/test_liveness_*.py`) that reaches done, dropped, or an explicit climb.
- Status: all in `dev`. The first upgrade of a daemon from an
  old release should still stop the service before starting the new one. The Planner waiting contract, the Engineer
  wait line and the host's background-job waits are deleted: Argus keeps no waiting state.

#### HO-16 The baton: one handoff line instead of footers and many pause states
`0.4` · `L` · Depends on: HO-2, HO-12, HO-15
- Today: the Engineer hands off with seven footer keys, a Planner wait needs a fingerprint, a token, and a recheck
  condition, and there are eleven `paused_*` statuses, each with its own resume logic.
- Do: per the handoff design, a handoff is a prose note plus one line
  `→ engineer|reviewer|planner|manager|operator: <question>|done`; the host enforces only the four
  invariants; the eleven pauses become one park whose reason is data; waiting stays inside the holder's turn; an
  unchanged return climbs instead of calling the same model again.
- Done when: an architecture test confirms only this handoff format and one park status remain; HO-1 shows the number
  of model calls made without a change never exceeds the height of the ladder.
- Can be split into: HO-16a read the line leniently (an unreadable line never means done); HO-16b merge the pause
  states; HO-16c (dropped: waits stay in the holder's turn, no `wait:` line); HO-16d unchanged return climbs; HO-16e
  delete the old footers and states.
- Status: HO-16a, HO-16b, HO-16d and HO-16e are done. The Engineer and the Reviewer are read only
  by their handoff line, and the Reviewer's five actions are one `submit_review`; the seven footer keys and the
  Engineer's decision event are no longer read. An unchanged return, judged from workspace snapshots and outside
  inputs and counted per lineage, climbs one rung at a time; a review the Engineer has not yet seen counts as new
  input, and a candidate identical to one already passed, with no new input, keeps that pass without a model call
  unless the host refused it (the task it ended failed or was sent back), in which case it climbs past the Reviewer.
  Nothing writes `operator_need` or `authority_impact` any more, so both, the plan challenge's `ask_operator` route,
  the Reviewer statuses no verdict produces (`research_incomplete`, `paused_no_breakthrough`,
  `exhausted_current_methods`) and the Reviewer's prose verdict parser (`reviewer/_parsing.py`, the `STATUS=` footer)
  are deleted, and a planner report carries only what `submit_review` writes (`plan_signal`, `learning`,
  `learning_reason`); old rows and events that carry the removed fields still read.
  Every writer writes `parked` with the reason in the task's `outcome.park.reason` and the
  completion event's `park_reason`; `paused_external_work`, `research_incomplete` and `exhausted_current_methods` have
  no writer left and are only read. Resuming is one path driven by new facts: the daemon's next pass ends its own
  parks (shutdown, cooldown), the operator's explicit Run ends the parks only the operator or the outside world can
  clear, an answer ends a question, and the Planner retires the parks it owns. One task's park no longer holds other
  tasks or planning: the three project-wide holds (provider fence, spent retry window, execution host that could not
  start) are gone. A model service that outlasts the automatic retries opens the runtime-failure circuit instead, which
  holds every role's model calls until the operator's Run or until a call a half-open window lets through (opened by a
  backend or model switch, operator input, or the timed fallback) is answered; then the fenced tasks go back to the
  queue on their own. An explicit Run wakes a sleeping daemon at once. Older `paused_*` rows and events still read, shown by their
  reason. An architecture test confirms no writer writes an old status.

#### HO-17 The Observer: watching in the person's place
`0.4` · `M` · Depends on: HO-16
- Today: nobody notices when a role goes round in circles, a wait drags on with nobody moving, the work drifts off
  the task, or money goes out faster than anything changes, unless someone happens to read the project logs. On the
  live trial that someone was Claude Code, which found the Planner idling every five minutes.
- Do: an Observer role (`observer` in code, shown as 监督者) on the same backends as the others, with the Engineer's
  tools and low reasoning effort. It watches only whether the work flows; whether the work is right stays
  the Reviewer's to judge. Each time the Engineer, Reviewer or Planner ends a turn (each model call of theirs ends
  one; a delivery ends a turn, so it is covered) it looks once at how that task has flowed so
  far: who held the baton and whom it passed to, how long each turn took and what it cost, a role called again and
  again with nothing new, idling, and drift from the task. Its own turns end none. Its prompt gives only where
  `events.jsonl` is, the task id of each turn that just ended, the event types that carry the flow, and its earlier
  remarks; it takes the task's events itself with rg and reads nothing else. Within a task it resumes one session,
  through each backend's own session resume, and each later look gives only the turns since and when the last look
  began, so it reads only the new events and the rest stays in the cache; the session ends with the task, and
  planning, which belongs to no task, is looked at afresh. When the flow is fine it says nothing
  and writes no event. Otherwise it writes `→ manager: <one sentence and where it saw it>`, in the operator's
  language, as one `life.observer.note` event naming its task: the Manager hears it through its supervision and decides what to
  do; with no Manager, or once the task is delivered, the operator gets it as an alert. It never makes a remark
  twice. A delivery does not wait for it, but a bounded daemon waits for its look at the last turns before it exits.
  Its model is `ARGUS_SKILL_OBSERVER_MODEL`, by default the Reviewer's.
- Done when: tests show it looks once at each ended turn and never at its own, resumes the task's session at its
  second look with a prompt carrying only the new event range, starts afresh once the task ends, reads only the
  events, stays silent when the flow is fine, gives a remark to the Manager's supervision or else alerts the
  operator, never repeats one, and that a failed look leaves the work running.
- Known gaps: the Manager's turns are not watched yet.
- Status: implemented on `feat/observer`; tried on small tasks on the real Copilot backend and on a copied stretch
  of a live project where the Planner kept waiting. Not yet merged.
---

### Theme CO: Reliability and the core — failures you can see, a structure you can follow

[Principles: 1, 4](PRINCIPLES.md)

**The problem today**: lots of `try/except` in the code turn errors into default values, so the system breaks without
knowing it; directory names don't match what's inside (the state machine lives in `skills/`, the task runtime in
`apps/`); the layering rules are already written as tests, but not a single move has started.

**What we want**: every failure is visible; each kind of state has one writer and one way to write atomically; eight
layers with dependencies pointing only downward, so a newcomer can find things from the directory names.

#### CO-1 List where errors get swallowed
`0.2` · `S` · Depends on: none · good first issue
- Do: per package, count `except Exception` and places that "return an empty or default value on error"; mark the ones
  on the task's main path; submit as a document or a list of issues.
- Done when: one list per package, with main-path locations marked separately.

#### CO-2 Clean up swallowed errors package by package
`0.9` · `L` · Depends on: CO-1 · good first issue (split by file)
- Do: follow the rules in [simplification-plan.md](simplification-plan.md), one package at a time: raise where it should
  raise, hand retries to the one owner of retries, and write down the reason where ignoring is truly fine. Start with
  places like "silently return an empty checklist" that make a task look successful.
- Done when: zero swallowed errors on the main path, and the repository-wide count clearly lower than at 0.2.

#### CO-3 A guard so swallowed errors only go down
`0.2` · `S` · Depends on: CO-1 · good first issue
- Do: add a test that records each package's current count of broad `except`, and fails when it grows (like the existing
  mypy baseline).

#### CO-4 Merge the 18 copies of atomic write
`0.7` · `M` · Depends on: CO-6
- Do: merge the `_atomic_write` implementations scattered around into one in `core` (LAYOUT phase 9).

#### CO-5 `MemoryBundle.root` has one meaning
`0.7` · `M` · Depends on: CO-7
- Do: stop it pointing at different directories in different situations (LAYOUT phase 8), with regression tests; this
  one carries behavior risk.

#### CO-6 LAYOUT phase 1: move leaf modules down into core
`0.7` · `M` · Depends on: none
- Do: move `backend_names`, `process_control`, `event_log`, `mission_outcome`, and `capability_vault` into `core`, and
  delete the matching lines from the architecture test's allowlist.

#### CO-7 LAYOUT phase 2: checklists into the contract
`0.7` · `S` · Depends on: none · good first issue
- Do: put `ChecklistItem` into the domain contract, create `verticals/inventory.py`, and keep a single definition of
  `DEFAULT_VERTICAL`.

#### CO-8 LAYOUT phase 3: create `pipeline/`
`0.7` · `M` · Depends on: CO-7
- Do: move stage_machine, vertical_select, and checklist_store into `pipeline/`.

#### CO-9 LAYOUT phase 4: put the inbox and actions where they belong
`0.7` · `S` · Depends on: none · good first issue
- Do: move `apps/_inbox.py` and `apps/_life_actions.py` into `life/`.

#### CO-10 LAYOUT phase 5: create `mission_runner/`
`0.7` · `L` · Depends on: CO-8
- Do: move `apps/_runtime*`, `loop.py`, and `skills/loop_*` into `mission_runner/`.

#### CO-11 LAYOUT phase 6: renames
`0.7` · `S` · Depends on: CO-10
- Do: rename `cli` to `terminal` and `maintenance` to `doctor`, keeping compatibility imports for one release cycle.

#### CO-12 LAYOUT phase 7: public loader names
`0.7` · `M` · Depends on: CO-6
- Do: change about 66 cross-package private imports to public names.

#### CO-13 LAYOUT phase 10: put tests next to their packages
`0.7` · `M` · Depends on: CO-10 · good first issue
- Do: move the 165 test files at the root into the directories of the packages they test.

#### CO-14 Finish moving the daemon services (maintenance map M4)
`0.9` · `M` · Depends on: none
- Do: move daemon lifecycle and upgrade logic into explicit service objects; an architecture test forbids reaching back
  into server.

#### CO-15 Shorter control-plane locks (M5)
`0.9` · `M` · Depends on: CO-14
- Do: building on shared-context isolation and compare-and-commit throughout settlement, replace the coarse lock with
  short locks.

#### CO-16 Split out a standalone scheduler component (M6)
`0.9` · `L` · Depends on: CO-10
- Do: in batches — "prepare context / settle results / planning input" — replace the mixins that share `self`, deleting
  the matching old mixin with each batch.

#### CO-17 Settle the storage question
`0.9` · `L` · Depends on: none
- Do: compare plain files, SQLite/WAL, and a mix (crash recovery, concurrency, migration, cross-platform), write it up as
  an architecture decision record, and act on the conclusion.

#### CO-18 Fault-injection suite
`0.9` · `M` · Depends on: none
- Today: recovery logic has individual tests, but no combined suite covering "process killed, machine restarted, network
  lost, model service rate-limited, disk full"; a full disk has happened in a real environment.
- Do: inject these faults in temporary directories and real subprocesses, and check that tasks are not lost, not done
  twice, and either continue or fail clearly.

#### CO-19 Unattended long run
`0.9` · `L` · Depends on: EV-6, CO-18
- Do: run the long tasks from the standard task set unattended and continuously in an isolated environment (initial 72
  hours), counting completion rate, recoveries, and human interventions.
- Done when: the completion-rate target of standard 1 is met.

#### CO-20 Delete the sha1 migration of project state directories
`0.2` · `S` · Depends on: none · good first issue
- Today: project state directories are named by percent-encoding the project path (or remote URL) instead of
  `sha1(path)[:12]`. `migrate_legacy_project_state` in `core/project.py` renames an old directory atomically when one is
  looked up; it is the last use of a hash in the repository. Before renaming it records `{old: new}` in
  `projects/.legacy-names.json`, which `resolve_legacy_project_state` uses to turn a saved old path into the new one.
- Do: once a release carrying the migration has been deployed for one release cycle, delete both functions,
  `_record_legacy_name`, `LEGACY_NAMES`, their calls in `core/paths.py`, `daemon/config.py` and the CLI `--life-dir`,
  and their tests.
- Done when: `hashlib` no longer appears under `argus/`.

---

### Theme CI: Continuous integration and cross-platform — it runs on your computer too

[Principles: 1, 9](PRINCIPLES.md)

**The problem today**: each commit runs only ruff, Linux backend tests, and the TypeScript check. Type checking, web and
terminal UI tests, macOS, and Windows all have to be triggered by hand. In the runtime, about 10 files use `fcntl`, which
exists only on Unix-like systems, and 3 places use `os.fork`.

**What we want**: core tests on all three platforms on every commit; type issues only go down; a new user's computer is
as clean as CI.

#### CI-1 Type-check gate on every commit
`0.2` · `S` · Depends on: none · good first issue
- Do: put the existing `typecheck_gate` into `tests.yml`, compare against the baseline per file, and fail on new issues.

#### CI-2 Web and terminal UI tests on every commit
`0.2` · `S` · Depends on: none · good first issue
- Do: add the Web and TUI type checks and tests to the CI that runs on every commit.

#### CI-3 Clear the existing type-check issues
`0.9` · `S×N` · Depends on: CI-1 · good first issue (one file at a time)
- Do: starting from the 1,433 existing diagnostics, fix them file by file.
- Done when: zero in the core packages, and no new ones anywhere in the repository.

#### CI-4 Replace `fcntl`
`0.5` · `M` · Depends on: none
- Do: replace `fcntl` in about 10 files with a cross-platform file lock.

#### CI-5 Replace `os.fork`
`0.5` · `M` · Depends on: none
- Do: change the 3 `fork` calls to a cross-platform way of starting subprocesses, keeping the current process-ownership
  semantics (see [windows-process-ownership.md](windows-process-ownership.md)).

#### CI-6 Grow the portable test set
`0.5` · `S` · Depends on: none · good first issue (ongoing)
- Do: gradually add tests that run on all three platforms to `PORTABLE_TESTS`.

#### CI-7 Core tests on all three platforms on every commit
`0.5` · `M` · Depends on: CI-4–6
- Done when: macOS and Windows, like Linux, run the core suite on every commit.

#### CI-8 PR regression gate in CI
`0.4` · `M` · Depends on: none
- Today: the gate that checks for regressions using past PRs is a prototype. It found evidence-backed regressions in 15 of
  50 past PRs, but it is not in CI.
- Do: first run it in CI without blocking (needs Docker isolation), collect false-positive data, then decide whether to
  make it blocking.

#### CI-9 PR gate on all platforms
`0.9` · `M` · Depends on: CI-8
- Do: make the gate run on macOS and Windows too.

#### CI-10 Cold-start test
`0.5` · `M` · Depends on: none
- Do: in a clean container, start from a pip install or the desktop installer and run all the way to the first finished
  task.
- Done when: it runs automatically before every release, and a failure blocks the release.

---

### Theme SC: Credible research results — numbers that hold up

[Principles: 5](PRINCIPLES.md)

**The problem today**: the integrity floor and the three levels "explore / develop / certify" are already in the code,
but whether a conclusion holds up still mostly depends on the Reviewer reading a narrative, and review is on the lenient
side: a small slice written up as a whole-dataset metric, a tie written up as a win, and a Reviewer that cannot reproduce
the numbers itself. Nobody has ever measured how often the Reviewer lets errors through.

**What we want**: every "certify"-level conclusion comes with a package that reruns it in one command; the host checks
sample counts, truncation rates, and wording automatically; how much the Reviewer lets through and how much it wrongly
rejects is measured, and public.

#### SC-1 Check the experiment's scale
`0.3` · `M` · Depends on: none
- Do: the host reads sample count, sequence length, and number of random seeds from the result files, compares them with
  the protocol in the method card (METHOD.md), and writes the result into the review material
  (`verticals/research/method_card.py`, `round_log.py`).
- Done when: a perplexity computed on a single segment is flagged as "protocol insufficient".

#### SC-2 Check for truncated generations
`0.3` · `M` · Depends on: none
- Do: LLM evaluations must report the share of generations that were truncated; the host reads it and writes it into the
  evidence.
- Done when: replaying the case where "12-token truncation left MATH-500 at only 6%" raises an alert.

#### SC-3 Make the wording match the results
`0.3` · `M` · Depends on: none
- Do: compare the wording of conclusions in papers and reports, item by item, against the "claim attainment table"; a tie
  cannot be written up as "matches or exceeds".

#### SC-4 Verify "no paper until the premise is reproduced"
`0.3` · `M` · Depends on: none
- Today: the mechanism is already in `dev`, but it has never been verified in a real run.
- Do: replay the scenario that went wrong back then, and confirm the Planner does not move to the paper-writing stage
  while the premise has not been reproduced.

#### SC-5 The Reviewer reruns one key number itself
`0.3` · `L` · Depends on: HO-3
- Do: at the certify stage, have the Reviewer rerun, with its own tools, at least one number that supports the main
  conclusion.

#### SC-6 Certify-level conclusions come with a one-command rerun package
`0.8` · `L` · Depends on: HO-7, SC-5
- Do: pack the code snapshot, a locked environment, data-fetching scripts, commands, and expected outputs into one
  package that anyone can rerun with a single command.
- Done when: at least one publicly shown result can be rerun independently by outsiders.

#### SC-7 Evaluation set with injected defects
`0.3` · `L` · Depends on: none · good first issue (each defect class on its own)
- Do: in projects known to be correct, inject implementation bugs, specification bugs, drifted conclusions, and fabricated
  numbers, one kind at a time, and measure how many the Reviewer catches and how many it wrongly rejects.
- Done when: standard 2's wrongly-passed and wrongly-rejected rates have a baseline.

#### SC-8 Paper regression set
`0.3` · `M` · Depends on: none · good first issue
- Do: build an anonymized example for each of the 9 problem classes listed in the paper quality plan; today only one
  class, "length and citation quotas", is covered.

#### SC-9 Add a "review too fast" counter-metric
`0.3` · `S` · Depends on: none · good first issue
- Do: add "median review time" and "zero continues" as two counter-metrics in the capability report; a review that goes
  too smoothly is itself a warning sign.

#### SC-10 Review figures separately
`0.8` · `M` · Depends on: none
- Do: make figure review its own task, with the Reviewer opening each PNG to check for clipped text and readability.

#### SC-11 When sending work back, name only the 1–3 most important problems
`0.3` · `S` · Depends on: none
- Do: on continue, give only the 1–3 problems with the biggest impact, and label each problem's type (the rest is in
  research TODO P-04).

#### SC-12 Narrative diagnostics from "hint only" to "can block"
`0.8` · `M` · Depends on: SC-8
- Do: first calibrate positively against at least two strong papers from the same venue, measure the false-positive rate,
  then decide whether to turn on blocking.

#### SC-13 Refresh venue standards every year
`0.8` · `S` · Depends on: none
- Do: refresh each venue's scientific standards every year from the official call for papers, reusing the existing
  `venue_research.py`.

#### SC-14 Update the checkboxes in the research TODO
`0.2` · `S` · Depends on: none · good first issue
- Today: every checkbox in `RESEARCH_AGENCY_AND_VERIFICATION_TODO.md` is empty, but P0-01, P0-03, P0-04, and K-01 are
  actually implemented.
- Do: check against the code, update the checkboxes, and note the commit for each completed item.

---

### Theme RA: Research ambition and new domains — stop going in circles

[Principles: 5, 6, 7](PRINCIPLES.md)

**The problem today**: the system leans conservative, and a bold, correct conclusion earns no more than a safe one; the
kernel domain's good practice of "judge novelty first" hasn't spread to other domains; the math domain has run end to end
on only one problem; none of the 24 community domains declares its verification levels.

**What we want**: it finds frontier methods on its own instead of fine-tuning locally; the math direction produces results
that can be verified independently; every domain has a smoke test and declared verification levels.

#### RA-1 Fix "afraid of saying too much, not afraid of saying too little"
`0.8` · `M` · Depends on: SC-7
- Do: design and build a way of reviewing in which the Reviewer weighs "understating, not daring to conclude" as heavily
  as "overclaiming", and validate it with the SC-7 evaluation set.

#### RA-2 Spread "judge novelty first"
`0.8` · `M` · Depends on: none
- Do: take the kernel Reviewer's practice of "first judge whether the method is new, and ask for a change of direction
  when needed" to research and math.

#### RA-3 Combine candidates against a shared baseline
`1.x` · `L` · Depends on: EV-6
- Do: compare several candidate methods against the same baseline and allow combining them, instead of automatically
  picking the first winner.

#### RA-4 Discover first, then build
`1.x` · `L` · Depends on: RA-3
- Do: first get an algorithm decision the Reviewer accepts, then change production code; kernel already has a discover
  stage, so spread it to other domains.

#### RA-5 When replanning, change the question, not just the method
`0.8` · `S` · Depends on: none
- Do: when the route is regenerated, require considering a different question, not only a different method on the same
  question.

#### RA-6 Fill in the tool registry
`0.8` · `M` · Depends on: none · good first issue (one entry at a time)
- Do: the 90 tool entries currently have only a link, executables, and when to use them; add compatibility, install
  steps, and a smoke command, entry by entry.

#### RA-7 Math: give "computational evidence" a producer
`0.8` · `M` · Depends on: none
- Today: the math domain's evidence layers include a "computational evidence" layer, but no code writes it.
- Do: connect the counterexample lab and Jacobian's `math.run` as producers of this layer.

#### RA-8 A small math benchmark
`0.8` · `M` · Depends on: none · good first issue
- Do: 10–20 problems, including some with known counterexamples, to measure progress in the math domain.

#### RA-9 Check that a decomposition really implies the original goal
`1.x` · `L` · Depends on: RA-7
- Do: after a math proof is split into subgoals, check that the subgoals together really imply the original goal.

#### RA-10 Community domains declare verification levels
`0.7` · `S` (per domain) · Depends on: EC-1 · good first issue
- Do: add `VERIFICATION_STAGE_PROFILES` to the 24 domains in the community repository.

#### RA-11 A 15-minute smoke task for every domain
`0.7` · `M` · Depends on: EC-2
- Do: give every domain a small task and a capability report, run in the version matrix.

---

### Theme CE: Efficiency and cost — simple things should be fast and cheap

[Principles: 4](PRINCIPLES.md)

**The problem today**: simple tasks take twice as long as using an Agent directly; the Planner makes twice as many calls
as the Engineer; input and output tokens run about 86:1, with a lot of context injected over and over; client-side cost
control was removed on 2026-10-09 (`28bde6e7`), so costs are now recorded but not controlled.

**What we want**: simple tasks take no more than 1.2 times as long as using an Agent directly; every task's spending is
visible on the web page and can have a budget; you pay for multiple roles only when multiple roles are needed.

#### CE-1 Cost per passed task as a core metric
`0.2` · `S` · Depends on: none · good first issue
- Do: add "cost per passed task" to `core/metrics.py`, and output it in the HO-1 report.
- Status: done (`b5578571`): `cost.per_passed_task_usd` in the metrics snapshot, the Prometheus gauge
  `argus_cost_per_passed_task_usd`, and the same figure in `efficiency_report`.

#### CE-2 Recheck the Planner idling
`0.2` · `S` · Depends on: none · good first issue
- Do: break down Planner calls by reason, and confirm that idling actually went down after `fix/planner-idle-spin` was
  merged.
- Status: done (a check only, no code change). `efficiency_report` on real runs: before the fix, the Planner was 52% of
  spend in argus-trial and 163 of its 175 calls brought no new work; in the six-task run of 10/19, 4 tasks had none and
  only 02-ecc had 26 (it was stuck on a deadlock and still ran the old recheck code); projects on 10/20 had 1–6 each,
  mostly calls made while a slot was free.

#### CE-3 Find out where simple tasks lose time
`0.4` · `M` · Depends on: none
- Do: add timing events segment by segment across classification, startup, and each role, find the parts that make 53
  seconds slower than 28, and cut them one by one.

#### CE-4 Cut the three most re-injected blocks of context
`0.4` · `M` · Depends on: none
- Do: use the existing `prompt_block_stats` to count tokens per block, and cut the three biggest.
- Status: done. Counted per block, the most repeated injection is the tool definitions sent on every turn (38% of the
  Planner's input, 27% of the Reviewer's, 20% of the Engineer's); every prompt section is at most 0.5K tokens except the
  Reviewer's change diff at 2.1K, which is not worth cutting. So the cut is the tools the three reading roles never used
  (sub-agents, workflows, the session store, semantic search): Planner and Observer (`8a74975e`, the Planner's tool
  definitions per turn went from 10K to 3.9K tokens) and Reviewer (`5be8781d`, fixed input per turn went from 21.2K to
  15.7K in a live run, and its review actions still work). The Engineer is unchanged: it is the role that edits code,
  so cutting its tools could cost ability and needs its own A/B.

#### CE-5 Separate "real work" from "management overhead"
`0.4` · `M` · Depends on: none
- Do: add fields to events and report useful execution as a share of total spending.

#### CE-6 Spending visible on the web page, with budgets
`0.4` · `M` · Depends on: CE-1
- Do: show each task's spending; let the Manager or the operator set a task budget; this is the same mechanism as the
  budget in HO-12.

#### CE-7 Can small models do the Reviewer's and Planner's jobs
`0.4` · `M` · Depends on: SC-7, EV-1
- Do: A/B test quality and cost when a small model acts as Reviewer or Planner.
- Status: on hold until SC-7 and EV-1. The cost half can be measured today, the quality half cannot: with no
  injected-defect set (SC-7) there is no way to say how many defects a small-model Reviewer misses, and with no fixed
  task set (EV-1) no way to say how much a small-model Planner lowers the success rate. Token use for the same input
  varies by about ±40% between live runs, so without a fixed task set each side needs 10 or more runs to show a
  difference.

#### CE-8 Check login status without spending money
`0.4` · `S` · Depends on: none · good first issue
- Today: some backends spend a model call just to find out the login has expired.
- Do: switch to a check that doesn't call the model.

---

### Theme EV: Evaluation — prove with numbers that things got better

[Principles: 9](PRINCIPLES.md)

**The problem today**: there is no ongoing end-to-end evaluation. Harbor is hooked up but has no public scores; the
baseline and weekly report for goal-level metrics haven't been done.

**What we want**: every merge into `dev` automatically replays a fixed set of real tasks; a public "Argus vs. using an
Agent directly" comparison table that keeps updating, covering four kinds of tasks: software, kernels, research, and
proofs.

#### EV-1 The first public comparison table
`0.4` · `M–L` · Depends on: EV-6
- Do: use Harbor to compare Argus with an Agent using the same model directly, reporting success rate, quality, time,
  tokens, model calls, tool calls, follow-up questions, and confidence intervals together, and publish the first table.

#### EV-2 Automatic replay on every merge
`0.4` onward, continuous · `L` · Depends on: EV-1
- Do: every merge into `dev` automatically replays the fixed task set and updates the comparison table; attach it to
  every release.

#### EV-3 Anonymized replay set of failures
`0.3` · `M` · Depends on: none
- Do: anonymize failure traces from real runs and turn them into a replayable test set.

#### EV-4 Weekly metrics report
`0.2` · `S` · Depends on: HO-1, CE-1 · good first issue
- Do: automatically summarize handoff, cost, success-rate, and other metrics every week.

#### EV-5 Write down negative results and abandoned designs too
`0.4` · `S` · Depends on: none · good first issue
- Do: create a directory recording designs that were tried and didn't work, and negative results, so nobody falls into
  the same hole twice.

#### EV-6 Define the standard task set
`0.4` · `M` · Depends on: none
- Do: layer tasks by how long a person would take (minutes, hours, days), covering the four kinds of tasks, as the shared
  foundation for EV-1, CO-19, and LE-8.

---

### Theme LE: Self-learning — prove "gets better with use" before talking about self-evolution

[Principles: 9](PRINCIPLES.md)

**The problem today**: the learning process really does run (31 times live, 28 with changes), but two real measurements
showed no improvement in task quality; nobody can say which piece of experience helped which task; default recall uses
local word hashing, not semantic search.

**What we want**: one public curve per domain — the same batch of unseen tasks, run once with experience and once with it
cleared, with success rate and cost improving as experience grows.

#### LE-1 Record which piece of experience was used
`0.8` · `M` · Depends on: none
- Do: building on the existing `recalled_paths` event, record whether skills and knowledge pages were opened and cited,
  and have the task view summarize "experience → task outcome".

#### LE-2 Learning on/off comparison
`0.8` · `M` · Depends on: LE-1, EV-6
- Do: run the same group of related tasks once with learning on and once with it off, and compare success rate, number of
  calls, and tokens.

#### LE-3 Retire useless experience
`0.8` · `M` · Depends on: LE-1
- Do: experience that no longer holds (later facts overturned it, or the problem it describes was fixed) stops being
  recalled. Facts and the agents' judgment decide; nothing expires by age and nothing is scored.
- Status: the knowledge-page part is implemented on `feat/retire-lessons`, not yet merged. Every recalled line carries
  the date the page was written and where it came from, and a page that holds only for one version or environment
  names it in its description; a role that finds current facts contradicting a page gives its path and why in its
  report. The post-mission reflection and the shared-principles pass name such a page on a `RETIRE: <page> — <why>`
  line, and the host moves the whole page into its library's `.history`, drops its INDEX line, and records who retired
  it, when and why in the knowledge journal. Retiring Skills is not done.

#### LE-4 Measure knowledge-page reuse across tasks
`0.8` · `S` · Depends on: none · good first issue
- Do: add the measurement "did later tasks use the knowledge page an earlier task wrote".

#### LE-5 Why nearly half of learning produced nothing
`0.2` · `S` · Depends on: none · good first issue
- Today: on the old instance, 16 of 36 learning runs changed nothing at all.
- Do: analyze why and write it up as a report.
- Status: report done (`e7d31388`, [docs/audits/le5-empty-learning-2026-10-22.md](audits/le5-empty-learning-2026-10-22.md)). The old instance's records of the 36 runs are not in the repository or on the machine, so the breakdown of the 16 cannot be checked; the report lists every path in code that ends with no change, including retiring a page or adding a reviewed fact, which are recorded as unchanged. An unchanged learning receipt now names its cause (`outcome.detail`), so the next instance can be counted directly.

#### LE-6 Try a local semantic search model
`0.8` · `M` · Depends on: LE-1
- Do: evaluate a small offline embedding model as the default recall method, and compare its recall hit rate with word
  hashing.

#### LE-8 The first public "gets better with use" curve
`0.8` · `L` · Depends on: LE-1, LE-2, EV-1
- Done when: at least one domain meets standard 4.

---

### Theme UX: Getting started and the product experience — a first result in 10 minutes

[Principles: 1, 8](PRINCIPLES.md)

**The problem today**: you first need to install and log into a coding Agent command line; the Mac installer is blocked
by the system the first time it opens; the invitation page is in Chinese only; few file formats can be uploaded; drafts,
pins, and archives live only in the current browser; remaining time shows "unknown".

**What we want**: download, open, click through a wizard, and you're started; your things are still there on another
device; results are easy to share and export.

#### UX-1 First-run wizard
`0.5` · `M` · Depends on: none
- Do: turn the diagnostics in `argus_doctor.py` into cards on the web page that you can click to fix, checking in turn
  whether the command-line tool is logged in, the PATH, and the available backends.
- Done when: a new machine can be set up without reading the docs.

#### UX-2 Sign and notarize the Mac installer
`0.5` · `M` · Depends on: a developer certificate
- Do: add signing and notarization to `release.yml`, so the installer is no longer blocked the first time it opens.

#### UX-3 English invitation page and English getting-started guide
`0.5` · `S` · Depends on: none · good first issue

#### UX-4 More starter templates, per domain
`0.5` · `S` · Depends on: none · good first issue
- Today: `StartPrompts.tsx` has only 4 general scenarios.
- Do: at least one starter template for every built-in domain.

#### UX-5 Nothing lost when you switch devices
`0.6` · `M` · Depends on: SE-3
- Do: sync drafts, pins, archives, and reading position to the server (`useSessionComposer`, `Sidebar`).

#### UX-6 Uploads resume after a refresh
`0.5` · `M` · Depends on: none
- Today: the chunked protocol can already query progress; what's missing is recovery in the frontend.

#### UX-7 Support archives and Office files
`0.6` · `M` · Depends on: none
- Do: unpack ZIP safely (guarding against path traversal), and support DOCX, XLSX, and PPTX (`webapi/attachments.py`).

#### UX-8 Import from a link
`0.6` · `M` · Depends on: none
- Do: support importing web pages, arXiv, and GitHub repository links.

#### UX-9 One-click export of results
`0.6` · `M` · Depends on: none
- Do: export reports as PDF or Word, and download the whole project as a package.

#### UX-10 Give remaining time as a range
`0.8` · `M` · Depends on: EV-6
- Do: based on the history of similar tasks, give a range for remaining time instead of "unknown"; when there's no
  basis, say honestly that there is none.

#### UX-11 Read-only share links
`0.6` · `M` · Depends on: SE-3
- Do: reuse the expiry mechanism in `pairing.py` to create read-only share links.

#### UX-12 Comment on and annotate results
`1.x` · `L` · Depends on: SE-3

#### UX-13 More notification channels
`0.6` · `M` (per channel) · Depends on: none · good first issue
- Do: following the existing Telegram and Feishu bridges, add email, Slack, WeCom, and DingTalk.

---

### Theme SE: Multi-user, security, and hosting — safe when many people use it

[Principles: 1, 8](PRINCIPLES.md)

**The problem today**: on self-hosted web, everyone shares one key, with no accounts and no isolation; during the trial
an Engineer once read another tenant's files, because the Engineer's tools on the web trial had no workspace boundary;
the temporary tunnel address changes on restart; users cannot export or delete all their data themselves.

**What we want**: real accounts and project isolation; an Agent can read and write only its own project's workspace; a
fixed address; data that can be exported and deleted; a full security review passed.

#### SE-1 An Agent can only touch its own workspace
`0.2` · `M`, long term `L` · Depends on: none · **top priority**
- Do: first add a workspace boundary at the tool layer for the Engineer and Reviewer, forbidding reads and writes of
  other projects' and other tenants' user data; in the long run, move to sandbox isolation.
- Done when: the SE-2 isolation tests pass.

#### SE-2 Tenant isolation tests
`0.2` · `M` · Depends on: SE-1
- Do: two tenants, two projects, each Agent tries to read the other's files; every attempt must fail.

#### SE-3 Accounts and project permissions
`0.6` · `L` · Depends on: SE-1
- Do: add user accounts and project-level permissions to self-hosted web (web authentication and `routes/context.py`).
- Done when: two users cannot see each other's projects.

#### SE-4 A fixed address
`0.6` · `S–M` · Depends on: none
- Do: replace the temporary tunnel with a fixed domain or a self-run tunnel (`deploy/`).

#### SE-5 Self-service export and deletion of all data
`0.6` · `M` · Depends on: SE-3

#### SE-6 Operations dashboard
`0.6` · `M` · Depends on: SE-3
- Do: show quota, concurrency, and failure rate on the existing admin data page.

#### SE-7 Public sign-up and buying quota
`1.x` · `L` · Depends on: SE-3, SE-8 · product decision
- Do: waitlist, sign-up, quota purchase (`trial/web_portal.py`, trial-gateway).

#### SE-8 A formal security review
`0.6` · `M` · Depends on: SE-1–5
- Do: review credential storage, log redaction, permission boundaries, authorization of irreversible actions, and
  dependency vulnerabilities; only after high-severity issues are cleared can we move to 0.7.

---

### Theme EC: Ecosystem — domains, plugins, and model backends

[Principles: 2](PRINCIPLES.md)

**The problem today**: community domains import about 15 private framework modules directly, so any framework refactor
can break them; the domain store can install things but they can't be managed on the web page, and tasks can't combine
domains; only 5 of the 9 backends can act as Reviewer.

**What we want**: a public, versioned domain SDK and compatibility matrix; anyone can write a domain in 15 minutes; every
backend can run all four roles.

#### EC-1 Domain SDK v1
`0.7` · `M–L` · Depends on: CO-6, CO-12
- Do: consolidate the private modules the community repository depends on into public, versioned interfaces, and write
  down the stability promise.

#### EC-2 Version matrix
`0.7` · `M` · Depends on: EC-1
- Do: in the community domain repository's CI, run contract tests against Argus `dev` and produce a version
  compatibility matrix.

#### EC-3 Release the research domain on its own
`1.x` · `L` · Depends on: EC-2

#### EC-4 Delete the old `argus_skill` entry points
`0.7` · `S` · Depends on: none · in progress
- Do: the old entry-point group has been kept for one release cycle and can be deleted.
- Status: `9957c427` on `chore/slim-round2` removes the import alias and the `argus-skill` command; it is not in
  `dev` yet. Before deployment, files in installed instances that still use the old name must be updated too.

#### EC-5 Install, update, and uninstall domains on the web page
`0.7` · `S–M` · Depends on: none
- Do: complete domain management in `VerticalStore.tsx`.

#### EC-6 Domain scaffolding and a "write a domain in 15 minutes" tutorial
`0.7` · `S` · Depends on: EC-1 · good first issue
- Do: one command generates a domain skeleton, with a step-by-step tutorial built on
  [building-a-vertical.md](building-a-vertical.md).

#### EC-7 Tasks that combine domains
`1.x` · `L` · Depends on: EC-1
- Today: [vertical-store.md](vertical-store.md) says plainly that this isn't supported yet.

#### EC-8 Workbench plugins for more backends
`1.x` · `M` · Depends on: none
- Today: only Codex, Copilot, and Pi are supported.

#### EC-9 Show compatibility and downloads in the domain catalog
`1.x` · `M` · Depends on: EC-2

#### EC-10 Give every backend the Reviewer's review actions
`0.7` · `M` · Depends on: none
- Do: add review action tools for cursor, opencode, and grok; if dsh can't do it, say clearly that it isn't supported
  instead of failing at runtime.

#### EC-11 Backend capability matrix
`0.7` · `M` · Depends on: EC-10
- Do: generate automatically which roles and features each backend supports, and guarantee it with contract tests.

#### EC-12 Turn the remaining text decisions into typed events
`0.9` · `L` · Depends on: none
- Do: turn the remaining model text decisions listed in [model-decision-boundary.md](model-decision-boundary.md) into
  typed events produced by the backend, one at a time.

#### EC-13 Finish real-model acceptance for the team and the Manager
`0.9` · `M` · Depends on: none
- Do: full Planner and Reviewer rounds under the Advisor, the Manager's four-stage supervision, and exchanges between
  daemons.

---

### Theme TS: The TypeScript runtime — a long-term track that doesn't block 1.0

[Principles: 2](PRINCIPLES.md)

**The problem today**: the general runtime is gradually moving to TypeScript, with Python still responsible for
production state. The migration plan is in [typescript-migration.md](typescript-migration.md) and is in progress.

**What we want**: TypeScript handles scheduling and state, and Python steps back to being a plugin worker. This is the
direction after 1.0, not a prerequisite for 1.0.

- **TS-1** Extend the write-command contract, and support a read-only mode in the frontend. `M`
- **TS-2** Connect the Pi adapter to external model services and verify it, and fill in training-data capture and
  session ownership. `M`
- **TS-3** Port the storage and budget primitives, inject faults, and run one complete P→E→R→M task that includes a
  restart and a cancellation. `L`
- **TS-4** Extend to continuous scheduling, other backends, and the write API. `L`
- **TS-5** A Python plugin worker, and converging installation and desktop delivery. `L`
- **TS-6** The task view's Python and TS sides share one set of standard test fixtures. `S` · good first issue

---

### Theme RL: Releases, docs, and community — 1.0 should look like 1.0

[Principles: 2, 9](PRINCIPLES.md)

**The problem today**: some docs disagree with the code (for example, the research TODO checkboxes and audit items that
are already fixed); there is no written promise about versions and compatibility; release notes are written by hand.

**What we want**: users know what an upgrade will do; contributors set up a development environment within 30 minutes;
the docs say what the code does.

#### RL-1 Version and compatibility promise
`0.7` · `M` · Depends on: none
- Do: write down the version-number rules, which interfaces are stable, how the project data format is upgraded, and how
  long deprecated features stay.

#### RL-2 Old-data upgrade test
`0.7` · `M` · Depends on: RL-1
- Do: with anonymized project data from 0.1.x instances, verify the new version can read it directly and keep running.

#### RL-3 Generate release notes automatically
`0.9` · `S` · Depends on: none · good first issue

#### RL-4 List of outdated docs
`0.2` · `S` · Depends on: none · good first issue
- Do: list the docs that disagree with the code, and fix each one or mark it as outdated.

#### RL-5 Keep Chinese and English docs in step
`0.9` · `S` · Depends on: none · good first issue

#### RL-6 One-command development setup
`0.9` · `S` · Depends on: none · good first issue
- Do: one command installs the Python and Node dependencies and runs a set of smoke tests; explain that large test runs
  should use `--basetemp` pointing at a directory with plenty of space.

#### RL-7 1.0 release rehearsal
`0.9` · `M` · Depends on: the other 0.9 tasks
- Do: rehearse the full release checklist once: build, install on all three platforms, cold start, upgrade old data,
  update the public comparison table, and release notes.

## After 1.0: the long view

1.0 is about "dependable, credible, easy to use". After that, Argus wants to go further:

- **A team research workbench**: several people working together in one project, commenting, dividing the work, and
  sharing experience (UX-12, SE-7).
- **Big tasks across domains**: one goal that brings in several domains at once, for example "build a new kernel, write
  it up as a paper, and release it as an open-source library" (EC-7).
- **Research results that can be verified independently**: in math, a machine-checkable proof or counterexample for an
  open problem; in machine learning, papers that outside teams can rerun in one command and that pass peer review
  (RA-3, RA-4, RA-9, SC-6).
- **A public record of self-improvement**: one continuously updated "gets better with use" curve per domain, and a public
  leaderboard against using Agents directly (LE-8, EV-2).
- **An open domain marketplace**: the community publishes domains on the SDK, with a compatibility matrix, ratings, and
  download counts (EC-3, EC-9).
- **The TypeScript runtime**: scheduling and state move to TS, with Python running as plugins (theme TS).

## How to contribute

1. **Pick something.** To get going quickly, start with a task marked "good first issue", for example HO-1, HO-4, CI-1,
   CO-1, SC-9, SC-14, LE-5, RL-4, UX-3, RA-8.
2. **Say so.** Put the task ID in an issue or draft PR so two people don't do the same thing. For L tasks, describe how
   you'll split it in an issue first.
3. **Branch from `dev`.** Name the branch `fix/<id>-<slug>` or `feat/<id>-<slug>` and open the PR against `dev`; see
   [CONTRIBUTING.md](../CONTRIBUTING.md).
4. **Explain why.** In the PR description, say which of the [principles](PRINCIPLES.md) the change serves. A new
   mechanism that can't name one doesn't go into Core.
5. **Write the failing test first.** Add a test that fails before the change and passes after; run the tests the task
   mentions and the full Python test suite.
6. **About the release manifest.** If you change only Python, the release manifest goes stale and
   `tests/release_tools/test_check_artifacts.py` will say so until `python -m argus.release_tools.build_release` runs;
   say in the PR whether you ran it. If you change frontend source, commit the rebuilt UI along with it.
7. **One PR, one thing.** Don't change interfaces, storage format, stage semantics, and deployment in the same patch. Old
   data must still be readable, and tasks already running finish under the rules they started with.
8. **Update this document when you're done.** Mark the status and the PR next to the task; if measured results change a
   target number, update that too.
