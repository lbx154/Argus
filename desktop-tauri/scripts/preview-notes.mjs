import { execFileSync } from 'node:child_process';

export const PREVIEW_IDENTIFIER = 'cn.argusbot.desktop.preview.integration20260913';
export const PREVIEW_DATA_DIRECTORY = 'argus-desktop-preview-integration-20260913';

/** Bind preview notes to a clean reviewed tree, not a stale hard-coded release. */
export function capturePreviewIdentity(repo, requestedBase) {
  const git = (...args) => execFileSync('git', args, { cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  const candidate = git('rev-parse', 'HEAD');
  const base = requestedBase || git('merge-base', 'HEAD', 'origin/dev');
  if (!/^[a-f0-9]{40}$/.test(base)) throw new Error('Preview base must be a full frozen commit SHA.');
  git('merge-base', '--is-ancestor', base, candidate);
  // Both staged and unstaged changes must be committed before native packaging.
  git('diff', '--quiet', 'HEAD');
  const untracked = git('ls-files', '--others', '--exclude-standard', '--',
    'argus', 'frontend/core/src', 'frontend/web/src', 'frontend/web/dist',
    'frontend/tui/src', 'frontend/tui/bundle', 'desktop-tauri/src', 'desktop-tauri/scripts', 'desktop-tauri/src-tauri');
  if (untracked) throw new Error('Untracked product inputs or generated assets: review and commit before packaging.');
  return { base_sha: base, candidate_sha: candidate, source_tree_sha: git('rev-parse', 'HEAD^{tree}'),
    identifier: PREVIEW_IDENTIFIER, data_directory: PREVIEW_DATA_DIRECTORY, reuses_previous_preview_profile: true };
}

export function validatePreviewReview(review, identity, manifest) {
  if (!review || review.candidate_sha !== identity.candidate_sha || review.base_sha !== identity.base_sha
      || review.release_id !== manifest.release_id || review.source_digest !== manifest.source_digest
      || !Array.isArray(review.checks) || !Array.isArray(review.pending) || !review.tasks) {
    throw new Error('Preview review evidence does not match this candidate/base/source identity.');
  }
  return review;
}

export function previewReadme({ version, manifest, identity, manualPreview, review, builtAt }) {
  const changes = review ? Object.entries(review.tasks).map(([task, result]) => `- ${task}: ${result}`).join('\n')
    : '未附带源码回归审查记录；不能据此宣称功能回归全部通过。';
  const checks = review ? review.checks.map(check => `- ${check.name}: ${check.result}`).join('\n')
    : '详见 TEST-RESULTS.txt；构建成功不等于全部源码回归通过。';
  const pending = review?.pending?.map(item => `- ${item}`).join('\n') || '- 未提供额外验收记录。';
  return `Argus ${version} · Windows x64 手测预览（不是正式安装包）
构建时间：${builtAt}
冻结整合 base：${identity.base_sha}
源码/候选 commit：${identity.candidate_sha}
Git tree：${identity.source_tree_sha}
Release ID：${manifest.release_id}
Source digest：${manifest.source_digest}

启动与退出
1. 完整解压到新目录，运行其中的 Argus.exe；不要仅移动 EXE，不要在 ZIP 内运行。
2. 需要 Windows 10/11 x64 和 Microsoft Edge WebView2 Runtime。包内包含 Loader DLL，但不包含系统 Runtime。
   缺少 Runtime 时请从 Microsoft 官方安装：https://developer.microsoft.com/microsoft-edge/webview2/
   无需另装 Python；真实任务需要你自己明确配置的 Agent CLI/登录，或获邀的内测 Key。
   本包不含账号、Key、用户数据；自动验收未使用真实账户或付费任务。
   内测试用首次连接可能联网下载 CLI 并消耗少量额度；不要把它当作离线检查。
3. 普通关闭隐藏到托盘，后台可能继续运行；托盘或再次运行 EXE 恢复窗口。
   明确退出请用“停止本地后端并退出”。不要按进程名称结束其他 Argus/Python。

数据隔离——请先阅读
应用标识：${identity.identifier}
数据目录：%APPDATA%\\${identity.data_directory}\\
运行时项目/配置位于其 argus-home 子目录，WebView 缓存位于 webview 子目录，默认端口 18799。
与正式版 %APPDATA%\\argus-desktop\\ 隔离，不导入或迁移正式数据。
但本包沿用 20260913 integration 预览命名空间：可能共享旧预览状态，不能与旧预览并行运行！
新 ZIP 名称不代表新 profile；请先用旧预览的明确退出菜单结束它，再启动本包。不会清空旧目录。
外部 Agent CLI 登录由 CLI 自己管理。不要让两份应用同时写同一工作目录。
本包编译期禁用发布更新；不安装、不签名、不上传、不发布，也不创建正式更新 manifest。

本轮实际范围
${changes}
工作台入口仍沿用“更多 → 工作台”；保留地图、研究排期、运行进程、AI IDE 和知识库。
只读说明针对视图，不限制 Agent 写文件；会话目录不是每个任务的 cwd 或安全沙箱。
原始 diff 可切换/复制；原后端截断提示保留，不宣称变更都来自当前 Agent。
草稿只在内存中；不支持刷新恢复、全局通知中心或通知时刻的历史文件版本恢复。

源码回归（与本候选绑定；详见 PREVIEW-VALIDATION.json，如有）
${checks}

原生成品检查
${manualPreview ? '本包采用 --manual-preview：实际 Argus.exe、冻结后端认证及短时健康检查通过后才生成 ZIP。\n详见 native-startup-check.json、COPY-VERIFIED.json、SOURCE-IDENTITY.json 和 TEST-RESULTS.txt。\n这不是完整 GUI/Toast/故障注入/30 分钟长稳已通过的声明。' : '完整原生检查的实际范围见 TEST-RESULTS.txt 和原生验收记录，不把浏览器模拟 IPC 算作真实 Toast。'}
待验/限制
${pending}

手测清单（使用新建的测试项目）
- A 输入草稿并添加合成附件，切 B 再回 A；地图/对话切换保留同会话内容。刷新会丢内存草稿。
- 当前在 B 时点击 A 的通知；应定位 A 的成果，B 草稿仍在。验证真实 Windows Toast 点击。
- 侧栏上方的插件、技能库、知识库入口可用；折叠偏好保留，会话搜索/列表和底部设置仍可操作。
- 中文模式下内置技能有中文名称、用途和导读，可以用中文搜索并展开原文；自定/学习内容不自动改写，英文模式仍可用。
- 知识库学习动态、关于你、最近更新、全局/领域/项目页面、全文与原则仍可用；类型说明不冒充正文翻译或真实性证明。
- 展开会话目录看完整路径和范围说明；确认主要操作未被挤掉。
- 更多 → 工作台 → AI IDE → Changes：检查增加/删除/hunk、原文、复制、长 diff 和截断提示；切换浅/深色。
- 关闭到托盘、再次打开；最后用“停止本地后端并退出”，确认本预览退出。
- 本轮不检查正式安装/升级；手测通过并明确批准后才讨论正式安装包，批准也不自动授权发布。
`;
}
