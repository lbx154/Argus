# Argus npm beta release

The maintainer-facing release surface is one command with no manually assigned
beta counter:

```bash
./scripts/release_beta.py
```

The command reads the stable base version from `pyproject.toml` and combines it
with the exact synchronized `origin/main` commit. For example:

```text
base version: 0.1.1
source commit: 0123456789abcdef0123456789abcdef01234567
release version: 0.1.1-beta.g0123456789ab
```

The command refuses to release unless:

- the current branch is `main`;
- tracked files are clean;
- local `HEAD` exactly matches `origin/main`;
- GitHub CLI authentication works; and
- none of the immutable npm versions already exist.

It then fills the protected workflow inputs, watches the native Linux and
Windows builds, waits for npm registry propagation, performs a clean npm
installation smoke test, and verifies the matching Git tag and GitHub
prerelease. The protected workflow still owns credential scans, tests,
PyInstaller builds, tarball allowlists, GitHub Environment gating, Trusted
Publishing, and uploading the audited Linux/Windows binaries to GitHub.

Every successful beta uses one version across all release surfaces:

```text
npm version:       0.1.1-beta.g0123456789ab
Git tag:           v0.1.1-beta.g0123456789ab
GitHub prerelease: Argus v0.1.1-beta.g0123456789ab
```

The GitHub prerelease attaches both native binaries, their SHA-256 files,
platform-specific third-party notices, and `release-metadata.json`. The metadata
records the full source commit, source digest, release identity, workflow run,
npm integrity values, and binary hashes. It reuses the exact artifacts that were
already built and audited for npm; it does not rebuild them.

To build and audit without publishing:

```bash
./scripts/release_beta.py --dry-run
```

To dispatch and return immediately:

```bash
./scripts/release_beta.py --no-watch
```

## Planned product changes

### One public command: `argus`

All user-facing operations must use the `argus` command. The public package,
website, setup output, help, diagnostics, and update instructions must stop
teaching `argus-skill` as a separate command.

Required behavior includes:

- `argus --version` and `argus version` report the installed product version;
- `argus --setup` performs first-time configuration;
- `argus --web` opens the Web cockpit;
- maintenance and diagnostic operations remain reachable under `argus`;
- the npm package no longer installs a public `argus-skill` executable; and
- any internal CLI/backend mode remains an implementation detail.

### Startup update notification

When an interactive user starts `argus`, it should detect whether the npm
`beta` dist-tag points to a newer version than the installed build. The check
must not make startup feel slower or prevent offline use.

Required behavior:

- use a short timeout and a persisted cache instead of blocking every launch;
- compare semantic versions, not release timestamps or source digests;
- show installed and available versions when an update exists;
- let the user install now or skip/remind later;
- never mutate the installation without explicit user approval;
- use the correct updater for npm versus the official website installer;
- avoid interactive prompts in CI, piped input, or other non-TTY sessions; and
- continue startup normally when npm is unreachable.

## Current beta access model

The binary beta is a public preview, not an invitation-only release:

- the npm package is public;
- the official Linux and Windows installer scripts are public;
- anyone who knows or discovers the package name or website URL can download
  the binary; and
- the source repository remains private.

Running useful missions still requires local configuration. Users need model
provider credentials or another configured backend. The current launcher also
requires at least one trusted operator special-prompt file. Setup creates such
files only for some selected capabilities, so a generic no-GPU/no-experiment
setup may still require a manually authored house-rules directive. This is a
configuration gate, not an account allowlist or license check.
