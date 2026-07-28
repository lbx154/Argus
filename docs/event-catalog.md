# Event Catalog

> Current protocol documentation. See
> [`DESIGN_AUTHORITY.md`](DESIGN_AUTHORITY.md) for precedence and historical
> document rules.

The catalog is the canonical **typed/frontend-semantic subset** of the event
stream. Define events here when a frontend branches on their meaning, when they
belong to the default signal trajectory, or when their payload needs validation.
Python definitions live in `argus_skill/core/event_catalog.py` and are mirrored
in `frontend/core/src/eventCatalog.ts`; a golden test requires both catalogs and
their signal/call-scoped groups to match.

Other diagnostic/vertical events may use the validated event-name grammar and
remain uncatalogued; frontends render them generically. Catalog membership must
not be used as an allowlist that drops otherwise valid history.

Typed payloads live in `argus_skill/core/event_payload_schemas.json`.
`scripts/generate_event_payload_types.py` generates the frontend discriminated
union in `frontend/core/src/eventPayloads.generated.ts`; CI checks that the
generated file is current.

## Envelope

New persisted events receive:

- `type`: canonical event name.
- `ts`: Unix timestamp.
- `event_schema_version`: envelope version, currently `1`.
- `payload_schema_version`: event-specific payload version when a typed schema
  exists.

Payload-specific versions remain separate. For example, `usage.recorded` uses
`schema_version: 2` while its generic event envelope uses
`event_schema_version: 1`.

Known catalog events can declare required fields. The persistent sink remains
fail-soft: an invalid event is retained with:

```json
{
  "event_validation": {
    "status": "invalid",
    "errors": ["missing required fields: call_id"]
  }
}
```

This makes producer defects observable without losing the surrounding timeline
or crashing a daemon. Frontend guardian logic surfaces these rows as warnings.

## Extension rules

1. Add events with frontend-specific semantics, signal membership, or typed
   payloads to both catalogs.
2. Use the catalog constant at production and consumption sites.
3. Add required fields only when every valid producer can provide them.
4. Keep vertical-local events extensible. Unknown names that follow the event
   naming grammar remain valid.
5. Add old names to `LEGACY_EVENT_ALIASES`; do not silently rewrite historical
   rows. Normalized rows expose `canonical_type` beside the original `type`.
6. Mark an event as signal only when it is useful in the default durable
   trajectory. Debug chatter belongs in full verbosity.

## Project completion lifecycle

- `project.completed`: `core/project_api.py::complete_project` accepted a
  completion source whose strength satisfies the active vertical's declared
  gate and atomically moved the Project lifecycle to DONE.
- `project.completion_refused`: the proposed source, evidence, or gate was
  insufficient, so no DONE write occurred.

Both are typed cross-component signal events. A Planner `project_done` verdict
by itself is not `project.completed`.

## Plan revision lifecycle

The current plan-revision trigger is a Reviewer verdict with
`status=replan_requested`, persisted in `round.review.completed` and the mission
outcome. LifeSupervisor then asks L4 to replace the remaining active plan nodes.

The correlated durable events are:

- `life.plan.revision.proposed`: L4 was asked to replace one active plan
  revision.
- `life.plan.revision.rejected`: no replacement was committed; the event
  identifies the expected plan/version and records why the old plan was kept.
- `life.plan.node.superseded`: one old active node became the immutable
  `superseded` terminal state.
- `life.plan.revision.committed`: the single locked backlog rewrite completed,
  with old/new plan identity and exact superseded/added item IDs.

Every `proposed` event must resolve to either `rejected` or `committed`.

`life.plan.signal` remains in the cross-version catalog so historical rows and
older frontends can name it, but current code has no producer. There is no
`off|shadow|active` Dynamic Plan mode, signal streak, or confirmation threshold.
