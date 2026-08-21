---
name: "Software Change Implementation"
description: "Implement a bounded software change with proportional inspection, repository-aware tooling, and decisive verification."
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
4. Run the cheapest decisive acceptance command from the task or repository. Add a focused probe only when existing coverage leaves a material boundary untested.
5. Once the requested artifacts exist and acceptance passes, write the checkpoint and hand off. Do not spend extra turns on unrelated repository scans, cache cleanup, or formatting churn.

## Evidence

Report exact commands and outcomes. In a non-Git workspace, name the inspected files and state that no baseline diff is available; do not treat missing Git metadata as a task failure.
