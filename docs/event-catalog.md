# Event Catalog

Cross-component event names are protocol. Define them in
`argus_skill/core/event_catalog.py` and mirror them in
`frontend/core/src/eventCatalog.ts`. A golden test requires both catalogs and
their signal/call-scoped groups to match.

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

1. Add cross-process or frontend-visible events to both catalogs.
2. Use the catalog constant at production and consumption sites.
3. Add required fields only when every valid producer can provide them.
4. Keep vertical-local events extensible. Unknown names that follow the event
   naming grammar remain valid.
5. Add old names to `LEGACY_EVENT_ALIASES`; do not silently rewrite historical
   rows. Normalized rows expose `canonical_type` beside the original `type`.
6. Mark an event as signal only when it is useful in the default durable
   trajectory. Debug chatter belongs in full verbosity.

## Dynamic Plan lifecycle

Dynamic Plan uses correlated durable events:

- `life.plan.signal`: Reviewer-authored `reconsider` observation, including
  mode, consecutive-signal count, confirmation threshold, reason, and bounded
  evidence-file references.
- `life.plan.revision.proposed`: L4 was asked to replace one active plan
  revision.
- `life.plan.revision.rejected`: no replacement was committed; the event
  identifies the expected plan/version and records why the old plan was kept.
- `life.plan.node.superseded`: one old active node became the immutable
  `superseded` terminal state.
- `life.plan.revision.committed`: the single locked backlog rewrite completed,
  with old/new plan identity and exact superseded/added item IDs.

Every `proposed` event must resolve to either `rejected` or `committed`.
`shadow` mode emits only `life.plan.signal` and cannot change mission or backlog
state.
