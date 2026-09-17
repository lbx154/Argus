# Model decision boundary

Argus models decide semantics that require judgment. The Host owns facts and
state it can observe, derive, authorize, or persist itself.

## Contract

Do not require a model to reproduce strict JSON, key-value fields, or footer
fields for Host-owned or Host-derivable values. Examples include workspace
snapshots, stage order, persisted status or revision, timestamps and generated
identifiers, tool results, authorization state, and paths already resolved by
the runtime. The Host computes these values once and passes them as
authoritative context where a role needs them.

Keep four schema categories distinct:

- **Internal state schemas** type runtime objects and transitions. They are not
  model output formats.
- **Security schemas** validate credentials, permissions, tool isolation, and
  irreversible actions at the Host boundary. A model cannot satisfy or waive
  them by formatting text.
- **Authority schemas** preserve the operator goal, GoalContract, explicit
  review waiver, and other owner decisions. Only the owning actor may change
  them.
- **Persistence schemas** define backend-independent files and events. The Host
  writes and validates them; role prose is input to a decision, not persisted
  truth by itself.

Natural reasoning may carry a small actionable decision footer while the
runtime lacks a trusted typed decision channel. That is a compatibility
carrier, not evidence that every runtime field belongs in the footer.

## Scope of the Manager STAGES removal

Fresh candidate domains use the runtime-owned `("execute", "validate")`
lifecycle. Manager routing no longer requests, parses, validates, adapts, or
persists model-authored `STAGES`. A stray legacy `STAGES` line is ignored.
Existing project-domain stage order, Planner tasks, checklist state, and the
runtime stage machine remain authoritative. Legacy data-domain files remain
loadable. This change removes no other Manager field and changes no GoalContract,
review, dispatch, research, Web-error, or classification-telemetry contract.

## Remaining model-authored carriers

These carriers still represent irreducible choices or handoffs and must not be
described as already eliminated:

- **Manager routing:** choice, vertical/domain, workflow mode, confidence,
  rationale, execution task, research target/direction/venue, review policy,
  operator-stated constraints/exclusions/ambiguities, and optional Live View
  choice. Front-door classification still carries intake, configuration,
  control, authorization, route, self mode, reply, lifetime, greeting, and
  title fields.
- **Manager stage and pending-question decisions:** stage action/target/reason,
  wait resolution and optional Live View fields; pending-question answer and
  resolution booleans plus the role-clean decision or clarification reply.
- **Planner:** project-done/reason or plan reason, repeated task fields and
  dependencies, optional stage advance/wait fields, and optional plan update.
- **Engineer:** milestone status, material result, next owner, and optional
  operator question/options.
- **Reviewer:** status, reason, next action, forward-progress and plan signals,
  with optional plan challenge, authority impact, operator question/options,
  and research-result fields. Older Reviewer bookkeeping fields remain readable
  for compatibility but are not all requested.

Current parsers accept named lines and, where supported, assistant-authored
`ARGUS_ROLE_DECISION` payloads or legacy JSON. Those are still model-output
carriers, not trusted backend-independent events.

## Removing further carriers

Remove an irreducible choice from model text only after every supported backend
emits a Host-observed event that is role-bound, typed, tied to the correct call,
preserved across streaming and resume, and fails visibly when absent or invalid.
The event must not be reconstructible solely from assistant prose. Until that
channel exists and has cross-backend regression coverage, retain the smallest
necessary carrier and its fail-closed behavior.

## Regression

The captured restaurant reply may include this exact obsolete line without
causing a format retry:

```text
STAGES=1.明确经营目标；2.制定采购排班方案；3.执行开业准备；4.复盘优化
```

Run the regression and the built-in-route non-regression with:

```bash
uv run pytest -q \
  tests/manager/test_manager_new_domain.py::test_restaurant_stages_line_is_ignored_on_exact_empty_snapshot \
  tests/manager/test_manager_new_domain.py::test_new_domain_requires_tools_for_inaccessible_or_contradictory_snapshot \
  tests/manager/test_manager.py::test_builtin_repository_route_accepts_host_snapshot_without_tool_retry
```
