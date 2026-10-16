# Roadmap: role handoffs

Chinese version: [ROADMAP.zh-CN.md](ROADMAP.zh-CN.md)

This roadmap turns [the handoff design](role-handoff-design.md) into work items
that can be picked up independently. Each item names the principle that
requires it, the code it touches, the evidence that it is done, and what it
depends on. Status describes `dev`; it says nothing about a release or a
deployment.

Sizes: **S** is under a day, **M** is one to three days, **L** is split into
sub-items that each fit in one pull request.

## Overview

| ID | Priority | Size | Item | Principle | Depends on | Status |
| --- | --- | --- | --- | --- | --- | --- |
| H0 | — | — | Audit fixes for Reviewer handoffs | 1, 5, 8 | — | Done (`ef216a16`) |
| H1 | P0 | S | Handoff metrics report | 9 | — | Open, good first issue |
| H2 | P0 | M | Do not review an unchanged candidate | 4, 5 | — | Open |
| H3 | P0 | M | Reviewer escalation and capability | 8 | — | Open |
| H4 | P1 | L | Host snapshots, baselines, and candidate diffs | 1, 5 | — | Open |
| H5 | P1 | L | Authored records and host interventions | 1 | — | Open |
| H6 | P1 | M | Acceptance criteria as the review standard | 5, 7 | — | Open |
| H7 | P2 | M | Budgets instead of progress heuristics | 1, 8 | H1, H2, H4 | Open |
| H8 | P2 | M | Review at candidates, not every turn | 5, 8 | H2, H4, H7 | Open |
| H9 | P2 | M | Projections rebuilt from records | 1, 4 | H5 | Open |
| B1 | P1 | S | Prose-only Reviewer replies | 3 | — | Open, good first issue |
| B2 | P1 | M | A Manager wait must not discard an accepted verdict | 1 | — | Open |
| B3 | P1 | S | One source for operator availability | 1 | — | Open, good first issue |
| B4 | P2 | S | Consistent checkpoint instructions | 1, 5 | — | Open, good first issue |
| B5 | P2 | S | Correct rendering of the Engineer decision | 3 | — | Open, good first issue |

H1, H2, H3, H4a, H5a, H6, and all B items can start in parallel.

## How to contribute

1. Pick an item and say so on an issue or a draft pull request that names its
   ID, so two people do not take the same one.
2. Branch from `dev` (`fix/<id>-<slug>` or `feat/<id>-<slug>`) and target the
   pull request at `dev`, as described in [CONTRIBUTING.md](../CONTRIBUTING.md).
3. In the description, name the principle the change serves. A new mechanism
   that cannot name one does not enter Core ([PRINCIPLES.md](PRINCIPLES.md)).
4. Add a regression test that fails before the change. Run the tests the item
   lists and the full Python suite with a `--basetemp` outside the system disk.
5. Python-only changes leave the release manifest stale, and
   `tests/release_tools/test_check_artifacts.py` reports it until
   `python -m argus.release_tools.build_release` runs. Say in the pull request
   whether you ran it. Frontend changes must include rebuilt bundles.
6. Keep one item per pull request. Do not change API, storage format, stage
   semantics, and deployment in the same patch.
7. Old state must stay readable. Missions already running finish under the
   rules they started with.

## Items

### H0 — Audit fixes (done)

Merged in `ef216a16`:
- Copilot Reviewers can read the state, checkpoint, and playbook paths their
  prompt names.
- Unreviewed Engineer turns, and the evidence they produced, reach the next
  Reviewer.
- Host placeholders no longer count as Reviewer judgments.
- A refused correction no longer settles on an earlier approval.
- Reruns of a mission keep their own round records.
- Reviewer-to-Planner signals reach the Planner's capsule, and a rotated
  session no longer resumes its old thread.

These are patches within the current structure. H2 through H8 remove the
structure that made them necessary.

### H1 — Handoff metrics report (P0, S)

- **Why:** principle 9. Every later item needs a baseline to report against.
- **Scope:** a read-only report in `argus/tools/`, next to
  `cache_hit_report.py`, that reads a project's `events.jsonl` and round
  handoffs. Per accepted mission it reports Reviewer calls, the share of calls
  on an unchanged candidate, `continue` verdicts by stated reason, wall time,
  tokens, and model calls. Write no state.
- **Done when:** a fixture event log yields the expected numbers in a test, and
  this roadmap shows the command that runs it.

