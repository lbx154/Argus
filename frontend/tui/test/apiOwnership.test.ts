import assert from 'node:assert/strict';
import { mkdtemp, writeFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';
import { readOwnedApi, writeOwnershipRecord, removeOwnershipRecord } from '../src/apiOwnership.js';
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

test('writeOwnershipRecord creates file with mode 0o600', async () => {
  const root = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  const ownerFile = join(root, 'owner.json');
  await writeOwnershipRecord(ownerFile, BASE_RECORD);
  const info = await stat(ownerFile);
  assert.equal(info.mode & 0o777, 0o600);
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
