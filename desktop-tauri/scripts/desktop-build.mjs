import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const operation = process.argv[2];
const repoPython = join(root, '..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.ARGUS_BUILD_PYTHON || (existsSync(repoPython) ? repoPython : (process.platform === 'win32' ? 'python' : 'python3'));
const run = (command, args) => execFileSync(command, args, { cwd: root, stdio: 'inherit' });
if (operation === 'backend') {
  run(python, ['scripts/build-backend.py']);
} else if (operation === 'prepare') {
  run(python, ['scripts/build-backend.py', '--prepare-only']);
} else if (operation === 'build' || operation === 'unsigned') {
  run(python, ['scripts/build-backend.py', '--prepare-only']);
  const bundles = process.platform === 'darwin' ? 'app,dmg' : 'nsis';
  const args = ['build', '--bundles', bundles];
  if (operation === 'unsigned') {
    args.push('--config', JSON.stringify({ bundle: { createUpdaterArtifacts: false } }));
  }
  run(process.execPath, [join(root, 'node_modules/@tauri-apps/cli/tauri.js'), ...args]);
} else {
  throw new Error(`Unknown desktop build operation: ${operation}`);
}
