# Changing a software project: a change that builds, whose tests ran, and that does what was asked

This is how an Argus team changes software: a fix, a feature, a refactor, an optimization. It is
advice, not a procedure Argus enforces: the Planner divides the work by it, the Engineer works by
it, and the Reviewer accepts work against its "done when". Beside it, `skills/change.md` is how to
make the change and `skills/review.md` how to judge it.

## Why the Reviewer builds the change itself

The most common and most expensive failure of a software change is code that does not build, and
a Reviewer that reads only the Engineer's report cannot see it. The Engineer runs the tests it can
see; when CI, a benchmark or a colleague later compiles the change against code the Engineer never
ran, a name or signature it never reconciled is a build error, and every test in the package fails
at once. Building the change and watching its tests run is cheap and catches that outright,
so the Reviewer does both, on the current files, every time it judges.

## How the work goes

A small change is one task: the Engineer makes and checks it, the Reviewer judges it. Larger work
is divided at boundaries that can each be checked on their own against the project's real
behaviour, not by files or sections: a plan whose tasks each pass while the interface they make
together is broken divided the work in the wrong place. Each task's brief names the files, the
closest existing code that does something similar, the behaviour to keep, the command that would
show the change is wrong, and the risks a task that only "implements the feature" forgets: return
types, field mappings, ordering, defaults, invalid inputs and boundary values. Shared setup is
named once, so no task rediscovers the project from scratch.

For optimization work, first measure a baseline on the task's own workload, and report a
like-for-like before and after: the improvement is the goal, deleting code only a means to it.
Replace a superseded path rather than keeping old and new side by side, and never change a product
interface only to make it testable.

## Done when

- **It builds.** The changed code compiles or type-checks cleanly with the project's own toolchain,
  in a build the Reviewer ran on the current files. A build error anywhere the change reaches (an
  undefined name, a wrong argument count, a mismatched type, an unused import the toolchain
  rejects) fails the change however right its logic reads.
- **Its tests ran.** The tests said to pass ran on the current files and passed. A test target
  that did not build, collected no tests or never ran counts as no test. When the acceptance tests
  are held back, the project's own tests passing is not enough: the change also passes a probe of
  every public signature and boundary case the task asks for (`skills/change.md`).
- **It does what was asked.** The requested behaviour is in the production code, every caller of
  what changed still works with it, and unrelated work in the checkout is untouched.

## What the Reviewer runs

The commands the project's CI runs are the ones the change will be judged by: look in
`.github/workflows/` or the CI file the project has, then its `Makefile`, `package.json` scripts,
`pyproject.toml`, `tox.ini` or `CONTRIBUTING`. Where the project names none, use its toolchain's:

| Project | Builds or type-checks | Runs the tests | The tests ran when the output shows |
|---|---|---|---|
| Node, TypeScript | `npx tsc --noEmit`, or the `build` script | `npm test` | the runner's count of passed tests, above zero, and no failure |
| Python | `python -m compileall -q .`, importing the changed modules, and the type checker or linter the project configures | `python -m pytest -q` | `N passed`; exit code 5 means no test was collected |
| Go | `go build ./... && go vet ./...` | `go test ./...` | `ok` for each changed package; `[no test files]` is no test |
| Rust | `cargo check --all-targets` | `cargo test` | `test result: ok. N passed` |
| Java | `mvn -q compile`, or `./gradlew compileJava` | `mvn test`, or `./gradlew test` | `Tests run: N, Failures: 0, Errors: 0` |
| C, C++ with CMake | `cmake --build build` | `ctest --test-dir build` | `100% tests passed, 0 tests failed out of N` |

The workspace is read-only for the Reviewer, so a build that writes into it (`target/`, `build/`,
`__pycache__`) runs on a copy: `cp -a . /tmp/ws && cd /tmp/ws`. The Engineer's letter gives the
commands it ran and the output lines that show the result, with long logs in `.argus/evidence/`;
the Reviewer's own run decides.

This way has no check script: how a project builds and tests differs from one project to the next,
and a script that guessed would sometimes run the wrong command and report a pass nobody earned.
