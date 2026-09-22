import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import {
  inspectApiMeta, requireReadSnapshot, requireProjectList, requireProjectCosts,
  validProjectId, READ_API,
} from '../src/index.js';

const fixtures = JSON.parse(readFileSync(new URL('../fixtures/read-api.json', import.meta.url), 'utf8')) as Record<string, unknown>;

test('shared metadata validation checks releases when requested and preserves drift warnings', () => {
  assert.equal(inspectApiMeta(fixtures.meta, { releaseId: 'fixture-release' }).compatible, true);
  assert.equal(inspectApiMeta(fixtures.meta, { releaseId: 'different-release' }).compatible, false);
  const changed = structuredClone(fixtures.meta) as { runtime: { release_matches_source: boolean } };
  changed.runtime.release_matches_source = false;
  assert.ok(inspectApiMeta(changed).warning);
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
  const partial = { ...(fixtures.snapshot as object), partial: true, diagnostics: [{ section: 'daemon', error_type: 'OSError', message: 'busy' }], request_usage: null };
  assert.equal(requireReadSnapshot(partial, 's-fixture').partial, true);
  assert.throws(() => requireProjectList({ projects: [{}], local_cwd: '' }), /invalid/);
  assert.throws(() => requireProjectCosts({ projects: [], generated_at: NaN }), /invalid/);
});

test('project identifiers are direct-child names with a bounded Unicode length', () => {
  for (const value of ['', '.', '..', 'a/b', 'a\\b', '\0', '\ud800', 'x'.repeat(256)]) assert.equal(validProjectId(value), false, value);
  for (const value of ['s-fixture', '研究 🌍', '🌍'.repeat(255)]) assert.equal(validProjectId(value), true, value);
});
