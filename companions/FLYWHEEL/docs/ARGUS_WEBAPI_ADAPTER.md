# Argus WebAPI adapter contract

Flywheel treats Argus as the research engine and talks to it over its public
WebAPI. It does not import Argus internals, start a daemon during connection
tests, or read an Argus worktree directly.

## Audited baseline

The adapter is aligned with the checked-in Argus protocol declaration:

- protocol: `argus.webapi/1.13`
- snapshot schema: `7`
- launch-critical capabilities: `daemon.admission.v1`, `mission.view.v1`,
  `research.events.v1`
- optional, feature-detected capabilities: `daemon.command.v1`,
  `snapshot.schema.v1`, `snapshot.budget.v1`, `usage.recorded.v2`,
  `manager.sse.v1`

An optional capability never makes an otherwise compatible target fail its
launch probe. The complete capability list, explicit feature booleans, and the
snapshot schema/budget/usage contract are persisted in connection metadata.
Callers can use `ConnectionTest.supports_feature(...)` before exposing an
optional control.

## Operator decisions

New decision cards use the typed WebAPI route:

```text
POST /api/projects/{sid}/decisions/{decision_id}/resolve
{"option_id": "...", "note": "..."}
```

Older pending-question cards remain supported through:

```text
POST /api/projects/{sid}/backlog/{item_id}/answer
{"text": "..."}
```

Flywheel keeps these as separate methods so a free-form legacy answer cannot
accidentally be sent as a typed option selection.

## Event cursor contract

Argus 1.13 provides a bounded ordered event tail, but it does not provide a
server-side cursor for `/events`. `poll_events` therefore stores ordered
SHA-256 fingerprints and computes the longest overlap between consecutive tail
windows. This preserves legitimate identical adjacent events. If a later
window has no overlap, `gap_detected` is true and all available rows are
returned; the data pipeline must retain them and record the possible gap.

`manager.sse.v1` is recorded as an optional capability, but is not represented
as a research-event WebSocket. Flywheel deliberately uses the reliable polling
contract until Argus advertises an appropriate streaming cursor contract.

## Raw artifact safety

Raw artifact access uses only Argus' allowlisted endpoint:

```text
GET /api/projects/{sid}/artifact/raw?path=...&download=true
```

The client additionally requires a normalized relative path, percent-encodes
it as a query value, refuses redirects, and never embeds credentials in URLs.
Remote bearer authentication requires HTTPS. Tokens containing control
characters are rejected.

Downloads default to 32 MiB per artifact and 128 MiB per batch. Both limits can
be lowered per operation and deliberately cannot be raised above the configured
client ceilings. Network reads are chunked, the limit is enforced even without
`Content-Length`, and SHA-256 is updated as chunks arrive. `artifact_digest`
does not retain content; `download_artifact` and `download_artifacts` return
bytes plus the same integrity receipt.
