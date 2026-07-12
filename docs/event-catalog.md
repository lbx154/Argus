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
