import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash, generateKeyPairSync, randomBytes, sign } from 'node:crypto';
import test from 'node:test';
import { stageWindowsRelease, windowsBundleDirectory, main } from '../scripts/stage-windows-release.mjs';
import { verifyTauriSignature } from '../scripts/verify-updater-signature.mjs';

const wrapped = text => Buffer.from(text, 'utf8').toString('base64');

// Synthetic test-only keys stay in memory; no private material is written to a
// file, environment, output or release fixture. Production staging only verifies.
function signingFixture() {
  const pair = generateKeyPairSync('ed25519');
  const keyId = randomBytes(8);
  const publicBytes = pair.publicKey.export({ type: 'spki', format: 'der' }).subarray(-32);
  const pubkey = wrapped('untrusted comment: generated test public key\n'
    + Buffer.concat([Buffer.from('Ed'), keyId, publicBytes]).toString('base64') + '\n');
  return {
    pubkey,
    signature(bytes) {
      const packet = sign(null, createHash('blake2b512').update(bytes).digest(), pair.privateKey);
      const comment = 'timestamp:1700000000\tfile:test-only.exe\tprehashed';
      return wrapped([
        'untrusted comment: generated test signature',
        Buffer.concat([Buffer.from('ED'), keyId, packet]).toString('base64'),
        'trusted comment: ' + comment,
        sign(null, Buffer.concat([packet, Buffer.from(comment)]), pair.privateKey).toString('base64'),
      ].join('\n') + '\n');
    },
  };
}

function fixture(t, { signed = true, filename = 'Argus_0.1.7_x64-setup.exe', validPe = true } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-windows-release-fixture-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const desktopRoot = path.join(root, 'source');
  const bundleDir = path.join(root, 'bundle');
  fs.mkdirSync(path.join(desktopRoot, 'src-tauri'), { recursive: true });
  fs.mkdirSync(bundleDir);
  const signer = signingFixture();
  fs.writeFileSync(path.join(desktopRoot, 'package.json'), JSON.stringify({ version: '0.1.7' }));
  const config = { version: '0.1.7', bundle: { createUpdaterArtifacts: true }, plugins: { updater: { pubkey: signer.pubkey } } };
  fs.writeFileSync(path.join(desktopRoot, 'src-tauri', 'tauri.conf.json'), JSON.stringify(config));
  const bytes = Buffer.alloc(1024 * 1024 + 128, 7);
  if (validPe) {
    bytes.write('MZ'); bytes.writeUInt32LE(128, 60);
    Buffer.from([80, 69, 0, 0, 0x4c, 1]).copy(bytes, 128);
  }
  const installer = path.join(bundleDir, filename);
  fs.writeFileSync(installer, bytes);
  if (signed) fs.writeFileSync(installer + '.sig', signer.signature(bytes) + '\n');
  return { desktopRoot, bundleDir, outputDir: path.join(root, 'new release'), root, installer, signer, config };
}

function updateConfig(f, config) {
  fs.writeFileSync(path.join(f.desktopRoot, 'src-tauri', 'tauri.conf.json'), JSON.stringify(config));
}

test('verifier accepts the upstream minisign prehashed interoperability vector', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-minisign-vector-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const filename = path.join(root, 'sample.bin'); fs.writeFileSync(filename, 'test');
  const pubkey = wrapped('untrusted comment: minisign public key\nRWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3\n');
  const signature = wrapped([
    'untrusted comment: minisign interoperability fixture',
    'RUQf6LRCGA9i559r3g7V1qNyJDApGip8MfqcadIgT9CuhV3EMhHoN1mGTkUidF/z7SrlQgXdy8ofjb7bNJJylDOocrCo8KLzZwo=',
    'trusted comment: timestamp:1556193335\tfile:test',
    'y/rUw2y8/hOUYjZU71eHp/Wo1KZ40fGy2VJEDl34XMJM+TX48Ss/17u3IvIfbVR1FkZZSNCisQbuQY+bHwhEBg==',
  ].join('\n'));
  assert.deepEqual(verifyTauriSignature(filename, signature, pubkey), {
    bytes: 4, sha256: createHash('sha256').update('test').digest('hex'),
  });
});

test('staging verifies copied fixture bytes and emits Windows-only metadata and hashes', t => {
  const f = fixture(t);
  const result = stageWindowsRelease({ ...f, now: new Date('2026-09-14T00:00:00Z') });
  assert.equal(result.signature_verified, true);
  const manifest = JSON.parse(fs.readFileSync(path.join(f.outputDir, 'latest.json'), 'utf8'));
  assert.deepEqual(Object.keys(manifest.platforms), ['windows-x86_64']);
  assert.equal(manifest.version, '0.1.7');
  assert.equal(manifest.pub_date, '2026-09-14T00:00:00.000Z');
  assert.equal(manifest.platforms['windows-x86_64'].url,
    'https://github.com/lbx154/Argus/releases/download/v0.1.7/Argus-0.1.7-setup.exe');
  const checked = verifyTauriSignature(path.join(f.outputDir, result.installer),
    manifest.platforms['windows-x86_64'].signature, f.signer.pubkey);
  assert.equal(result.sha256, checked.sha256);
  for (const line of fs.readFileSync(path.join(f.outputDir, 'SHA256SUMS'), 'utf8').trim().split('\n')) {
    const [expected, name] = line.split('  ');
    assert.equal(createHash('sha256').update(fs.readFileSync(path.join(f.outputDir, name))).digest('hex'), expected);
  }
});

