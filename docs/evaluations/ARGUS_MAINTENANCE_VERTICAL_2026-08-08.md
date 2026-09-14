# Argus Maintenance Vertical — 2026-08-08

## Result

Added a built-in `argus_maintenance` vertical for changes to Argus itself.

```text
inspect → change → verify
```

Implementation:

- `argus/verticals/argus_maintenance/stages.py`
- `argus/verticals/argus_maintenance/architecture_audit.py`
- one Engineer Skill and one Reviewer Skill

Run the audit with:

```bash
python -m argus.verticals.argus_maintenance.architecture_audit
```

The report lists candidates. It does not automatically edit code or turn counts into gates.

## Architecture changes

- Verticals declare `MISSION_KIND`; Manager no longer owns `_OPTIMIZE_VERTICALS`.
- Repository grounding is selected through vertical metadata.
- Research-only library preparation moved behind a research provider hook.
- Automatic framework-maintenance work carries `argus_maintenance` into its isolated worktree.
- `BacklogItem.manager_decision` now survives serialization.
- Core has no concrete vertical imports.

## Initial cleanup

- Production `assert`: 14 → 0.
- Generic-layer GPU literals: 7 → 0.
- Removed the legacy no-op `--skill-cleanse`, `--skill-stats`, and `--skill-stats-json` commands.
- Removed two unused compatibility aliases.
- Removed machine-specific NanoChat playbooks and historical score anchors.

## Audit baseline

The current scan covers 1,529 maintained files and reports 478 candidates:

- 194 value fallback chains
- 131 oversized functions
- 122 silent broad exceptions
- 16 fixed digests
- 14 thin wrappers
- 1 concrete vertical import

One direct `learning` vertical import remains in the legacy CLI. Replacing it would currently require more command-extension machinery than the single call site justifies.

## Verification

- Python: 4,767 passed / 13 skipped.
- Web: 150 passed.
- TUI: 224 passed.
- Linux, Windows portable surface, macOS portable surface, release build, and Ruff passed.
