// Offline source-contract check only. Never fetches latest.json, starts an
// installer, stages a release, signs bytes, or reads private signing material.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ENDPOINT = 'https://github.com/lbx154/Argus/releases/latest/download/latest.json';
const IDENTIFIER = 'cn.argusbot.desktop';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function requireCheck(condition, message) {
  if (!condition) throw new Error(message);
}

export function checkUpgradeContract({ baseline, candidate, preview }) {
  requireCheck(baseline?.version === '0.1.10', 'Baseline must be the reviewed 0.1.10 configuration.');
  requireCheck(candidate?.version === '1.0.0', 'Candidate must be stable 1.0.0, not a renamed preview.');
  for (const config of [baseline, candidate]) {
    requireCheck(config.identifier === IDENTIFIER && config.productName === 'Argus', 'Stable application identity changed.');
    requireCheck(config.bundle?.createUpdaterArtifacts === true, 'Stable updater signatures must remain enabled.');
    requireCheck(config.bundle?.windows?.nsis?.installMode === 'currentUser', 'Windows installation scope changed.');
    requireCheck(config.bundle?.windows?.allowDowngrades === false, 'Downgrade protection changed.');
    requireCheck(JSON.stringify(config.plugins?.updater?.endpoints) === JSON.stringify([ENDPOINT]), 'Stable update endpoint changed.');
  }
  const key = baseline.plugins?.updater?.pubkey;
  requireCheck(typeof key === 'string' && key.trim().length > 0 && candidate.plugins?.updater?.pubkey === key,
    'The original updater public key must be retained.');
  requireCheck(preview?.identifier === 'cn.argusbot.desktop.preview.v1'
    && preview.productName === 'Argus Preview' && /^1\.0\.0-preview\.[1-9]\d*$/.test(preview.version)
    && preview.bundle?.createUpdaterArtifacts === false, 'Preview must remain a separate unsigned identity.');
  return {
    status: 'static-contract-pass', from: '0.1.10', to: '1.0.0', platform: 'windows-x86_64',
    originalIdentityRetained: true, originalPublicKeyRetained: true, originalEndpointRetained: true,
    previewIdentitySeparate: true, networkRequests: 0, installersExecuted: 0,
    expectedArtifacts: ['Argus-1.0.0-setup.exe', 'Argus-1.0.0-setup.exe.sig', 'latest.json'],
    releaseReady: false,
    remaining: [
      'operator-preview-acceptance-and-separate-publication-approval',
      'legacy-data-migration-and-recovery',
      'real-cli-compatibility',
      'publisher-signed-stable-build-and-public-key-verification',
      'isolated-nsis-install-uninstall-and-0.1.10-upgrade',
      'post-publication-real-update-chain',
    ],
  };
}

function configFile(filename, expectedName) {
  requireCheck(path.basename(filename) === expectedName, 'Only an explicitly named Tauri public configuration is accepted.');
  for (let current = path.resolve(filename); ; current = path.dirname(current)) {
    requireCheck(!/(?:^\.env(?:\.|$)|^credentials\.|private.*key|token|secret)/i.test(path.basename(current)),
      'Sensitive configuration paths are not accepted.');
    requireCheck(!fs.lstatSync(current).isSymbolicLink(), 'Configuration paths must not traverse links or junctions.');
    if (path.dirname(current) === current) break;
  }
  const stat = fs.lstatSync(filename);
  requireCheck(stat.isFile() && stat.nlink === 1 && stat.size <= 1024 * 1024, 'Configuration must be a bounded regular file.');
  return JSON.parse(fs.readFileSync(filename, 'utf8'));
}

export function main(args = process.argv.slice(2)) {
  const names = { '--baseline-config': 'baseline', '--candidate-config': 'candidate', '--preview-config': 'preview' };
  const paths = {};
  for (let i = 0; i < args.length; i += 2) {
    const name = names[args[i]];
    if (!name || !args[i + 1] || args[i + 1].startsWith('--') || paths[name]) {
      throw new Error('Usage: check-upgrade-contract.mjs --baseline-config PATH [--candidate-config PATH] [--preview-config PATH]');
    }
    paths[name] = args[i + 1];
  }
  requireCheck(!!paths.baseline, 'An explicit reviewed baseline configuration is required.');
  paths.candidate ??= path.join(desktop, 'src-tauri/tauri.conf.json');
  paths.preview ??= path.join(desktop, 'src-tauri/tauri.preview.conf.json');
  return checkUpgradeContract({
    baseline: configFile(paths.baseline, 'tauri.conf.json'),
    candidate: configFile(paths.candidate, 'tauri.conf.json'),
    preview: configFile(paths.preview, 'tauri.preview.conf.json'),
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { console.log(JSON.stringify(main(), null, 2)); }
  catch { console.error('Offline upgrade contract failed. No release/update action was performed.'); process.exitCode = 1; }
}
