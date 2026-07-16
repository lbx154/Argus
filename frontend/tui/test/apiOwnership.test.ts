import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';
import {
  claimApiOwnership,
  defaultApiOwnershipPath,
  readOwnedApi,
  writeOwnershipRecord,
  removeOwnershipRecord,
} from '../src/apiOwnership.js';
import type { ApiOwnershipRecord } from '../src/apiOwnership.js';

// ── helpers ────────────────────────────────────────────────────────────────

const BASE_RECORD: ApiOwnershipRecord = {
  schema: 1,
  pid: 4321,
  host: '127.0.0.1',
  port: 8899,
  backendBin: '/repo/.venv/bin/argus-skill',
  startedAt: '2026-07-14T00:00:00Z',
};

const aliveInspect = async () => ({
  alive: true,
  argv: [BASE_RECORD.backendBin, '--web', '--web-port', String(BASE_RECORD.port)],
});

const deadInspect = async () => ({ alive: false, argv: [] as string[] });

async function tmpOwner(record: unknown): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'owner.json');
  await writeFile(ownerFile, JSON.stringify(record));
  return ownerFile;
}

test('default ownership path is stable per local host and port', () => {
  assert.equal(
    defaultApiOwnershipPath('127.0.0.1', 8909, {
      HOME: '/home/alex',
      ARGUS_SKILL_HOME: '/state/argus',
    }),
    join(resolve('/state/argus'), 'runtime', 'webapi-127.0.0.1-8909.owner.json'),
  );
  assert.equal(
    defaultApiOwnershipPath('localhost', 8799, { HOME: '/home/alex' }),
    '/home/alex/.argus-skill/runtime/webapi-localhost-8799.owner.json',
  );
  assert.equal(defaultApiOwnershipPath('10.0.0.5', 8799, { HOME: '/home/alex' }), undefined);
});

// ── Task-brief required tests ──────────────────────────────────────────────

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

// ── Negative tests ─────────────────────────────────────────────────────────

test('rejects malformed JSON', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'owner.json');
  await writeFile(ownerFile, '{not valid json!!');
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: '127.0.0.1',
    port: 8899,
    backendBin: BASE_RECORD.backendBin,
    inspect: aliveInspect,
  }), null);
});

test('rejects a dead PID', async () => {
  const ownerFile = await tmpOwner(BASE_RECORD);
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: deadInspect,
  }), null);
});

test('rejects host mismatch', async () => {
  const ownerFile = await tmpOwner(BASE_RECORD);
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: '192.168.1.100',
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: aliveInspect,
  }), null);
});

test('rejects port mismatch', async () => {
  const ownerFile = await tmpOwner(BASE_RECORD);
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: 9999,
    backendBin: BASE_RECORD.backendBin,
    inspect: aliveInspect,
  }), null);
});

test('rejects backend binary path mismatch', async () => {
  const ownerFile = await tmpOwner(BASE_RECORD);
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: '/other/.venv/bin/argus-skill',
    inspect: aliveInspect,
  }), null);
});

test('rejects when argv is missing --web flag', async () => {
  const ownerFile = await tmpOwner(BASE_RECORD);
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: async () => ({
      alive: true,
      argv: [BASE_RECORD.backendBin, '--web-port', String(BASE_RECORD.port)],
    }),
  }), null);
});

test('rejects when argv has wrong --web-port', async () => {
  const ownerFile = await tmpOwner(BASE_RECORD);
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: async () => ({
      alive: true,
      argv: [BASE_RECORD.backendBin, '--web', '--web-port', '7777'],
    }),
  }), null);
});

test('rejects when schema is not 1', async () => {
  const ownerFile = await tmpOwner({ ...BASE_RECORD, schema: 2 });
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: aliveInspect,
  }), null);
});

test('rejects when pid is not a positive integer', async () => {
  const ownerFile = await tmpOwner({ ...BASE_RECORD, pid: -1 });
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: aliveInspect,
  }), null);
});

test('rejects a missing ownership file (no throw)', async () => {
  assert.equal(await readOwnedApi({
    path: '/nonexistent/path/owner.json',
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: aliveInspect,
  }), null);
});

// ── writeOwnershipRecord tests ─────────────────────────────────────────────

test('writeOwnershipRecord writes readable file and readOwnedApi accepts it', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'owner.json');
  await writeOwnershipRecord(ownerFile, BASE_RECORD);
  const owned = await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: aliveInspect,
  });
  assert.equal(owned?.pid, BASE_RECORD.pid);
});

test('writeOwnershipRecord creates a private POSIX file or regular Windows file', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'owner.json');
  await writeOwnershipRecord(ownerFile, BASE_RECORD);
  const info = await stat(ownerFile);
  if (process.platform === 'win32') assert.ok(info.isFile());
  else assert.equal(info.mode & 0o777, 0o600);
});

test('writeOwnershipRecord creates a missing private runtime directory', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'nested', 'runtime', 'owner.json');
  await writeOwnershipRecord(ownerFile, BASE_RECORD);
  assert.equal(JSON.parse(await readFile(ownerFile, 'utf-8')).pid, BASE_RECORD.pid);
  const runtimeInfo = await stat(join(root, 'nested', 'runtime'));
  if (process.platform === 'win32') assert.ok(runtimeInfo.isDirectory());
  else assert.equal(runtimeInfo.mode & 0o077, 0);
});

test('claimApiOwnership records only a verified live endpoint', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'runtime', 'owner.json');
  assert.equal(await claimApiOwnership({
    path: ownerFile,
    ...BASE_RECORD,
    inspect: aliveInspect,
  }), true);
  assert.equal(JSON.parse(await readFile(ownerFile, 'utf-8')).pid, BASE_RECORD.pid);

  const rejectedFile = join(root, 'runtime', 'rejected.json');
  assert.equal(await claimApiOwnership({
    path: rejectedFile,
    ...BASE_RECORD,
    inspect: async () => ({
      alive: true,
      argv: ['/usr/bin/python', '-m', 'http.server', String(BASE_RECORD.port)],
    }),
  }), false);
  await assert.rejects(() => readFile(rejectedFile, 'utf-8'), /ENOENT/);
});

// ── removeOwnershipRecord tests ────────────────────────────────────────────

test('removeOwnershipRecord deletes the file', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'owner.json');
  await writeOwnershipRecord(ownerFile, BASE_RECORD);
  await removeOwnershipRecord(ownerFile);
  // file is gone — readOwnedApi returns null
  assert.equal(await readOwnedApi({
    path: ownerFile,
    host: BASE_RECORD.host,
    port: BASE_RECORD.port,
    backendBin: BASE_RECORD.backendBin,
    inspect: aliveInspect,
  }), null);
});

test('removeOwnershipRecord is a no-op when file does not exist', async () => {
  await assert.doesNotReject(() => removeOwnershipRecord('/nonexistent/owner.json'));
});
