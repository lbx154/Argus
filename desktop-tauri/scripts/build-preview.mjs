import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, delimiter } from 'node:path';
import { fileURLToPath } from 'node:url';

// An internal, unpack-and-run preview, not another installer/update channel.
// Never call dist, stage-release, signing or upload scripts from this path.
if (process.platform !== 'win32' || process.arch !== 'x64') {
  throw new Error('The desktop preview is built on Windows x64.');
}
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repo = resolve(desktop, '..');
const python = process.env.ARGUS_BUILD_PYTHON || join(repo, '.venv', 'Scripts', 'python.exe');
if (!existsSync(python)) throw new Error('Set ARGUS_BUILD_PYTHON to a project Python with PyInstaller installed.');
const env = {
  ...process.env,
  PATH: `${dirname(python)}${delimiter}${process.env.PATH || ''}`,
  PYTHONUTF8: '1',
  PYTHONIOENCODING: 'utf-8',
  CARGO_TARGET_DIR: join(desktop, 'build', 'preview-target'),
};
// Preview compilation must not consume a publisher's signing configuration.
for (const name of Object.keys(env)) if (name.startsWith('TAURI_SIGNING_')) delete env[name];

function run(command, args, cwd = repo) {
  const result = spawnSync(command, args, { cwd, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed (exit ${result.status}). Preview not packaged.`);
}
function powershell(script, args = []) {
  run('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', join(desktop, 'scripts', script), ...args]);
}

// Rebuild the checked-in Web/TUI bundles from the current source.
run('npm', ['--prefix', join(repo, 'frontend', 'web'), 'run', 'build']);
run('npm', ['--prefix', join(repo, 'frontend', 'tui'), 'run', 'build']);
powershell('build-backend.ps1', ['-SkipInstall', '-PythonExecutable', python]);
powershell('prepare-backend.ps1');
const base = JSON.parse(readFileSync(join(desktop, 'src-tauri', 'tauri.conf.json'), 'utf8'));
const config = {
  productName: 'Argus Preview',
  identifier: 'cn.argusbot.desktop.preview',
  app: { windows: base.app.windows.map((window) => ({ ...window, title: 'Argus Preview' })) },
  bundle: { active: false, createUpdaterArtifacts: false },
};
run(process.execPath, [
  join(desktop, 'node_modules', '@tauri-apps', 'cli', 'tauri.js'),
  'build', '--no-bundle', '--no-sign', '--features', 'preview', '--config', JSON.stringify(config),
], desktop);

const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
const name = `Argus-${base.version}-preview-${stamp}-win-x64`;
const stage = join(desktop, 'build', 'previews', name);
if (existsSync(stage) || existsSync(`${stage}.zip`)) throw new Error(`Preview destination already exists: ${stage}`);
mkdirSync(stage, { recursive: true });
cpSync(join(env.CARGO_TARGET_DIR, 'release', 'Argus.exe'), join(stage, 'Argus.exe'), { errorOnExist: true, force: false });
cpSync(join(desktop, 'resources', 'WebView2Loader.dll'), join(stage, 'WebView2Loader.dll'), { errorOnExist: true, force: false });
cpSync(join(desktop, 'build', 'argus-backend'), join(stage, 'argus-backend'), { recursive: true, errorOnExist: true, force: false });
cpSync(join(repo, 'LICENSE'), join(stage, 'LICENSE.txt'));
writeFileSync(join(stage, 'README-预览说明.txt'), `Argus ${base.version} · Windows x64 内部预览
构建时间：${new Date().toISOString()}
版本：${base.version}

1. 完整解压此目录，再双击 Argus.exe。不要在 ZIP 内直接启动或单独移动 EXE。
2. 首次启动选择已安装并登录的 Agent CLI，然后确认。以后自动使用已保存的选择。
3. 更换 CLI、端口：文件 → 设置，或 Ctrl+,。新建对话：Ctrl+N。浅／深色使用工作台左下角全局切换。
4. 主对话与地图使用统一输入框，草稿和附件在视图间共用；工作台保留项目概览、运行进程、AI IDE。
   角色使用蓝／紫／青绿／琥珀色区分，界面配色固定，不再提供标准／渐变选择。
5. 主动打开右侧文件时适度加宽；PDF 支持放大后四边滚动及“适合页面”。AI IDE 文件区跟随全局主题。
6. 点窗口关闭按钮会隐藏到托盘，任务继续。要结束后台工作，使用“停止本地后端并退出”。

本预览不安装到系统，不覆盖正式版，不下载或安装发布更新。
桌面设置／日志／WebView 缓存：%APPDATA%\\argus-desktop-preview\\
预览项目数据：%APPDATA%\\argus-desktop-preview\\argus-home\\
默认端口：18799（占用时请在设置中更换）。不会自动导入正式版项目。
不要让预览项目与正式版同时写入同一个工作目录。

已包含 Python 后端，不需要单独安装 Python；Agent CLI 及其登录由你自己的安装提供。
需要 Windows 10/11 x64 和 Microsoft Edge WebView2 Runtime。
如果提示缺少 WebView2，请从 Microsoft 官方下载页面安装 Evergreen Runtime：
https://developer.microsoft.com/microsoft-edge/webview2/
这是未签名的内部试用程序，Windows 可能显示信誉提示；请核对来源。

本目录无安装器、无 latest.json 更新清单；内部 JSON 是应用运行必需数据。
测试范围与限制见 TEST-RESULTS.txt。真实模型调用和 CLI 鉴权不由不耗费额度的自动化测试替代。
`, { encoding: 'utf8', flag: 'wx' });

// Test the exact staged bytes before creating a deliverable archive.
const soakSeconds = process.env.ARGUS_PREVIEW_SOAK_SECONDS || '1800';
run(process.execPath, [join(desktop, 'scripts', 'smoke-preview.mjs'), join(stage, 'Argus.exe'), '--soak-seconds', soakSeconds], desktop);
const psQuote = (value) => `'${value.replaceAll("'", "''")}'`;
run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
  `Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory(${psQuote(stage)}, ${psQuote(`${stage}.zip`)}, [System.IO.Compression.CompressionLevel]::Optimal, $true)`]);
console.log(`\nPreview ZIP: ${stage}.zip\nNo installer or updater manifest was generated.`);
