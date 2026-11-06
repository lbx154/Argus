# Windows 覆盖与数据保留验收

## 范围与入口

`desktop-tauri/scripts/windows-install-preservation.mjs` 是**新建 scratch 内的载荷覆盖 fixture**，
不是安装器、产品备份器或桌面审计器。仅输入受信任的已构建载荷；绝不能把真实账户目录作为
scratch。它不运行 `Argus.exe`，避免触发生产桌面的单实例转发；原生 UI 继续使用既有桌面审计。

```powershell
node desktop-tauri/scripts/windows-install-preservation.mjs --help
node desktop-tauri/scripts/windows-install-preservation.mjs `
  --old-dir 'X:\reviewed-old-payload' `
  --new-dir 'X:\reviewed-new-payload' `
  --scratch 'X:\scratch-parent\NEW-unique-directory'
# 单独的能力边界负例；Windows 预期 exit 1，result.json 记 preflight exit 3。
node desktop-tauri/scripts/windows-install-preservation.mjs `
  --old-dir 'X:\reviewed-old-payload' --new-dir 'X:\reviewed-new-payload' `
  --scratch 'X:\scratch-parent\ANOTHER-new-directory' --long-path yes

node --test desktop-tauri/tests/windows-install-preservation.node.mjs `
  desktop-tauri/tests/verified-copy.node.mjs desktop-tauri/tests/windows-release.node.mjs
```

scratch 的父目录必须已存在，scratch 本身必须不存在；路径及输入祖先不接受 junction/symlink。
工具只取 `Argus.exe`、`WebView2Loader.dll`、`argus-core/` 及可选 `licenses/`，不复制 PDB、Cargo
缓存或账户文件。前三项必需。允许 Cargo 的只读输入硬链接，暂存复制会断开别名；目标文件硬链接
一律拒绝，防止覆盖影响目录外同一文件。不要在另一路正在重建的目录上验收；输入最终也会核对 SHA。

一次运行依次执行：

1. 建立中文、空格、`%`、`&` 的私有安装路径；建立独有 `ARGUS_HOME`、`ARGUS_SHARED`、
   HOME/APPDATA/LOCALAPPDATA/TEMP。仅 seed 项目、事件、设置、认证占位值、真实
   `$ARGUS_SHARED/skills/` 下的用户技能及目录内外 canary，不读取或继承真实凭据。
2. 用现有 `verified-copy.mjs` 把旧/新载荷分别暂存。保留所有输入逐文件 SHA256/字节数。
3. 旧载荷 → 新载荷就地覆盖 → 从旧载荷回退；每阶段使用现有 Windows 安装 preflight，
   只校验进程身份，不终止任何安装。覆盖保留未知文件及旧版多余资源，符合现行 NSIS 的文件写入方式。
4. 每阶段用当前 Node 启动安装路径中的 `argus-core/dist/argus.mjs --port 0 --backend pi`，
   等待其一次 stdout 就绪事件，只做一次认证 `/api/meta` 读取，核对 PID 与私有 state root，
   然后通过保留的 ChildProcess 句柄停止自己的子进程。不做模型、账户或原生 UI 调用。
5. 回退仅恢复备份 manifest 内的旧载荷，并仅删除新 manifest 引入、SHA 仍匹配的新文件；
   不删除安装目录、项目、设置、认证、用户技能或未知文件。任何失败保留现场，不自动重试/回退。

`SCRATCH/evidence/result.json` 给出每阶段核心版本/PID、载荷 SHA、seed 前后内容 SHA、字节数、
文件 dev/ino/birthtime/mtime/ctime，以及 `seed_integrity_pass`。seed 原始文件对象在全流程保持
打开，目录身份也检查，**删除重建或写回相同字节不算保留**。错误时仍写失败与 seed 对比；
就绪/HTTP 有一次性测试超时，没有轮询或产品 watchdog。每次新 scratch，旧证据不会覆盖。

