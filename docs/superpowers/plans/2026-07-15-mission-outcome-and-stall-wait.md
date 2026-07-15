# Mission Outcome and Stall-Wait Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mission status truthful across Web/TUI and prevent healthy supervised-subagent waits from being terminated as semantic stalls.

**Architecture:** The backend emits one normalized `outcome_class` for each terminal mission event, while a shared frontend helper renders both new and legacy events consistently. The runner accepts an exact last-line Engineer wait directive and a structured Reviewer wait directive, validates both against the supervised-subagent registry, and excludes validated waits from stall and decision-timeout accounting.

**Tech Stack:** Python 3.13, dataclasses/JSON schema, pytest, TypeScript, Node test runner, Vitest.

## Global Constraints

- `Mission failed` is reserved for actual execution failures.
- `research_incomplete`, `no_progress`, and `blocked` remain distinct operator-visible outcomes.
- The default semantic-stall threshold is 4 reviewed nondecision rounds.
- A validated supervised-subagent wait neither increments stall nor consumes decision-progress timeout.
- Dynamic Plan behavior remains unchanged.
- Do not rewrite persisted JSONL history.
- Do not restart a daemon during an active mission.
- Do not commit or push while Git identity is `lbx154`; wait for Storm72 identity and an explicit operator instruction.

---

### Task 1: Normalize mission outcomes

**Files:**
- Create: `argus_skill/life/mission_outcome.py`
- Modify: `argus_skill/life/supervisor/_mission_execution.py`
- Modify: `argus_skill/life/supervisor/_core.py`
- Modify: `argus_skill/core/event_payload_schemas.json`
- Regenerate: `frontend/core/src/eventPayloads.generated.ts`
- Test: `tests/life/test_mission_outcome.py`

**Interfaces:**
- Produces: `mission_outcome_class(status: str, success: bool) -> str`
- Produces event field: `outcome_class: completed|incomplete|stalled|blocked|failed|ended`

- [ ] Write parameterized tests covering `done`, research pause states, `no_progress`, blocked states, actual errors, and unknown legacy states.
- [ ] Run `pytest -q tests/life/test_mission_outcome.py` and confirm the helper is absent.
- [ ] Implement the pure status classifier and add `outcome_class` to every `life.mission.completed` emission path.
- [ ] Extend the event schema and regenerate TypeScript event payload types with `python scripts/generate_event_payload_types.py`.
- [ ] Re-run the focused Python test and schema-generation check.

### Task 2: Share truthful frontend rendering

**Files:**
- Create: `frontend/core/src/missionOutcome.ts`
- Modify: `frontend/core/src/index.ts`
- Modify: `frontend/core/src/missionView.ts`
- Modify: `frontend/web/src/lib/eventRender.ts`
- Modify: `frontend/tui/src/eventRender.ts`
- Test: `frontend/tui/test/missionView.test.ts`
- Test: `frontend/web/src/test/core.test.ts`
- Test: `frontend/web/src/test/eventRender.test.ts`
- Test: `frontend/tui/test/eventRender.test.ts`

**Interfaces:**
- Consumes event fields: `outcome_class`, `status`, `success`
- Produces: `missionOutcomePresentation(event): { outcomeClass, label, glyph, tone, missionStatus }`

- [ ] Add failing table-driven tests for completed, incomplete, stalled, blocked, failed, and unknown legacy outcomes.
- [ ] Verify `research_incomplete` currently renders as `Mission failed`.
- [ ] Implement a shared compatibility mapper that prefers `outcome_class` and derives it from legacy `status` without defaulting unknown states to failure.
- [ ] Replace the Web, TUI, and mission-view local success/failure branches with the shared mapper.
- [ ] Run focused Node/Vitest tests and TypeScript type checks.

### Task 3: Accept a strict last-line Engineer wait directive

**Files:**
- Modify: `argus_skill/engineer/background_subagents.py`
- Test: `tests/test_background_subagents.py`
- Test: `tests/test_runner_background_subagents.py`

**Interfaces:**
- Produces: `parse_wait_sentinel(message: str | None) -> str | None`
- Rule: the final non-empty line may be `WAIT_FOR_SUBAGENT: <task_id>`; earlier prose is ignored, and any text after the directive rejects it.

- [ ] Add failing tests for Summary/HANDOFF text followed by an exact final directive, prose after the directive, embedded prose mentions, code fences, and empty IDs.
- [ ] Run the focused parser tests and confirm the Summary/HANDOFF case fails.
- [ ] Change parsing to inspect only the final non-empty control line while retaining strict task-ID extraction.
- [ ] Add a runner integration test proving a summary plus final directive skips Reviewer and emits background-wait events.
- [ ] Run both background-subagent test modules.

### Task 4: Add structured Reviewer wait fallback

**Files:**
- Modify: `argus_skill/core/models.py`
- Modify: `argus_skill/reviewer/_core.py`
- Modify: `argus_skill/reviewer/_parsing.py`
- Modify: `argus_skill/reviewer/reviewer_schema.json`
- Modify: `argus_skill/reviewer/reviewer_research_schema.json`
- Modify: `argus_skill/engineer/runner.py`
- Test: `tests/test_reviewer_progress_class.py`
- Test: `tests/test_runner_background_subagents.py`

**Interfaces:**
- Produces ReviewDecision fields: `control_action: str`, `control_task_id: str`
- Structured JSON shape: `"control": {"action": "wait_for_subagent", "task_id": "..."}`

- [ ] Add failing schema/parser tests for valid structured wait control and malformed/unknown control.
- [ ] Add runner tests showing a healthy registered task triggers cadence wait after Reviewer, while unknown, stale, unhealthy, direct-mode, or terminal tasks emit `round.background_wait.rejected` and receive normal classification.
- [ ] Extend Reviewer instructions and schemas without parsing prose.
- [ ] Validate the Reviewer request through `find_waitable_subagent`; on success, pause the decision clock, reset no-output streak, preserve semantic-stall streak, and continue. On rejection, emit the explicit diagnostic event and classify normally.
- [ ] Run focused reviewer and background-wait tests.

### Task 5: Raise the semantic-stall threshold

**Files:**
- Modify: `argus_skill/engineer/runner.py`
- Test: `tests/test_semantic_stall_classify.py`

**Interfaces:**
- Produces default: `SupervisedConfig().stall_threshold == 4`

- [ ] Change the default expectation to 4 and add boundary tests: streak 3 continues, streak 4 returns `no_progress`.
- [ ] Run the tests and confirm they fail against the current threshold 2.
- [ ] Change only the default threshold to 4; retain environment/config overrides and existing progress-class semantics.
- [ ] Re-run semantic-stall tests.

### Task 6: Replay the production failure and run targeted regression

**Files:**
- Test: `tests/test_runner_background_subagents.py`
- Test: `frontend/tui/test/missionView.test.ts`

**Interfaces:**
- Consumes the verified production sequence: `setup_only -> evidence -> setup_only -> validated wait`.

- [ ] Add a deterministic replay proving the final validated wait does not become the second semantic stall and does not terminate the mission.
- [ ] Run the combined focused Python suite:

```bash
pytest -q \
  tests/life/test_mission_outcome.py \
  tests/test_background_subagents.py \
  tests/test_runner_background_subagents.py \
  tests/test_reviewer_progress_class.py \
  tests/test_semantic_stall_classify.py
```

- [ ] Run the frontend tests and type checks from their existing package scripts.
- [ ] Run the generated-event-payload check and the smallest existing life/supervisor suite covering all changed event emission paths.
- [ ] Inspect `git diff --check` and `git status --short`; leave all changes local and do not restart, commit, or push.
