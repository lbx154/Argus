# Manager-to-Kernel Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a newly accepted TEAM task resumes a completed project, starts execution, and leaves an accurate durable conversational acknowledgement.

**Architecture:** Add a Manager-dispatch lifecycle helper that uses existing lifecycle inference/resume/persistence. Add one post-start acknowledgement function shared by blocking and streaming Web endpoints so transcript, SSE, and result state agree.

**Tech Stack:** Python 3.11+, FastAPI, SSE, existing project lifecycle and transcript APIs, pytest.

## Global Constraints

- Only TEAM dispatch may auto-resume lifecycle `done`.
- Chat, SELF, config, status, and no-dispatch never alter lifecycle.
- Quarantined and archived states remain explicit operator gates.
- Never rewrite lifecycle JSON directly.
- Acknowledgements reflect started, already-running, admission-required, or failed states accurately.
- Streaming and blocking endpoints persist equivalent acknowledgements.
- Lifecycle or acknowledgement persistence failure is explicit, never success-shaped.

---

### Task 1: Resume completed lifecycle on TEAM dispatch

**Files:**
- Modify: `argus_skill/manager/dispatch.py`
- Modify: `argus_skill/webapi/manager_bridge.py`
- Test: `tests/webapi/test_wave1.py`

**Interfaces:**
- Produces: `resume_done_lifecycle_for_team_dispatch(mem) -> bool`.
- Consumes: `infer_observable_status`, `load_persisted`,
  `apply_persisted_to_status`, `project_lifecycle.resume`, and `append_event`.

- [ ] **Step 1: Write failing lifecycle dispatch tests**

Create temp session metadata with `launch_cwd`, persist lifecycle `done`, and
exercise a mocked TEAM Manager turn. Assert:

```py
persisted = load_persisted(life_dir)
assert persisted["state"] in {"incubating", "running", "writing"}
assert persisted["history"][-1]["reason"] == "manager_team_dispatch"
```

Add parameterized tests proving `quarantined` and `archived` raise an explicit
dispatch error and remain unchanged. Add a chat/SELF test proving `done`
remains `done`.

- [ ] **Step 2: Run focused tests and verify red**

```bash
pytest tests/webapi/test_wave1.py -k "lifecycle and manager" -q
```

Expected: FAIL because TEAM dispatch does not resume lifecycle.

- [ ] **Step 3: Implement lifecycle helper**

In `manager/dispatch.py`:

```py
def resume_done_lifecycle_for_team_dispatch(mem: Any) -> bool:
    life_dir = Path(front_door._life_dir_for(mem))
    persisted = load_persisted(life_dir)
    state = str(persisted.get("state") or "")
    if state != "done":
        if state in {"quarantined", "archived"}:
            raise RuntimeError(
                f"project lifecycle is {state}; explicit resume is required"
            )
        return False
    root = life_dir.parent.parent
    meta = read_session_meta(root, life_dir.name)
    observable_root = (
        Path(meta.launch_cwd)
        if meta is not None and meta.launch_cwd and Path(meta.launch_cwd).exists()
        else life_dir
    )
    status = infer_observable_status(observable_root, project_id=life_dir.name)
    status = apply_persisted_to_status(status, persisted)
    new_status, event = lifecycle_resume(
        status,
        reason="manager_team_dispatch",
    )
    append_event(life_dir, new_status=new_status, event=event)
    return True
```

Call it in `manager_bridge.manager_message` after chat/SELF/no-dispatch returns
and immediately before lifetime selection/enqueue, inside the existing explicit
enqueue error path.

- [ ] **Step 4: Run focused lifecycle tests**

```bash
pytest tests/webapi/test_wave1.py -k "lifecycle and manager" -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add argus_skill/manager/dispatch.py argus_skill/webapi/manager_bridge.py \
  tests/webapi/test_wave1.py
git commit -m "fix(manager): resume completed projects for new team work"
```

### Task 2: Durable post-start dispatch acknowledgement

