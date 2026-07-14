# Bounded Project Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Argus CLI/TUI heap exhaustion by bounding every text field returned by the project index before JSON serialization.

**Architecture:** Keep complete project objectives in per-project state and snapshot endpoints. Convert `GET /api/projects` into a bounded summary at `project_state.list_projects`, so every Web/TUI client receives a small response before its JSON parser allocates strings.

**Tech Stack:** Python 3.11+, FastAPI, pytest, TypeScript/Ink TUI, Node.js 18+

## Global Constraints

- Work only in `.worktrees/fix-tui-project-index-oom`.
- Keep the running mathematics mission alive.
- Do not operate on port 8799.
- Do not use `NODE_OPTIONS` heap enlargement as the fix.
- Bound `display_name` and `label` to 180 characters and `objective` to 1,000 characters in `GET /api/projects`.
- Preserve the complete objective in the per-project snapshot.
- Before pushing, fetch and rebase onto the latest `origin/main`, rebuild release artifacts, and rerun regressions.
- Deployment may restart only the 8921 WebAPI; it must not restart project daemons.

---

## File Structure

- Modify `argus_skill/webapi/project_state.py`: define project-index text bounds and apply them while constructing summary rows.
- Modify `tests/webapi/test_server_m0.py`: prove a multi-megabyte objective produces a bounded project index while remaining complete in the snapshot.
- Regenerate `argus_skill/release_manifest.json`: update shipped-source identity.
- Regenerate `frontend/core/src/release.generated.ts`: expose the same release identity to frontends.
- Regenerate `frontend/tui/bundle/argus.mjs`: embed the current release identity.
- Regenerate `frontend/web/dist/index.html` and its referenced hashed entry asset: embed the current release identity.

### Task 1: Bound the Project Index at the Server

**Files:**
- Modify: `tests/webapi/test_server_m0.py`
- Modify: `argus_skill/webapi/project_state.py:37-45`
- Modify: `argus_skill/webapi/project_state.py:457-489`
- Generated: `argus_skill/release_manifest.json`
- Generated: `frontend/core/src/release.generated.ts`
- Generated: `frontend/tui/bundle/argus.mjs`
- Generated: `frontend/web/dist/index.html`
- Generated: `frontend/web/dist/assets/` entry bundle

**Interfaces:**
- Consumes: `SessionMeta`, `write_session_meta`, `project_state.list_projects`, and `project_state.build_snapshot`.
- Produces: bounded `ProjectRow` dictionaries with `display_name: str`, `label: str`, and `objective: str`; snapshots continue to expose the unabridged session objective.

- [ ] **Step 1: Write the failing large-history regression**

Add this test after `test_project_label_does_not_use_raw_operator_transcript`:

```python
def test_project_index_bounds_large_text_without_truncating_snapshot(
    tmp_path: Path,
) -> None:
    sid = "s-large-index"
    life_dir = tmp_path / "projects" / sid
    objective = "first objective line\n" + ("x" * (2 * 1024 * 1024))
    display_name = "large project\n" + ("n" * 1024)
    write_session_meta(
        tmp_path,
        SessionMeta(
            id=sid,
            display_name=display_name,
            objective=objective,
            created=1,
            last_active=1,
            cwd=str(life_dir),
        ),
    )

    project = next(
        item
        for item in project_state.list_projects(
            global_root=tmp_path,
            include_empty=True,
        )
        if item["id"] == sid
    )
    snapshot = project_state.build_snapshot(sid, global_root=tmp_path)

    assert snapshot is not None
    assert snapshot["session"]["objective"] == objective
    assert len(project["display_name"]) <= 180
    assert len(project["label"]) <= 180
    assert "\n" not in project["label"]
    assert len(project["objective"]) <= 1_000
    assert len(json.dumps({"projects": [project]})) < 5_000
```

- [ ] **Step 2: Run the new regression and confirm it fails**

Run:

```bash
cd /home/argustest/dev/www/argus-skill/.worktrees/fix-tui-project-index-oom
PYTHONPATH=$PWD /home/argustest/dev/www/.venv/bin/pytest \
  tests/webapi/test_server_m0.py::test_project_index_bounds_large_text_without_truncating_snapshot -q
```

Expected: FAIL because `display_name`, `label`, and `objective` still contain unbounded text.

- [ ] **Step 3: Implement bounded project-index summaries**

Add these constants and helper below `DAEMON_ADMISSION_FILE`:

```python
_PROJECT_INDEX_LABEL_CHARS = 180
_PROJECT_INDEX_OBJECTIVE_CHARS = 1_000


def _project_index_text(value: Any, limit: int, *, single_line: bool = False) -> str:
    text = str(value or "")[:limit]
    if single_line:
        text = " ".join(text.splitlines()).strip()
    return text
```

Replace the objective/label construction in `list_projects` with:

```python
        raw_objective = item.get("objective") or campaign_objective
        display_name = _project_index_text(
            item.get("display_name"),
            _PROJECT_INDEX_LABEL_CHARS,
            single_line=True,
        )
        objective = _project_index_text(
            raw_objective,
            _PROJECT_INDEX_OBJECTIVE_CHARS,
        )
        item["display_name"] = display_name
        item["objective"] = objective
        item["label"] = (
            display_name
            or _project_index_text(
                objective,
                _PROJECT_INDEX_LABEL_CHARS,
                single_line=True,
            )
            or meta.id
        )
```

