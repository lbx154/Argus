Trimmed from the same trial. Task t4 worked in its own git worktree, cherry-picked its commit into the
workspace, deleted the worktree (the task's to decide), and handed its work to its Reviewer. Argus still
started the Reviewer in the deleted folder: the spawn failed with ENOENT (reported as `spawn bwrap`), and
the turn was not retried.