这证明的是载荷文件覆盖及核心启动时的 seed 保留。桌面设置在此只做保留检查，未由原生宿主加载；
不证明 NSIS 注册表/快捷方式、WebView2 安装、已运行项目恢复、任意历史 schema 的向后兼容或签名信任。
两个版本字符串相同但 SHA 不同也可验证工具，不得把这种结果称为跨版本正式升级验收。

## 已有 NSIS 能力和失败安全前提

配置与实现以 `src-tauri/tauri.conf.json`、`installer-template.nsi`、`installer-hooks.nsh` 和
`scripts/installer-preflight.ps1` 为准：currentUser 安装、原目录覆盖、不运行旧卸载器、保留未知
文件；`allowDowngrades=false`，新旧相同版本允许重装，新版覆盖旧版允许；降级或无法识别的已装
版本在 EarlyChecks 报错（2），不是一个支持的回退入口。运行中/身份不可核实的目标也阻止写入。
更新不是原子目录交换，写入中断可能留下混合载荷；因此必须先停止目标并保留可恢复备份。

**只改 APPDATA/USERPROFILE/ARGUS_HOME 不能隔离 NSIS 的 HKCU 注册表、快捷方式和卸载登记。**
安装生产标识的 EXE 还可能转发到同一登录会话的生产桌面。不要为了“隔离测试”在团队运行账户
直接启动生产 NSIS。最终候选应先在独立 Windows 用户/VM 快照中安装；实际生产安装仅由发布负责人
在任务边界批准并完成。不要手改版本登记、关闭降级保护、生成替代签名密钥或绕过系统安全提示。

手动 NSIS 的精确命令（**仅在上述隔离账户/VM 或已授权的最终安装窗口**）：

```powershell
$Installer = 'X:\approved\Argus-VERSION-setup.exe'
$Install = 'X:\验收 空格\Argus' # 推荐短路径；不是用户资料目录
# 已验证安装器签名/哈希、备份、停止目标；应先有 WebView2 Runtime。
# NSIS /D= 必须是最后一项，其值不加引号，空格由 NSIS 作为末尾路径读取。
$p = Start-Process -FilePath $Installer -ArgumentList "/S /NS /D=$Install" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "NSIS failed: $($p.ExitCode); keep evidence and do not start a mixed payload" }
# 升级使用更高版本安装器、完全相同的 $Install 和命令，不先卸载。
```

`/S` 静默、`/NS` 不创建快捷方式；它们不关闭进程/版本保护。手动安装不要随便加 `/UPDATE`：
模板的 updater 模式会跳过缺失 WebView2 的首次安装分支。需要交互确认时直接运行安装器。
静默 exit 0 仍需核对安装文件 SHA、登记版本和实际冷启动，不等于最终验收。

Windows PowerShell 5 的现有 preflight 在本机 `LongPathsEnabled=0`、>260 字符安装目标上
返回 **3 并安全拒绝**；fixture 对此要求零载荷写入、所有 seed 不变。短中文空格目录通过。
这不是已证明支持超长 NSIS 安装路径；不修改机器策略来让测试通过。运行中 EXE 的真实 Windows
文件锁与目标 guard 都有原生回归，guard 不杀目标或其他安装。

## 回退的真实边界

- **没有已支持的 NSIS 降级/自动事务回滚命令。** 重新运行旧安装器会被拒绝。
- fixture 的回退是私有目录的**文件级**回退，不改注册表，不表示已安装产品完成降级。
- 真实回退应先在隔离 VM 复原升级前快照（安装树、所需用户数据、登记状态一起），或由发布负责人
  制定并验证一致的人工恢复方案。仅复制旧 EXE 不是恢复：core/web/prompts/skills、Loader 和用户
  schema 都可能不一致；仅恢复文件还会留下新版安装登记，后续 updater/卸载语义不能冒称正确。
- 用户在升级后产生的新数据不可被无条件旧备份覆盖。活跃项目恢复与凭据后端的迁移需另行验证；
  保留字节的 seed 测试不能代替数据 schema 降级兼容性证明。

最终候选构建、根 `npm run check`、正式签名/发布及实际安装激活由发布负责人执行。签名材料
缺失时只可用现有 `build:unsigned` 产生诊断候选；见 [桌面发行文档](windows-desktop.md)。
