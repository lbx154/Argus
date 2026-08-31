# Windows Desktop（Tauri / Rust）

Argus 的 Windows 桌面端由 **Tauri 2 + Rust** 实现。它不分叉 Argus 的
Manager、Planner、Engineer、Reviewer、WebAPI 或 Web UI：桌面宿主启动同一份
冻结 Python 运行时，并把检查入库的 Web cockpit 显示在本地受限容器中。

当前 Windows 桌面端只保留 `desktop-tauri/` 这一套实现。它继续使用产品标识
`cn.argusbot.desktop`、`Argus.exe`、既有 Python 后端协议和兼容的 per-user 数据根，
因此迁移不会丢失已选 CLI、Web token 或可验证的后端 ownership record。

## 安装与使用

当 GitHub Release 提供 `Argus-<version>-setup.exe` 时，下载并运行该 NSIS 安装包。
它包含冻结的 Argus backend；终端用户不需要为桌面端另行安装 Python、Node.js 或
virtual environment。若 Release 页面没有匹配的安装包，请使用主 README 中的 Windows
pip 安装方式，不要把贡献者构建目录当作发布物。

首次启动会在后台启动本地后端，后端就绪后直接打开 cockpit；不会强制弹出配置向导。
需要修改 Agent CLI、可执行文件、端口或桌面外观时，使用 **文件 → 设置**。普通关闭和
菜单中的 **隐藏窗口并在后台继续** 是同一个行为：隐藏到系统托盘并保留正在进行的工作；
只有 **停止本地后端并退出** 会终止已验证的后端。

## 功能范围

Tauri 桌面端提供完整的原生宿主功能：

- 首次运行时选择 Codex、Claude、Copilot、Cursor、Pi、OpenCode、Grok Build、Qoder 或
  DeepSeek Harness，并支持显式选择可执行文件；
- 启动、认证、接管和安全停止冻结的 `argus-backend.exe`；
- 对 PID、启动时间、可执行文件、release manifest digest、端口和 Web-token hash
  做严格 ownership 验证；
- 有界的健康检测和自动恢复，避免无限 crash loop；
- 原生 Windows 标题栏、单实例、托盘、隐藏到后台、菜单和显式“停止后端并退出”；
- 后端失败时仍可操作的启动/恢复页，和脱敏诊断 ZIP 导出；
- 完成交付时的去重通知、工作台定位和右侧成果视图；
- 完整的 Web cockpit，而非维护第二套桌面 UI；
- **签名自动更新**：启动后异步检查、发现新版本时桌面通知和页面提示、用户确认后
  下载、验证 minisign 签名、交给 NSIS 安装器并重启。

它不会接管无法证明属于当前桌面安装的监听进程，也不会从未经签名或 HTTP 更新源
下载/执行内容。

Desktop 不改变 Manager、Planner、Engineer、Reviewer、Workbench 或 Vertical 的职责；
这些行为始终由远端主线的 Argus Python 运行时拥有。冻结包中的
`resources/argus-backend/_internal` 是 release payload，不是源码 checkout。框架修复必须在
独立源码工作区中完成并经过审查，再通过唯一的 reviewed deployment boundary 进入新的
Desktop release，不能直接修改安装目录中的冻结文件。

首次启动不再强制显示配置向导：Argus 会在后台无控制台窗口地启动本地后端，并在就绪后
直接打开 cockpit。Agent CLI、端口和外观只从 **文件 → 设置** 修改；cockpit 左下角设置
按钮保留为当前项目的预算、角色与模型等运行时配置入口。文件/帮助菜单由可信 Tauri shell
渲染为随浅色/深色主题变化的渐变栏，不再使用与上下内容割裂的 Windows 原生菜单色块。

## 架构

```text
Windows native non-client frame
  └─ Argus.exe (Tauri/Rust)
       ├─ 本地 launcher / settings / update shell
       ├─ 隔离 iframe：认证后的 127.0.0.1 Web cockpit
       ├─ 受控托盘、菜单、通知和诊断
       └─ resources/argus-backend/argus-backend.exe
            └─ 既有 Argus WebAPI + checked-in Web cockpit
```

工作台 iframe 和 Tauri shell 是两个安全域。Tauri IPC 只存在于本地 shell；iframe
只可通过 `postMessage` 发送长度受限的交付通知数据。父窗口同时验证消息来源必须是
当前 iframe，且 origin 必须等于配置的 `http://127.0.0.1:<port>`。因此 loopback
cockpit 不会获得文件系统、Shell、更新器或任意 Tauri command 的权限。

Windows 包会把 Microsoft 的 `WebView2Loader.dll` 显式放在 `Argus.exe` 同目录；这和
WebView2 Runtime 是两件事。安装器仍会按 Tauri 的 `downloadBootstrapper` 策略处理
缺失或过旧的 Runtime，但启动不再依赖构建机 PATH 中碰巧存在的 Loader DLL。原生
Windows 标题栏会随 launcher 设置和 cockpit 当前的浅色/深色主题同步，不使用覆盖
Windows caption controls 的黑色自绘条。

