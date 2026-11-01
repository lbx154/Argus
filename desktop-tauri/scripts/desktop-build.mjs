import { execFileSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const operation = process.argv[2];
const run = (command, args, cwd = root) =>
  execFileSync(command, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' && command === 'npm' });
// The Argus the desktop ships (tauri.conf.json bundle.resources): packages/core
// built, with the web cockpit it serves, as `npm pack` carries it.
const buildArgus = () => {
  if (process.platform === 'win32') run(process.execPath, ['scripts/prepare-node-runtime.mjs']);
  run('npm', ['run', 'prepack'], join(root, '..', 'packages', 'core'));
};
if (process.platform === 'win32' && ['build', 'dist'].includes(operation)
    && !process.env.TAURI_SIGNING_PRIVATE_KEY?.trim()) {
  throw new Error('Signed Windows builds require publisher-provided signing configuration. No key is generated or read from a local file. Use build:unsigned only for an explicitly unsigned candidate.');
}
if (operation === 'argus') {
  buildArgus();
} else if (operation === 'dist') {
  if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('dist is the Windows x64 NSIS release entry point.');
  run(process.execPath, [fileURLToPath(import.meta.url), 'build']);
  const args = ['scripts/stage-windows-release.mjs'];
  if (process.env.ARGUS_WINDOWS_RELEASE_DIR) args.push('--output-dir', process.env.ARGUS_WINDOWS_RELEASE_DIR);
  run(process.execPath, args);
} else if (operation === 'build' || operation === 'unsigned') {
  buildArgus();
  const bundles = process.platform === 'darwin' ? 'app,dmg' : process.platform === 'linux' ? 'appimage,deb' : 'nsis';
  const args = ['build', '--bundles', bundles];
  if (operation === 'unsigned') {
    args.push('--config', JSON.stringify({ bundle: { createUpdaterArtifacts: false } }));
  }
  // A Windows candidate must use the reviewed Cargo.lock, not resolve new crates.
  if (process.platform === 'win32') args.push('--', '--locked');
  run(process.execPath, [join(root, 'node_modules/@tauri-apps/cli/tauri.js'), ...args]);
} else {
  throw new Error(`Unknown desktop build operation: ${operation}`);
}
