# Contributing

## Branches

This policy applies to `lbx154/Argus`.

- `main` is the stable source branch and remains the repository default.
- `dev` is the development integration branch. Start new work from `dev` and
  target development pull requests at `dev`.
- Promote reviewed changes with their relevant validation results from `dev`
  to `main`. Creating the branch does not establish that every platform passes.
- Short-lived feature branches are removed after merge. Keep `main` and `dev`.
- Desktop releases still use explicitly authorized version tags. Merging to
  `main` does not publish an installer or change a running service.

Both long-lived branches run the existing CI. Branch organization adds no
separate release validation workflow.

## TypeScript runtime

The root npm workspace contains the shared contracts, experimental runtime and
read API packages. With Node 22.12+, run `npm ci` and `npm run check`. Web and TUI retain
their own install/build commands. Contract edits require
`npm run contracts:generate`; generated files are checked by CI.
Build with `npm run build` before running Python tests to include the real
Node/Python read API integration tests.

See [the migration guide](docs/typescript-migration.md) for supported behaviour,
Python compatibility, state ownership and the remaining migration steps.

## Archived work

The September 14, 2026 cleanup preserved unmerged remote branch tips under
`archive/2026-09-14/branches/<old-branch>` and open PR heads under
`archive/2026-09-14/pr-<number>`. These tags preserve work; they do not indicate
that the changes were accepted into `main` or `dev`.

To inspect an archived branch in a separate worktree:

```bash
git fetch origin --tags
git worktree add --detach ../argus-archived-work \
  'refs/tags/archive/2026-09-14/branches/<old-branch>'
```

For new development, start from current `dev` and bring across only the changes
you intend to finish. The archived PR discussions remain available on GitHub.
