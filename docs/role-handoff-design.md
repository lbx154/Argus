# Role handoff: from principles to design

Chinese version: [role-handoff-design.zh-CN.md](role-handoff-design.zh-CN.md)

Status: proposal (2026-10-16). Implementation is tracked in
[the roadmap](ROADMAP.md); this document explains why each item exists. The
liveness part ([The baton](#the-baton)) starts with fixes for the defects
listed under [Handoffs that do not end](#handoffs-that-do-not-end), each an
instance of one invariant.

## Why this document exists

Handoffs between Engineer, Reviewer, Planner, and Manager are the most
frequently repaired area of Argus. The repository history has 774 commits that
mention the Reviewer and 182 that mention handoffs; about twenty fixes for the
same class of defect landed between 2026-10-08 and 2026-10-10. The audit of
2026-10-16 found about a dozen more, fixed in `ef216a16`, and each fix again
patched one instance.

The defects are not independent. Every one of them is an implementation of a
handoff that departs from a principle Argus already states in
[PRINCIPLES.md](PRINCIPLES.md). The same document says what to do then: change
the implementation, not add an exception. This proposal does that.

## First principles of a handoff

Argus uses more than one role for two reasons only: an independent judgment
that the producer cannot give itself, and authority that the producer does not
hold. Splitting labour or saving context does not need a second role.

A handoff therefore exists for one purpose: **let the judging actor decide
whether the world reached the agreed state, without being steered by the actor
that produced it.** Four properties follow, and each is now stated in the
principles:

| Property | Principle | What breaks without it |
| --- | --- | --- |
| The judge reads the change itself, defined by the runtime, not the producer's account of it | 1, 5 | The Reviewer judges a story; work the story omits is accepted |
| Each record has one author; runtime interventions are the runtime's own records | 1 | Nobody can tell what the Reviewer actually said; guards count host placeholders as verdicts |
| The standard is fixed by its owner before the candidate exists | 5 | Review moves the goalposts; correctness and scope concerns are indistinguishable |
| Access and obstacles go to the actor who can act on them | 8 | A Reviewer that cannot read or run something returns the work to an Engineer who cannot fix it |

These properties make a judgment trustworthy; they do not make it happen. A
handoff must also **end**. A wait that nobody is obliged to end, a return that
sends unchanged work back to the same actor, or a decision that nobody is
allowed to make stops a programme as surely as a crash, and on long tasks it is
the more common failure. Four invariants guarantee that every handoff ends;
they are described under [The baton](#the-baton).

## What the current implementation does

Measured on `origin/dev` at `a05dcb98` and on recorded runs from the previous
production instance and the current trial.

### The Reviewer judges an account, scoped to the last round

- `roles/prompts/reviewer.py` (`_changed_artifact_block`) lists at most twenty
  file names whose mtime is newer than the start of the current review window.
  There is no content diff. `.argus` is skipped entirely, so run outputs under
  `.argus/runs` never appear.
- The same prompt states that "the Engineer's own account is primary"; the
  execution log is a one-line fallback.
- The window closes after every completed review, so a `done` judges the
  changes since the previous review, not the mission. Earlier rounds reach the
  Reviewer only as carried-over prose.
- Copilot Reviewers could not read the files their prompt named until
  `ef216a16`: eleven of fourteen live Reviewer calls had a denied read.
- `skills/round_checkpoint.py` snapshots git workdirs, but its only trigger
  (`checkpoint_recommended`) is never set by the native review path, nothing
  reads the refs back, and no real workdir is a git repository at its root
  (22 of 22 checked).

### Verdicts are rewritten and their provenance is guessed

- `ReviewDecision.review_source` defaults to `"reviewer"`, and twelve call sites
  fall back to `"reviewer"` when it is empty. Provenance is spread across five
  fields (`review_source`, `reviewer_authored`, `reviewer_words`, `host_notes`,
  `host_placeholder`).
- Four host paths change a Reviewer-authored verdict in place: venue
  acceptance (`core/venue_review.py`), the background-run hold
  (`engineer/runner.py`), the operator-question policy, and the terminal
  question strip (`engineer/round_settlement.py`). A venue-rewritten verdict is
  still sealed with `producer_role=reviewer`.
- `RoundLoopState.reviewer_next_action`, which the next Engineer prompt is
  built from, has five writers.
- `events.jsonl` is the only append-only record. `latest.json`, the mission
  brief, and Backlog outcome fields are projections, but several are written
  directly rather than derived.

### The host makes semantic judgments

| Host rule | Where | Judgment it makes |
| --- | --- | --- |
| A non-empty Engineer message is a "successful work signal" | `round_stop_signals._runner_result_has_successful_work_signal` | Whether work happened |
| Two obstacles are the same at 60% word overlap | `obstacle_stall.SAME_OBSTACLE_OVERLAP` | Whether the agent is stuck |
| After the soft limit, two judgments without `forward_progress=true` stall the mission | `round_settlement` | Whether work progresses |
| From the hard limit, `continue` without explicit progress becomes `blocked`, a rule never shown to the Reviewer | `round_settlement` | Same |
| At most two consecutive Engineer self-handoffs (added in `ef216a16`) | `round_self_review` | When work needs review |
| The rejection adaptor prepends a playbook to the next action | `round_settlement` | What the Engineer should do |

About 2,000 to 2,500 of the 10,400 lines in the round loop and Reviewer exist to
maintain these rules, placeholders, rewrites, and their provenance rendering.

### Where review effort actually goes

59 `continue` verdicts from the previous production instance and the current
trial, classified by their stated reason:

| Reason | Count |
| --- | ---: |
| Waiting on a running job; nothing new to judge | 30 |
| The Reviewer lacked access or a tool the task demanded | 17 |
| A correctness or execution defect | 5 |
| A concern outside the acceptance check | 4 |
| An unmet acceptance criterion | 3 |

Most review calls that did not accept the work judged nothing new, or could
not judge. The trial instance otherwise converges well: 22 of 25 reviewed
missions ended `done`, 13 in one round.

### The bar comes from several places

The Planner writes `acceptance_check` "when useful"; 35 of 39 recorded missions
had one. The Engineer sees it in the mission contract, and the Reviewer sees it
concatenated into `review_objective`, beside the original request, goal
contract clauses, and stage guidance. The verdict is free-form prose: nothing
ties `approve_review` to the criteria. The fix that ties completion to an
explicit objective status (`fix/completion-objective-status`) was never merged
into `dev`.

### Handoffs that do not end

Measured by reading the code on `origin/dev` at `8c188e69`; every item below
was traced to a line.

There are about thirty distinct handoffs. By where the work goes next, they
reduce to six moves:

| Move | Direction | Today |
| --- | --- | --- |
| Delegate | down: new work | Manager dispatch; Planner enqueue and claim; teammate and subagent launch; advisor consult |
| Submit / return | sideways: to an independent judge, or back to the producer | Engineer to Reviewer; Reviewer `continue` |
| Escalate | up: to whoever holds the authority or can remove the obstacle | operator questions; `replan_requested`; `blocked`; "the Planner needs direction" |
| Park | out: to the world or the clock | background-run waits; Planner `WAKE_ON`; eleven `paused_*` statuses; Manager WAIT |
| Resume | the same role, later | Engineer self-handoff; role-session rotation; resume after a pause; blue/green daemon handoff |
| Preempt | in: from above, into a running holder | operator STEER; Manager steer and wait; pause and abort |

A Reviewer verdict is not a seventh move: `continue` is a return,
`replan_requested` and `blocked` are escalations, and `done` is acceptance. A
seventh kind exists only implicitly, as the host rewriting a move a model made:
verdict rewrites, a question turned into a replan, a Planner batch discarded by
`PLAN_RETRY`, a Planner stage advance applied without the Manager.

Each of the thirty handoffs carries its own wait, timeout, and counter, and
more than a dozen thresholds (2, 3, 4, 12, 24, 60, 120, 300 s, 1,800 s,
3,600 s, a 0.6 word overlap) each patch one loop. Without one rule for how a
handoff ends, four classes of defect appear:

| Class | What happens | Examples |
| --- | --- | --- |
| Orphan wait | The holder waits for an event that nobody is obliged to produce, with no deadline | Dependents of a `research_incomplete` or `infra_blocked` item stay pending forever: the cascade fires only on terminal states, and the Planner can retire only pending items. The runtime-failure circuit clears only after a `runtime_failure_canary` mission that nothing creates. A subagent whose process is alive but wedged is healthy forever, because `stale_after` is computed and never compared. A blue/green candidate that cannot take the lock within 60 s exits after the roll request was consumed, leaving no daemon. A Manager WAIT without a mission freezes all planning. |
| Unbounded return | Work goes back and forth with no mechanical measure of change | Missions with `require_independent_review=False` self-continue without settlement while `max_rounds` defaults to 0. `forward_progress=true` disables the stall guards, and round counters restart on every resume. Team tasks retry without a budget. |
| Authority vacuum | A decision has no entitled decider | With an operator available and questions forbidden, nobody may accept a residual risk. The Manager's supervision channel cannot stop a mission. |
| Lossy handoff | The move is lost or misrouted in translation | A Planner verdict that both waits and proposes tasks drops the tasks. A question raised on an archived item is swallowed. `early_stopped` and `crashed` are not terminal to the daemon. A stale decision card answers a newer question. |

## Target design

### Records

- **Facts**: the event log, plus host-owned workspace snapshots.
- **Authored records**: each actor appends only its own record type. The
  Reviewer's verdict comes only from its review action. Host interventions such
  as a background hold, the question policy, a venue minimum, a budget stop, or
  an unjudged turn are `host` records that reference the verdict they affect.
  Neither type is edited after it is written.
- **Effective outcome**: settlement computes it from the verdict and the
  interventions. It is a derived value, recorded with the inputs it was derived
  from.
- **Projections**: `latest.json`, the mission brief, and Backlog outcome
  fields are rebuilt from records. `CHECKPOINT.md` remains the
  Engineer's own working note; it is not acceptance evidence.

A record without a known author is a defect, not a Reviewer verdict.

### Candidates and baselines

- The host keeps a private snapshot store per project: a bare repository under
  the project state directory whose work tree is the mission workdir. The
  user's workdir, branches, and index are never touched.
- Generated outputs, virtual environments, nested repositories, and files above
  a size cap are recorded in a manifest (path, size, mtime) rather than stored.
  On the largest live workspace (220 GB, 164k files) this leaves 2,141 files:
  3.7 s for the first snapshot and 0.03 s for each later one.
- A snapshot is identified by `(mission, run, sequence)`, which is recorded in
  the event log and used as the name of a private ref. Git's object ids stay an
  internal detail, so no new hash identifier is introduced.
- The baseline is the snapshot at mission start. It moves only when the
  Reviewer accepts a candidate.
- A candidate is the snapshot taken when review is requested. The Reviewer
  receives the change from the baseline to the candidate: names and status,
  bounded text diffs, manifest changes, the commands the Engineer ran, and any
  new external results. The Engineer's account comes after the change and is
  labelled as the producer's account.
- A verdict names the candidate it judged. A stage certificate or a replayed
  acceptance is valid only for that candidate.

### When review happens

- Review is requested when the Engineer declares a candidate or a
  principal-set checkpoint budget is reached. It is not requested after every
  Engineer turn.
- If the candidate equals the last judged candidate and no external result or
  operator directive arrived since, the host records that and does not call the
  Reviewer. This alone removes the largest class of `continue` verdicts.
- Background work is judged when its result exists. The Engineer waits for it
  in its own turn; nobody asks a Reviewer to judge that nothing happened.

### The Reviewer's decision

- The acceptance criteria come from the mission contract, by reference, owned
  by whoever wrote them (Planner, Manager, or operator, per
  [goal-contract-authority.md](goal-contract-authority.md)).
- A review handing off `→ done` or `→ engineer` states a status for each criterion.
  Approval requires every criterion to be met or explicitly accepted by its
  owner.
- A concern outside the criteria is a follow-up: recorded, routed to the
  Planner or Manager, and not blocking. A breach of the integrity floor (false
  claims, fabricated evidence, broken provenance) always blocks.
- A rejection states what would change the verdict.
- An obstacle the Engineer cannot remove, such as missing access, a missing
  tool, or a contract conflict, is escalated to the host or the contract owner
  instead of being returned to the Engineer.

### Capability matches mandate

A role is given what its decision needs, or the decision is assigned
elsewhere. The Reviewer has the Engineer's access, so it reruns a decisive
check itself; that it left the work alone is a fact the host checks by
comparing workspace snapshots, not a permission.

### The host enforces budgets, not progress

Rounds, wall time, and cost per mission are budgets set by the Manager or the
operator. When one is reached, the host stops and records that fact, and the
Manager decides what happens next. The host does not estimate progress from
message length, word overlap, or missing fields, and the Reviewer prompt does
not explain host mechanics it cannot influence.

### The baton

Every open item (a mission, or the project's planning slot when no mission is
runnable) has exactly one **holder**: Engineer, Reviewer, Planner, Manager, or
the operator. A handoff is a prose note and one line naming the next holder:

```text
<what was done, what was found, what is open, why the next holder>
→ reviewer
```

The line takes one of six values: `engineer`, `reviewer`, `planner`,
`manager`, `operator: <one-sentence question>`, or `done`. A question that
picks between a few answers lists them under the line, one `- <answer>` each:
the operator answers with one tap or in their own words, and the Manager,
answering in their place, picks one of them. A list that cannot be read
leaves the question without options, never without its question; a list
under an empty `operator:` line asks the last line of prose above it.
Routing is free. Any holder may pass to any role: a Reviewer straight to the
Manager when the criteria conflict, an Engineer straight to the Planner when a
task is mis-scoped. The host never decides where work goes. It enforces four
invariants, and together they are the whole state machine:

1. **One holder.** An item has one holder at a time, and items wait on each
   other only through the dependency graph, which is acyclic. No circular wait
   can form. A question parks only its own item and that item's descendants,
   never the project and never the operator's control channel.
2. **Waiting happens in the holder's own turn, and the backend handles it.**
   Argus has no waiting state and watches nothing on a holder's behalf
   (principle 1: the runtime is not a parent; design question 7: if the backend
   can do it, do not do it for it). A long job is started detached and waited
   for inside the turn with the backend's blocking commands. A question goes to
   the operator, or to the Manager while the operator cannot be asked. The
   Planner is called only when something is handed to it: a mission ends, the
   operator writes, the Manager directs.
3. **An unchanged return climbs.** Suppose the item would return to a holder
   that already held it at the same candidate, with no new external result and
   no new operator input since. Then the host does not call that model again.
   Instead the item moves one rung up the ladder Engineer → Reviewer → Planner →
   Manager → operator. "Changed" is a mechanical fact read from the candidate
   snapshot and the event log. It is counted on the mission's lineage, so
   resuming, replanning, or rewording does not reset it. For the Engineer, a
   review handed to it that it has not yet worked on is new input; the next
   unchanged return still stops at the Reviewer. An Engineer's own line stands
   the same way: unchanged work it handed on goes where that line sent it, so
   only a handoff to the Reviewer resumes at review. A Reviewer that accepted the
   candidate as it is does not pass it up: its acceptance stands without a
   model call, and the record names the verdict it reused. The host refusing
   that acceptance (the task it ended failed, or was sent back to run again)
   is a new external result: the acceptance no longer stands, and the
   unchanged candidate climbs past the Reviewer.
4. **Authority never vanishes.** Every decision type has an owner. When the
   owner cannot be asked, the decision falls to the next permitted rung
   instead of disappearing. Only irreversible decisions may wait on the operator
   without a deadline, and only while the operator can be asked. A reversible
   question carries the asker's default ("without an answer by T, continue
   with X").

| Decision | Owner | When the owner cannot be asked |
| --- | --- | --- |
| Accept a candidate | Reviewer (the Engineer where review is not required) | — |
| Create, retire, or replace work | Planner | Manager |
| Change the stage | Manager | — |
| Accept a residual risk | operator | Manager |
| Stop a mission | Manager or operator | — |
| Payment, publication, destructive actions, releases | operator | Manager |
| Credentials | operator | Manager decides how to proceed without them |

"Cannot be asked" means the operator said not to be asked, or the run has no
operator (principle 8). The operator's whole authority then passes to the
Manager. Each decision the Manager takes in the operator's place is recorded as
its own, with the evidence it cited, and the delivery report lists it.

**Why every item ends.** Each handoff does one of three things:

- It changes the candidate or brings a new fact, which spends budget.
- It climbs one rung, which can happen at most four times.
- It ends the item.

A wait lasts at most the turn it happens in. Within a bounded number of
handoffs, every item is therefore done, dropped, or held by an operator who
can be asked on an irreversible decision that blocks nothing else. Deadlock is impossible, and
the ladder's height bounds the number of model calls made without a change.

**What this replaces.** Done (October 2026): no round status sits under the
baton any more, and no named pause state beside it.

- `continue` is `→ engineer`, `replan_requested` is `→ planner`, `blocked` is
  `→` whoever can remove the obstacle, and `done` is acceptance of a named
  candidate. A round, its record and the task it settles carry the holder
  itself (`engineer`, `planner`, `manager`, `operator`, `done`); nothing
  translates it back. A record an older release wrote is read once, in
  `holder_of` (argus/core/handoff.py) and `settledStatus`
  (frontend/core/src/missionOutcome.ts), and never written.
- A task handed to the Manager or the operator is parked in their hands, never
  failed, whoever handed it: a question the Manager puts to the operator parks
  that same task, and the answer resumes it, never a copy.
- The `paused_*` statuses are one park whose reason is data
  (argus/core/stop_kinds.py), for backlog rows, Planner verdicts and the
  notices the operator reads; the words come from the reason. Older rows and
  events load through the same table.
- A Planner with nothing new to start names who acts next: `→ engineer` (the
  work in flight decides what comes next), `→ manager` (the stage blocks it), or
  `→ operator: <question>`. There is no waiting contract.
- The Engineer's seven footer keys and its wait line become the single line
  above; the wait itself moves into the Engineer's turn.

Structured payloads remain only where a side effect needs them: a Planner
task's title and objective (plus dependencies and owned paths where they
exist), a question, and an authorization. The line is read leniently: case,
role names in the operator's language, and Markdown decoration do not matter. A
missing or unreadable line never ends a mission and never means `done`. A
changed candidate goes to the Reviewer, and an unchanged one climbs.

### The Observer

The baton moves between the four roles, the operator, and the world. The Observer
never holds it. It is the person who used to sit beside the screen, glancing at
whether the work flows: who holds the baton and whom it passed to, how long each
turn took and what it cost, a role called again and again with nothing new, a
wait that goes on with nobody moving, a course drifting from the task. Whether
the work is right stays the Reviewer's to judge. It decides nothing: stopping or
redirecting a mission is still the Manager's or the operator's.

It looks once at each turn the Engineer, Reviewer or Planner ends, never at its
own, reading only the task's events. Within a task it resumes one session and
reads only the events added since its last look; the session ends with the
task, and planning, which belongs to no task, is looked at afresh. A turn nobody
took, such as a Planner call skipped because its inputs did not change, is not a
turn. When the flow is off it writes one line,
`→ manager: <sentence and where it was seen>`, as one `life.observer.note`
event naming its task. The Manager hears it through its supervision, as news reported once,
like a stalled turn, and decides whether to steer, pause, or stop; with no
Manager, or once the task is delivered, the operator gets an alert. The remark
moves no baton and changes no candidate, and the Observer never repeats one. A
steer it prompts is read by whoever holds the work at their next boundary and
never hands planning to the Planner, so however a remark is worded, it cannot
keep a handoff going on its own.

## What is deleted

Removed once the replacement is in place, each in its own roadmap item:

- `_changed_artifact_block` and the mtime review window, replaced by candidate
  diffs;
- `skills/round_checkpoint.py` and `ARGUS_SKILL_ROUND_CHECKPOINT`;
- in-place verdict rewrites in `venue_review`, the background hold, and the
  question policy, replaced by host records;
- the `"reviewer"` default and fallbacks for `review_source`, together with
  the overlapping provenance fields;
- the non-empty-message progress test, obstacle word overlap, the soft and
  hard progress rules, the self-handoff limit, and the rejection-adaptor
  playbook;
- the harness-mechanics blocks in the Reviewer prompt;
- `review_objective` concatenation, replaced by the mission contract by
  reference;
- the prose-parsing paths in `reviewer/_parsing.py` that have no production
  callers;
- the remaining per-loop counters that stand in for the unchanged-return rule:
  the empty-turn and stall streaks, the Planner's probe missions, and its
  fixed re-grant cadence;
- the eleven `paused_*` statuses as separate states, replaced by one park
  record;
- the Planner's wait-contract fields and the Engineer's footer keys, replaced
  by the single handoff line.

## Compatibility

- Existing state stays readable. New record types and snapshot ids are
  additive, and old round files, events, and Backlog fields are not rewritten.
- Missions started before a change finish under the rules they started with.
  No migration rewrites in-flight state.
- Each roadmap item is a separate, reviewable change. API, storage format,
  stage semantics, and deployment do not change in the same patch, following
  [runtime-maintainability.md](runtime-maintainability.md).
- The old footers are no longer read: the Engineer's and the Reviewer's
  prompts ask for the handoff line, and a footer line left in an older
  message is plain prose.

## How success is measured

Following principle 9, success is measured on real tasks against matched
controls, not by gate counts. Per accepted mission, track:

- Reviewer calls, and the share made on an unchanged candidate (target: zero);
- the reason classes of `continue` verdicts, as above;
- accepted missions later found incomplete (false `done`);
- wall time, tokens, model calls, and cost;
- operator questions and escalations, and who resolved them;
- items that no holder could act on, and how long they stayed that way
  (target: none);
- model calls made by any role on an unchanged candidate (target: zero);
- climbs per rung, and how each item that reached the operator was resolved.

The roadmap's metrics item produces these numbers from `events.jsonl`, so every
later item can report its effect against the same baseline.

## Open questions

- Snapshot exclusion defaults per vertical: research runs produce large
  artifacts that are evidence. The manifest records them; whether a Reviewer
  needs their content is a vertical decision.
- Whether per-criterion status should be mandatory for exploratory missions,
  which by principle 5 make no claims.
- How follow-ups are prioritized against the Planner's existing frontier.
- Default deadlines per kind of park and per vertical, given that research runs
  legitimately take days.
- Whether the holder is only derived from handoff events in `events.jsonl`, or
  is also kept on the Backlog item for cheap reads.
