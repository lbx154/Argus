import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { checkUpgradeContract, main } from '../scripts/check-upgrade-contract.mjs';

// This suite compares public configuration contracts, NOT signed installer bytes.
// A string stands in for a public key; no keys are generated or signatures issued.
function configs() {
  const baseline = {
    version: '0.1.10', identifier: 'cn.argusbot.desktop', productName: 'Argus',
    bundle: { createUpdaterArtifacts: true, windows: { allowDowngrades: false, nsis: { installMode: 'currentUser' } } },
    plugins: { updater: { pubkey: 'synthetic-public-configuration', endpoints: [
      'https://github.com/lbx154/Argus/releases/latest/download/latest.json',
    ] } },
  };
  return {
    baseline, candidate: { ...structuredClone(baseline), version: '1.0.0' },
    preview: { identifier: 'cn.argusbot.desktop.preview.v1', productName: 'Argus Preview',
      version: '1.0.0-preview.1', bundle: { createUpdaterArtifacts: false } },
  };
}

test('0.1.10 -> 1.0.0 source compatibility never claims installation or publication readiness', () => {
  const report = checkUpgradeContract(configs());
  assert.equal(report.status, 'static-contract-pass');
  assert.equal(report.releaseReady, false);
  assert.equal(report.installersExecuted, 0);
  assert.equal(report.networkRequests, 0);
  assert.deepEqual(report.expectedArtifacts, ['Argus-1.0.0-setup.exe', 'Argus-1.0.0-setup.exe.sig', 'latest.json']);
  assert.ok(report.remaining.includes('legacy-data-migration-and-recovery'));
  assert.ok(report.remaining.includes('publisher-signed-stable-build-and-public-key-verification'));
  assert.equal(JSON.stringify(report).includes('synthetic-public-configuration'), false);
});

for (const [label, change, error] of [
  ['wrong baseline', c => { c.baseline.version = '0.1.9'; }, /Baseline/],
  ['preview version', c => { c.candidate.version = '1.0.0-preview.1'; }, /stable 1.0.0/],
  ['application identifier', c => { c.candidate.identifier += '.new'; }, /identity/],
  ['preview product name', c => { c.candidate.productName = 'Argus Preview'; }, /identity/],
  ['disabled signing', c => { c.candidate.bundle.createUpdaterArtifacts = false; }, /signatures/],
  ['new installation scope', c => { c.candidate.bundle.windows.nsis.installMode = 'perMachine'; }, /scope/],
  ['downgrades enabled', c => { c.candidate.bundle.windows.allowDowngrades = true; }, /Downgrade/],
  ['replacement public key', c => { c.candidate.plugins.updater.pubkey = 'another-public-configuration'; }, /original updater public key/],
  ['empty trust anchor', c => { c.baseline.plugins.updater.pubkey = c.candidate.plugins.updater.pubkey = ''; }, /original updater public key/],
  ['different update endpoint', c => { c.candidate.plugins.updater.endpoints = ['https://example.invalid/latest.json']; }, /endpoint/],
  ['additional update endpoint', c => { c.candidate.plugins.updater.endpoints.push('https://example.invalid/latest.json'); }, /endpoint/],
  ['shared preview identity', c => { c.preview.identifier = c.candidate.identifier; }, /Preview/],
  ['signed preview overlay', c => { c.preview.bundle.createUpdaterArtifacts = true; }, /Preview/],
]) {
  test(`offline contract rejects ${label}`, () => {
    const values = configs(); change(values);
    assert.throws(() => checkUpgradeContract(values), error);
  });
}

test('CLI reads only explicit public Tauri configurations without creating output files', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-upgrade-contract-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const values = configs(); const args = [];
  for (const [name, value] of Object.entries(values)) {
    const folder = path.join(root, name); fs.mkdirSync(folder);
    const filename = path.join(folder, name === 'preview' ? 'tauri.preview.conf.json' : 'tauri.conf.json');
    fs.writeFileSync(filename, JSON.stringify(value));
    args.push(`--${name}-config`, filename);
  }
  assert.equal(main(args).status, 'static-contract-pass');
  assert.deepEqual(fs.readdirSync(root).sort(), ['baseline', 'candidate', 'preview']);
  for (const name of ['baseline', 'candidate', 'preview']) assert.equal(fs.readdirSync(path.join(root, name)).length, 1);
});

test('no default baseline and no ambiguous or arbitrary file inputs', () => {
  assert.throws(() => main([]), /explicit reviewed baseline/);
  assert.throws(() => main(['--baseline-config']), /Usage/);
  assert.throws(() => main(['--baseline-config', 'x', '--baseline-config', 'y']), /Usage/);
  assert.throws(() => main(['--baseline-config', 'arbitrary.json']), /public configuration/);
  assert.throws(() => main(['--publish', 'yes']), /Usage/);
});
