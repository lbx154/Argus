# 桌面端（Tauri / Rust）

Argus 的桌面端由 **Tauri 2 + Rust** 实现。它不分叉 Argus 的角色、Web API 或 Web UI：
安装包带着 TypeScript 核心（npm 包 `packages/core` 的 `dist/argus.mjs`，旁边是它读的
`prompts/`、`ways/` 和网页工作台 `web/`），宿主用**本机的 Node.js** 运行它，再把它服务的
Web cockpit 显示在本地受限容器中。

`desktop-tauri/` 是唯一一套桌面实现。它继续使用产品标识 `cn.argusbot.desktop`、`Argus.exe`
和既有的 per-user 数据根，因此升级不会丢失已选 CLI 和 Web token。

## 安装与使用

本仓库的发行渠道是
[lbx154/Argus Releases](https://github.com/lbx154/Argus/releases)；
[microsoft/ArgusAgent Releases](https://github.com/microsoft/ArgusAgent/releases)
是另一个上游渠道，不要混用更新签名或安装包。
本地产物只有完成验收、真实签名并经发布负责人批准上传后才是该渠道的正式 Release。
Windows 下载对应页面提供的 `Argus-<version>-setup.exe` 并运行 NSIS 安装包；macOS 和 Linux
的构建见下文。

**需要 Node.js 22 或更高版本。** Argus 驱动的 Codex、Claude Code、Copilot CLI 和 Pi 都是
npm 包，装它们时本机已经有了 Node。桌面端找不到 Node 或版本过低时，启动页显示
“需要 Node.js 22 或更高版本”、找到的版本和 <https://nodejs.org/> 下载链接；安装 LTS 版本后
点“重试”。桌面端不会自动下载或安装 Node。

首次启动必须在设置向导中明确选择并确认已安装、已登录的 Agent CLI（Codex、Claude Code、
Copilot CLI 或 Pi），确认前不会启动本地后端。桌面保存过选择且仍能找到对应 CLI 时，后续
启动直接打开 cockpit；CLI 已被卸载或路径失效时，重新显示向导。检测到可执行文件不代表已经
完成鉴权。需要修改 Agent CLI、可执行文件或端口时，使用 **文件 → 设置**。浅／深色使用工作台
的全局切换，启动页和原生标题栏同步该选择。

## 架构

```text
Argus.exe / Argus.app (Tauri/Rust)
  ├─ 本地 launcher / settings / update shell
  ├─ 隔离 iframe：带令牌的 127.0.0.1 Web cockpit
  ├─ 托盘、菜单、通知和诊断
  └─ node <resources>/argus-core/dist/argus.mjs --port P --backend B
       └─ Argus：Web API、项目的工作、它带的 web/ cockpit
```

桌面端启动 Argus 的流程（`desktop-tauri/src-tauri/src/backend.rs`）：

1. 设置已完成，所选 CLI 仍在磁盘上；随包的 `argus-core/dist/argus.mjs` 存在。
2. 找 Node：先看所选 CLI 的目录（nvm、Volta、Homebrew 把 npm 装的 CLI 放在 Node 旁边），
   再看 PATH，再看常见安装目录（`Program Files\nodejs`、nvm-windows、Volta、scoop、
   `/opt/homebrew/bin`、`/usr/local/bin`、`~/.volta/bin`、`~/.nvm/versions/node/*/bin`），
   取第一个 `node --version` 不低于 22 的。桌面应用不继承登录 shell 的 PATH（macOS 从
   Finder 启动时只有系统目录），所以要看这些目录。
3. 端口上若还有一个认得本桌面 Web token 的 Argus（上次桌面崩溃或被强杀后留下的），先停掉它：
   只有本桌面持有这个 token，`/api/meta` 认证通过即证明它是自己启动的。
4. 启动 `node argus.mjs --port <设置的端口> --backend <所选 CLI>`，环境变量：
   `ARGUS_WEB_TOKEN`（设置里的 Web token，cockpit 的每个项目请求都要带它）、
   `ARGUS_HOME`（状态根，默认 `~/.argus`；兼容产品用 `$<PROFILE>_HOME` 或 `~/.<profile>`，
   预览构建用自己数据目录下的 `argus-home/`）、`PATH`（Node 和所选 CLI 的目录在前，Argus
   按名字找 CLI）。工作目录是状态根；Windows 上不弹控制台窗口。
5. Argus 开始监听后在 stdout 打印 `Argus is at <url>`。这一行是唯一的就绪信号，cockpit 的
   地址取自它；子进程退出是唯一的失败信号，启动页显示退出码和最后几行输出。宿主不轮询健康、
   不自动重启，用户点“重试”。
6. 停止：Windows 用 `taskkill /T` 结束进程树；macOS、Linux 发 SIGTERM，Argus 收到后停下
   正在进行的调用并归还状态根。

工作台 iframe 和 Tauri shell 是两个安全域。Tauri IPC 只存在于本地 shell；iframe
只可通过 `postMessage` 发送长度受限的交付通知数据。父窗口同时验证消息来源必须是
当前 iframe，且 origin 必须等于 Argus 报告的 `http://127.0.0.1:<port>`。因此 loopback
cockpit 不会获得文件系统、Shell、更新器或任意 Tauri command 的权限。

原型冻结仅在可信顶层 shell 的初始化脚本中执行。Windows WebView2 会把 Tauri 初始化
脚本也注入 iframe；全局 `freezePrototype` 会破坏 d3／React Flow 的正常原型继承，
因此不能用于整棵 WebView。iframe 保持普通浏览器语义，但原生命令仍受 capability/origin
限制。
Web 懒加载失败最多在同一 tab 的一分钟内自动刷新一次；代码执行错误直接显示恢复页，
不会把所有错误误认为旧资源而反复刷新。

Windows 包会把 Microsoft 的 `WebView2Loader.dll` 显式放在 `Argus.exe` 同目录；这和
WebView2 Runtime 是两件事。安装器仍会按 Tauri 的 `downloadBootstrapper` 策略处理
缺失或过旧的 Runtime，但启动不再依赖构建机 PATH 中碰巧存在的 Loader DLL。原生
Windows 标题栏会随 launcher 设置和 cockpit 当前的浅色/深色主题同步，不使用覆盖
Windows caption controls 的黑色自绘条。

Windows 包会把 Microsoft 的 `WebView2Loader.dll` 显式放在 `Argus.exe` 同目录；这和
WebView2 Runtime 是两件事。安装器仍会按 Tauri 的 `downloadBootstrapper` 策略处理
缺失或过旧的 Runtime，但启动不再依赖构建机 PATH 中碰巧存在的 Loader DLL。原生
Windows 标题栏会随 launcher 设置和 cockpit 当前的浅色/深色主题同步，不使用覆盖
Windows caption controls 的黑色自绘条。

### 兼容产品宿主

下游产品可以把 `argus-desktop` 作为 Rust path dependency，设置
`default-features = false`，再调用
`argus_desktop_lib::run_with_context(tauri::generate_context!())`。产品自己的
Tauri context 提供名称、版本、identifier、图标、资源和 capabilities；后端、设置和生命周期
仍由共享宿主负责。默认 Argus 构建保留 `default-host` feature，行为不变。编译时须设置独立的
`ARGUS_DESKTOP_PROFILE`，它决定桌面数据目录和 Argus 状态根（`$<PROFILE>_HOME` 或
`~/.<profile>`），产品不会读到 Argus 的数据。

共享启动页的 Vite 构建接受 `ARGUS_DESKTOP_BRAND` JSON，字段为 `name`、
`mark`、`markDark`，两张图片使用 `data:image/...` URL。自定义产品展示自己的
浅／深色图标，不等待已隐藏的 Argus 眼睛动画。未配置时保留原始 Argus 外观。

下游不得继承 Argus 的更新 endpoint 或签名公钥。未设置产品签名更新渠道时，
产品入口应设置 `ARGUS_DESKTOP_DISABLE_UPDATE_CHECK=1`，并关闭自己的
`createUpdaterArtifacts`。这些扩展不表示下游产品已完成任何平台的安装包验收。

## 关闭、后台运行和单实例

- 普通 **关闭** 按钮会隐藏窗口到托盘，Argus 和进行中的工作继续；
- 托盘左键、菜单“显示 Argus”或再次启动 `Argus.exe` 会恢复同一窗口；
- **停止本地后端并退出** 停止 Argus 后退出；退出应用（包括 macOS 的 Cmd+Q）也会停止它：
  Argus 是桌面进程的子进程，随它结束；
- NSIS 安装器只核对目标安装目录的进程；目录仍在运行或无法核实进程路径时阻止替换，
  提示使用该安装的 **停止本地后端并退出** 后重试，不按名称终止其他安装或预览；
- Windows 升级采用就地替换，不执行旧版卸载器；否则旧卸载器可能绕过新版保护，
  终止独立预览。卸载仍是通过新安装的卸载器执行的独立操作。

## 完成交付

cockpit 通过 `postMessage` 把交付通知（标题、摘要、可选的成果路径和项目）交给宿主；宿主按
`delivery_id` 去重，窗口隐藏或失焦时发送原生通知。点击通知恢复已认证的 cockpit，并在有
成果路径时定位到它。成果在打开或下载前仍由 Argus 的 artifact API 校验；宿主只接受当前
Argus 地址发出的 blob 下载。

## 自动更新与签名信任

更新配置在 `desktop-tauri/src-tauri/tauri.conf.json`：

```text
https://github.com/lbx154/Argus/releases/latest/download/latest.json
```

Tauri updater 只接受 HTTPS，读取 `latest.json` 中与当前 Windows/NSIS target 对应的
资产 URL 和 minisign signature。它在安装前对下载字节进行验证；仅篡改 manifest URL、
镜像、DNS 或下载文件都不能绕过嵌入应用的公钥。配置没有
`dangerousInsecureTransportProtocol`，也不允许证书绕过。

已安装版本会先恢复有效的本地更新缓存，并在应用启动 30 秒后才进行后台网络检查，避免与
cockpit 首屏竞争。成功检查按 6 小时节流；临时网络失败使用 15 分钟起、最长 2 小时的有界
指数退避，而不会错误地沉默 6 小时。manifest 请求有 15 秒超时；用户明确批准的安装包下载
拥有独立的较长超时，下载进度最多每 200 ms 推送一次，避免大量 IPC/UI 重绘。

只有检测到**严格高于当前版本**、且未被用户忽略的新版本时，才会触发一次 Windows 原生通知
和 shell 中的更新卡片。卡片显示版本、release notes 和签名验证说明，等待用户选择“查看并
安装”；它不会自动下载或执行任何更新包。若后台复核发现缓存候选已撤回或不再更新，会及时
撤下旧卡片。

手动菜单“检查更新”可绕过 6 小时缓存，并显示“已是最新版本”或具体错误。后台检查中的
“检查中”、当前已是最新版本、网络失败和暂时无法取得 manifest 都只写入脱敏日志，不打断
cockpit 工作流。确认安装后，先完成下载与签名验证，再由当前宿主停止自己启动的 Argus，
最后交给 NSIS。下载失败、取消或验签失败不会停止任务；请在任务边界操作。安装器会再次核对
目标进程已退出，拒绝降级，也不会执行历史卸载器或从注册表读取任意旧文件名后删除它。
WebView2 安装器使用每个安装事务自己的临时目录，不清空共享 TEMP 中的同名文件。
发布物只提供签名的 NSIS 安装包，不把未签名候选或便携预览当作正式版本。

### 发布者一次性密钥设置

仓库只保存 updater **公钥**。私钥绝不能加入 Git、release asset、日志或诊断包。
已有安装的更新必须沿用与内置公钥匹配的签名材料；公钥不匹配或材料不可用时停止发布，
不能为了凑齐 SIG/JSON 临时生成另一对密钥。以下初始化命令只适用于发布负责人明确批准的
首次建立信任链，不适用于普通补丁发行：

```powershell
npm --prefix desktop-tauri run update:generate-key
```

私钥写入被忽略的 `desktop-tauri/.keys/argus-updater.key`。发布负责人必须把同一私钥
的**内容**作为 GitHub Actions secret `TAURI_SIGNING_PRIVATE_KEY`；若使用带密码私钥，
还需设置 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`。私钥丢失意味着不能继续为该公钥签发
更新，需通过新的完整安装包轮换公钥。

### 国内/镜像双通道（可选）

借鉴 Tianshu-harness 的模式，`desktop-tauri/scripts/update-mirror-worker.js` 提供了
Cloudflare Worker 模板：它仅代理 `lbx154/Argus` 的允许 release 资产，并将
`latest.json` 内 GitHub URL 改写为自己的受限资产路由。它不重签、不改字节，因此
客户端签名验证仍是唯一完整性锚点。

`desktop-tauri/scripts/upload-update-to-oss.ps1` 是显式 `-Apply` 才会写 OSS 的发布
操作脚本。它下载 GitHub Release、重写镜像 manifest URL、上传安装器和 `.sig`。
在配置 OSS/Worker 前，主 GitHub endpoint 仍是可用且安全的默认通道。部署镜像后，
将 HTTPS mirror endpoint 放到下一次 Tauri release 的 updater endpoint 列表首位，
并保留 GitHub 作为回退。每次发布都应比对 OSS 与 GitHub 上各安装器的字节数。

这些脚本不会在本地 build、测试或应用启动时写入任何远程服务。

## 开发与构建

开发要求：Node.js 22.12+；Rust stable（Windows 用 `x86_64-pc-windows-msvc` toolchain 和
Visual Studio 2022 Build Tools，macOS 用 Xcode command-line tools，Linux 见 Tauri 的
WebKitGTK 依赖）；一个已登录的支持 Agent CLI（如需实际运行任务）。不需要 Python。

```bash
npm ci
npm --prefix frontend/web ci
npm --prefix desktop-tauri ci
# 随包的 Argus：packages/core 的 dist/argus.mjs 与 web/（Tauri 编译时把它们复制为资源）
npm --prefix desktop-tauri run build:argus
npm --prefix desktop-tauri run dev
```

宿主在编译时把 `packages/core` 的资源复制到可执行文件旁（开发构建也一样），所以改了核心后
要重新 `build:argus` 并重新编译宿主。未签名安装包：
`npm --prefix desktop-tauri run build:unsigned`，产物在
`desktop-tauri/src-tauri/target/release/bundle/`（Windows NSIS、macOS `.app`/`.dmg`、
Linux `.AppImage`/`.deb`）。

## 启动排查

- **需要 Node.js 22 或更高版本**：见上文；装好后点“重试”。用 nvm 等版本管理器时，确认
  默认版本不低于 22。
- **本地后端启动失败**：启动页显示退出码和最后几行输出，完整输出在 desktop log。
  `listen EADDRINUSE` 表示端口被别的程序占用，在 **文件 → 设置** 换一个端口；
  `... is in use by process N` 表示另一个 Argus（例如终端里用同一 `ARGUS_HOME` 运行的
  `argus`）占着状态根，先停掉它。
- **本地后端意外退出**：同上看输出；点“重试”重新启动，项目状态在状态根里，不会丢。

## 验证

```bash
npm --prefix desktop-tauri run ui:typecheck
npm --prefix desktop-tauri run test:ui
npm --prefix desktop-tauri run test:release
# cargo check + 宿主单元测试；其中一个测试用本机 Node 启动随包的 Argus，
# 确认它报告地址、认得 token、使用 ARGUS_HOME、在 SIGTERM 后干净退出
npm --prefix desktop-tauri run test:rust
```

CI：`.github/workflows/desktop.yml` 在 Windows、macOS（arm64、x86_64）和 Linux 上构建
未签名安装包、跑以上测试，并检查安装包里带着 `argus-core/dist/argus.mjs` 与 `argus-core/web/`。

构建 Tauri NSIS 包时，使用**新的源码工作区与输出目录**，不要在含有旧构建的目录中依赖
自动清理。构建先生成随包的 Argus（`packages/core` 的 `npm run prepack`：TypeScript 构建、
esbuild 打包成 `dist/argus.mjs`、从 `frontend/web` 源码构建 `web/`），再编译宿主并打包。

```powershell
# 在已经安装依赖的新源码工作区执行，MSVC/SDK 环境应已配置。
# 签名材料由发布负责人通过受控签名环境预置，不读取或打印私钥。
# dist 在未配置签名时立即失败，不会生成临时密钥或假签名。
$env:CARGO_TARGET_DIR = Join-Path $PWD "desktop-tauri\build\windows-release-target"
$env:ARGUS_WINDOWS_RELEASE_DIR = Join-Path $PWD "desktop-tauri\release"
npm --prefix desktop-tauri run dist
```

`dist` 的 Windows 路径先一致构建、签名；正式发行只产生 NSIS installer、detached `.exe.sig`
和 updater `latest.json`。发布暂存尊重实际 `CARGO_TARGET_DIR`；也可通过
`stage-release.ps1 -BundleDirectory <NSIS目录> -OutputDirectory <新目录>` 指定路径。
目标发行目录已存在时直接拒绝，不会清空它。只有复制后的安装器与输入逐字节一致，且安装器和
trusted comment 均通过应用内置公钥验证，才写 `latest.json`；缺失、空白、错版本、错架构、
错误公钥或被篡改的签名不能生成发布元数据。发布说明列出版本号、git 提交号和各文件的字节数。
JSON 是更新元数据，不是账户凭据。

只有诊断候选才使用 `build:unsigned`；此路径没有可发布的 SIG/JSON，
不能把文件改名后称为正式版。新的发行还必须使用高于已发布版本的统一版本号。

Windows NSIS 模板基于固定的 Tauri CLI 2.11.4，修改范围与来源哈希记录在
`desktop-tauri/src-tauri/installer-template.nsi`，MIT 授权随源码和安装包提供。升级 CLI 时应一并
审查模板和进程保护测试，不能只更新锁文件。新安装器不自动迁移/卸载外部 WiX 或其他渠道。

`.exe.sig` 是供 Tauri updater 验证下载字节的 minisign 签名，不是 Windows Authenticode
证书签名；未配置证书时 Windows 仍可能显示信誉提示。成品仍需在不触碰真实用户数据的环境中
验证安装/升级、实际原生启动及必要回归；本地构建成功不等于验收完成。
本地 build 不会发布 GitHub Release、上传 OSS、创建 PR 或推送 Git。

## 本地数据与诊断

桌面设置、日志、更新检查缓存保留在每个用户的桌面数据目录（Windows
`%APPDATA%\argus-desktop\`，macOS `~/Library/Application Support/argus-desktop/`，Linux
`~/.local/share/argus-desktop/`）；项目在 Argus 的状态根（默认 `~/.argus`）：

```text
settings.json
logs/desktop.log
update-check.json
```

`Export diagnostics` 只包含脱敏后的 settings、最后 500 KB desktop log 和平台元数据；
它会遮蔽 JSON token、URL token 和 bearer authorization。仍应在分享前由操作员审阅 ZIP 内容。
