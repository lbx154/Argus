# Windows Desktop（Tauri / Rust）

Argus 的 Windows 桌面端由 **Tauri 2 + Rust** 实现。它不分叉 Argus 的
Manager、Planner、Engineer、Reviewer、WebAPI 或 Web UI：桌面宿主启动同一份
冻结 Python 运行时，并把检查入库的 Web cockpit 显示在本地受限容器中。

当前 Windows 桌面端只保留 `desktop-tauri/` 这一套实现。它继续使用产品标识
`cn.argusbot.desktop`、`Argus.exe`、既有 Python 后端协议和兼容的 per-user 数据根，
因此迁移不会丢失已选 CLI、Web token 或可验证的后端 ownership record。

## 安装与使用

官方 GitHub Release 位于
[microsoft/ArgusAgent Releases](https://github.com/microsoft/ArgusAgent/releases)；
开发预览安装包位于
[lbx154/Argus Releases](https://github.com/lbx154/Argus/releases)。
先选择渠道，再下载该页面提供的 `Argus-<version>-setup.exe` 并运行 NSIS 安装包。
它包含冻结的 Argus backend；终端用户不需要为桌面端另行安装 Python、Node.js 或
virtual environment。若 Release 页面没有匹配的安装包，请使用主 README 中的 Windows
pip 安装方式，不要把贡献者构建目录当作发布物。

预览 EXE 可能落后于源码 `main`。源码修复提交或两仓同步不会重新发布安装包；
需要某个修复时，先确认安装包的 release notes，或使用 README 的源码安装方式。

首次启动必须在设置向导中明确选择并确认已安装、已登录的 Agent CLI，确认前不会启动
本地后端。共享配置仅提供候选，不会跳过首次确认，也不会默默选择 Codex。桌面保存过
选择且仍能找到对应 CLI 时，后续启动直接打开 cockpit；CLI 已被卸载或路径失效时，
重新显示向导。检测到可执行文件不代表已经完成鉴权；桌面中明确保存的选择优先于共享配置。
试用版另提供 **使用内部测试 Key** 入口：持有邀请 Key 的用户可直接粘贴并点击
**开始试用**。客户端会验证 Key、下载官方独立版 Copilot、校验安装包，并完成一次真实
连接验证后进入工作台。此路径不需要终端、Python、Node.js 或用户自己的 Coding 账号。
试用配置会保留，重启后直接进入工作台；每个 Key 累计额度为 100 万 token。
这个入口从 0.1.3 桌面安装包提供；0.1.2 及更早的 EXE 需要升级。
Mac 构建与当前验证范围见 [桌面内部试用版](desktop-trial.md)。
需要修改 Agent CLI、可执行文件或端口时，使用 **文件 → 设置**。浅／深色使用工作台的全局切换，启动页和原生标题栏同步该选择。普通关闭和
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

已完成桌面首次确认的安装不重复显示向导：Argus 会在后台无控制台窗口地启动本地后端，并在就绪后
直接打开 cockpit。缺少 CLI 或可执行文件时先完成设置；之后从 **文件 → 设置**
修改 Agent CLI 和端口。cockpit 左下角设置
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

原型冻结仅在可信顶层 shell 的初始化脚本中执行。Windows WebView2 会把 Tauri 初始化
脚本也注入 iframe；全局 `freezePrototype` 会破坏 d3／React Flow 的正常原型继承，
因此不能用于整棵 WebView。iframe 保持普通浏览器语义，但原生命令仍受 capability/origin
限制：真实宿主测试同时验证 shell 已冻结、iframe 未冻结以及 iframe 原生调用被拒绝。
Web 懒加载失败最多在同一 tab 的一分钟内自动刷新一次；代码执行错误直接显示恢复页，
不会把所有错误误认为旧资源而反复刷新。

Windows 包会把 Microsoft 的 `WebView2Loader.dll` 显式放在 `Argus.exe` 同目录；这和
WebView2 Runtime 是两件事。安装器仍会按 Tauri 的 `downloadBootstrapper` 策略处理
缺失或过旧的 Runtime，但启动不再依赖构建机 PATH 中碰巧存在的 Loader DLL。原生
Windows 标题栏会随 launcher 设置和 cockpit 当前的浅色/深色主题同步，不使用覆盖
Windows caption controls 的黑色自绘条。

Desktop 首次 ready 时读取已有配置并检测 CLI，决定打开 cockpit 还是首次设置。
同一 URL 的后端重连保留现有 React cockpit，
不会整页重载。WebSocket 事件按短帧批处理，长会话的离屏事件行由 WebView2 跳过 layout/paint；
嵌入模式还避免第二层启动 splash 和持续全屏模糊动画。以上只减少宿主与渲染开销，不改变
Manager、Planner、Engineer、Reviewer、轮询安全网或任务状态语义。

Desktop 输入框默认显示 **自动** 模式，由 front-door 区分对话与正式任务，避免问候、状态查询
等消息误入 Manager → Planner 任务管线。操作员可显式切换为 **任务**，把确定的工作直接交给
Manager 路由并省去消息类别调用；也可切换为 **对话**，保证消息不入队。Planner 签发、
Engineer 执行和 Reviewer 审查仍全部保留。Codex
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

健康检测每 5 秒一次。当前宿主的预期版本指纹编译进 EXE，已认证的 PID、启动时间、路径和
启动证明保存在当前会话中；不再因磁盘清单／ownership 记录暂时不可读而变成“陌生后端”。
ownership 记录使用同目录临时文件与原子替换写入，写入失败会明确拒绝完成启动。

磁盘包完整性与活跃进程身份分别检查。清单缺失、无效或与当前 EXE 不匹配时，保持已验证
的运行连接并显示非阻断提示，但拒绝从损坏／混版目录重启。真正的认证、PID、启动时间、
路径、构建身份或启动证明冲突仍 fail closed，不会接管或终止未知进程。

短暂网络故障快速重试两次；进程仍存活时转为低频健康观察并提示重连，不因忙碌或响应体
超时强杀任务。确认进程退出后按 0.5 s、1.5 s、4 s 最多自动恢复三次；每次恢复有独立
生命周期代次，稳定 60 秒才重置熔断计数。响应体超时／连接截断不再误分类为身份冲突。

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

## 内部体验预览（不安装、不发布）

`npm --prefix desktop-tauri run build:preview` 是独立的本地验收路径，不调用 `dist`、
NSIS、签名或上传脚本。它刷新仓库已有的运行身份清单和 Web/TUI 静态资源，冻结当前
Python 后端，以 Tauri `--no-bundle --no-sign --features preview` 编译宿主，再对实际
暂存目录执行 WebView2 端到端检查。仅检查通过后生成 `desktop-tauri/build/previews/`
下的独立 ZIP；不生成安装器、签名文件或 `latest.json`。内置 `release_manifest.json`
仍是后端身份校验的必要数据，不是更新发布清单。

完整解压后运行 `Argus.exe`。预览使用单独标识 `cn.argusbot.desktop.preview`，
桌面设置、日志与 WebView 缓存位于 `%APPDATA%/argus-desktop-preview/`，项目与运行时
配置位于其 `argus-home/` 子目录，默认端口 18799。即使从设置过 `ARGUS_SKILL_HOME`
的终端启动，也不读取该生产数据根；不会迁移正式版设置。CLI 自身的登录仍由原 CLI
管理。不要让两个版本同时写同一个项目工作目录。

预览在编译期禁用发布检查和安装，手动“检查更新”会解释原因。它不是新增的正式便携
发布渠道，也不会创建 Start Menu 安装记录。需要 Windows 10/11 x64、WebView2 Runtime
及一个已安装并登录的 Agent CLI；没有 WebView2 时请安装 Microsoft 官方 Evergreen Runtime。

桌面设置提供端口校验和重新检测 CLI，不再提供独立外观选项。界面固定统一配色，旧渐变
偏好归一到标准；工作台全局浅／深色同步并持久化到宿主，主题变动不重载工作台。保存失败保留编辑内容
并就地显示原因，已占用的新端口不会中断当前后端；后端恢复时保留同 URL 的 Web 文档和
未发送输入。`Ctrl+,` 与 `Ctrl+N` 在工作台 iframe 内同样有效。中文和英文界面的角色名
均为 Manager、Planner、Engineer、Reviewer，普通操作（例如管理会话）不误改为角色。
四种角色标记分别使用蓝、紫、青绿和琥珀色，空闲或失败时也保留角色颜色，状态另以文字表达。

主动打开右侧文件时，预览扩到窗口宽度约 45%（上限 840px，并保留至少 360px 对话区）；
仍支持手动拖拽，后台刷新不主动改宽度。PDF 的放大页面使用可扩展的滚动内容区域，四边均可
滚动到达；“适合页面”恢复整页，缩放保留阅读中心，先在离屏 canvas 完成绘制再替换可见画面。

工作台只显示项目概览、运行进程和 AI IDE，概览卡片与标签共享同一份入口定义。
其他页面的源码／后端能力和项目数据没有删除，但不再加载其 UI 或专用轮询。
AI IDE 的编辑区、文件树、Git 区域、终端和状态栏统一跟随全局浅色／深色主题；切换主题不
重新打开文件；已访问模块切换时保留文件选择、目录展开状态及阅读位置。隐藏工作台或切到其他
模块时暂停 IDE 文件／目录／Git 轮询，已停止的进程视图不再每秒刷新，
并尊重系统减少动态效果设置。

主对话与地图使用同一 `ComposerSurface` 和样式；主对话保留模式选择、斜杠命令、改写、
附件及停止等待。主页输入框宽度固定为可用空间的 100%（最大 680px），不随空白、悬停、
聚焦、输入或清空伸缩；多行高度自适应与地图的 compact／hover 行为保持不变。
草稿／附件由 App 统一管理，视图切换或上传期间输入新内容不会被旧提交
清空。眼睛动效只在启动／处理消息时出现，故障和空闲不持续转动，并尊重减少动态效果。

`build:preview` 默认还要求 1800 秒连续运行及故障注入检查通过，再生成 ZIP。开发者可通过
`ARGUS_PREVIEW_SOAK_SECONDS` 缩短本地诊断，但交付验收不得把短检查当成长稳测试。
测试会在自有临时运行包中模拟清单缺失、替换和 ownership 记录损坏，恢复原始字节后继续
观测同一认证 PID；不会修改用户安装。正式版本不能被 `ARGUS_DESKTOP_DEV` 环境变量切换
到源码运行，开发调试使用 `tauri dev` 的 debug 构建。

浏览器交互回归：`npm --prefix desktop-tauri run test:ui`（Windows 使用已安装的 Edge）。
已暂存的真实预览可以再次执行 `npm --prefix desktop-tauri run smoke:preview -- <Argus.exe>`。
测试创建独立 fixture，不读取实际用户配置、不发送付费模型任务；研究地图以 kiosk
只读模式验证。宿主验证还覆盖 CLI 预检、再次启动、单实例恢复、退出和独立数据目录。

正式发布仍要求 MSVC。仅本地预览可使用已安装的 `stable-x86_64-pc-windows-gnu` 和现代
SEH/UCRT MinGW，通过当前进程的 `RUSTUP_TOOLCHAIN` 与 PATH 选择；不要使用旧的 SJLJ
编译器。预览会在无编译器 PATH 的环境中测试 DLL 布局。

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
npm --prefix desktop-tauri run test:ui
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
均位于 `desktop-tauri/release/`。`.exe.sig` 是供 Tauri updater 验证下载字节的 minisign 签名，
不是 Windows Authenticode 证书签名；未配置证书时 Windows 仍可能显示信誉提示。
本地 build 不会发布 GitHub Release、上传 OSS、创建 PR 或推送 Git。

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
