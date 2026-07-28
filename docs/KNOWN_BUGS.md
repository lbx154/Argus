# Known Compatibility Debt and Operational Risks

This file tracks current, reproducible design debt. Historical incidents and
already-fixed bugs belong in Git history, not the main documentation tree.
Architecture precedence is defined in
[`DESIGN_AUTHORITY.md`](DESIGN_AUTHORITY.md).

## 1. Different daemons may run different source revisions

**Status:** open operational risk; allowed by development defaults.

Argus records each daemon's loaded source root, Git revision, release id,
manifest digest, and recomputed runtime digest. However,
`ARGUS_SKILL_REQUIRE_RELEASE_MATCH` defaults to off so editable development
checkouts and daemon-local adopted repairs can run before a release rebuild.

Consequences:

- package version equality does not imply behavior equality;
- a bug fixed on `main` can remain live in an older or self-maintained daemon;
- reproducing a failure requires the daemon's `runtime` identity, not only the
  project id.

Mitigation:

- inspect `daemon.status.json` or WebAPI meta before debugging;
- rebuild with `python scripts/build_release.py` after shipped-source changes;
- use `ARGUS_SKILL_REQUIRE_RELEASE_MATCH=1` for controlled deployments;
- drain/restart old daemons rather than assuming source edits affect them.

## 2. Legacy self-review names remain in compatibility surfaces

**Status:** compatibility debt; no current self-review production path.

Current mission rounds always execute Engineer then independent Reviewer. Some
historical event values, test fixtures, filenames such as
`engineer/round_self_review.py`, and old evidence still contain
`engineer_self_review`. They must not be interpreted as an active
`review=skip` feature.

Removal requires an explicit historical-data migration/compatibility decision;
until then, new prompts and current design docs must not advertise the old path.

## 3. `life.plan.signal` is catalogued but has no producer

**Status:** legacy protocol residue.

The abandoned Dynamic Plan `off|shadow|active` mechanism was removed. Current
replanning starts with Reviewer `status=replan_requested` and emits plan
revision proposed/rejected/committed events. `life.plan.signal` remains named in
the cross-version event catalog for old rows/frontends but current code never
emits it.

Do not build new behavior on this event. A future removal must coordinate the
Python catalog, frontend catalog, old event rendering, and release protocol.

## 4. Versioned technical-report claims can differ from current runtime

**Status:** accepted documentation boundary.

`technical_report/` and the North-Star review describe the revisions they name.
They remain useful evidence, but current maintenance must start from
`DESIGN_AUTHORITY.md` and the live contract docs. Date-specific plans, reviews,
incidents and experiment notes are intentionally kept out of main and remain
available through Git history.
