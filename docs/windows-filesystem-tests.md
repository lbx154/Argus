# Filesystem tests without elevated Windows privileges

These checks implement Principles 1 and 8: the runtime keeps file boundaries; a working
installation must not depend on administrator privileges or developer mode. Test the
capability rather than silently skipping Windows or weakening the product boundary.

## Real links, with the smallest capability gate

`packages/core/test/helpers/fsLinks.ts` has two explicit contracts:

- `await directoryLink(target, path)` creates a real directory junction on Windows and a
  directory symlink elsewhere. Give it a target resolved from the fixture directory, not
  relative to the link. Failure fails the test. The tests exercise visible in-root aliases,
  hidden targets, outside targets, broken targets and cycles without following them in trees.
- `if (!await fileLink(t, target, path)) return` belongs inside an awaited **file-link-only
  subtest**. Only Windows `EPERM` skips that subtest, with the reason in the test report.
  Never use it to skip uploads, tree traversal, previews, validation or the parent test.
  Directory-link checks still run without privilege. Linux executes the file-link assertions.

A file-link fixture failing at `symlink` says nothing about whether HTTP file handling works.
Keep the rest running. A test program whose absence proves that a reader ran no configured
program must itself be runnable: use Node, quote its paths, and run a positive control before
testing the reader. Construct Git file URLs with `pathToFileURL`, not `file://${path}`.

## Names and paths

`filesystem.test.ts` uses real HTTP and native I/O: project creation, Chinese/space and
long paths, dot segments, text/raw results, HTML/assets, zips, attachments and trash/restore.
Windows additionally exercises backslashes, drive casing, extended-length paths, another
local drive when the workspace and native temp roots differ, and an occupied state file.
`worktrees.test.ts` exercises actual Git worktree creation through a directory alias.

Node's Windows extended paths can create names such as `NUL.txt`; their successful creation
is **not** evidence that normal Win32 applications can use them. `portableFilename` adds
`_` to reserved device names on Windows, shared by attachments and zip downloads.
POSIX keeps its existing legal names (for example `NUL.txt` and `CON.zip`); both platforms
have actual upload/download I/O regressions. The original attachment name is retained
separately. The official list includes superscript digits in `COM¹`/`LPT²`, not just ASCII 1–9.

An occupied `project.json` is held with Windows `FileShare.Read`, not a mock. The existing
atomic-replace policy (Windows busy errors retried every 20 ms, up to 2 s) is unchanged.
A persistent lock then fails visibly, leaves the previous JSON readable, and the HTTP API
reports the error. The next explicit save after releasing the handle succeeds; there is no
unlink-before-rename fallback. Native `realpath`/`relative` checks remain in place.

## Built/installed entry point

After building, run the reusable, model-free smoke against the actual bundle:

```sh
node packages/core/test/fixtures/filesystem-smoke.mjs packages/core/dist/argus.mjs /absolute/scratch-parent
```

The fixture allocates a new `ARGUS_HOME` **and** `ARGUS_SHARED`, opens the built cockpit,
uses real HTTP, checks downloaded zip contents and CRCs, then ends the CLI. It prints its
unique evidence root and keeps stdout/stderr there. It never uses the operator's state root.
The same fixture can check an installed bundle by passing that bundle's `argus.mjs` path.
Run it in one foreground call that returns when it ends.

## Limits on claims

Passing a local long path does not prove all Windows applications support it. Microsoft's
legacy `MAX_PATH` is 260 characters; Node's extended-path API can exceed it. Record the
actual path length and runtime in the evidence rather than claiming the full theoretical
limit. Local extended paths are not a network UNC-share test. Report untested network
shares and other filesystems explicitly. Run Linux too, so Windows's file-link subtest
skips do not become permanent missing coverage.

References (checked 2026-11-06):

- [Node fs.symlink](https://nodejs.org/docs/latest-v22.x/api/fs.html#fspromisessymlinktarget-path-type):
  Windows junctions have absolute targets and can only point to directories.
- [Microsoft file naming](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file):
  reserved device names/extensions, superscript digits and the Win32 path namespace.
- [Microsoft maximum path lengths](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation):
  distinguish legacy `MAX_PATH`, extended paths and application/OS opt-in behaviour.
