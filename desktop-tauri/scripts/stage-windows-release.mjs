import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { copyVerified } from './verified-copy.mjs';
import { verifyTauriSignature } from './verify-updater-signature.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function noLinkedParents(filename) {
  let current = path.resolve(filename);
  while (true) {
    try {
      if (fs.lstatSync(current).isSymbolicLink()) throw new Error('Release paths must not traverse links or junctions.');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
}

function smallText(filename, limit = 1024 * 1024) {
  noLinkedParents(filename);
  const fd = fs.openSync(filename, 'r');
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > limit) throw new Error('Release metadata is not a bounded regular file.');
    const buffer = Buffer.alloc(limit + 1);
    const count = fs.readSync(fd, buffer, 0, buffer.length, 0);
    if (count !== stat.size) throw new Error('Release metadata changed while reading.');
    return buffer.subarray(0, count).toString('utf8').trim();
  } finally { fs.closeSync(fd); }
}

function assertInstaller(filename) {
  const fd = fs.openSync(filename, 'r');
  try {
    const stat = fs.fstatSync(fd);
    const header = Buffer.alloc(64);
    if (!stat.isFile() || stat.size < 1024 * 1024 || fs.readSync(fd, header, 0, 64, 0) !== 64
        || header.toString('ascii', 0, 2) !== 'MZ') throw new Error('NSIS installer is not a release-sized PE executable.');
    const offset = header.readUInt32LE(60);
    const pe = Buffer.alloc(6);
    if (offset < 64 || offset > stat.size - 6 || fs.readSync(fd, pe, 0, 6, offset) !== 6
        || !pe.subarray(0, 4).equals(Buffer.from([80, 69, 0, 0]))
        || ![0x14c, 0x8664].includes(pe.readUInt16LE(4))) {
      throw new Error('NSIS installer has an invalid Windows PE header.');
    }
  } finally { fs.closeSync(fd); }
}

function writeNew(filename, content) {
  const fd = fs.openSync(filename, 'wx');
  try { fs.writeFileSync(fd, content, 'utf8'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
}

export function windowsBundleDirectory(root = desktop, env = process.env) {
  const target = env.CARGO_TARGET_DIR
    ? path.resolve(root, env.CARGO_TARGET_DIR) : path.join(root, 'src-tauri', 'target');
  const triple = env.CARGO_BUILD_TARGET;
  if (triple && triple !== 'x86_64-pc-windows-msvc') throw new Error('Windows release staging requires the x64 MSVC target.');
  return path.join(target, ...(triple ? [triple] : []), 'release', 'bundle', 'nsis');
}

/** Publish locally into a NEW directory only. Missing/invalid signatures never
 * clear a previous release, and latest.json is written only after copied bytes
 * have independently passed public-key verification.
 */
export function stageWindowsRelease({
  desktopRoot = desktop, bundleDir, outputDir, notes, now = new Date(),
} = {}) {
  const root = path.resolve(desktopRoot);
  const bundle = path.resolve(bundleDir || windowsBundleDirectory(root));
  const output = path.resolve(outputDir || path.join(root, 'release'));
  for (const filename of [root, bundle, output]) noLinkedParents(filename);
  const overlaps = (a, b) => a === b || a.startsWith(b + path.sep) || b.startsWith(a + path.sep);
  if (overlaps(output, bundle)) throw new Error('Release output must be separate from its build inputs.');
  if (fs.existsSync(output)) throw new Error('Release output already exists; choose a new directory.');
  const version = JSON.parse(smallText(path.join(root, 'package.json'))).version;
  if (typeof version !== 'string' || !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)) {
    throw new Error('A stable three-part release version is required.');
  }
  const config = JSON.parse(smallText(path.join(root, 'src-tauri', 'tauri.conf.json')));
  if (config.version !== version || config.bundle?.createUpdaterArtifacts !== true) {
    throw new Error('Tauri version and signed-update configuration must match the release package.');
  }
  const escapedVersion = version.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp(`^Argus_${escapedVersion}_x64-setup\\.exe$`);
  const installers = fs.readdirSync(bundle).filter(name => pattern.test(name));
  if (installers.length !== 1) throw new Error(`Expected exactly one NSIS installer for version ${version}; found ${installers.length}.`);
  const input = path.join(bundle, installers[0]);
  noLinkedParents(input);
  assertInstaller(input);
  const signature = smallText(input + '.sig', 8192);
  const verified = verifyTauriSignature(input, signature, config.plugins?.updater?.pubkey);
  const installerName = `Argus-${version}-setup.exe`;
  const signatureName = installerName + '.sig';
  const manifest = {
    version,
    notes: String(notes || `Argus ${version} Windows desktop update.`).slice(0, 4000),
    pub_date: now.toISOString(),
    platforms: {
      'windows-x86_64': {
        url: `https://github.com/lbx154/Argus/releases/download/v${version}/${installerName}`,
        signature,
      },
    },
  };

  // Validation above has no output-side effects. Reserve the new destination
  // exclusively; a failed copy leaves evidence but never a usable manifest.
  fs.mkdirSync(path.dirname(output), { recursive: true });
  noLinkedParents(output);
  fs.mkdirSync(output);
  const copied = copyVerified(input, path.join(output, installerName));
  const copiedSignature = copyVerified(input + '.sig', path.join(output, signatureName));
  const stagedSignature = smallText(path.join(output, signatureName), 8192);
  const staged = verifyTauriSignature(path.join(output, installerName), stagedSignature, config.plugins.updater.pubkey);
  if (copied.sha256 !== verified.sha256 || staged.sha256 !== verified.sha256 || stagedSignature !== signature) {
    throw new Error('Release inputs changed during staging; no update manifest was produced.');
  }
  const manifestText = JSON.stringify(manifest, null, 2) + '\n';
  const manifestDigest = createHash('sha256').update(manifestText).digest('hex');
  writeNew(path.join(output, 'SHA256SUMS'), [
    `${staged.sha256}  ${installerName}`,
    `${copiedSignature.sha256}  ${signatureName}`,
    `${manifestDigest}  latest.json`,
  ].join('\n') + '\n');
  writeNew(path.join(output, 'latest.json'), manifestText);
  return { version, output, installer: installerName, bytes: staged.bytes, sha256: staged.sha256, signature_verified: true };
}

export function main(args = process.argv.slice(2)) {
  const options = {};
  for (let index = 0; index < args.length; index += 2) {
    const names = { '--bundle-dir': 'bundleDir', '--output-dir': 'outputDir' };
    if (!names[args[index]] || !args[index + 1] || args[index + 1].startsWith('--')) {
      throw new Error('Usage: stage-windows-release.mjs [--bundle-dir PATH] [--output-dir NEW_PATH]');
    }
    if (options[names[args[index]]] !== undefined) throw new Error('Duplicate staging option.');
    options[names[args[index]]] = args[index + 1];
  }
  return stageWindowsRelease({ ...options, notes: process.env.ARGUS_DESKTOP_UPDATE_NOTES });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { console.log(JSON.stringify(main(), null, 2)); }
  catch (error) { console.error(`Windows release not staged: ${error.message}`); process.exitCode = 1; }
}
