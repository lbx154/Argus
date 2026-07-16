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
Windows builds, waits for npm registry propagation, and performs a clean npm
installation smoke test. The protected workflow still owns credential scans,
tests, PyInstaller builds, tarball allowlists, GitHub Environment gating, and
Trusted Publishing.

To build and audit without publishing:

```bash
./scripts/release_beta.py 0.1.1-beta.3 --dry-run
```

To dispatch and return immediately:

```bash
./scripts/release_beta.py 0.1.1-beta.3 --no-watch
```
