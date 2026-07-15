# Continuity Task 1 Report: Resume completed lifecycle on TEAM dispatch

## Status: ✅ COMPLETE (v2 — review findings addressed)

## Commits

```
98f8cfc fix(manager): resume completed projects for new team work
db42921 fix(manager): address continuity task-1 review findings
```

## Summary

Implemented `resume_done_lifecycle_for_team_dispatch(mem)` in `argus_skill/manager/dispatch.py` (commit `98f8cfc`) and subsequently addressed all code-review findings (commit `db42921`):

1. **Exception safety** — moved the lifecycle resume call inside the single `try/except` that covers `maybe_promote_to_continuous` + `enqueue_mission`. Quarantined/archived `RuntimeError` is now caught and returned as `{"kind": "error", "reply": "could not enqueue: ..."}` — never an unhandled exception / HTTP 500.

2. **`global_root` path** — dispatch.py now prefers `mem.global_root` (stable `MemoryBundle` attribute) for `read_session_meta`; falls back to `life_dir.parent.parent` only when the attribute is absent.

3. **Atomic done-transition** — added `resume_atomically_if_done()` to `project_lifecycle_io.py`: acquires the lifecycle lock, re-reads persisted state inside the critical section, and only writes if state is still `done`. This collapses the check→write into one critical section and prevents a TOCTOU race where two concurrent `manager_message` calls both observe `done` and both try to resume. `dispatch.py` now uses this helper instead of `append_event()`.

4. **TOCTOU documentation** — the pre-lock read (`infer_observable_status`, `apply_persisted_to_status`) is documented in dispatch.py as a residual low-risk concern: if two concurrent callers compute `new_status` concurrently, `resume_atomically_if_done` ensures at most one write lands; the second is a no-op.

5. **Blocking manager-message test** — added `TestManagerMessageLifecycleErrors` with parametrised quarantined/archived tests that call `manager_message` end-to-end (front-door stubbed, no real model) and assert `{"kind": "error", ...}` — not a raised exception.

6. **Worktree import fix** — added `conftest.py` at worktree root that removes `_ArgusLatestFinder` from `sys.meta_path` (installed by the shared venv's editable `.pth` hook) so `PYTHONPATH=$PWD` correctly routes `import argus_skill` to this worktree during test runs.

### Behaviour matrix (unchanged)

| Prior state | Action | Result |
|---|---|---|
| `done` | TEAM dispatch | Resume → `incubating`/`running`/`writing` (reason: `manager_team_dispatch`) |
| `quarantined` | TEAM dispatch | `{"kind": "error", "reply": "could not enqueue: project lifecycle is quarantined; ..."}` |
| `archived` | TEAM dispatch | `{"kind": "error", "reply": "could not enqueue: project lifecycle is archived; ..."}` |
| `incubating`/`running`/`writing` | TEAM dispatch | No-op (returns `False`) |
| No lifecycle file | TEAM dispatch | No-op (returns `False`) |
| `done` | chat/SELF | No mutation — resume never called |

## Files modified

| File | Change |
|---|---|
| `argus_skill/manager/dispatch.py` | Prefer `mem.global_root`; use `resume_atomically_if_done`; TOCTOU doc |
| `argus_skill/webapi/manager_bridge.py` | Merge lifecycle call into inner try/except; update import |
| `argus_skill/life/project_lifecycle_io.py` | Add `resume_atomically_if_done()` (+46 lines) |
| `tests/webapi/test_wave1.py` | Add `TestManagerMessageLifecycleErrors` (+2 parametrised tests) |
| `conftest.py` | New — remove `_ArgusLatestFinder` so worktree tests import correctly |

## Tests

### `TestLifecycleResumeOnTeamDispatch` (unit, unchanged, 9 tests)

| Test | Assertion |
|---|---|
| `test_done_project_resumes_to_active_state` | `state ∈ {incubating, running, writing}`, `reason == "manager_team_dispatch"` |
| `test_done_project_resume_uses_launch_cwd` | With `paper/main.tex` in launch_cwd workspace → `state == "writing"` |
| `test_quarantined_and_archived_raise[quarantined]` | `RuntimeError`, state unchanged |
| `test_quarantined_and_archived_raise[archived]` | `RuntimeError`, state unchanged |
| `test_active_states_are_noop[incubating]` | Returns `False`, state unchanged |
| `test_active_states_are_noop[running]` | Returns `False`, state unchanged |
| `test_active_states_are_noop[writing]` | Returns `False`, state unchanged |
| `test_chat_self_never_mutates_done` | `state == "done"` (no resume call made) |
| `test_no_lifecycle_file_returns_false` | Returns `False` |

### `TestManagerMessageLifecycleErrors` (integration, new, 2 tests)

| Test | Assertion |
|---|---|
| `test_quarantined_archived_return_structured_error[quarantined]` | `result["kind"] == "error"`, `"could not enqueue"` in reply, `"quarantined"` in reply |
| `test_quarantined_archived_return_structured_error[archived]` | `result["kind"] == "error"`, `"could not enqueue"` in reply, `"archived"` in reply |

### Test results

```
$ PYTHONPATH=$PWD pytest tests/webapi/test_wave1.py -k "Lifecycle" -v
...........                                                              [100%]
11 passed

$ PYTHONPATH=$PWD pytest tests/webapi/test_wave1.py -v
.........................................                                [100%]
41 passed in 2.05s
```

## Concerns

1. **Residual TOCTOU (documented)**: `infer_observable_status` and `apply_persisted_to_status` run outside the lifecycle lock. Two concurrent dispatches can both read `state=done` and both compute a `new_status`; `resume_atomically_if_done` ensures at most one write lands per check — the second caller's write is a no-op (state is no longer `done`). In the pathological case both writes land back-to-back, which is idempotent. This is low-risk in practice (manager_message uses per-sid locking).

2. **Race with daemon tick**: Daemon tick uses `lifecycle.lock` for all writes. `resume_atomically_if_done` also takes the same lock, so concurrent daemon tick + dispatch serialize correctly.

3. **No lifecycle file**: Returns `False` (no-op). A fresh project gets a clean pass-through.
