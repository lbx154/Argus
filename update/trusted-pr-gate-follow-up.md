# Trusted PR Gate Follow-up

## Problem

The current `pull_request` workflow checks out the PR merge tree and executes
the gate implementation and configuration from that tree. A pull request can
therefore modify the checker, disable its criteria, or otherwise make its own
required check pass.

Read-only workflow permissions and `persist-credentials: false` reduce token
exposure, but they do not make the result trustworthy when the checked code
controls the checker.

## Two-step migration

PR #69 bootstraps the gate implementation. It must be merged first because a
`pull_request_target` workflow is loaded from the trusted base branch, and the
current `main` branch does not yet contain the gate implementation or
configuration.

The migration is therefore:

1. Merge PR #69 so that `main` contains the reviewed gate implementation and
   configuration.
2. In an immediate follow-up change, switch the workflow trigger to
   `pull_request_target`.
3. For every later pull request, load and execute the workflow, gate
   implementation, and configuration from the trusted base revision.
4. Fetch the pull request head only as diff input.
5. Calculate patch statistics from the merge base to the PR head without
   checking out or executing PR-provided code.
6. Keep `persist-credentials: false`, retain read-only permissions, and fail
   closed if the trusted gate cannot run.

After the follow-up is merged, future pull requests will not be able to modify
the checker or configuration used to evaluate themselves.
