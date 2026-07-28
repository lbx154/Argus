# Release Identity

> Current release/source identity contract. See
> [`DESIGN_AUTHORITY.md`](DESIGN_AUTHORITY.md).

Protocol compatibility is necessary but not sufficient: two checkouts can
implement the same protocol version with different code. Argus therefore builds
one deterministic release identity from shipped backend, frontend, contract,
and built-in skill source.

The release build's internal manifest preparation step writes:

- `argus_skill/release_manifest.json`
- `frontend/core/src/release.generated.ts`

Direct write-mode invocation of `scripts/generate_release_manifest.py` is
rejected because it would update those files without rebuilding the shipped
Web/TUI bundles. Use `scripts/build_release.py`; `--check` remains available
for read-only validation.

The release id is `<package-version>+<source-digest-prefix>`. WebAPI and daemon
runtime identity include the manifest digest, a digest recomputed from the loaded
checkout, and whether they match. TUI/Web bundles embed the same release id and
reject a backend from another release even when protocol major/minor versions are
compatible.

Frontend production builds run the generator in `--check` mode before compiling,
so stale checked-in bundles cannot be produced from changed source. Installed
wheels that do not contain a source checkout trust the packaged manifest;
editable/source checkouts recompute and verify it.

At runtime, a different generated release id remains a hard frontend/backend
incompatibility. A live editable checkout whose source digest differs from its
last generated manifest is instead accepted with an explicit warning: source
identity is a release-integrity check, while wire compatibility is governed by
the versioned protocol, snapshot schema, and required capabilities. Repository
release tests still fail on that drift, so it cannot be shipped silently.

Before committing or deploying any shipped source change, rebuild the complete
release atomically:

```bash
python scripts/build_release.py
```

`scripts/check_release_artifacts.py` verifies that both the tracked Web entry
bundle and the tracked TUI bundle embed the current release id. The Python test
suite runs this check, so a release manifest can no longer be current while the
actual user-facing bundles remain stale.
