# Bounded Project Index Design

## Problem

The Ink TUI crashed in V8 `JSON.parse` after requesting `GET /api/projects`.
That endpoint returned each session's complete objective and also reused the
same unbounded text as its label. The TUI therefore had to materialize the
whole project history index before it could render or truncate any row.

The 8921 WebAPI remained alive during the crash. An isolated reproduction with
40 three-megabyte objectives exhausted a 192 MiB Node heap while parsing the
project index, confirming that the failure is in the CLI/TUI data path rather
than the daemon backend.

## Design

Keep full objectives authoritative in per-project snapshot and detail
endpoints. Make `GET /api/projects` a bounded summary:

- `display_name`: at most 180 characters.
- `label`: a single-line value of at most 180 characters.
- `objective`: a search summary of at most 1,000 characters.
- Never duplicate a complete objective into `label`.

Apply these bounds in `project_state.list_projects`, before FastAPI serializes
the response. Client-side truncation is insufficient because the OOM occurs
during response decoding.

No pagination or protocol revision is needed. The endpoint already caps the
row count, and existing consumers only use objective text for project search
and selection.

## Validation

Add a WebAPI regression using a multi-megabyte session objective and assert
that the project index fields and serialized response stay bounded while the
full objective remains available from the project snapshot. Run the related
WebAPI large-history/event tests and the TUI project, panel, protocol, and
stream tests.

Before integration, fetch and rebase onto the latest `origin/main`, rerun the
targeted regressions, and push the repair commit. Deploy only the 8921 WebAPI;
do not restart project daemons or touch 8799.
