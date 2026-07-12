# Metrics and SLOs

Argus appends durable operational measurements to `metrics.jsonl` under the
shared runtime root. Metrics cover:

- provider calls: status, pricing status, duration, cost, and token totals;
- daemon commands: operation and terminal ACK status;
- WebAPI requests: method, path, status, and duration;
- persisted event payload validation failures;
- live cost reservations and unresolved pricing.

`GET /api/metrics` returns the current local-day aggregate. `GET /metrics`
renders the same state in Prometheus text format.

Current SLO checks are intentionally conservative:

- provider success rate at least 95% once five attempted calls exist;
- daemon command success rate at least 98% once three attempts exist;
- WebAPI 5xx rate at most 1%;
- zero event validation failures;
- zero unresolved cost calls.

Snapshots include the SLO verdict. TUI and Web surfaces display `SLO degraded`
with the concrete violations instead of requiring an operator to inspect raw
logs.

Deployment tests use real OS processes to verify cross-process budget locking,
daemon-command idempotency, and a live Uvicorn WebAPI exposing matching release,
protocol, project, JSON metrics, and Prometheus endpoints.
