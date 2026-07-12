# Release Identity

Protocol compatibility is necessary but not sufficient: two checkouts can
implement the same protocol version with different code. Argus therefore builds
one deterministic release identity from shipped backend, frontend, contract,
and built-in skill source.

`scripts/generate_release_manifest.py` writes:

- `argus_skill/release_manifest.json`
- `frontend/core/src/release.generated.ts`

The release id is `<package-version>+<source-digest-prefix>`. WebAPI and daemon
runtime identity include the manifest digest, a digest recomputed from the loaded
checkout, and whether they match. TUI/Web bundles embed the same release id and
reject a backend from another release even when protocol major/minor versions are
compatible.

Frontend production builds run the generator in `--check` mode before compiling,
so stale checked-in bundles cannot be produced from changed source. Installed
wheels that do not contain a source checkout trust the packaged manifest;
editable/source checkouts recompute and verify it.
