# Argus Runtime Protocols

Argus components can run from different checkouts. Compatibility is explicit;
clients must not infer it from an HTTP 200 response or from the package version.

## WebAPI handshake

`GET /api/meta` returns:

- `service`: stable service identity.
- `protocol`: protocol name plus major/minor version.
- `snapshot_schema_version`: exact snapshot schema consumed by current clients.
- `capabilities`: feature-level contracts required by the TUI and Web UI.
- `runtime`: loaded source root, configured source root, Git revision, PID, Python
  executable, and process start time.

The endpoint is unauthenticated so a client can distinguish an incompatible
service from an unreachable port. When Web auth is enabled, filesystem paths
are redacted unless the request carries the correct bearer token.

Compatibility policy:

1. Protocol name and major version must match.
2. Server minor version must satisfy the client's minimum.
3. Every client-required capability must be present.
4. Snapshot schema versions must match exactly.
5. If `ARGUS_SKILL_SOURCE_ROOT` is configured, it must match the source root
   that actually loaded the WebAPI.

A breaking wire change increments the major version. An additive feature adds a
capability and may increment the minor version. A changed snapshot shape always
increments `snapshot_schema_version`.

## Snapshot contract

Snapshots include `schema_version`, `partial`, and `diagnostics`. Fail-soft reads
must preserve the complete field shape and append a diagnostic; they must not
silently omit fields or substitute a misleading zero.

Both frontends validate the snapshot at runtime. Missing budget, usage, request,
or diagnostic fields are treated as protocol errors even when TypeScript types
would otherwise accept the JSON.

## Daemon status protocol

Each running daemon writes protocol, capability, and runtime identity metadata
to `daemon.status.json`. The WebAPI reports `protocol_compatible` and
`protocol_error` in the snapshot's daemon section.

A running legacy daemon with no protocol metadata is incompatible until it is
restarted from the current checkout. Stopped sessions have no daemon protocol
requirement because there is no active process to coordinate with.