### H2 — Do not review an unchanged candidate (P0, M)

- **Why:** 30 of 59 recorded `continue` verdicts judged nothing new.
  Principle 4 says reviewing an unchanged artifact is not work.
- **Scope:** before calling the Reviewer
  (`engineer/round_reviewer.py`, `_invoke_reviewer_with_retry`), the host
  checks whether anything changed since the last completed review. That means
  workspace files, a newly arrived external result, or a new operator context
  revision. If nothing changed, it records a host event and continues or waits
  without a Reviewer call. Start with the current changed-file window; H4
  replaces it with snapshots.
- **Done when:** a test where a background run is still pending makes no
  Reviewer call, and one where its result arrives makes exactly one. H1 shows
  the unchanged-candidate share falling on a replayed log.

### H3 — Reviewer escalation and capability (P0, M)

- **Why:** 17 of 59 `continue` verdicts were the Reviewer reporting missing
  access or tools, then returning the work to an Engineer who could not fix
  it. Principle 8.
- **Scope:**
  - Let a review action route an obstacle to the host (missing access or tool)
    or to the contract owner (conflict), using the existing
    `manager_attention` path instead of a new channel. The Engineer is not
    rerun for an obstacle it cannot remove.
  - Let a vertical declare whether a stage's review requires independent
    execution. If it does, enable the existing validation sandbox
    (`ARGUS_SKILL_REVIEWER_VALIDATION_IMAGE`, see
    [ROLE_SESSIONS_AND_SKILLS.md](ROLE_SESSIONS_AND_SKILLS.md)). If not, remove
    rerun demands from that stage's Reviewer guidance.
- **Files:** `reviewer/tools.py`, `roles/prompts/reviewer.py`,
  `engineer/round_settlement.py`, the vertical contract, and
  `verticals/research/prompt_policy.py`.
- **Done when:** a test where the Reviewer reports an access obstacle ends with
  an escalation and no further Engineer turn, and a stage that requires
  execution starts its Reviewer with the sandbox.

### H4 — Host snapshots, baselines, and candidate diffs (P1, L)

- **Why:** principles 1 and 5. The Reviewer should read the change itself, and
  a `done` should cover the whole mission, not the last round.
- **H4a, snapshot store:** a Core module that snapshots a workdir into a
  private repository under the project state directory, with the workdir as
  its work tree.
  - Exclude generated outputs, virtual environments, and nested repositories,
    and record files above a size cap in a manifest (path, size, mtime).
  - Identify snapshots by `(mission, run, sequence)` in a private ref, and
    never touch the user's repository, branch, or index.
  - Tests: a non-git workdir, a git workdir, a nested `.git`, a large file,
    excluded directories, and a re-snapshot of 2,000 files in under a second.
- **H4b, review input:** take the baseline at mission start and a candidate at
  each review request. Render baseline-to-candidate changes, bounded text
  diffs, manifest changes, and the recorded commands before the Engineer's
  account. Replace `_changed_artifact_block` in `roles/prompts/reviewer.py`.
- **H4c, binding:** move the baseline only on acceptance. Record the candidate
  id in `record_reviewed_handoff`, the stage certificate, and stage replay, so
  an acceptance is valid only for what it judged.
- **H4d, deletion:** remove `skills/round_checkpoint.py`, the
  `ARGUS_SKILL_ROUND_CHECKPOINT` knob, and the mtime review window.

### H5 — Authored records and host interventions (P1, L)

- **Why:** principle 1. Four host paths rewrite Reviewer verdicts in place,
  provenance defaults to `"reviewer"`, and the next Engineer action has five
  writers.
- **H5a:** add a host-intervention record type that references the verdict it
  affects. Settlement computes the effective outcome from the verdict and its
  interventions and records both inputs. No behaviour change yet.
- **H5b:** convert the venue acceptance rewrite in `core/venue_review.py`.
- **H5c:** convert the background-run hold (`hold_review_for_pending_background_run`).
- **H5d:** convert the operator-question policy and the terminal question
  strip in `engineer/round_settlement.py`.
- **H5e:** remove the `"reviewer"` default and fallbacks for `review_source`,
  so an unknown author fails visibly. Collapse the overlapping provenance
  fields.
- **H5f:** build the next Engineer action from the verdict plus rendered
  interventions, with one writer.
