---
name: "Software Change Implementation"
description: "Implement a bounded software change with proportional inspection, repository-aware tooling, and decisive verification; for staged work, decompose by architecture boundaries with each task's compatibility risks and independent oracle named."
---

# Software Change Implementation

## Method

1. Read the operator task, named implementation files, tests, and repository instructions before editing.
2. Detect repository capabilities before invoking them. Keep capability probes
	 non-failing, for example:

	 ```bash
	 if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		 git status --short
	 else
		 echo "not a Git worktree; inspect named files directly"
	 fi
	 ```

	 A plain directory is a valid software workspace; do not emit a failed Git
	 command merely to discover that fact.
3. Make the smallest change that satisfies the stated contract. Preserve public interfaces and unchanged behavior covered by existing tests.
4. Run the cheapest decisive check the task or repository provides. Add a focused probe only when existing coverage leaves a material boundary untested.
5. Once the requested files exist and the decisive check passes, write the checkpoint and finish. Do not spend extra turns on unrelated repository scans, cache cleanup, or formatting churn.

## When the work is staged

Staged software work is decomposed by independently verifiable architecture
boundaries, not by prose sections or file counts: each task should be checkable
on its own against the repository's real behavior, and a plan whose tasks can
each pass while the integrated public interface stays incompatible has divided
the work in the wrong place. Before scheduling implementation, identify the
independent oracle for each task (the unchanged callers, the closest sibling
implementation, the existing tests, or the narrow command that can falsify the
change) and verify it with a bounded inspection rather than trusting a summary.
Put the exact compatibility risks into each task's objective: return types,
field mappings, ordering, invalid inputs, boundary values, and platform or user
semantics, since these are what held-back tests probe and what a task that only
"implements the feature" forgets. Give each task the relevant file paths, the
closest analogue, the behavioral contract and the falsifying command, and keep
shared setup and verification dependencies explicit so nobody rediscovers the
project from scratch in every task; do not include opaque hashes or a copy of
the whole repository survey.

## Evidence

Report exact commands and outcomes. In a non-Git workspace, name the inspected files and state that no baseline diff is available; do not treat missing Git metadata as a task failure.
