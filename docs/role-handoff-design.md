# Role handoff: from principles to design

Status: proposal (2026-10-16). Implementation is tracked in
[the roadmap](ROADMAP.md); this document explains why each item exists.

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
- `events.jsonl` is the only append-only record. `latest.json`, `frontier.json`,
  the mission brief, and Backlog outcome fields are projections, but several
  are written directly rather than derived.

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
- **Projections**: `latest.json`, the frontier, the mission brief, and Backlog
  outcome fields are rebuilt from records. `CHECKPOINT.md` remains the
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
- Background work is judged when its result exists. Until then the host waits;
  it does not ask a Reviewer to judge that nothing happened.

### The Reviewer's decision

- The acceptance criteria come from the mission contract, by reference, owned
  by whoever wrote them (Planner, Manager, or operator, per
  [goal-contract-authority.md](goal-contract-authority.md)).
- `approve_review` and `revise_review` state a status for each criterion.
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
elsewhere. If a vertical stage requires independent execution, its Reviewer
gets the existing read-only validation sandbox. If not, the Reviewer judges
the commands the Engineer recorded, and the prompt does not ask it to rerun
them.

### The host enforces budgets, not progress

Rounds, wall time, and cost per mission are budgets set by the Manager or the
operator. When one is reached, the host stops and records that fact, and the
Manager decides what happens next. The host does not estimate progress from
message length, word overlap, or missing fields, and the Reviewer prompt does
not explain host mechanics it cannot influence.

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
  callers.

## Compatibility

- Existing state stays readable. New record types and snapshot ids are
  additive, and old round files, events, and Backlog fields are not rewritten.
- Missions started before a change finish under the rules they started with.
  No migration rewrites in-flight state.
- Each roadmap item is a separate, reviewable change. API, storage format,
  stage semantics, and deployment do not change in the same patch, following
  [runtime-maintainability.md](runtime-maintainability.md).

## How success is measured

Following principle 9, success is measured on real tasks against matched
controls, not by gate counts. Per accepted mission, track:

- Reviewer calls, and the share made on an unchanged candidate (target: zero);
- the reason classes of `continue` verdicts, as above;
- accepted missions later found incomplete (false `done`);
- wall time, tokens, model calls, and cost;
- operator questions and escalations, and who resolved them.

The roadmap's metrics item produces these numbers from `events.jsonl`, so every
later item can report its effect against the same baseline.

## Open questions

- Snapshot exclusion defaults per vertical: research runs produce large
  artifacts that are evidence. The manifest records them; whether a Reviewer
  needs their content is a vertical decision.
- Whether per-criterion status should be mandatory for exploratory missions,
  which by principle 5 make no claims.
- How follow-ups are prioritized against the Planner's existing frontier.
