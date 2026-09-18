import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, delimiter } from 'node:path';
import { fileURLToPath } from 'node:url';
import { copyVerified } from './verified-copy.mjs';
import { capturePreviewIdentity, PREVIEW_IDENTIFIER, previewReadme, validatePreviewReview } from './preview-notes.mjs';

// Windows portable acceptance only: never run an installer, signer or publisher.
if (process.platform !== 'win32' || process.arch !== 'x64') {
  throw new Error('The desktop preview is built on Windows x64.');
}
const manualPreview = process.argv.includes('--manual-preview');
if (process.argv.slice(2).some(arg => arg !== '--manual-preview')) throw new Error('Unknown preview build option.');
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repo = resolve(desktop, '..');
const python = process.env.ARGUS_BUILD_PYTHON || join(repo, '.venv', 'Scripts', 'python.exe');
if (!existsSync(python)) throw new Error('Set ARGUS_BUILD_PYTHON to a project Python with PyInstaller installed.');
const buildEnvironment = { ...process.env };
for (const name of Object.keys(buildEnvironment)) {
  if (/API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|^ARGUS_|^PI_|^TAURI_SIGNING_/i.test(name)) delete buildEnvironment[name];
}
const env = {
  ...buildEnvironment,
  PATH: `${dirname(python)}${delimiter}${process.env.PATH || ''}`,
  PYTHONUTF8: '1',
  PYTHONIOENCODING: 'utf-8',
  CARGO_TARGET_DIR: join(desktop, 'build', 'preview-target'),
  ARGUS_SKILL_HOME: join(repo, '.integration', 'build-runtime-home'),
  ARGUS_WORKBENCH_HOST_ROOT: join(repo, '.integration', 'build-runtime-home'),
  PYINSTALLER_CONFIG_DIR: join(repo, '.integration', 'pyinstaller-cache'),
};
mkdirSync(env.ARGUS_SKILL_HOME, { recursive: true });
mkdirSync(env.PYINSTALLER_CONFIG_DIR, { recursive: true });
for (const name of Object.keys(env)) if (name.startsWith('TAURI_SIGNING_')) delete env[name];

