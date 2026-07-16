# Argus npm beta release

The maintainer-facing release surface is one command with one required version:

```bash
./scripts/release_beta.py 0.1.1-beta.3
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
npm version:       0.1.1-beta.3
Git tag:           v0.1.1-beta.3
GitHub prerelease: Argus v0.1.1-beta.3
```

The GitHub prerelease attaches both native binaries, their SHA-256 files, and
platform-specific third-party notices. It reuses the exact artifacts that were
already built and audited for npm; it does not rebuild them.

To build and audit without publishing:

```bash
./scripts/release_beta.py 0.1.1-beta.3 --dry-run
```

To dispatch and return immediately:

```bash
./scripts/release_beta.py 0.1.1-beta.3 --no-watch
```