**Files:**
- Modify: `argus_skill/webapi/manager_bridge.py`
- Modify: `argus_skill/webapi/server.py`
- Modify: `frontend/web/src/App.tsx`
- Test: `tests/webapi/test_wave1.py`
- Test: `frontend/web/src/test/apiProtocol.test.ts`

**Interfaces:**
- Produces: `record_task_dispatch_ack(sid, result, *, global_root, on_fragment=None) -> str`.
- Consumes: final result after `start_project_daemon`.

- [ ] **Step 1: Write failing acknowledgement tests**

Parameterize final daemon states:

```py
[
  ({"rc": 0, "pid": 42}, "executor started"),
  (None, "executor already running"),
  ({"admission_required": True}, "waiting for an executor slot"),
  ({"rc": 2, "error": "auth failed"}, "executor failed to start: auth failed"),
]
```

For each, assert returned `result["reply"]`, latest transcript Argus turn, and
SSE delta text agree. Add a blocking endpoint test with the same assertion.
Inject transcript write failure and assert explicit endpoint/SSE error.

- [ ] **Step 2: Run focused tests and verify red**

```bash
pytest tests/webapi/test_wave1.py -k "dispatch_ack" -q
```

Expected: FAIL because task dispatch returns `reply: None`.

- [ ] **Step 3: Implement shared acknowledgement**

In `manager_bridge.py`, derive truthful text from `result["daemon"]` and
`result["daemon_alive"]`, then:

```py
append_turn(life_dir, "argus", text)
_emit_ui_turn(life_dir, "argus", text, message_id=f"dispatch-{uuid.uuid4().hex}")
if on_fragment is not None:
    on_fragment("delta", {"text": text, "message_id": "dispatch"})
result["reply"] = text
return text
```

Do not swallow `append_turn` errors.

Call this helper after daemon start in both `_post_message` and
`_post_message_stream`; call it even when `daemon_alive` was already true.

- [ ] **Step 4: Update Web task completion handling**

Keep transcript refetch in `onDone`. Change `dispatchTask` success notices to
use `result.reply` only as a non-durable accessibility notice; do not create a
second local conversation message.

- [ ] **Step 5: Run backend and Web tests**

```bash
pytest tests/webapi/test_wave1.py -k "dispatch_ack or lifecycle" -q
cd frontend/web
npx vitest run src/test/apiProtocol.test.ts
npm run typecheck --silent
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add argus_skill/webapi/manager_bridge.py argus_skill/webapi/server.py \
  tests/webapi/test_wave1.py frontend/web/src/App.tsx \
  frontend/web/src/test/apiProtocol.test.ts
git commit -m "fix(web): persist truthful task dispatch acknowledgements"
```

### Task 3: Release, reproduction repair, and deployment

**Files:**
- Generated release artifacts from `scripts/build_release.py`

- [ ] **Step 1: Run targeted and full suites**

```bash
pytest tests/webapi/test_wave1.py tests/life/test_project_lifecycle.py -q
cd frontend/tui && npm test && npm run typecheck --silent
cd ../web && npm test -- --run && npm run typecheck --silent
```

- [ ] **Step 2: Build fenced release**

```bash
python scripts/build_release.py
python scripts/check_release_artifacts.py
```

- [ ] **Step 3: Rebase and push**

```bash
git fetch origin --prune
git rebase origin/main
git push origin HEAD:main
```

- [ ] **Step 4: Restart only 8798**

Verify and stop the exact 8798 backend PID, start the new backend, and confirm
the 8799 proxy PID is unchanged. Assert frontend and `/api/meta` release IDs
match through port 8799.

- [ ] **Step 5: Repair observed session**

For `s-dd7b46db`, use the same lifecycle resume API with reason
`operator_recovery_after_continuity_fix`, then start its daemon. Do not mutate
backlog entries. Verify lifecycle is allocatable, daemon alive, and the newest
pending task is claimable.

