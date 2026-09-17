import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, delimiter } from 'node:path';
import { fileURLToPath } from 'node:url';
import { copyVerified } from './verified-copy.mjs';

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

// Refresh source identity and both frontends together before freezing Python.
// External plugin wheels remain independently published and checksum-pinned.
run(python, ['-m', 'argus.release_tools.build_release']);
powershell('build-backend.ps1', ['-SkipInstall', '-PythonExecutable', python]);
powershell('prepare-backend.ps1');
const base = JSON.parse(readFileSync(join(desktop, 'src-tauri', 'tauri.conf.json'), 'utf8'));
const config = {
  productName: 'Argus Preview',
  identifier: 'cn.argusbot.desktop.preview.integration20260913',
  app: { windows: base.app.windows.map(window => ({ ...window, title: 'Argus Preview' })) },
  bundle: { active: false, createUpdaterArtifacts: false },
};
run(process.execPath, [
  join(desktop, 'node_modules', '@tauri-apps', 'cli', 'tauri.js'),
  'build', '--no-bundle', '--no-sign', '--features', 'preview', '--config', JSON.stringify(config),
], desktop);

const manifest = JSON.parse(readFileSync(join(repo, 'argus', 'release_manifest.json'), 'utf8'));
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

writeFileSync(join(stage, 'README-预览说明.txt'), `Argus ${base.version} · Windows x64 完整整合预览
构建时间：${new Date().toISOString()}
运行身份：${manifest.release_id}
上游整合基线：5b25300287296c40a8d6c58306a3923dd26c2937

这是未签名、完整解压运行的预览，不是安装器，不会发布或安装更新。
本构建流程不推送、不创建 PR。请先完成手动测试，再决定是否提交。

启动与干净环境
1. 完整解压到新目录后运行 Argus.exe，不要只移动 EXE，不要在 ZIP 内运行。
2. 本轮使用独立数据根（该目录可能已经有你的预览数据，重建不会清空它）：
   %APPDATA%\\argus-desktop-preview-integration-20260913\\
   账号设置、WebView 缓存、会话、插件与科学环境均保留在此目录。
   不复制旧 Argus 预览或正式版的数据。外部 Agent CLI 自己的登录及 Windows 设置独立保留。
3. 首次必须选择并确认自己的 Agent CLI，或在原生设置中输入受邀获得的内测 Key。
   没有附带可用 Key。自己的 CLI 登录由你自己的安装提供。
   内测试用会联网准备 Copilot，并验证一次真实模型回复；该验证会消耗少量额度。
   argus-trial 是通信别名，不代表实际模型名称；真实模型由服务端选择。
4. 首次主题为浅色；以后保留你保存的主题。窗口关闭会隐藏到托盘，任务仍可继续。
   要结束后台工作，请用“停止本地后端并退出”。不要强制结束所有 python.exe。

本轮重点
5. 黑瞳孔保留原有小轨道，白色高光随同一动画相位在瞳孔内转动。
   眼睛动画固定开启，不再提供开启／跟随系统／关闭选项，旧的关闭偏好不再生效。
   不修改 Windows 动效设置，其他界面继续尊重系统减少动态效果。
   冷启动与再次双击唤醒按实际可见帧交接；后者不重启后端或丢失草稿。
6. CrystalPilot 的“浏览…”可调用原生目录选择；选择只回填，仍需你确认打开。
   取消保留原路径，CIF 选择保留。请把科研项目放在程序源码和安装目录之外。
7. 保留严格费用核对、显式风险确认、真实失败防重放、提供方暂停及认证恢复。
   未知费用不冒充免费，也不自动解除旧暂停或重放旧任务。
8. 保留发行身份和宿主／后端一致性校验。首次 JS/MJS 适配器变化也纳入源码摘要。
   包内 release_manifest.json 是运行必需文件，不能单独删除或手改。
9. 整合上游 Windows 发现／Unicode／状态修复、Pi 与审查交接、共享阅读器和地图。
   地图采用新布局；主对话和地图共享草稿与附件，失败不会覆盖后来输入的内容。
   地图说明失败保留原始证据和缓存，遵守有界批次、期限和持久冷却。

插件、许可与数据
10. CrystalPilot 仍是独立的 0.4.0 专有插件，wheel 未修改、未重打包。
    在“插件”里按需安装。首次科学环境需要下载较大依赖。
    PLATON 官方许可须由你确认，SHELX 仍需要你自己的授权。
    第一方 Windows 适配器不修改官方科学程序，不执行系统安装器或修改全局 PATH。
11. 内测 Key 存放于当前 Windows 用户保护下的私有配置，没有宣称 DPAPI 加密。
    请求会经过网关和上游服务，不是完全离线或端到端加密。
    不要把 Key、账号配置、账本或科研数据粘贴到聊天或提交到仓库。
12. 本包不会自行删除旧包、账户或项目。清理须另行确认准确目录。
    不要让两份 Argus 同时写同一科研工作目录。

验收边界
13. ${manualPreview ? '本包按用户要求仅做最低限度检查；详见 TEST-RESULTS.txt 和 native-startup-check.json。\n    未进行成品像素动效、完整界面及 30 分钟长稳验收，交由用户手动测试。' : '随包 TEST-RESULTS.txt 和 native-motion-baseline.json 记录本次成品验收。\n    原生动画验收不使用 Playwright 默认媒体／焦点模拟。'}
    自动化使用隔离数据及模型替身，不等于真实付费模型、完整求解／精修已验收。
    请用新建的测试项目完成你的手动验收，勿直接重放旧科研任务。
14. 本次交付按 Windows 桌面范围验证，不要求安装 Linux 或 Docker。
    Linux/macOS 与上游全量跨平台 CI 未作为本次预览包的验收结论；这不是全部 PR 门禁已通过的声明。

需要 Windows 10/11 x64 和 Microsoft Edge WebView2 Runtime。
缺少 WebView2 时请从 Microsoft 官方安装 Evergreen Runtime：
https://developer.microsoft.com/microsoft-edge/webview2/
`, { encoding: 'utf8', flag: 'wx' });

