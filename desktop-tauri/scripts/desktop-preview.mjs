import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('This preview targets Windows x64.');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const run = (script, args = []) => execFileSync(process.execPath, [script, ...args], { cwd: root, stdio: 'inherit' });
// No signer configuration and no release staging/upload code is used by this entry point.
run('scripts/prepare-node-runtime.mjs');
run('scripts/desktop-build.mjs', ['argus']);
run('node_modules/@tauri-apps/cli/tauri.js', ['build', '--features', 'preview', '--config', 'src-tauri/tauri.preview.conf.json', '--bundles', 'nsis', '--', '--locked']);
const directory = join(root, 'src-tauri/target/release/bundle/nsis');
const version = JSON.parse(readFileSync(join(root, 'src-tauri/tauri.preview.conf.json'), 'utf8')).version;
const installers = readdirSync(directory).filter(name => name.includes(`_${version}_`) && name.endsWith('.exe'));
if (installers.length !== 1) throw new Error(`Expected exactly one ${version} installer.`);
const output = resolve(root, '../artifacts', `preview-${new Date().toISOString().replace(/[:.]/g, '-')}`);
if (existsSync(output)) throw new Error('Preview output already exists.');
mkdirSync(output, { recursive: true });
const name = `Argus-${version}-setup.exe`;
copyFileSync(join(directory, installers[0]), join(output, name));
const hash = createHash('sha256').update(readFileSync(join(output, name))).digest('hex');
writeFileSync(join(output, 'SHA256SUMS.txt'), `${hash}  ${name}\n`, { flag: 'wx' });
writeFileSync(join(output, 'README.txt'), [
  `Argus ${version} — local acceptance preview`,
  '',
  'Install separately as Argus Preview. Do not uninstall or replace Argus 0.1.10.',
  'Data: %APPDATA%/argus-desktop-preview-1.0.0 (not the stable data directory).',
  'The preview never checks for or installs release updates. Nothing was published to GitHub.',
  'Node.js is bundled. Install/sign in to your Agent CLIs separately; Argus does not copy their credentials.',
  'Adapters: Codex, Claude Code, Copilot CLI, Cursor Agent, Pi, OpenCode, Grok Build, Qoder CLI, DSH.',
  'See CLI-COMPATIBILITY.md for native-versus-protocol verification and installation/login blockers.',
  'This local preview is unsigned. Only run it if you trust its local source and build.',
  'Open Select model to configure Manager, Planner, Engineer and Reviewer independently.',
  'CLI/model changes take effect on the next role turn; in-flight calls keep their old route.',
  'Legacy Python project import and automatic upgrade validation are release gates, not preview claims.',
  '',
  `SHA256: ${hash}`, '',
].join('\r\n'), { flag: 'wx' });
console.log(`LOCAL_PREVIEW_PACKAGE=${join(output, name)}`);