Desktop ready 路径只取得已认证 cockpit URL，不在首屏前扫描 Agent CLI 或 Pi 配置；这些
设置数据只在操作员打开“文件 → 设置”时读取。同一 URL 的后端重连保留现有 React cockpit，
不会整页重载。WebSocket 事件按短帧批处理，长会话的离屏事件行由 WebView2 跳过 layout/paint；
嵌入模式还避免第二层启动 splash 和持续全屏模糊动画。以上只减少宿主与渲染开销，不改变
Manager、Planner、Engineer、Reviewer、轮询安全网或任务状态语义。

Desktop 输入框默认显示 **任务** 模式，像 Codex 一样把操作员明确提交的工作直接交给 Manager
路由，省去一次容易误判且耗时的消息类别模型调用；Planner 签发、Engineer 执行和 Reviewer
审查仍全部保留。可切换为 **自动**（由 front-door 判断）或 **对话**（保证不入队）。Codex
无工具控制调用继续读取用户的 provider/auth，但临时关闭 plugins、MCP、JS REPL 和 rules
加载；`workflow_mode=direct` 使用精简但真实的单节点 Planner 签发提示。隔离真实性能探针中，
严格 Planner-owned dispatch 从 141.160 秒降到 28.025 秒（Manager 10.550 秒、Planner
16.465 秒）。实际网络/provider 负载仍会造成波动。

## 关闭、后台运行和单实例

- Windows 的普通 **关闭** 按钮会隐藏窗口到托盘，后端、daemon 和进行中的任务继续；
- 托盘左键、菜单“显示 Argus”或再次启动 `Argus.exe` 会恢复同一窗口；
- 过去两个近似的“关闭/退出但保留后端”入口已合并为一个 **隐藏窗口并在后台继续**；
- **停止本地后端并退出** 是唯一会终止后台进程树的路径，且只对已验证 ownership 的
  PID/root PID 使用 `taskkill /t /f`；
- NSIS 安装器在显式升级事务中会结束 `Argus.exe` 和 `argus-backend.exe`，避免旧版
  “关闭即入托盘”阻塞替换文件；
- 项目管理中的 **立即停止** 使用 PID/start identity 已验证的 force-stop：先给 daemon 1 秒
  响应控制请求，再只终止该进程树。状态刷新为“未运行”后，删除按钮立即可用；删除仍只是
  移入可恢复 trash，workspace 默认保留。

## 完成交付

成功任务只产生一份 durable delivery receipt。稳定的 `delivery_id` 关联生命周期事件、
Manager 对话、transcript replay、Mission View 和右侧成果面板；receipt 包含经验证的摘要、
审查状态及最多六个安全的 workspace-relative target。

目标只来自 Reviewer 明确给出的 evidence、当前 Vertical 声明的主交付物，或 Manager Live
View 的展示回退；Desktop 不扫描 workspace 猜测成果。每个 target 在打开或下载前仍需通过
受保护 artifact API 校验。窗口隐藏或失焦时 Tauri 发送原生通知；点击通知恢复已认证的
cockpit，并在存在目标时定位到对应成果。没有可展示文件时仍显示可信摘要和 Mission View，
不会虚构产物。

## 后端身份与恢复

首次启动会生成 32-byte URL-safe Web token，保存在当前用户的
`%APPDATA%\argus-desktop\settings.json`。原 token 从不会写进 ownership record、
日志或诊断包。每次启动或健康检查均需认证 `/api/meta`，并验证：

1. listener PID 与启动时间；
2. 精确后端 executable path；
3. 当前 release manifest 的 `source_digest`；
4. loopback host/port；
5. Web token 的 SHA-256；
6. 本次 spawn 的随机 `ARGUS_DESKTOP_LAUNCH_NONCE`。

新版本只能替换两类旧 listener：完整旧 ownership record 精确匹配的后端，或经过
当前 token 认证且路径精确等于 bundled backend 的兼容旧版 listener。其他端口占用、
远程地址、身份缺字段或 digest 不一致均 fail closed。

健康检测每 5 秒一次；短暂网络故障最多重试两次，确认失效后按 0.5 s、1.5 s、4 s
最多自动恢复三次，稳定 60 秒才重置熔断计数。

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
cockpit 工作流。确认安装后，NSIS 会按既有升级语义结束受管理后端；请在任务边界操作。发布物
只提供签名的 NSIS 安装包，不再生成或维护便携版。

### 发布者一次性密钥设置

仓库只保存 updater **公钥**。私钥绝不能加入 Git、release asset、日志或诊断包。
本地初始化时可生成一对密钥：

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
并保留 GitHub 作为回退。每次发布都应比对 OSS 与 GitHub 的 SHA-256。

这些脚本不会在本地 build、测试或应用启动时写入任何远程服务。

## 开发要求

- Windows 10/11 x64；
- Python 3.11+ 与 PyInstaller；
- Node.js 22.12+；
- Rust stable 的 `x86_64-pc-windows-msvc` toolchain；
- Visual Studio 2022 Build Tools（Desktop development with C++ / MSVC）；
- 一个已登录的支持 Agent CLI（如需实际运行任务）。

