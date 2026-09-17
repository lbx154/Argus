# Stable web trial

The trial uses the same mission map and project workspaces as the normal WebAPI.
Deploy a fixed, tested commit and its checked-in `frontend/web/dist` together.
Keep the existing state directory and access token when updating an established
preview; back up both its launcher and state before restarting it.

## Execution and page refresh

An accepted foreground request belongs to the server. Refreshing or closing the
page detaches its stream and does not cancel the work. The project snapshot
includes active request identities so another page can recover the Stop action
without submitting the task again. Explicit request cancellation and daemon
stop remain authoritative. Server restart still interrupts foreground work;
perform deployment after active requests finish.

Direct execution records completion even when it verifies existing outputs or
makes a code change without a new report. A successful provider response that
contains only prose and no observed tool action gets one bounded recovery
attempt. If it still performs no action, it is reported as incomplete. This
recovery is not used after observed tool activity or a provider failure.

After a reviewed finite task completes with no remaining work, the Manager does
not start another background steering call during daemon shutdown. Continuous
campaigns, pending tasks, and issued decisions awaiting delivery retain their
normal supervision.

## Status questions

Simple status questions use one classification call, followed by a native read
of the requested scope: the current project, projects in this Argus instance,
or host activity. The native reply distinguishes a live WebAPI and foreground
requests from each project's background worker. Unreadable or unobserved scopes
remain unknown; an empty project never establishes that the server is idle.
Host summaries read resource totals and process names for the service account,
without reading command arguments, environment variables, or other accounts'
process details. These queries create no tasks or background learning calls.

## Concurrency

Research candidate count and worker concurrency are independent. A twelve-route
idea portfolio uses the configured pool width instead of launching twelve
workers at once. Defaults are two teammates per pool and a total of two live
teammates per daemon:

```text
ARGUS_TEAM_DEFAULT_WIDTH=2
ARGUS_TEAM_MAX_TOTAL_IN_FLIGHT=2
```

These are teammate limits; provider request limits have their own settings.
Existing explicit operator settings remain supported. Nested Teams remain
subject to the existing disabled-by-default formation policy.

## Results and downloads

Completed direct replies and registered reports expose their explicitly linked,
existing workspace files. Report-relative links work in both the side preview
and delivery modal. Numeric model files, ZIP archives, and patches can be
downloaded through the authenticated artifact API. Inline report images use the
same authenticated route. Unknown files, credentials, parent traversal and
symlinks outside the workspace remain excluded.

Transcript and live reply identities are shared so a result appears once after
replay. Legacy receipts can be matched by their durable delivery identity.

## Deployment acceptance

Before changing the public service, verify the Python checks, frontend tests and
production build, then use a real browser to exercise:

- A new project, selection, reload, and browser navigation.
- A task that survives reload, exposes Stop, and actually stops on request.
- A completed direct task with and without a new-file receipt.
- A real backend task that writes files and verifies their contents.
- Report-relative links, an authenticated binary download, and image preview.
- The map, conversation, file panel and skills library at desktop and mobile sizes.

Keep the exact tested revision, backend revision, browser evidence, checks and
rollback command in the operator's deployment record. Test fixtures must be
identified separately from real model execution.
