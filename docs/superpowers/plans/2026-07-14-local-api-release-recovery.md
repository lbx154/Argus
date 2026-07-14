# Local Argus API Release Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `argus` under `/home/argustest/argustest2` preserve its isolated 8899 environment while safely replacing only a proven self-owned stale WebAPI.

**Architecture:** Add an opt-in ownership record and fail-closed stale-process recovery to the TUI API launcher. The directory-local shell wrapper enables this recovery and points both client and server at `argus-skill-latest`; unknown listeners remain untouched.

**Tech Stack:** TypeScript 5.6, Node 18+, Linux `/proc`, Node test runner, Bash shell integration, existing release identity protocol.

## Global Constraints

- Keep port 8899, directory-local `ARGUS_SKILL_HOME`, dedicated `COPILOT_HOME`, and account-level session discovery.
- Never restart the public 8798 WebAPI or persistent 8799 proxy.
- Automatic recovery requires a valid ownership record plus live-process verification.
- Never use `pkill`, `killall`, or a force signal.
- Unknown ownership, malformed records, and failed graceful shutdown must fail closed.
- Client and backend must come from `/home/argustest/argustest2/argus-skill-latest`.

---

### Task 1: Ownership record verification

**Files:**
- Create: `frontend/tui/src/apiOwnership.ts`
- Create: `frontend/tui/test/apiOwnership.test.ts`

**Interfaces:**
- Produces: `ApiOwnershipRecord`, `writeOwnershipRecord()`, `readOwnedApi()`, and `removeOwnershipRecord()`.
- Consumes: expected host, port, backend executable, and ownership-file path.

- [ ] **Step 1: Write failing ownership tests**

Create `frontend/tui/test/apiOwnership.test.ts` using a temporary directory and
dependency injection:

```ts
import assert from 'node:assert/strict';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';
import { readOwnedApi } from '../src/apiOwnership.js';

test('accepts only a matching live Argus WebAPI record', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'owner.json');
  const record = {
    schema: 1,
    pid: 4321,
    host: '127.0.0.1',
    port: 8899,
    backendBin: '/repo/.venv/bin/argus-skill',
    startedAt: '2026-07-14T00:00:00Z',
  };
  await writeFile(ownerFile, JSON.stringify(record));
  const owned = await readOwnedApi({
    path: ownerFile,
    host: '127.0.0.1',
    port: 8899,
    backendBin: record.backendBin,
    inspect: async () => ({
      alive: true,
      argv: [record.backendBin, '--web', '--web-port', '8899'],
    }),
  });
  assert.equal(owned?.pid, 4321);
});

test('rejects an unknown or mismatched process', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'owner.json');
  await writeFile(ownerFile, JSON.stringify({
    schema: 1,
    pid: 4321,
    host: '127.0.0.1',
    port: 8899,
    backendBin: '/repo/.venv/bin/argus-skill',
    startedAt: '2026-07-14T00:00:00Z',
  }));
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: '127.0.0.1',
    port: 8899,
    backendBin: '/repo/.venv/bin/argus-skill',
    inspect: async () => ({
      alive: true,
      argv: ['/usr/bin/python', '-m', 'http.server', '8899'],
    }),
  }), null);
});
```

Also cover malformed JSON, dead PID, host mismatch, port mismatch, backend path
mismatch, and missing `--web`.

- [ ] **Step 2: Run the test and verify it fails**

```bash
cd frontend/tui
node --import tsx --test test/apiOwnership.test.ts
```

Expected: FAIL because `apiOwnership.ts` does not exist.

- [ ] **Step 3: Implement fail-closed ownership**

Define:

```ts
export interface ApiOwnershipRecord {
  schema: 1;
  pid: number;
  host: string;
  port: number;
  backendBin: string;
  startedAt: string;
}

export interface ProcessInspection {
  alive: boolean;
  argv: string[];
}
```

Write records atomically with mode `0o600` using `writeFile(temp)`, `chmod`, and
`rename`. The default Linux inspector reads `/proc/<pid>/cmdline`, splitting
NUL-delimited arguments, and checks liveness with `process.kill(pid, 0)`.

`readOwnedApi()` returns a record only when all of these are true:

- schema is 1;
- PID is a positive integer and alive;
- host and port equal the requested endpoint;
- `backendBin` resolves to the expected binary;
- argv includes that binary, `--web`, and the requested `--web-port`.

