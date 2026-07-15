# Manager-to-Kernel Continuity

**Date:** 2026-07-15

## Problem

A Web conversation can accept a TEAM task without visibly or operationally
starting execution:

- a project whose lifecycle is already `done` still accepts new backlog items;
- the supervisor then refuses to consume them because `done` is non-allocatable;
- the daemon eventually exits on idle timeout;
- successful TEAM dispatch returns `reply: None`, leaving no durable assistant
  response in the conversation.

The observed session `s-dd7b46db` had nine pending tasks, lifecycle `done`, no
daemon after idle timeout, and an operator `继续` turn with no Argus turn.

## Lifecycle Rule

When and only when the Manager has classified an operator message as a TEAM
task, dispatch checks the persisted lifecycle before enqueueing:

- `done` is automatically resumed through the existing project-lifecycle
  transition API with reason `manager_team_dispatch`;
- allocatable states are left unchanged;
- `quarantined` and `archived` remain blocked and require explicit operator
  action;
- chat, SELF, config, status, and no-dispatch messages never alter lifecycle.

The resume operation must use existing lifecycle inference, event history, and
atomic persistence rather than rewriting `lifecycle.json` directly.

## Durable Dispatch Acknowledgement

After TEAM classification and daemon start/admission:

- append one Argus transcript turn describing the real state;
- emit the same text as an SSE delta before the final `done` frame;
- set `result.reply` to that acknowledgement.

Acknowledgements distinguish:

- executor started;
- executor already running;
- task queued but waiting for daemon capacity;
- executor failed to start.

The acknowledgement must not claim execution started when admission or startup
failed. The blocking `/message` endpoint and streaming `/message/stream`
endpoint must produce equivalent durable outcomes.

## Web Behavior

- Successful TEAM dispatch appears in the conversation, not only as a toast.
- Admission and startup failures remain error notices and also appear in the
  durable transcript.
- The transcript is refetched after the final stream frame.
- Plain chat behavior and Manager streaming remain unchanged.

## Error Handling

- Lifecycle read/persist failure prevents enqueue and returns an explicit error.
- No broad fallback silently converts lifecycle failure into successful dispatch.
- Transcript persistence failure is surfaced in the dispatch result; it must
  not fabricate a durable acknowledgement.
- Existing provider/auth failures remain visible through backlog and activity
  evidence; this change does not hide them.

## Tests and Acceptance

1. A TEAM task in a `done` project transitions lifecycle to an allocatable state,
   enqueues, starts the daemon, and records reason `manager_team_dispatch`.
2. Chat/SELF messages in a `done` project do not resume it.
3. Quarantined and archived projects are not auto-resumed.
4. Successful, already-running, admission-required, and startup-failed task
   results each produce accurate durable acknowledgements.
5. Streaming and blocking endpoints have equivalent task acknowledgement and
   transcript behavior.
6. The original `done + pending backlog + idle daemon` reproduction no longer
   leaves a newly accepted TEAM task unconsumed without explanation.
