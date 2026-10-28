# Making the change

Use this when the Engineer makes a software change.

1. Before editing, read the task, the files it names, their tests and the project's own
   instructions (`AGENTS.md`, `CONTRIBUTING`, the CI configuration). Find the closest existing code
   that does something similar, and every caller of what will change.
2. Find out what the project and the machine have without a command that fails:

   ```bash
   if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git status --short; else echo "not a git worktree"; fi
   command -v rg || echo "no rg: use grep"
   ```

   A plain directory is a valid workspace: name the files you read and say there is no baseline
   diff, rather than treating the missing git history as a failure.
3. Make the smallest change that does what was asked. Keep public interfaces, and the behaviour
   existing tests cover, unless the task changes them.
4. Build it, run the cheapest decisive check the task or the project gives, then the tests around
   the change. Add a probe of your own only where the existing tests leave a boundary the task
   names untested.
5. When the acceptance tests are held back, write a temporary probe of every public signature and
   boundary case the task asks for, run it, and then delete only that probe. Its expected values
   must not come from your patch: take them from the task text, the unchanged callers, the closest
   similar code or the project's conventions, never from the patch's own return shape, ordering,
   defaults or field mapping. Try to break exact return types, argument and command order, zero
   and default values, the complete field mapping, invalid inputs and boundary values.
6. When the build and the decisive check pass, hand the work to the Reviewer with the commands you
   ran and the output lines that show the result. Spend no turns on unrelated scans, cache cleanup
   or formatting churn.