### Role session 复用

Desktop 不需要机器专用的 Pi 设置来复用角色上下文。共享运行时默认使用
`ARGUS_SKILL_ROLE_SESSION_POLICY=auto`：支持 resume 的 Pi、Codex、Claude/Qoder、
Copilot、OpenCode 和 Grok 使用有界、按角色隔离的 rolling session；DeepSeek Harness 等
fresh-only backend 保持 fresh。Planner、Engineer、Reviewer 各自维护独立 capsule，不会跨角色
或无关任务共享 provider thread；达到 turn/token 上限或身份相关上下文变化时自动轮换。完整
契约见 [Role sessions and on-demand Skills](ROLE_SESSIONS_AND_SKILLS.md)。

安装依赖：

```powershell
uv venv --python 3.12 --seed .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e . pytest ruff "pyinstaller>=6.11,<7"

npm --prefix frontend/web ci
npm --prefix desktop-tauri ci
rustup toolchain install stable-x86_64-pc-windows-msvc
```

开发模式使用源码 Python runtime，不需要构建冻结后端：

```powershell
$env:ARGUS_DESKTOP_DEV = "1"
$env:ARGUS_DESKTOP_REPO_ROOT = (Get-Location).Path
$env:ARGUS_SKILL_BIN = "$PWD\.venv\Scripts\python.exe"
npm --prefix desktop-tauri run dev
```

只允许一个受管理 API 占用同一端口。桌面端可以替换其已验证的前一版本 listener，
但不会接管手工启动、未认证、非本地或路径不一致的 `argus --web`。

### 终端退出与后台任务

`argus` TUI、loopback WebAPI 和每个项目 daemon 是不同进程。默认交互退出策略为 `detach`：
关闭终端只结束 TUI，WebAPI 和任务可继续；再次进入同一目录会重连现有 session。需要清理时
应显式使用 `argus --exit-policy stop-api` 或 `argus --exit-policy stop-all`。这些路径按
ownership、PID/start identity 和 daemon 控制协议 fail closed，不应以 Task Manager 杀任意 PID
代替正常停止。

## 启动排查

- **本地后端启动超时**：检查 Desktop log 和认证后的 `/api/meta`；ownership 同时记录
  Windows launcher/root PID 与真实 listener PID。
- **安装更新后提示端口版本不一致**：正常关闭旧 Desktop，完成新安装后启动一次。只有完整旧
  ownership record 匹配，或当前 token 可认证且 executable 精确等于 bundled backend 时，
  Tauri 才能替换旧 listener；其余端口占用保持 fail closed。
- **项目列表已出现但仍显示 Connecting**：API handshake 和项目索引已经成功，当前 snapshot
  仍在有界读取中；这不代表 CLI 与 Web 使用了不同状态。
- **`snapshot refresh failed · fetch failed`**：表示针对共享本地 WebAPI 的新 REST 请求失败，
  应查看 socket 原因和后端日志。
- **`background executor failed to start (rc=...)`**：检查 UI 诊断和最新
  `daemons/boot-*.log`；当前运行时保留 helper stderr、workdir/interpreter 校验及 Windows exit code。
- **角色 turn 成功后出现可选 CHECKPOINT 文件错误**：checkpoint 只承载 role-session metadata，
  不是 mission authority；capsule 持久化失败不能覆盖 provider/Reviewer 的权威结果。
- **冻结 backend 需要框架更新**：`_internal` 不是源码 checkout，应安装新构建的 release，
  不要原地编辑冻结文件。

## 验证

```powershell
.\.venv\Scripts\python.exe -m ruff check argus_skill desktop-tauri/scripts tests/desktop
.\.venv\Scripts\python.exe -m pytest -q tests/desktop
npm --prefix frontend/web test
npm --prefix desktop-tauri run ui:typecheck
npm --prefix desktop-tauri run test:rust
# 在已完成 Tauri build 后，以临时 AppData/端口验证真实宿主启动；不会触碰用户数据
npm --prefix desktop-tauri run smoke:host
```

构建冻结后端与 Tauri NSIS 包：

```powershell
npm --prefix frontend/web run build
.\desktop-tauri\scripts\build-backend.ps1 -SkipInstall
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content "$PWD\desktop-tauri\.keys\argus-updater.key" -Raw
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""  # 无密码私钥；带密码时设置实际密码
npm --prefix desktop-tauri run dist
```

`dist` 只产生 NSIS installer、其 detached signature 和 `latest.json`（有 signing key 时），
均位于 `desktop-tauri/release/`。本地 build 不会发布 GitHub Release、上传 OSS、创建 PR
或推送 Git。

## 本地数据与诊断

桌面设置、ownership record、日志、更新检查缓存均保留在
`%APPDATA%\argus-desktop\`：

```text
settings.json
runtime/backend.json
logs/desktop.log
update-check.json
```

`Export diagnostics` 只包含脱敏后的 settings、ownership、最后 500 KB desktop log
和平台元数据；它会遮蔽 JSON token、URL token 和 bearer authorization。仍应在分享前
由操作员审阅 ZIP 内容。
