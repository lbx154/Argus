import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const operation = process.argv[2];
const repoPython = join(root, '..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.ARGUS_BUILD_PYTHON || (existsSync(repoPython) ? repoPython : (process.platform === 'win32' ? 'python' : 'python3'));
const run = (command, args) => execFileSync(command, args, { cwd: root, stdio: 'inherit' });
if (process.platform === 'win32' && ['build', 'dist'].includes(operation)
    && !process.env.TAURI_SIGNING_PRIVATE_KEY?.trim()) {
  throw new Error('Signed Windows builds require publisher-provided signing configuration. No key is generated or read from a local file. Use build:unsigned only for an explicitly unsigned candidate.');
}
if (operation === 'dist') {
  if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('dist is the Windows x64 NSIS release entry point.');
  run(python, ['scripts/build-backend.py']);
  run(process.execPath, [fileURLToPath(import.meta.url), 'build']);
  const args = ['scripts/stage-windows-release.mjs'];
  if (process.env.ARGUS_WINDOWS_RELEASE_DIR) args.push('--output-dir', process.env.ARGUS_WINDOWS_RELEASE_DIR);
  run(process.execPath, args);
} else if (operation === 'backend') {
  run(python, ['scripts/build-backend.py']);
} else if (operation === 'prepare') {
  run(python, ['scripts/build-backend.py', '--prepare-only']);
} else if (operation === 'build' || operation === 'unsigned') {
  run(python, ['scripts/build-backend.py', '--prepare-only']);
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