- **Done when:** an architecture test forbids host code from replacing fields
  of a Reviewer-authored decision, and a venue-held verdict is sealed with the
  Reviewer's own words plus a separate host record.

### H6 — Acceptance criteria as the review standard (P1, M)

- **Why:** principles 5 and 7. Today the bar is spread across
  `review_objective`, the original request, goal-contract clauses, and stage
  guidance, and the verdict is free prose.
- **Scope:**
  - The Planner always writes acceptance criteria for work that will produce a
    claim.
  - `approve_review` and `revise_review` carry a status per criterion; approval
    requires all of them to be met or accepted by their owner.
  - Add a non-blocking follow-up field that is routed to the Planner. Breaches
    of the integrity floor still block.
  - The Reviewer reads the mission contract by reference instead of the
    concatenated `review_objective`.
  - Re-land the objective-status work from `fix/completion-objective-status`,
    which never reached `dev`.
- **Files:** `roles/prompts/planner.py`, `reviewer/tools.py`,
  `life/supervisor/_mission_execution_runtime.py`, and `life/context_packet.py`.
- **Done when:** tool validation rejects an approval that leaves a criterion
  unaddressed, and a follow-up neither blocks the mission nor is lost.

### H7 — Budgets instead of progress heuristics (P2, M)

- **Why:** principle 1. The host should enforce budgets the principal set, not
  estimate progress.
- **Scope:**
  - Express round, wall-time, and cost limits as a mission budget set by the
    Manager or operator. At the limit the host stops, records the fact, and the
    Manager decides.
  - Remove the non-empty-message progress test, `SAME_OBSTACLE_OVERLAP`, the
    soft and hard progress rules, the self-handoff limit added in H0, and the
    rejection-adaptor playbook.
  - Remove host-mechanics text from the Reviewer prompt.
- **Done when:** an architecture test lists the permitted host guards, and H1
  shows no loss in success rate or cost on matched tasks.

### H8 — Review at candidates, not every turn (P2, M)

- **Why:** principles 5 and 8. Work in progress is not gated turn by turn.
- **Scope:** the Engineer works through reversible steps without a Reviewer
  call after each turn. Review is requested when the Engineer declares a
  candidate or a checkpoint budget is reached. `NEXT_OWNER` becomes a
  candidate-ready signal.
- **Done when:** H1 shows fewer Reviewer calls per accepted mission with no
  rise in false `done` on matched tasks.

### H9 — Projections rebuilt from records (P2, M)

- **Why:** principle 1. Summaries must not be a second source of truth.
- **Scope:** derive `latest.json`, the frontier, the mission brief, and Backlog
  outcome fields from records. Continue the single-source `MISSION.md`
  projection described in
  [architecture-simplification-plan.md](architecture-simplification-plan.md).
- **Done when:** deleting the projections and rebuilding them from records
  gives identical files in a test.

### B1 — Prose-only Reviewer replies (P1, S)

When the Reviewer writes its judgment in prose without calling a review action,
the call counts as a backend failure, and two in a row end the mission with an
error. Retry with an explicit note that no verdict was submitted, and delete
the prose-rescue code in `reviewer/_parsing.py` that has no production callers.

### B2 — A Manager wait must not discard an accepted verdict (P1, M)

In `engineer/round_settlement.py`, a Manager wait checked after a Reviewer
`done` is recorded turns the mission into a pause. On resume, the Engineer
starts again at round 1. Settle the accepted verdict first, or resume from it.

### B3 — One source for operator availability (P1, S)

The Reviewer's tool grounding reads an environment knob, while settlement reads
the project's question policy. When they disagree, the Reviewer is told to ask
an operator that settlement will refuse. Read both from the effective policy.

### B4 — Consistent checkpoint instructions (P2, S)

The Engineer is told to put evidence paths in `CHECKPOINT.md`. The Reviewer is
told not to read it, and the rotation note refers it to approvals recorded
there. Evidence belongs in the workspace and, after H4, in the candidate diff;
make the prompts agree, and remove the unused Reviewer branch of
`shared_checkpoint_instructions`.

### B5 — Correct rendering of the Engineer decision (P2, S)

`_engineer_decision_message` in `engineer/round_execution.py` renders `blocked`
as `continue`, and shows `NEXT_OWNER=engineer` when routing defaults to the
Reviewer. `latest_role_decision` returns the first decision of a turn rather
than the last. The path is dormant today but should not mislead when used.
