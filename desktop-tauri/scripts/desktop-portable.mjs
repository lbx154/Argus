// Local, unsigned, no-installer preview. No signing, updater staging or upload.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyVerified } from './verified-copy.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export function packagePortable({ desktopRoot = desktop, releaseDir = path.join(desktopRoot, 'src-tauri/target/release'), outputDir }) {
  const configRoot = path.join(desktopRoot, 'src-tauri');
  const read = name => JSON.parse(fs.readFileSync(path.join(configRoot, name), 'utf8'));
  const preview = read('tauri.preview.conf.json'), stable = read('tauri.conf.json'), windows = read('tauri.windows.conf.json');
  if (preview.identifier !== 'cn.argusbot.desktop.preview.v1' || preview.bundle?.createUpdaterArtifacts !== false
      || !/^1\.0\.0-preview\.\d+$/.test(preview.version)) throw Error('A separate unsigned preview configuration is required.');
  if (!outputDir || !path.isAbsolute(outputDir)) throw Error('Choose a new absolute output directory.');
  const output = path.resolve(outputDir);
  for (let p = output; ; p = path.dirname(p)) {
    if (fs.existsSync(p) && fs.lstatSync(p).isSymbolicLink()) throw Error('Portable paths cannot traverse links.');
    if (path.dirname(p) === p) break;
  }
  if (fs.existsSync(output) || fs.existsSync(output + '.zip')) throw Error('Portable output already exists; choose a new directory.');
  const resources = { ...stable.bundle.resources, ...windows.bundle.resources };
  const files = [];
  const collect = (source, relative) => {
    if (/(?:^\.env(?:\.|$)|^credentials\.|private.*key|token|secret)/i.test(path.basename(source))) throw Error('Unexpected sensitive-looking payload filename.');
    if (path.isAbsolute(relative) || relative.split(/[\\/]/).includes('..')) throw Error('Invalid portable resource destination.');
    const stat = fs.lstatSync(source);
    if (stat.isSymbolicLink()) throw Error('Linked payloads are not accepted.');
    if (stat.isDirectory()) for (const name of fs.readdirSync(source).sort()) collect(path.join(source, name), path.join(relative, name));
    else if (stat.isFile()) files.push({ source, relative });
    else throw Error('Payload must contain regular files only.');
  };
  collect(path.join(releaseDir, 'Argus.exe'), 'Argus.exe');
  for (const [source, relative] of Object.entries(resources)) collect(path.resolve(configRoot, source), relative);
  const names = new Set(files.map(f => f.relative.toLowerCase()));
  if (names.size !== files.length) throw Error('Overlapping portable resources.');
  for (const required of ['Argus.exe', 'node-runtime/node.exe', 'argus-core/dist/argus.mjs', 'argus-core/web/index.html', 'WebView2Loader.dll']) {
    if (!files.some(f => f.relative.replaceAll('\\', '/') === required)) throw Error(`Missing portable resource: ${required}`);
  }
  fs.mkdirSync(path.dirname(output), { recursive: true }); fs.mkdirSync(output);
  const sums = [];
  for (const { source, relative } of files) {
    const target = path.join(output, relative); fs.mkdirSync(path.dirname(target), { recursive: true });
    copyVerified(source, target);
    sums.push(`${createHash('sha256').update(fs.readFileSync(target)).digest('hex')}  ${relative.replaceAll('\\', '/')}`);
  }
  fs.writeFileSync(path.join(output, 'SHA256SUMS.txt'), sums.join('\n') + '\n', { flag: 'wx' });
  fs.writeFileSync(path.join(output, '开始使用.txt'), [
    `Argus ${preview.version} 免安装本地预览`, '',
    '完整解压到一个新目录，双击 Argus.exe 即可启动，不运行任何安装器。',
    '请保留整个文件夹，不要只复制 EXE；Node、WebView2Loader 与内核资源都已附带。',
    '需要 Windows x64 和系统 WebView2 Runtime。本机已有 WebView2；此包不自动安装系统组件。',
    '启动前请退出其他 Argus Preview 实例；不需要卸载或覆盖 Argus 0.1.10。',
    '数据沿用隔离预览目录 %APPDATA%\\argus-desktop-preview-1.0.0，不读取或迁移旧版项目。',
    '本地未签名预览，更新禁用，不会触发旧版升级。只在信任本地来源时运行，不要关闭系统安全保护。', '',
    '本轮：淡黄色设置页；CLI 选择只显示本机可发现项；四角色独立费率及来源；增量回复与分阶段耗时。',
    '费率来自 CLI 声明或 models.dev 公共参考目录，并显示查询时间；无法确认的别名不猜价。',
    '费率未知不等于免费。订阅/额度及代理计费可能不同，这不是实际账户账单。',
    '模型目录和公共价格查询不推理；发送消息或点击“测试此配置”才可能产生模型费用。',
    '九种 CLI 的协议测试不等于九种真实账号通过。Claude/Codex 原有服务阻塞没有自动重试。',
    '未完成的旧数据迁移、全部真实 CLI 联调和正式升级验证仍为发布门槛。',
    '本地预览验收不等于正式发布许可；只有另行明确批准后才可上传正式三件套。', '',
  ].join('\r\n'), { flag: 'wx' });
  return { output, version: preview.version, files: files.length, installer: false, updatesEnabled: false };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.platform !== 'win32' || process.arch !== 'x64') throw Error('Windows x64 is required.');
  if (process.argv.length > 3) throw Error('Usage: desktop-portable.mjs [NEW_ABSOLUTE_OUTPUT_DIRECTORY]');
  const run = (script, args = []) => execFileSync(process.execPath, [script, ...args], { cwd: desktop, stdio: 'inherit' });
  run('scripts/desktop-build.mjs', ['argus']);
  run('node_modules/@tauri-apps/cli/tauri.js', ['build', '--features', 'preview', '--config', 'src-tauri/tauri.preview.conf.json', '--no-bundle', '--', '--locked']);
  const outputDir = process.argv[2] || path.resolve(desktop, '../artifacts', `portable-${new Date().toISOString().replace(/[:.]/g, '-')}`);
  const result = packagePortable({ outputDir });
  const quote = value => "'" + value.replaceAll("'", "''") + "'";
  execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
    `$ErrorActionPreference='Stop'; Compress-Archive -LiteralPath ${quote(result.output)} -DestinationPath ${quote(result.output + '.zip')} -CompressionLevel Optimal`], { stdio: 'inherit' });
  console.log(JSON.stringify(result, null, 2));
}