Any read, parse, inspection, or validation error returns `null`.

- [ ] **Step 4: Run ownership tests**

```bash
cd frontend/tui
node --import tsx --test test/apiOwnership.test.ts
npm run typecheck --silent
```

Expected: all tests PASS and TypeScript exits 0.

- [ ] **Step 5: Commit ownership support**

```bash
git add frontend/tui/src/apiOwnership.ts frontend/tui/test/apiOwnership.test.ts
git commit -m "feat(tui): record local WebAPI ownership"
```

### Task 2: Graceful stale-release recovery

**Files:**
- Modify: `frontend/tui/src/ensureApi.ts`
- Modify: `frontend/tui/src/args.ts`
- Modify: `frontend/tui/test/protocol.test.ts`

**Interfaces:**
- Consumes: `readOwnedApi()` and `writeOwnershipRecord()`.
- Produces: optional `ownerFile` behavior in `ensureApi()`.

- [ ] **Step 1: Add failing recovery tests**

Extend `frontend/tui/test/protocol.test.ts` with injected process operations:

```ts
// Extend the existing ensureApi import with `type ApiProbeResult`.
const staleProbe: ApiProbeResult = {
  state: 'incompatible' as const,
  message: 'backend release 0.1.0+stale does not match client release',
};
const downProbe: ApiProbeResult = {
  state: 'unreachable',
  message: 'connection refused',
};
const currentProbe: ApiProbeResult = {
  state: 'compatible' as const,
  message: 'current release',
};
const probeSequence = (...values: ApiProbeResult[]) => {
  let index = 0;
  return async () => values[Math.min(index++, values.length - 1)];
};
const ownedRecord = {
  schema: 1 as const,
  pid: 4321,
  host: '127.0.0.1',
  port: 8899,
  backendBin: '/repo/.venv/bin/argus-skill',
  startedAt: '2026-07-14T00:00:00Z',
};

test('replaces a proven owned stale API with SIGTERM only', async () => {
  const signals: Array<[number, NodeJS.Signals]> = [];
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: probeSequence(staleProbe, downProbe, currentProbe),
      readOwnedApi: async () => ownedRecord,
      signal: (pid, signal) => signals.push([pid, signal]),
      spawnApi: async () => ({ pid: 9876 }),
      sleep: async () => undefined,
    },
  });
  assert.deepEqual(signals, [[4321, 'SIGTERM']]);
  assert.equal(result.reachable, true);
});

test('never signals an incompatible unowned listener', async () => {
  const signal = mock.fn();
  const result = await ensureApi({
    host: '127.0.0.1',
    port: 8899,
    ownerFile: '/tmp/argus-owner.json',
    dependencies: {
      probeApi: async () => staleProbe,
      readOwnedApi: async () => null,
      signal,
    },
  });
  assert.equal(signal.mock.callCount(), 0);
  assert.match(result.message, /ownership could not be proven/);
});
```

Add a third test where SIGTERM is sent but the listener never disappears;
assert no spawn occurs and the message says graceful shutdown timed out.

- [ ] **Step 2: Run the recovery tests and verify they fail**

```bash
cd frontend/tui
node --import tsx --test test/protocol.test.ts
```

Expected: FAIL because `ensureApi()` has no ownership-aware recovery.

- [ ] **Step 3: Add opt-in owner-file argument**

Extend `Args` and `parseArgs()`:

```ts
ownerFile?: string;
// default
ownerFile: process.env.ARGUS_TUI_API_OWNER_FILE,
```

Pass it from `cli.tsx` to `ensureApi()`. Do not add a public CLI flag; this is
an environment-scoped deployment control.

- [ ] **Step 4: Implement graceful recovery**

Refactor `ensureApi.ts` so process operations are injectable in tests. On an
incompatible local endpoint:

1. If `ownerFile` is absent, preserve the current refusal.
2. Call `readOwnedApi()` with the resolved backend binary.
3. If ownership fails, return an error containing host, port, detected release,
   expected release, and `ownership could not be proven`.
4. Send only `SIGTERM` to the recorded PID.
5. Probe every 250 ms for at most 8 seconds, stopping when unreachable.
6. If still reachable, return `graceful shutdown timed out`; do not spawn.
7. Spawn the current backend and atomically write its new PID record.
8. Reuse the existing compatibility polling and require a matching release.