- [ ] **Step 4: Run focused WebAPI regressions**

Run:

```bash
cd /home/argustest/dev/www/argus-skill/.worktrees/fix-tui-project-index-oom
PYTHONPATH=$PWD /home/argustest/dev/www/.venv/bin/pytest -q \
  tests/webapi/test_server_m0.py
```

Expected: all tests pass, including event UI filtering and large raw-tail scanning.

- [ ] **Step 5: Run TUI project/event regressions**

Run:

```bash
cd /home/argustest/dev/www/argus-skill/.worktrees/fix-tui-project-index-oom/frontend/tui
npm test
npm run typecheck
```

Expected: all TUI tests pass and TypeScript reports no errors.

- [ ] **Step 6: Refresh and verify release artifacts**

Run:

```bash
cd /home/argustest/dev/www/argus-skill/.worktrees/fix-tui-project-index-oom
PYTHONPATH=$PWD /home/argustest/dev/www/.venv/bin/python scripts/build_release.py
PYTHONPATH=$PWD /home/argustest/dev/www/.venv/bin/python scripts/check_release_artifacts.py
git diff --check
```

Expected: release build and artifact check succeed; `git diff --check` prints nothing.

- [ ] **Step 7: Commit the implementation**

```bash
git add \
  argus_skill/webapi/project_state.py \
  tests/webapi/test_server_m0.py \
  argus_skill/release_manifest.json \
  frontend/core/src/release.generated.ts \
  frontend/tui/bundle/argus.mjs \
  frontend/web/dist
git commit -m "fix(webapi): bound project index summaries" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: fa406665-64cc-4e78-ba0c-8c7292d76d31"
```

Expected: one implementation commit containing source, regression, and matching release artifacts.

### Task 2: Rebase, Integrate, and Deploy 8921 Only

**Files:**
- Revalidate: `argus_skill/webapi/project_state.py`
- Revalidate: `tests/webapi/test_server_m0.py`
- Rebuild: release artifacts listed in Task 1

**Interfaces:**
- Consumes: the bounded `GET /api/projects` response from Task 1.
- Produces: `origin/main` and the 8921 runtime on the same verified revision and release identity.

- [ ] **Step 1: Fetch and rebase onto current main**

Run:

```bash
cd /home/argustest/dev/www/argus-skill/.worktrees/fix-tui-project-index-oom
git fetch origin
git rebase origin/main
```

Expected: rebase succeeds without unrelated conflict resolution.

- [ ] **Step 2: Rebuild release identity after the rebase**

Run:

```bash
PYTHONPATH=$PWD /home/argustest/dev/www/.venv/bin/python scripts/build_release.py
git diff --check
```

If tracked release artifacts changed, stage them and create a non-amended release refresh commit with the required trailers.

- [ ] **Step 3: Rerun the complete targeted regression set**

Run:

```bash
PYTHONPATH=$PWD /home/argustest/dev/www/.venv/bin/pytest -q \
  tests/webapi/test_server_m0.py \
  tests/apps/test_tui_launcher.py
cd frontend/tui
npm test
npm run typecheck
cd ../..
PYTHONPATH=$PWD /home/argustest/dev/www/.venv/bin/python scripts/check_release_artifacts.py
```

Expected: every command exits 0.

- [ ] **Step 4: Push the rebased branch directly to main**

Run:

```bash
git push origin HEAD:main
```

Expected: `origin/main` advances to the worktree HEAD.

- [ ] **Step 5: Fast-forward the deployed checkout and preflight imports**

Run:

```bash
git -C /home/argustest/dev/www/argus-skill pull --ff-only origin main
cd /home/argustest/dev/www/argus-skill
PYTHONPATH=$PWD /home/argustest/dev/www/.venv/bin/python -c \
  "from argus_skill.webapi.server import create_app; print('webapi import ok')"
```

Expected: the main checkout is clean at `origin/main` and the import succeeds.

- [ ] **Step 6: Restart only the 8921 WebAPI**

Read the listener PID from:

```bash
ss -ltnp | grep '127.0.0.1:8921'
```

Confirm the PID command is `python -m argus_skill --web --web-port 8921`, terminate that exact numeric PID only, wait for port 8921 to close, then launch:

```bash
cd /home/argustest/dev/www
source /home/argustest/dev/www/activate.sh
cd "$ARGUS_SKILL_SOURCE_ROOT"
exec "$ARGUS_SKILL_PYTHON" -m argus_skill \
  --web --web-host 127.0.0.1 --web-port 8921
```

Run the replacement as a detached process with its own log. Do not signal any project daemon PID and do not inspect or alter port 8799.

- [ ] **Step 7: Verify deployed revision and bounded response**

Run:

```bash
curl --max-time 10 -sS http://127.0.0.1:8921/api/meta
curl --max-time 30 -sS http://127.0.0.1:8921/api/projects
```

Assert:

- `/api/meta.runtime.revision` equals `git rev-parse --short=12 origin/main`.
- `/api/meta.runtime.release_matches_source` is `true`.
- Every project `display_name` and `label` is at most 180 characters.
- Every project `objective` is at most 1,000 characters.
- The mathematics mission daemon PID and alive status are unchanged.

- [ ] **Step 8: Clean diagnostic resources**

Stop the isolated `replay-api` shell session and remove `/tmp/argus-oom-replay` plus `/tmp/argus-*` diagnostic files created by the investigation. Do not stop any unrelated process.
