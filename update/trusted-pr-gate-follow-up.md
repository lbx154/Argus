# Trusted PR Gate Follow-up

## Problem

The current `pull_request` workflow checks out the PR merge tree and executes
the gate implementation and configuration from that tree. A pull request can
therefore modify the checker, disable its criteria, or otherwise make its own
required check pass.

Read-only workflow permissions and `persist-credentials: false` reduce token
exposure, but they do not make the result trustworthy when the checked code
controls the checker.

## Planned update

After the initial PR gate has been merged into `main`:

1. Change the workflow trigger to `pull_request_target`.
2. Load the workflow, gate implementation, and configuration from the trusted
   base revision.
3. Fetch the pull request head only as diff input.
4. Calculate patch statistics from the merge base to the PR head without
   checking out or executing PR-provided code.
5. Set `persist-credentials: false` and retain read-only permissions.
6. Fail closed if required criteria are absent, invalid, or all disabled.

## Timing

This should be implemented after PR #69 is merged because the current `main`
branch does not yet contain the gate. A `pull_request_target` workflow is loaded
from the base branch, so switching the initial PR prematurely would prevent it
from exercising the new trusted implementation.