When the existing endpoint is compatible, leave it running and refresh no
ownership metadata.

- [ ] **Step 5: Run protocol and argument tests**

```bash
cd frontend/tui
node --import tsx --test test/protocol.test.ts test/args.test.ts \
  test/apiOwnership.test.ts
npm run typecheck --silent
```

Expected: all tests PASS and TypeScript exits 0.

- [ ] **Step 6: Commit safe recovery**

```bash
git add frontend/tui/src/ensureApi.ts frontend/tui/src/args.ts \
  frontend/tui/src/cli.tsx frontend/tui/test/protocol.test.ts \
  frontend/tui/test/args.test.ts
git commit -m "fix(tui): safely replace owned stale WebAPI"
```

### Task 3: Install and verify the directory-local wrapper

**Files:**
- Modify local environment: `/home/argustest/.bashrc`
- Modify generated release files produced by `scripts/build_release.py`

**Interfaces:**
- Consumes: `ARGUS_TUI_API_OWNER_FILE` recovery support.
- Produces: a working isolated `argus` command under `argustest2`.

- [ ] **Step 1: Build one matching release**

```bash
cd /home/argustest/argustest2/argus-skill-latest
python scripts/build_release.py
```

Expected: Web and TUI artifacts report one matching release ID.

- [ ] **Step 2: Add the owner file to the existing local wrapper**

In the existing `local-argus-override` block of `/home/argustest/.bashrc`, add:

```bash
ARGUS_TUI_API_OWNER_FILE="$__argus_local_repo/.argus-skill/webapi-8899.owner.json" \
```

Keep these existing assignments unchanged:

```bash
ARGUS_SKILL_BIN="$__argus_local_repo/.venv/bin/argus-skill"
ARGUS_TUI_PORT="8899"
ARGUS_SKILL_HOME="$__argus_local_repo/.argus-skill"
ARGUS_SKILL_WEB_SESSION_ROOTS="$HOME/.argus-skill"
COPILOT_HOME="$HOME/.argus-skill/copilot-home"
```

- [ ] **Step 3: Retire the current legacy 8899 process explicitly**

Discover the listener and verify it before signaling:

```bash
pid="$(ss -ltnp '( sport = :8899 )' | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p')"
ps -o pid,cmd -p "$pid"
readlink "/proc/$pid/cwd"
```

Proceed only if the command is `argus-skill --web --web-port 8899` and the cwd
is `/home/argustest/argustest2`. Then run:

```bash
kill "$pid"
for _ in $(seq 1 32); do
  ss -ltn '( sport = :8899 )' | grep -q LISTEN || break
  sleep 0.25
done
! ss -ltn '( sport = :8899 )' | grep -q LISTEN
```

Expected: the exact verified PID exits and port 8899 is no longer listening.
If either identity check fails or the listener remains, stop; do not escalate.

- [ ] **Step 4: Verify a fresh interactive shell uses the intended checkout**

```bash
cd /home/argustest/argustest2
bash -lic 'type argus; printf "%s\n" "$__argus_local_repo"'
```

Expected: `argus` is the local function and the repo is
`/home/argustest/argustest2/argus-skill-latest`.

- [ ] **Step 5: Launch a headless smoke and verify release identity**

```bash
cd /home/argustest/argustest2
bash -lic 'argus --once --json --count 1'
curl -fsS http://127.0.0.1:8899/api/meta
```

Expected: the smoke exits 0; `/api/meta` reports the same release ID embedded in
`argus-skill-latest/argus_skill/release_manifest.json`; the owner record names
the live 8899 PID.

- [ ] **Step 6: Confirm public services were untouched**

```bash
ss -ltnp '( sport = :8798 or sport = :8799 )'
```

Expected: the pre-existing 8798 WebAPI and 8799 proxy PIDs are unchanged.

- [ ] **Step 7: Commit release artifacts**

```bash
cd /home/argustest/argustest2/argus-skill-latest
git add argus_skill/release_manifest.json argus_skill/_frontend \
  frontend/core/src/release.generated.ts frontend/tui/bundle frontend/web/dist
git commit -m "build: publish local API recovery release"
```