// Full acceptance remains available; manual preview is an explicit reduced
// scope, never a short soak falsely reported as complete native acceptance.
if (manualPreview) {
  run(python, [join(desktop, 'scripts', 'smoke-host.py'), '--binary', join(stage, 'Argus.exe'),
    '--preview', '--timeout', '90', '--health-window', '8', '--report', join(stage, 'native-startup-check.json')], desktop);
  writeFileSync(join(stage, 'TEST-RESULTS.txt'), `Argus Windows 手测预览 · 最低限度检查\n${new Date().toISOString()}\nRelease: ${manifest.release_id}\n\nPASS: 源码与 Web/TUI 发行身份一致构建\nPASS: 每个构建输入复制后校验\nPASS: 成品冻结运行时模块加载\nPASS: 实际 Argus.exe 启动、冻结后端认证及短时健康检查（详见 native-startup-check.json）\n\n眼睛动画固定开启，调节菜单和原生命令已删除。\n未执行成品原生像素检查、完整界面自动化、故障注入及 30 分钟长稳。\n没有使用真实账户或执行付费模型任务；不代表科研求解/插件安装全流程已通过。\nLinux/macOS 和上游全量 PR CI 未验证。本包不是正式发行或 PR-ready 声明。\n请使用新的测试项目手动验收。\n`, { flag: 'wx' });
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
const psQuote = value => `'${value.replaceAll("'", "''")}'`;
run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
  `Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory(${psQuote(stage)}, ${psQuote(`${stage}.zip`)}, [System.IO.Compression.CompressionLevel]::Optimal, $true)`]);
console.log(`\nPreview ZIP: ${stage}.zip\nNo installer, publisher or update manifest was invoked.`);
