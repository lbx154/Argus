import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import {
  inspectApiMeta, requireReadSnapshot, requireProjectList, requireProjectCosts,
  validProjectId, READ_API,
} from '../src/index.js';

const fixtures = JSON.parse(readFileSync(new URL('../fixtures/read-api.json', import.meta.url), 'utf8')) as Record<string, unknown>;

test('shared metadata compatibility depends on protocol, capabilities and snapshot schema', () => {
  const meta = fixtures.meta as { runtime: object; protocol: object };
  assert.equal(inspectApiMeta(meta).compatible, true);
  assert.equal(inspectApiMeta({ ...meta, runtime: { ...meta.runtime, package_version: 'another-version' } }).compatible, true);
  assert.equal(inspectApiMeta({ ...meta, protocol: { ...meta.protocol, major: 999 } }).compatible, false);
  assert.equal(inspectApiMeta({ ...meta, capabilities: [] }).compatible, false);
  assert.equal(inspectApiMeta({ ...meta, snapshot_schema_version: -1 }).compatible, false);
});

test('the new read-only service cannot pass the full WebAPI handshake', () => {
  assert.equal(inspectApiMeta({ ...(fixtures.meta as object), service: READ_API.service }).compatible, false);
});

test('shared read projections retain unknown additive fields but reject missing structure', () => {
  assert.equal(requireProjectList(fixtures.projects).projects.length, 1);
  assert.equal(requireProjectCosts(fixtures.costs).projects.length, 1);
  assert.equal(requireReadSnapshot(fixtures.snapshot, 's-fixture').session.id, 's-fixture');
  assert.throws(() => requireReadSnapshot(fixtures.snapshot, 's-wrong'), /invalid/);
  const malformed = { ...(fixtures.snapshot as object), backlog: {} };
  assert.throws(() => requireReadSnapshot(malformed, 's-fixture'), /invalid/);
  assert.throws(() => requireReadSnapshot({ ...(fixtures.snapshot as object), schema_version: 7 }, 's-fixture'), /incompatible snapshot schema/);
  assert.throws(() => requireProjectList({ projects: [{}], local_cwd: '' }), /invalid/);
  assert.throws(() => requireProjectCosts({ projects: [], generated_at: NaN }), /invalid/);
});

test('project identifiers are direct-child names with a bounded Unicode length', () => {
  for (const value of ['', '.', '..', 'a/b', 'a\\b', '\0', '\ud800', 'x'.repeat(256)]) assert.equal(validProjectId(value), false, value);
  for (const value of ['s-fixture', '研究 🌍', '🌍'.repeat(255)]) assert.equal(validProjectId(value), true, value);
});

test('project list carries the operator preview and nullable recorded-call spend', () => {
  const original = fixtures.projects as { projects: object[]; local_cwd: string };
  const fields = { last_message: 'Excel or CSV?', spend_usd: null, spend_status: 'unpriced' };
  const list = { ...original, projects: [{ ...original.projects[0], ...fields }] };
  const row = requireProjectList(JSON.parse(JSON.stringify(list))).projects[0]!;
  assert.deepEqual([row.last_message, row.spend_usd, row.spend_status], ['Excel or CSV?', null, 'unpriced']);
  for (const invalid of [{ last_message: {} }, { spend_usd: NaN }, { spend_status: 1 }]) {
    assert.throws(() => requireProjectList({ ...list, projects: [{ ...list.projects[0], ...invalid }] }), /invalid project row/);
  }
});
