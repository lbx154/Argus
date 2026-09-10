# Windows / Mac / Linux 内部试用版

首次启动或 **文件 → 设置** 中可打开 **输入内部测试 Key**。
启动恢复页也提供 **使用内部测试 Key** 按钮。界面提示：

> 如果您是拿到了内部测试的 Key，可以直接在这个地方填入使用

粘贴 Key 后点击 **开始试用**，客户端依次验证余额、自动下载官方 Copilot 独立程序、
校验 SHA-256、验证一次真实模型回复，成功后直接打开 Argus 工作台。
工作台随桌面包提供；Copilot 在首次启用试用时下载。用户无需运行命令行，
无需预装 Python、Node.js 或 Copilot，也无需登录 GitHub、Codex、Claude 账号。
首次准备需要连接 `argusbot.cn` 和 GitHub Releases；失败时显示错误并允许重试。

试用模式会自动选择本机回环端口：原端口被其他服务占用时，使用系统分配的空闲端口，
保存后供本地后端和工作台共同使用；以后启动或重启时也会自动处理冲突，无需手动设置。
已验证归属的运行中后端仍会复用，不会接管或终止其他服务。使用自己的账号时仍遵循手动选择的端口。

v0.1.4 试用默认模型为 **GPT-5.5，推理强度 high**。服务端已切换到
Copilot Responses 接口并统一强制 high，旧客户端的试用请求也使用该模型，
不会回退到 GPT-4.1。新安装配置会保存 GPT-5.5 和 high。

v0.1.4 的下载反馈：有文件总大小时显示实际下载百分比与已下载/总大小；
没有总大小时显示不定进度条和已接收大小，不估造百分比。连接或下载连续
30 秒没有进度事件时，提示检查网络或开启代理（梯子）的系统代理 / TUN 模式。
收到新数据会清除提示；下载后分别显示校验、安装和连接验证阶段。
网络超时或下载中断后显示可操作的错误，清除进度并允许重新输入 Key 重试。
当前仍使用 GitHub 官方源及固定 SHA-256，没有切换到未经验证的第三方镜像。
v0.1.3 安装包不包含新增下载进度界面，请升级到 v0.1.4。
v0.1.4 同时修正了工作台样式优先级和概览布局：按中间区域实际宽度调整分栏，
恢复标题字号、按钮间距，避免左右侧栏打开时挤压内容。

Mac 支持 Apple Silicon / Intel，最低 macOS 13；Windows 的原生 CLI 适配
x64 / ARM64，当前安装包构建任务覆盖 Windows x64。运行时固定使用官方
Copilot 1.0.83，与包内 SHA-256 对照后才执行。

Key 经桌面 IPC 和子进程标准输入传递，不放入命令行参数、桌面日志或浏览器存储。
只在本机用户目录保存该用户的试用 Key；服务端 GitHub 凭据不会下载到客户端。
Mac/Linux 的试用配置文件权限为 0600；Windows 使用用户目录的继承访问权限。
试用配置和 Copilot 路径会保留，重启后复用。验证失败恢复原试用配置。
每个 Key 累计 100 万 input + output tokens，可跨设备共享余额。
服务端统一限制 10 路活跃请求、1000 万 TPM，详见 [转发服务](trial-gateway.md)。

Linux 桌面试用包支持 x86_64，构建和原生验收使用 Ubuntu 22.04。可通过系统软件
安装器安装 `.deb`，或在文件属性中允许 AppImage 执行后双击打开；无需安装 Python、
Node.js 或自行配置 CLI。运行需要图形桌面环境。

## 构建

需要在目标操作系统和架构上构建：Python 3.11+、Node.js 22.12+、Rust stable；
Windows 另需 MSVC Build Tools，Mac 需 Xcode Command Line Tools。安装开发依赖后执行：

```bash
python -m pip install -e ".[trial]" "pyinstaller>=6.11,<7" tzdata
npm --prefix frontend/web ci
npm --prefix frontend/tui ci
npm --prefix desktop-tauri ci
npm --prefix frontend/web run build && npm --prefix frontend/tui run build
npm --prefix frontend/web run build
npm --prefix desktop-tauri run build:backend
npm --prefix desktop-tauri run build:unsigned
```

若 Python 不在默认路径上，通过 `ARGUS_BUILD_PYTHON` 指定构建环境解释器。
Windows 产物在 `desktop-tauri/src-tauri/target/release/bundle/nsis/`；
Mac 的 `.app` / `.dmg` 位于相邻的 `macos/` / `dmg/`。
现有 `npm run dist` 仍是 Windows 的签名更新发布流程。

`.github/workflows/release.yml` 在 Windows、Mac Apple Silicon 和 Intel 上构建原生包。
发布检查使用 Windows Edge / Mac WebKit 加载真实工作台前端和临时项目 API，
验证多种窗口宽度、工作台模块切换及试用设置界面；浏览器测试中的桌面 IPC 为模拟实现。
发布前会安装 EXE / DMG，使用受保护的 `ARGUS_TRIAL_SMOKE_KEY` 验证真实公网试用、
两次工作台启动和持久化后的本地工具调用。三个平台的更新包均使用既有 Argus updater
密钥签名；`latest.json` 合并三个平台的下载地址和签名。
Mac Developer ID 签名和 notarization 尚未配置；首次启动若被 macOS 阻止，可在
**系统设置 → 隐私与安全性 → 仍要打开** 中确认。该确认不需要命令行。
`desktop-trial.yml` 另外提供无需发布凭据的未签名构建检查。

## 当前验证范围（2026-09-10）

Linux 上已通过桌面 TypeScript / Vite 构建、Rust 编译及核心测试、Python 客户端与桌面测试。
真实冻结后端在全新用户目录、空 PATH 下完成自动下载、Key 验证、真实模型回复；
第二个冻结进程读取持久化配置并完成本地文件工具调用。
浏览器模拟 IPC 已检查 Key 格式校验、输入清空、错误重试和成功进入工作台。

Mac / Windows 原生安装、GUI 和重启由发布工作流验证；任一平台失败都会阻止发布。
正式下载请使用 [GitHub Releases](https://github.com/lbx154/Argus/releases)，
不要使用尚未通过安装验证的构建产物。