function run(command, args, cwd = repo) {
  const result = spawnSync(command, args, { cwd, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed (exit ${result.status}). Preview not packaged.`);
}
function powershell(script, args = []) {
  run('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', join(desktop, 'scripts', script), ...args]);
}

const identity = capturePreviewIdentity(repo, process.env.ARGUS_PREVIEW_BASE);
const reviewInput = process.env.ARGUS_PREVIEW_REVIEW
  ? JSON.parse(readFileSync(process.env.ARGUS_PREVIEW_REVIEW, 'utf8')) : null;

// Refresh source identity and both frontends together before freezing Python.
// External plugin wheels remain independently published and checksum-pinned.
run(python, ['-m', 'argus.release_tools.build_release']);
powershell('build-backend.ps1', ['-SkipInstall', '-PythonExecutable', python]);
powershell('prepare-backend.ps1');
const base = JSON.parse(readFileSync(join(desktop, 'src-tauri', 'tauri.conf.json'), 'utf8'));
const config = {
  productName: 'Argus Preview',
  identifier: PREVIEW_IDENTIFIER,
  app: { windows: base.app.windows.map(window => ({ ...window, title: 'Argus Preview' })) },
  bundle: { active: false, createUpdaterArtifacts: false },
};
run(process.execPath, [
  join(desktop, 'node_modules', '@tauri-apps', 'cli', 'tauri.js'),
  'build', '--no-bundle', '--no-sign', '--features', 'preview', '--config', JSON.stringify(config),
], desktop);

const manifest = JSON.parse(readFileSync(join(repo, 'argus', 'release_manifest.json'), 'utf8'));
const review = reviewInput ? validatePreviewReview(reviewInput, identity, manifest) : null;
const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
const name = `Argus-${base.version}-${manualPreview ? 'manual-test' : 'integration'}-preview-${stamp}-win-x64`;
const stage = join(desktop, 'build', 'previews', name);
if (existsSync(stage) || existsSync(`${stage}.zip`)) throw new Error(`Preview destination already exists: ${stage}`);
mkdirSync(stage, { recursive: true });
const copied = [];
for (const [source, target] of [
  [join(env.CARGO_TARGET_DIR, 'release', 'Argus.exe'), join(stage, 'Argus.exe')],
  [join(desktop, 'resources', 'WebView2Loader.dll'), join(stage, 'WebView2Loader.dll')],
  [join(desktop, 'build', 'argus-backend'), join(stage, 'argus-backend')],
  [join(repo, 'LICENSE'), join(stage, 'LICENSE.txt')],
]) copied.push({ target: target.slice(stage.length + 1), ...copyVerified(source, target) });
writeFileSync(join(stage, 'COPY-VERIFIED.json'), JSON.stringify({ release_id: manifest.release_id, inputs: copied }, null, 2) + '\n', { flag: 'wx' });
run(join(stage, 'argus-backend', 'argus-backend.exe'), ['--verify-frozen-runtime'], stage);

writeFileSync(join(stage, 'SOURCE-IDENTITY.json'), JSON.stringify({ ...identity,
  release_id: manifest.release_id, source_digest: manifest.source_digest }, null, 2) + '\n', { flag: 'wx' });
if (review) writeFileSync(join(stage, 'PREVIEW-VALIDATION.json'), JSON.stringify(review, null, 2) + '\n', { flag: 'wx' });
writeFileSync(join(stage, 'README-预览说明.txt'), previewReadme({ version: base.version, manifest, identity,
  manualPreview, review, builtAt: new Date().toISOString() }), { encoding: 'utf8', flag: 'wx' });

// Full acceptance remains available; manual preview is an explicit reduced
// scope, never a short soak falsely reported as complete native acceptance.
if (manualPreview) {
  run(python, [join(desktop, 'scripts', 'smoke-host.py'), '--binary', join(stage, 'Argus.exe'),
    '--preview', '--timeout', '90', '--health-window', '8', '--report', join(stage, 'native-startup-check.json')], desktop);
  writeFileSync(join(stage, 'TEST-RESULTS.txt'), `Argus Windows 手测预览 · 最低原生检查\n${new Date().toISOString()}\nBase: ${identity.base_sha}\nCandidate: ${identity.candidate_sha}\nRelease: ${manifest.release_id}\nSource digest: ${manifest.source_digest}\n\nPASS: 源码与 Web/TUI 发行身份一致构建\nPASS: 每个构建输入复制后校验\nPASS: 成品冻结运行时模块加载\nPASS: 实际 Argus.exe 启动、冻结后端认证及短时健康检查（详见 native-startup-check.json）\n预览检查使用隔离 profile 和随机测试命名空间，保留单实例保护。\n\n源码回归及已知限制见 PREVIEW-VALIDATION.json（如有），不把旧基线成绩标为本包成绩。\n未执行真实 Windows Toast、成品像素、完整界面自动化、故障注入及 30 分钟长稳。\n托盘关闭/唤醒/明确退出仍待手测，测试清理只终止本脚本创建的进程树。\n没有使用真实账户或执行付费模型任务；不代表科研求解/插件安装全流程已通过。\nLinux/macOS 和上游全量 PR CI 未验证。本包不是正式发行或 PR-ready 声明。\n`, { flag: 'wx' });
} else {
  const soakSeconds = process.env.ARGUS_PREVIEW_SOAK_SECONDS || '1800';
  run(process.execPath, [join(desktop, 'scripts', 'smoke-preview.mjs'), join(stage, 'Argus.exe'), '--soak-seconds', soakSeconds], desktop);
}
// A long native check must not certify source changed by another build/session.
run(python, ['-m', 'argus.release_tools.generate_manifest', '--check']);
run(python, ['-m', 'argus.release_tools.check_artifacts']);
const stagedManifest = JSON.parse(readFileSync(join(stage, 'argus-backend', '_internal', 'argus', 'release_manifest.json'), 'utf8'));
if (stagedManifest.release_id !== manifest.release_id || stagedManifest.source_digest !== manifest.source_digest) {
  throw new Error('Staged backend identity changed during verification; preview not packaged.');
}
const verifiedIdentity = capturePreviewIdentity(repo, identity.base_sha);
if (verifiedIdentity.candidate_sha !== identity.candidate_sha || verifiedIdentity.source_tree_sha !== identity.source_tree_sha) {
  throw new Error('Candidate commit/tree changed during native verification; preview not packaged.');
}
const psQuote = value => `'${value.replaceAll("'", "''")}'`;
run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
  `Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory(${psQuote(stage)}, ${psQuote(`${stage}.zip`)}, [System.IO.Compression.CompressionLevel]::Optimal, $true)`]);
console.log(`\nPreview ZIP: ${stage}.zip\nNo installer, publisher or update manifest was invoked.`);
