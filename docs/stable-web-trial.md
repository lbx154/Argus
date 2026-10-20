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

## Results and downloads

The first-use examples prepare editable drafts; they do not submit work.
Conversation, map and workbench keep their existing visual style and separate
responsibilities. Opening Files shows the registered files first, not execution
logs. Revise brings a specific result back into the conversation, appends to
any unsent text and waits for the user to send.

Completed direct replies and registered reports expose their explicitly linked,
existing workspace files. Report-relative links work in both the side preview
and delivery modal. Numeric model files, ZIP archives, and patches can be
downloaded through the authenticated artifact API. Inline report images use the
same authenticated route. Unknown files, credentials, parent traversal and
symlinks outside the workspace remain excluded.

Transcript and live reply identities are shared so a result appears once after
replay. Legacy receipts can be matched by their durable delivery identity.

## Browser continuity and project organization

Text drafts are saved per project in this browser's local storage. They are
not a server backup or encrypted storage: clear drafts on a shared device
before handing it over. File bytes are never stored there or uploaded by
selecting a file. After reload, the interface names unuploaded files that must
be selected again or explicitly discarded before sending. Storage failures
remain visible, and asynchronous replies cannot overwrite a newer draft.
Conversation reading position and file selection are also project-specific.

Pins, manual follow-up markers and archives organize only this browser's
project list. Archive does not delete files, stop a worker or change server
permissions. Archived projects can be restored. Follow-up is an explicit user
marker, not a claim about unobserved background tasks.

Knowledge & skills opens on Learning feed, followed by Across projects,
Vertical and Project. Explicit links to a document still select its own scope.

Use in conversation adds an explicitly selected, loaded knowledge/skill
document to the attachment draft with its source scope. Sending copies that
content to the current project; the original library's scope is unchanged.
Private-source documents warn about this copy before sending. This action
does not execute a skill or assert that a model has already read the material.
Capability descriptions remain separate from reported environment readiness;
deployment settings are expandable and retain the server's allowed actions,
license consent and failure details.

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