test('missing signatures do not create an output directory', t => {
  const f = fixture(t, { signed: false });
  assert.throws(() => stageWindowsRelease(f));
  assert.equal(fs.existsSync(f.outputDir), false);
});

test('an existing release is never cleared, even with an unsigned input', t => {
  const f = fixture(t, { signed: false }); fs.mkdirSync(f.outputDir);
  fs.writeFileSync(path.join(f.outputDir, 'keep.txt'), 'previous reviewed delivery');
  assert.throws(() => stageWindowsRelease(f), /already exists/);
  assert.equal(fs.readFileSync(path.join(f.outputDir, 'keep.txt'), 'utf8'), 'previous reviewed delivery');
});

test('a package signed by another key cannot issue an update manifest', t => {
  const f = fixture(t); f.config.plugins.updater.pubkey = signingFixture().pubkey;
  updateConfig(f, f.config);
  assert.throws(() => stageWindowsRelease(f), /embedded public key/);
  assert.equal(fs.existsSync(f.outputDir), false);
});

test('tampered installer bytes fail before output creation', t => {
  const f = fixture(t); fs.appendFileSync(f.installer, 'tampered');
  assert.throws(() => stageWindowsRelease(f), /package signature verification failed/);
  assert.equal(fs.existsSync(f.outputDir), false);
});

test('the trusted comment is authenticated, not just the package bytes', t => {
  const f = fixture(t);
  const parts = Buffer.from(fs.readFileSync(f.installer + '.sig', 'utf8').trim(), 'base64').toString('utf8').trim().split('\n');
  parts[2] = 'trusted comment: attacker changed the filename';
  fs.writeFileSync(f.installer + '.sig', wrapped(parts.join('\n')));
  assert.throws(() => stageWindowsRelease(f), /trusted-comment signature verification failed/);
  assert.equal(fs.existsSync(f.outputDir), false);
});

for (const filename of ['Argus_0.1.6_x64-setup.exe', 'Argus_0.1.7_arm64-setup.exe', 'Argus_0.1.7_x86-setup.exe']) {
  test(`staging never renames a stale or other-architecture installer: ${filename}`, t => {
    const f = fixture(t, { filename });
    assert.throws(() => stageWindowsRelease(f), /Expected exactly one NSIS installer for version/);
    assert.equal(fs.existsSync(f.outputDir), false);
  });
}

test('a signed non-installer is still not a Windows release', t => {
  const f = fixture(t, { validPe: false });
  assert.throws(() => stageWindowsRelease(f), /PE executable/);
  assert.equal(fs.existsSync(f.outputDir), false);
});

test('version/configuration drift blocks staging', t => {
  const f = fixture(t); f.config.version = '0.1.6'; updateConfig(f, f.config);
  assert.throws(() => stageWindowsRelease(f), /configuration must match/);
  assert.equal(fs.existsSync(f.outputDir), false);
});

test('release output cannot overlap build inputs', t => {
  const f = fixture(t);
  assert.throws(() => stageWindowsRelease({ ...f, outputDir: path.join(f.bundleDir, 'release') }), /separate/);
});

test('failed copy cannot leave a published latest.json', t => {
  const f = fixture(t);
  t.mock.method(fs, 'fsyncSync', () => { throw new Error('synthetic flush failure'); });
  assert.throws(() => stageWindowsRelease(f), /synthetic flush failure/);
  assert.equal(fs.existsSync(path.join(f.outputDir, 'latest.json')), false);
});

test('junction outputs are refused rather than writing through to another directory', t => {
  const f = fixture(t); const linked = path.join(f.root, 'linked');
  try { fs.symlinkSync(f.root, linked, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Host cannot create links'); throw error; }
  assert.throws(() => stageWindowsRelease({ ...f, outputDir: path.join(linked, 'release') }), /links or junctions/);
  assert.equal(fs.existsSync(path.join(f.root, 'release')), false);
});

test('Cargo target locations are respected, including an explicit MSVC target triple', () => {
  const root = path.resolve('desktop-fixture'); const target = path.resolve('build-fixture');
  assert.equal(windowsBundleDirectory(root, { CARGO_TARGET_DIR: target }), path.join(target, 'release/bundle/nsis'));
  assert.equal(windowsBundleDirectory(root, { CARGO_TARGET_DIR: target, CARGO_BUILD_TARGET: 'x86_64-pc-windows-msvc' }),
    path.join(target, 'x86_64-pc-windows-msvc/release/bundle/nsis'));
  assert.throws(() => windowsBundleDirectory(root, { CARGO_BUILD_TARGET: 'aarch64-pc-windows-msvc' }), /x64 MSVC/);
});

test('ambiguous CLI arguments fail before touching a build directory', () => {
  assert.throws(() => main(['--output-dir']), /Usage/);
  assert.throws(() => main(['--output-dir', 'a', '--output-dir', 'b']), /Duplicate/);
});
