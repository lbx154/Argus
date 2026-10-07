# Scoped and composable workflows

A vertical may declare several complete task scopes without creating more
verticals. The Manager chooses `WORKFLOW_PROFILE` from the advertised menu.
This is separate from `WORKFLOW_MODE` (`direct`/`staged`): a scoped profile uses
staged execution over only its selected stages, not direct early completion.

```python
WORKFLOW_PROFILES = {
    "rtl": {
        "purpose": "implement and verify RTL without synthesis",
        "stages": ("specification", "rtl", "verification"),
    },
    "full": {
        "purpose": "complete synthesized delivery",
        "stages": STAGE_ORDER,
    },
}
```

Every profile needs a nonempty purpose and a nonempty, duplicate-free, ordered
subsequence of the provider's existing stage order. `full` must exactly preserve
the original order. Existing checklist items, validators, review policy and
completion strength remain attached. Providers may accept a keyword-only
`workflow_profile` and `workflow_stages` in `stage_completion_issues`; the framework
supplies the saved profile (or `full` for legacy tasks) and effective stage order.
This lets tool-readiness checks match scope
without weakening selected-stage evidence.

For a new task in a profile-capable vertical, the Manager must choose the smallest
profile or valid composition covering the requested deliverable. Full delivery remains explicit.
Missing/unknown choices fail rather than silently falling back to another flow.
Supplemental work retains the active profile. Changing it requires the existing
operator-authorized replacement handoff, which resets stage state.

`PIPELINE_STATE.json` records `workflow_profile` and `workflow_stages` together
with the vertical. Project-local provider views drive planning, checklist
rendering, progression and completion without mutating cached modules. The saved
stage order must still match the provider: a later plugin update cannot silently
change an active task's scope. Use a new handoff for an intentional change.

All selected stages must be completed. Stage jumps and direct early-completion
flags cannot omit a profile stage. A shorter profile finishes at its own final
stage; omitted stages are outside scope, not accepted or skipped. Validators
still receive the separate evidence directory and state root.

## Compose requested outcomes with required companions

Providers can additionally declare `WORKFLOW_STAGE_REQUIREMENTS`, covering every
canonical stage. Its values are mandatory companion stages, not scheduling edges:
creating RTL can require verification that executes *later*. The graph must use
known stages and have no duplicates, self-dependencies or cycles.

```python
WORKFLOW_STAGE_REQUIREMENTS = {
    "specification": (),
    "rtl": ("specification", "verification"),
    "verification": (),
    "synthesis": ("verification",),
    "delivery": ("specification", "rtl", "verification", "synthesis"),
}
```

If no preset describes the request, the Manager emits:

```text
WORKFLOW_MODE=staged
WORKFLOW_PROFILE=custom
WORKFLOW_STAGES=rtl;synthesis
```

Structured JSON uses `"workflow_stages": ["rtl", "synthesis"]`. Footer values
use semicolons or pipes, not commas. Empty or unknown goals fail visibly.
`custom` is reserved and cannot be declared as a named preset.
Named profiles reject requested-stage overrides.

The host computes transitive closure and keeps canonical execution order:
`specification -> rtl -> verification -> synthesis` in this example.
`PIPELINE_STATE.json` separately saves `workflow_requested_stages` and effective
`workflow_stages`. Manager output, lifecycle event text and role banners explain
requested work, added requirements with reasons, and excluded stages.
The Manager should clarify a conflict such as "implement RTL but never verify"
before dispatch; the host never waives a required companion.

Only creation or modification goals request those stages. Verifying existing RTL
does not automatically recreate its specification or implementation. Provider
validators must check the actual existing inputs, provenance and freshness.
Missing inputs block completion; they do not authorize silently expanding scope.
Likewise, a target such as FPGA is not by itself a request to complete all FPGA
implementation stages.

Resolve selections from `load_vertical_contract(..., scoped=False)` using
`for_profile("custom", requested_stages=(...))` or `compose_workflow(...)`.
A scoped contract cannot be reselected: it no longer contains omitted checklists.
Composition never mutates global providers. Continuations retain both the goals
and effective stages, even if a different goal list would have the same closure.
Changing either requires the existing operator-authorized replacement handoff.
Dependency changes that alter an active stage snapshot also fail visibly.

This is an ordered subset with mandatory obligations, not a parallel DAG engine
or an arbitrary stage-reordering interface. Repeated engineering/verification
iterations use existing repair and review mechanisms within the frozen scope.
Composition does not automatically accept old evidence or add a confirmation UI.
Named presets and legacy tasks are not retroactively expanded by the graph.

**Compatibility:** providers without profiles and existing projects without a
saved profile retain their previous behavior. Profile-capable plugins should
check for `VerticalContract.for_profile`; composable providers should check for
`VerticalContract.compose_workflow` and reject older frameworks visibly.
Upgrade the framework before installing such plugins. This does not install
external tools, change backend-account configuration or migrate existing work.
