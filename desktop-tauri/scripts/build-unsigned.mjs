import { execFileSync } from 'node:child_process';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const configPath = join(root, 'src-tauri', 'tauri.conf.json');
const temporaryPath = join(root, 'src-tauri', '.tauri-unsigned.conf.json');
const config = JSON.parse(readFileSync(configPath, 'utf8'));
config.bundle.createUpdaterArtifacts = false;
writeFileSync(temporaryPath, `${JSON.stringify(config, null, 2)}\n`, 'utf8');
try {
  execFileSync(process.execPath, [
    join(root, 'node_modules', '@tauri-apps', 'cli', 'tauri.js'),
    'build', '--config', temporaryPath, '--bundles', 'nsis',
  ], { cwd: root, stdio: 'inherit' });
} finally {
  rmSync(temporaryPath, { force: true });
}
