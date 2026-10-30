---
name: software
description: "Changing an existing software codebase with a build toolchain and tests"
---

## Why the Reviewer builds the change itself

The most common and most expensive failure of a software change is code that does not build, and
a Reviewer that reads only the Engineer's report cannot see it. The Engineer runs the tests it can
see; when CI, a benchmark or a colleague later compiles the change against code the Engineer never
ran, a name or signature it never reconciled is a build error, and every test in the package fails
at once. Building the change and watching its tests run is cheap and catches that outright, so the
Reviewer does both on the current files.

## What counts as done

- **It builds** with the project's own toolchain, in a build the Reviewer ran on the current files.
  A build error anywhere the change reaches fails it however right its logic reads.
- **Its tests ran** on the current files and passed. A test target that did not build, collected
  no tests or never ran counts as no test.
- **It does what was asked**, in the production code, every caller of what changed still works
  with it, and unrelated work in the checkout is untouched.

When the acceptance tests are held back, the project's own tests passing is not enough: probe every
public signature and boundary case the task names, with expected values taken from the task text,
the unchanged callers or the closest similar code, never from the patch's own return shape,
ordering, defaults or field mapping. A branch, field, alias or ordering the closest similar code has
and the patch lacks is a failure even when the happy path passes, and a probe of a lower-level
function does not show that the wrapper, CLI or serialization above it works. For optimization,
measure a baseline on the task's own workload and report a like-for-like before and after.

## Tasks side by side in one repository

Each works on its own branch, in a git worktree under a visible folder of the workspace: files in a
hidden one cannot be handed over. Merging them is a task of its own that waits for them (`after`),
and removes their worktrees and branches once merged.

## The commands

The commands the project's CI runs are the ones the change will be judged by: look in
`.github/workflows/` or the CI file the project has, then its `Makefile`, `package.json` scripts,
`pyproject.toml`, `tox.ini` or `CONTRIBUTING`.

The Reviewer's workspace is read-only, so a build that writes into it (`target/`, `build/`,
`__pycache__`) runs on a copy: `cp -a . /tmp/ws && cd /tmp/ws`.
