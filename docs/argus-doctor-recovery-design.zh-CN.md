# Argus Bootstrap Doctor & Recovery 设计规范

> 状态：已批准；Phase 1 已实现  
> 日期：2026-08-13  
> 当前实现：`argus doctor` / `argus --doctor` 只读诊断、`--json`、`--deep`、`--verify`，以及 `argus repair --plan|--safe`。`--safe` 首批仅注册并执行“删除已验证死亡 PID 的 stale daemon.pid”；其余修改仍明确标为人工操作。独立 Rescue Runtime、Desktop Recovery Screen 和高风险 Repair Provider 仍按后续阶段实施。  
> 适用项目：Argus  
> 目标平台：Windows、Linux、macOS  
> 目标入口：CLI、TUI、Web、Desktop、外层 AI Terminal / Agent  
> 实施约束：本规范批准前不编写实现代码

---

## 1. 摘要

本规范设计一个跨平台的 Argus 环境诊断、修复、更新与回滚系统。

它解决的核心问题是：当 Argus 主程序、Python 运行时、WebAPI、Desktop 后端、TUI、AI Backend、daemon 或安装状态发生异常时，用户可能无法进入完整 Argus，也就无法使用现有的 Argus 能力诊断 Argus 本身。

因此，完整方案不能只扩展当前依赖 `argus_skill` 正常导入的 `argus --doctor`，而应提供两个互补层级：

1. **Bootstrap Doctor / Rescue Runtime**：不依赖完整 Argus Core，在 Argus 无法正常启动时仍能运行。
2. **Full Doctor & Recovery**：当 Argus Core 或 API 可用时，提供完整诊断、修复计划、授权执行、验收和回滚。

核心原则：

> 确定性检测 → 结构化 Finding → 根因排序 → 修复计划 → 分级授权 → 注册动作执行 → 验收 → 回滚 → 审计。

AI 终端可以辅助解释和编排，但不能成为底层事实来源，也不能自由生成 Shell 命令并直接修改用户环境。

---

## 2. 命令命名

### 2.1 推荐命令

```bash
argus doctor
```

兼容已有入口：

```bash
argus --doctor
```

帮助命令保持标准形式：

```bash
argus -h
argus --help
```

不建议将以下形式作为正式文档入口：

```bash
argus -doctor
```

单横线通常用于单字符参数。若后续确有兼容需求，可以将 `-doctor` 做成隐藏别名，但所有公开文档统一使用 `argus doctor`。

### 2.2 诊断与修复必须分离

```bash
argus doctor                 # 只读检测，永不修改
argus repair --plan          # 生成修复计划，永不修改
argus repair --apply <ID>    # 授权后执行指定计划
argus doctor --verify        # 修复后验收
```

`doctor` 不得为了方便而顺便修复。任何产生副作用的行为必须进入 `repair` 阶段。

---

## 3. 背景与问题定义

当前 Argus 已经具备以下基础能力：

- `argus --doctor`：检查 backend、认证、daemon、锁与 Session。
- `argus --setup`：配置 backend 和认证模式。
- `argus update`：对干净源码仓库执行 fast-forward 更新。
- `/api/projects/{sid}/doctor`：项目级 WebAPI Doctor。
- `runtime_identity.py` / `release.py`：版本、源码和 Release 身份检查。
- `daemon_upgrade.py`：在任务边界安全切换 daemon。
- `daemon/self_maintenance.py`：框架代码故障的隔离修复与 canary。
- Windows Desktop 已具备 Electron host、bundled backend、ownership 检查和有限恢复能力。

但现有 Doctor 默认要求：

- `argus` 命令可以执行；
- Python 解释器存在；
- `argus_skill` 能够导入；
- CLI 入口没有损坏。

以下故障会让现有 Doctor 自身失效：

- `argus` 不在 PATH；
- 虚拟环境被删除、移动或损坏；
- Python 不存在或版本不兼容；
- `argus_skill` 无法 import；
- Desktop bundled backend 丢失；
- WebAPI 启动失败；
- Electron 可启动但 Python backend 起不来；
- Web 资产与 API protocol 不一致；
- 安装只完成了一半；
- macOS Gatekeeper 阻止应用；
- Windows 文件锁阻止替换；
- Linux service 配置错误；
- AI backend 本身未登录或不可用。

因此，本功能的首要要求是：

> 诊断入口不能完全依赖被诊断的完整系统。

---

## 4. 目标

### 4.1 产品目标

1. 用户无法进入完整 Argus 时，仍有可用诊断入口。
2. Windows、Linux、macOS 使用统一 Finding 和 Repair Plan 协议。
3. CLI、Web、Desktop、TUI 和外层 AI Agent 展示同一事实。
4. 不同故障对象由不同 Repair Provider 处理。
5. Doctor 始终只读。
6. 修复操作可审查、可授权、可验证、尽量可回滚。
7. 没有 AI 时仍能完成确定性诊断。
8. 有 AI 时可以获得更清晰的解释和选项比较。
9. 更新不得破坏活动任务或用户本地修改。
10. 所有日志和 Support Bundle 必须脱敏。

### 4.2 技术目标

1. 建立统一 Maintenance Core。
2. 建立 Platform Adapter：Windows、Linux、macOS。
3. 建立 Installation Adapter：源码、托管运行时、Desktop、外部包管理器、容器。
4. 建立独立 Rescue Runtime。
5. 建立系统级 Maintenance API。
6. 建立稳定 JSON Schema 和 Finding ID。
7. 建立 Repair Action Registry。
8. 建立 append-only Operation Journal。

---

## 5. 非目标

以下内容不属于首版范围：

1. 不允许 AI 自由生成命令后直接执行。
2. 不自动输入、迁移或重置用户凭据。
3. 不自动执行 sudo、UAC 或 root 操作。
4. 不自动 `git stash`、`merge`、`rebase`、`reset --hard`。
5. 不自动强制终止活动实验或未知进程。
6. 不自动修复任意第三方软件。
7. 不承诺在磁盘完全损坏或 Rescue Runtime 也被删除时本机自我恢复。
8. 不在首版支持所有 Linux 发行版和所有 CPU 架构。
9. 不让浏览器直接检查浏览器所在设备；Web Doctor 检查的是 Argus 服务所在主机。
10. 不把现有框架代码 self-maintenance 与主机环境 repair 混成一个无边界系统。

---

## 6. 核心设计原则

### 6.1 Local-first

CLI 和 Rescue Runtime 必须能在 WebAPI 不工作时运行。

### 6.2 Deterministic-first

底层检测和风险判断由确定性代码完成；AI 只能解释、比较和选择已注册动作。

### 6.3 Read-only Doctor

`argus doctor` 不得修改文件、配置、进程、服务或网络状态。

### 6.4 Typed Repair

所有修复必须是注册的、类型化的 RepairAction，禁止执行 Finding 中的任意文本命令。

### 6.5 Fail closed

无法验证进程所有权、安装归属、计划新鲜度或权限时，必须拒绝修改。

### 6.6 One truth, many surfaces

CLI、Web、Desktop 和 TUI 只负责展示和交互，Maintenance Core 是唯一诊断与修复规则来源。

### 6.7 Platform × Installation

修复策略不仅取决于操作系统，还取决于安装方式。

### 6.8 Verify, not assume

命令退出码为 0 不等于修复成功。每个动作必须定义验收检查。

---

## 7. 分层恢复模型

### 7.1 Level 0：命令不存在

表现：

```text
'argus' is not recognized
command not found: argus
```

普通 `argus doctor` 无法运行。

需要独立的 Rescue Runtime：

```text
Windows: argus-doctor.exe
Linux:   argus-doctor
macOS:   argus-doctor
```

Rescue Runtime 必须：

- 独立于当前 Argus venv；
- 不依赖 Node.js；
- 不依赖 WebAPI；
- 不依赖 AI backend；
- 能读取安装清单；
- 能搜索已知安装目录；
- 能检查 PATH、Python、Node、Git 和安装完整性；
- 能输出稳定 JSON；
- 能恢复 launcher 或给出官方重装计划；
- 发行时具有签名或可验证哈希。

如果 Rescue Runtime 也不存在，外层 AI Terminal 必须使用官方、可校验的恢复包，不能静默执行来源不明的 `curl | sh` 或 PowerShell 脚本。

### 7.2 Level 1：Launcher 可运行，Core 损坏

典型问题：

- `ModuleNotFoundError: argus_skill`；
- venv Python 不存在；
- editable install 指向已移动目录；
- release manifest 缺失；
- frontend bundle 缺失；
- Desktop bundled backend 缺失。

由 Bootstrap Doctor 检查安装和启动链路，不导入完整 Argus Core。

### 7.3 Level 2：Core 可运行，WebAPI 失败

典型问题：

- FastAPI/uvicorn 无法加载；
- 端口被占用；
- API crash loop；
- ownership record 不一致；
- CLI/API Release 不一致。

CLI 直接调用本地 Maintenance Core，不通过 HTTP。

### 7.4 Level 3：API 可运行，UI 失败

典型问题：

- Web 白屏；
- bundle 与 API protocol 不兼容；
- token 过期；
- Desktop preload/IPC 失败；
- 浏览器缓存旧资产。

通过系统级 Maintenance API 诊断。

### 7.5 Level 4：UI 正常，运行时异常

典型问题：

- backend 未登录；
- provider/model 不匹配；
- daemon 未启动；
- stale lock；
- workspace lease 冲突；
- Session 路径失效；
- 项目状态损坏。

由 Full Doctor 处理。

---

## 8. 总体架构

```text
┌──────────────────────────────────────────────┐
│              外层 AI Terminal                │
│ Codex / Claude / Pi / Copilot / OpenCode     │
└──────────────────────┬───────────────────────┘
                       │ 执行命令 / 读取 JSON
                       ▼
┌──────────────────────────────────────────────┐
│             Argus Rescue Runtime             │
│ Bootstrap Doctor / Launcher Repair           │
└──────────────────────┬───────────────────────┘
                       │ Core 可用时扩展能力
                       ▼
┌──────────────────────────────────────────────┐
│              Maintenance Core                │
│                                              │
│ Collect → Diagnose → Rank → Plan             │
│ → Authorize → Execute → Verify → Rollback    │
│ → Journal                                    │
└───────────────┬────────────────┬─────────────┘
                │                │
                ▼                ▼
┌──────────────────────┐  ┌────────────────────┐
│ Platform Adapters    │  │ Installation       │
│ Windows/Linux/macOS  │  │ Adapters           │
└──────────────────────┘  └────────────────────┘
                │                │
                └────────┬───────┘
                         ▼
┌──────────────────────────────────────────────┐
│ CLI / TUI / Web / Desktop / MCP / Agent      │
└──────────────────────────────────────────────┘
```

---

## 9. 适配维度

### 9.1 平台维度

```text
windows
linux
macos
```

### 9.2 安装维度

```text
source_checkout
managed_runtime
desktop_bundle
external_package_manager
container
unknown
```

同一问题在不同安装模式下修复方式不同。

例如：

- Homebrew 安装不得被 `pip install -e .` 覆盖；
- Desktop bundle 不得使用源码仓库更新流程；
- dirty source checkout 不得自动 fast-forward；
- container 中不得尝试修改只读基础镜像。

---

## 10. Platform Adapter 设计

概念接口：

```text
PlatformAdapter
├─ platform_info()
├─ architecture()
├─ find_executables(name)
├─ inspect_process(pid)
├─ process_owner(pid)
├─ process_tree(pid)
├─ port_owner(host, port)
├─ inspect_path_environment()
├─ check_file_permissions(path)
├─ inspect_file_lock(path)
├─ atomic_replace(source, target)
├─ service_status(name)
├─ graceful_stop_owned_process(identity)
├─ open_url(url)
├─ elevation_requirement(action)
└─ support_capabilities()
```

不支持的能力返回明确状态：

```text
NOT_APPLICABLE
UNSUPPORTED
MANUAL_REQUIRED
CHECK_UNAVAILABLE
```

`NOT_APPLICABLE` 不得被计为失败。

---

## 11. Installation Adapter 设计

概念接口：

```text
InstallationAdapter
├─ detect()
├─ installation_identity()
├─ verify_integrity()
├─ check_update()
├─ stage_update()
├─ verify_candidate()
├─ switch_to_candidate()
├─ rollback()
├─ repair_launcher()
└─ owned_paths()
```

任何修改前必须确认当前安装由对应 Adapter 管理。

---

## 12. Doctor 检测对象

### 12.1 Host

检查：

- OS 和版本；
- CPU 架构；
- 用户权限；
- 可用磁盘空间；
- 临时目录；
- 时间和时区；
- 网络、DNS 和代理摘要；
- 文件系统能力；
- WSL、容器、SSH、远程桌面等运行环境。

### 12.2 Argus Installation

检查：

- 安装类型；
- 安装路径；
- 安装 ID；
- 当前版本；
- Release ID；
- Source Digest；
- 是否存在多个安装；
- 当前命令实际解析到哪个安装；
- Desktop、CLI、WebAPI 是否属于同一个 Release；
- 安装是否完整；
- 是否处于半升级状态。

### 12.3 Python Runtime

检查：

- Python 是否存在；
- 版本是否满足要求；
- 架构是否匹配；
- venv 是否存在；
- `sys.executable`；
- `argus_skill` 是否可导入；
- 必要依赖是否存在；
- editable install 是否指向移动或删除的源码目录；
- launcher 与 Python 是否来自不同环境。

### 12.4 Node/TUI/Web Runtime

检查：

- Node.js 是否存在；
- Node 版本；
- TUI bundle 是否存在；
- Web dist 是否存在；
- bundle Release ID；
- 前端与 API protocol 是否兼容；
- Node 架构是否与系统匹配。

### 12.5 CLI/TUI

检查：

- PATH 中有多少个 `argus`；
- 当前命令解析路径；
- shim 是否失效；
- Console 编码；
- TTY 能力；
- TUI 是否能启动；
- CLI 和后台 API ownership 是否一致。

### 12.6 WebAPI

检查：

- 监听端口；
- 端口占用者；
- API 进程身份；
- `/api/meta`；
- API schema/version；
- token 配置；
- ownership record；
- crash loop；
- Web 静态资产；
- loopback/LAN 安全设置。

### 12.7 Desktop

检查：

- Electron 应用版本；
- bundled backend 是否存在；
- backend 哈希；
- Desktop/backend Release ID；
- preload 和 IPC；
- bundled Web；
- owned backend PID；
- crash recovery 状态；
- 更新是否中断；
- 上一个可回滚版本。

### 12.8 AI Backend

检查：

- Codex、Claude、Copilot、Pi、OpenCode、Grok 是否存在；
- 版本；
- 登录状态；
- 认证目录；
- provider；
- model catalog；
- role model 是否存在；
- API route 是否配置；
- 可选网络连通性；
- 限流、配额或账户问题；
- 当前配置与实际可用 backend 是否冲突。

### 12.9 Daemon/Session/Project

检查：

- daemon 是否存活；
- PID 是否真实；
- PID 是否属于 Argus；
- daemon protocol；
- daemon 与 CLI Release 是否一致；
- stale lock；
- workspace lease；
- Session metadata；
- 项目路径；
- 状态 schema；
- backlog 是否需要执行器；
- active mission 是否允许重启。

### 12.10 Update

检查：

- upstream；
- ahead/behind/diverged；
- Git dirty；
- detached HEAD；
- 最低支持版本；
- 可用稳定版本；
- 安全更新；
- 状态迁移兼容性；
- 活动任务是否允许切换。

---

## 13. Finding 与根因排序

Doctor 不应只输出一组平级错误，而要区分根因和症状。

示例：

```text
症状：daemon 没有运行
根因：配置的 backend executable 不存在
```

推荐修复必须优先指向根因，而不是反复建议启动 daemon。

### 13.1 Finding ID

建议稳定编号：

```text
ARGUS-HOST-001
ARGUS-INSTALL-001
ARGUS-PYTHON-001
ARGUS-NODE-001
ARGUS-PATH-001
ARGUS-CLI-001
ARGUS-WEB-001
ARGUS-DESKTOP-001
ARGUS-BACKEND-001
ARGUS-DAEMON-001
ARGUS-CONFIG-001
ARGUS-STATE-001
ARGUS-UPDATE-001
ARGUS-PERMISSION-001
```

同一问题在 CLI、API、Web 和 Desktop 中必须使用相同 Finding ID。

---

## 14. 不同对象的修复路由

修复路由键：

```text
Finding Code
+ Target Kind
+ Platform
+ Installation Kind
+ Capability Matrix
= Repair Provider + Repair Action
```

示例：

| 问题 | Windows Desktop | Linux CLI | macOS Desktop |
|---|---|---|---|
| backend 文件缺失 | 恢复 bundled backend | 重建 venv/package | 恢复 `.app` bundle |
| daemon 不响应 | ownership 验证后重启 | systemd/user process | launchd/user process |
| PATH 缺失 | user PATH/shim | shell/XDG shim | Homebrew/shim |
| 版本过旧 | Desktop updater | package/source update | signed/notarized updater |
| 认证过期 | 官方登录 | 官方登录 | 官方登录 |
| 端口占用 | Windows PID identity | `/proc`/socket | `lsof`/process identity |
| 文件锁定 | 退出 owned process 后替换 | atomic rename | 退出 App Helper 后替换 |

禁止存在：

```text
run_shell(finding.fix)
```

允许的只能是注册动作，例如：

```text
restart_owned_backend
remove_verified_stale_lock
reinstall_editable_package
repair_argus_launcher
persist_backend_selection
stage_source_update
restore_desktop_bundle
launch_official_login
```

---

## 15. 完整工作流程

### 15.1 Discovery

收集：

```text
HostSnapshot
InstallationSnapshot
RuntimeSnapshot
BackendSnapshot
ProcessSnapshot
ConfigSnapshot
UpdateSnapshot
```

Quick 模式默认不访问网络。

### 15.2 Diagnosis

确定性规则引擎生成 Finding。

### 15.3 Root Cause Ranking

构建 Finding 依赖关系，将根因排在症状之前。

### 15.4 Repair Planning

生成 Repair Plan，包含：

- Plan ID；
- schema version；
- snapshot hash；
- 有效期；
- 动作顺序；
- 依赖关系；
- 风险等级；
- 影响对象；
- 网络要求；
- 权限要求；
- 重启要求；
- 回滚方案；
- 验收方法。

### 15.5 Authorization

按风险等级授权。

### 15.6 Execution

执行前重新验证：

- Finding 仍存在；
- Snapshot 未变化；
- 目标进程身份未变化；
- Plan 未过期；
- 活动任务允许操作；
- 当前用户具备所需权限。

### 15.7 Verification

每个动作执行后运行专门验收，再运行相关 Doctor checks。

### 15.8 Rollback

验收失败时：

- 恢复配置备份；
- 切回旧 runtime；
- 恢复旧 launcher；
- 恢复旧 Desktop bundle；
- 恢复旧 daemon；
- 保留失败日志。

### 15.9 Journal

所有动作写入 append-only Operation Journal。

---

## 16. 风险等级

### 16.1 SAFE

可在 `argus repair --safe` 中执行：

- 删除已验证死亡的 stale lock；
- 创建缺失的 Argus 状态目录；
- 重建非权威缓存；
- 清理 Argus 自己拥有的临时文件；
- 恢复可重新生成的索引。

### 16.2 CONSENT

必须明确确认：

- 修改持久化配置；
- 重装 Argus；
- fast-forward 更新；
- 重启 daemon；
- 重启 Desktop backend；
- 修改用户 PATH；
- 切换 backend；
- 状态 schema 迁移；
- 切换 runtime 版本。

### 16.3 MANUAL

只能提供指导：

- 输入凭据；
- 官方登录；
- sudo/UAC；
- Git merge/rebase；
- dirty/diverged branch；
- 删除未知进程；
- 强制中止活动实验；
- 修改系统范围配置。

AI 不得改变动作的风险等级。

---

## 17. AI Terminal / Recovery Advisor

### 17.1 AI 是可选能力

无 AI 时：

```bash
argus doctor --offline
```

必须仍能输出完整确定性报告。

### 17.2 Advisor 与 Execution Backend 分离

```text
Execution Backend
```

用于 Manager、Planner、Engineer、Reviewer。

```text
Recovery Advisor
```

用于解释 Doctor 报告。

即使 Argus 配置的 Codex backend 损坏，也可以临时使用 Claude、Pi 等已登录 CLI 解释报告。

建议参数：

```bash
argus doctor --advisor auto
argus doctor --advisor none
argus doctor --advisor codex
argus doctor --advisor claude
argus doctor --advisor copilot
argus doctor --advisor pi
argus doctor --advisor opencode
argus doctor --advisor grok
```

### 17.3 AI 可以做什么

- 解释 Finding；
- 比较多个已注册修复方案；
- 识别 Finding 之间的可能联系；
- 生成适合用户阅读的步骤；
- 从 RepairAction Registry 中选择候选动作；
- 帮助外层 Agent 编排标准流程。

### 17.4 AI 禁止做什么

- 生成任意 Shell 后直接执行；
- 绕过确认；
- 降低风险等级；
- 读取或打印密钥；
- 自动 sudo/UAC；
- 自动强杀进程；
- 自动 Git merge/reset；
- 在 Doctor 阶段修改系统。

### 17.5 AI 输入脱敏

AI 只接收：

- 平台和版本；
- 组件版本；
- Finding；
- 脱敏路径；
- 错误类别；
- RepairAction 列表；
- 非秘密配置摘要。

不得接收：

- API Key；
- Token；
- Authorization Header；
- 完整环境变量；
- 完整 private log；
- backend auth 文件。

---

## 18. 外层 AI Agent 标准流程

当用户在 Codex、Claude、Pi 等终端里说“帮我检测并修复 Argus”时，Agent 应执行：

```text
1. 定位 argus 或 argus-doctor
2. 运行 doctor --json
3. 必要时运行 doctor --deep --json
4. 向用户解释根因和风险
5. 运行 repair --plan --json
6. 展示会修改的内容
7. 等待授权
8. apply 指定 Plan ID
9. doctor --verify
10. 报告结果和剩余人工步骤
```

如果 `argus` 命令不存在：

```text
1. 查询安装清单和已知目录
2. 查找独立 argus-doctor
3. 如仍不存在，使用官方签名恢复包
4. 先恢复 launcher
5. 再运行完整 Doctor
```

---

## 19. CLI 设计

### 19.1 Quick Doctor

```bash
argus doctor
```

要求：

- 只读；
- 无网络；
- 快速；
- 检查核心启动链路；
- 输出最高优先级根因。

### 19.2 Deep Doctor

```bash
argus doctor --deep
```

增加：

- 网络；
- backend auth；
- provider catalog；
- update check；
- API route reachability；
- Desktop/Web Release 一致性；
- 完整状态检查。

### 19.3 指定目标

```bash
argus doctor --target host
argus doctor --target install
argus doctor --target cli
argus doctor --target web
argus doctor --target desktop
argus doctor --target backend
argus doctor --target daemon
argus doctor --target project
argus doctor --target update
```

### 19.4 JSON

```bash
argus doctor --json
```

供 AI Terminal、CI、Desktop、Web、自动化脚本和 Support Tool 使用。

### 19.5 Repair

```bash
argus repair --plan
argus repair --plan --finding ARGUS-PATH-002
argus repair --safe
argus repair --apply rp-20260813-001
```

### 19.6 Verification

```bash
argus doctor --verify
argus doctor --verify --operation op-xxx
```

### 19.7 建议退出码

保持与现有语义兼容：

```text
0  无 blocking Finding
2  参数或用法错误
3  存在 blocking Finding / not ready
4  Doctor 自身降级，部分检查不可用
5  内部一致性错误
```

Warning 可以在退出码 0 下报告，是否升级为非零由 CI 模式决定。

---

## 20. Web 设计

增加系统级 Health Center：

```text
Overview
Host
Installation
CLI
Web/API
Desktop
AI Backends
Daemons
Projects
Updates
Repair History
```

每个 Finding 展示：

- Finding ID；
- 严重度；
- 目标对象；
- 脱敏证据；
- 影响；
- 根因；
- 推荐修复；
- 风险等级；
- 是否需要重启；
- 是否可回滚。

Web 必须明确显示目标主机：

```text
Target host:
Ubuntu 24.04 x86_64
argus-server-03
```

用户可能在 Windows/macOS 浏览器中修复 Linux 服务器。浏览器系统不是诊断目标。

### 20.1 API 正常时

通过系统级 API 工作。

### 20.2 API 异常时

普通浏览器无法直接检查服务端进程，这是物理限制。

恢复方式：

1. 本机 CLI/Rescue Runtime；
2. 外层 AI Terminal；
3. Desktop Bootstrap Recovery；
4. 可选独立 Recovery Gateway。

### 20.3 Recovery Gateway（后续）

若要求“Full WebAPI 崩溃时仍有 Web 恢复页”，需要稳定 Launcher：

```text
Stable Launcher
├─ Recovery UI/API
└─ Full Argus Backend
```

Recovery Gateway 只监听 loopback。远程访问通过 SSH Tunnel 或严格配对。

---

## 21. Desktop 设计

Desktop 需要两个恢复面。

### 21.1 正常 Health Center

由现有 Web Cockpit 提供，与浏览器 Web 共用。

### 21.2 Bootstrap Recovery Screen

由 Electron main/preload 自己提供，不依赖 Python backend。

允许：

- 验证 bundled backend；
- 检查 backend 是否缺失；
- 检查 Release ID；
- 检查端口；
- 检查进程 ownership；
- 重启自己拥有的 backend；
- 调用独立 Rescue Runtime；
- 恢复上一个 Desktop 版本；
- 启动签名更新器；
- 导出脱敏日志。

禁止：

- 修改项目状态；
- 修改 AI 凭据；
- 任意执行 Shell；
- 终止非 Desktop 所有的进程；
- 自动修改 Git。

---

## 22. 平台细节

### 22.1 Windows

重点检查：

- `where.exe argus` 多安装；
- PATH 指向失效 venv；
- PowerShell/CMD/Git Bash PATH 差异；
- Python Launcher；
- npm global bin；
- CP936/UTF-8；
- UAC；
- Windows 进程树和 PID 复用；
- 文件锁；
- 端口占用；
- `%APPDATA%` / `%LOCALAPPDATA%`；
- NTFS ACL；
- 长路径；
- Defender 隔离；
- NSIS 半更新；
- x64/arm64 不一致。

Windows 更新应采用并排版本或专用 updater，不假设能覆盖正在运行的 `.exe`。

### 22.2 Linux

重点检查：

- systemd user service；
- 无 systemd 的进程模式；
- XDG 路径；
- owner/group/mode；
- sudo；
- bash/zsh/fish；
- 交互 Shell、systemd、cron PATH 不一致；
- glibc/musl；
- headless/SSH；
- `/tmp`；
- `ulimit`；
- 容器；
- AppImage/tar/deb/rpm；
- x64/arm64。

首版不自动调用 apt/dnf/pacman。

### 22.3 macOS

重点检查：

- Intel x64 / Apple Silicon arm64；
- Rosetta；
- `/usr/local/bin` / `/opt/homebrew/bin`；
- Gatekeeper；
- quarantine；
- codesign；
- notarization；
- `.app` bundle；
- launchd；
- Keychain；
- App Translocation；
- Electron Helper；
- GUI 与 Terminal PATH 不一致。

macOS Desktop 更新包必须签名并 notarize。

---

## 23. 配置与状态权威

所有入口必须共享同一个核心状态根目录：

```text
ARGUS_SKILL_HOME
默认 ~/.argus-skill
```

禁止出现：

```text
CLI 一份 backend 配置
Web 一份 backend 配置
Desktop 一份 backend 配置
```

Desktop 可以单独保存窗口、主题、端口和 ownership record，但 backend、provider、model、daemon、projects、update policy 和 repair history 必须由 Core 统一管理。

---

## 24. Release Unit

Desktop 发布时以下组件是不可拆分的 Release Unit：

```text
Electron 主程序
Python frozen backend
Web frontend dist
TUI bundle
API protocol version
release manifest
state schema compatibility
```

启动时校验：

```text
desktop_release_id
backend_release_id
web_release_id
api_protocol_version
source_digest
```

不一致时进入恢复模式，不得静默继续。

---

## 25. 更新策略

### 25.1 Source Checkout

```text
检测 Git 状态
→ fetch
→ 检查 clean/ahead/behind/diverged
→ 隔离 worktree 准备候选版本
→ 安装候选环境
→ doctor + smoke tests
→ 等待任务边界
→ drain 旧 daemon
→ 切换
→ 验收
→ 失败回滚
```

禁止自动 stash、merge、rebase、reset。

### 25.2 Managed Runtime

```text
~/.argus-skill/runtimes/
├─ 0.1.1/
├─ 0.1.2/
└─ current → 0.1.2
```

Windows 可使用 launcher 配置文件替代符号链接。

### 25.3 Desktop Bundle

- Windows：NSIS/签名更新包；
- macOS：签名、notarized DMG/ZIP；
- Linux：首版 AppImage 或 tar.gz，后续 deb/rpm；
- 必须原生平台构建；
- 失败恢复旧版本。

### 25.4 External Package Manager

若检测到 brew、pipx、apt 等外部管理方式，Argus 默认只输出正确更新建议，不绕过包管理器覆盖安装。

### 25.5 更新政策

```text
off
notify
safe
```

默认 `notify`。自动更新不应根据“安装时间久”单独触发，而要综合版本、兼容性、Release、Git 状态和活动任务。

---

## 26. 数据模型

### 26.1 Finding

| 字段 | 类型 | 约束 |
|---|---|---|
| id | string | 单次 Finding 实例 ID |
| code | string | 稳定 Finding 编号 |
| scope | string | host/install/runtime/project 等 |
| target | TargetRef | 被诊断对象 |
| severity | enum | info/warning/error/blocker |
| status | enum | active/resolved/suppressed/unavailable |
| summary | string | 人类可读摘要 |
| detail | string | 脱敏详情 |
| evidence | Evidence[] | 结构化脱敏证据 |
| root_cause | boolean | 是否为根因 |
| caused_by | string[] | 上游 Finding ID |
| confidence | number | 0..1 |
| repair_action_ids | string[] | 已注册动作 |
| detected_at | timestamp | 检测时间 |
| fingerprint | string | 去重指纹 |

### 26.2 RepairAction

| 字段 | 类型 | 约束 |
|---|---|---|
| id | string | 稳定动作 ID |
| finding_code | string | 对应 Finding |
| target_kind | string | 目标类型 |
| platforms | string[] | 支持平台 |
| installation_kinds | string[] | 支持安装类型 |
| risk | enum | safe/consent/manual |
| description | string | 修改说明 |
| changes | Change[] | 预期变化 |
| requires_network | boolean | 网络要求 |
| requires_elevation | boolean | 权限要求 |
| requires_restart | boolean | 重启要求 |
| preconditions | Check[] | 前置条件 |
| verification | Check[] | 验收条件 |
| rollback | RollbackSpec | 回滚定义 |
| idempotent | boolean | 是否幂等 |

### 26.3 RepairPlan

| 字段 | 类型 | 约束 |
|---|---|---|
| plan_id | string | 唯一 ID |
| schema_version | integer | 协议版本 |
| created_at | timestamp | 创建时间 |
| expires_at | timestamp | 过期时间 |
| snapshot_hash | string | 环境快照哈希 |
| finding_ids | string[] | 处理对象 |
| actions | PlannedAction[] | 有序动作 |
| dependencies | Dependency[] | 动作依赖 |
| risk_summary | object | 风险摘要 |
| requires_confirmation | boolean | 是否需要确认 |

### 26.4 RepairResult

| 字段 | 类型 | 约束 |
|---|---|---|
| operation_id | string | 操作 ID |
| plan_id | string | 来源计划 |
| status | enum | running/succeeded/partial/failed/rolled_back |
| action_results | object[] | 每个动作结果 |
| verification | object | 总体验收 |
| rollback | object | 回滚结果 |
| remaining_findings | string[] | 剩余问题 |
| started_at | timestamp | 开始时间 |
| completed_at | timestamp | 完成时间 |

---

## 27. API 契约

### 27.1 诊断

```http
GET /api/system/health
GET /api/system/health?mode=deep
GET /api/system/capabilities
```

### 27.2 Repair

```http
POST /api/system/repair/plan
POST /api/system/repair/apply
POST /api/system/repair/cancel
GET  /api/system/operations/{id}
GET  /api/system/operations/{id}/events
```

### 27.3 Update

```http
POST /api/system/update/check
POST /api/system/update/plan
POST /api/system/update/apply
```

### 27.4 Support

```http
POST /api/system/support-bundle
```

### 27.5 安全要求

修改类 API 必须：

- Bearer Token 鉴权；
- 默认只允许 loopback；
- 绑定 Plan ID；
- 绑定 snapshot hash；
- 绑定一次性确认；
- 防止重复执行；
- 记录 actor；
- 拒绝任意 Shell 文本。

未鉴权请求最多返回脱敏健康摘要，不得返回日志尾部、真实路径或进程详情。

---

## 28. Operation Journal

建议路径概念：

```text
<ARGUS_SKILL_HOME>/maintenance/operations/<operation-id>.jsonl
```

记录：

```text
operation_id
plan_id
finding_id
actor
approval
before_snapshot_hash
actions
result
verification
rollback
timestamps
```

Journal append-only，默认不记录 Secret。

---

## 29. 功能要求

### FR-1 Bootstrap 可用性

Doctor **必须**在完整 Argus Core 不可用时提供 Bootstrap 诊断。

### FR-2 Doctor 只读

`argus doctor` **必须只读**，不得修改文件、配置、进程或服务。

### FR-3 平台识别

Doctor **必须**识别 Windows、Linux、macOS 和 CPU 架构。

### FR-4 安装识别

Doctor **必须**识别安装类型，禁止使用错误更新方式覆盖外部包管理器。

### FR-5 结构化 Finding

所有诊断 **必须**返回结构化 Finding。

### FR-6 注册动作

所有修复 **必须**来自 RepairAction Registry。

### FR-7 多入口一致性

CLI、Web、Desktop 和 TUI **必须**使用相同 Finding ID 和 Schema。

### FR-8 AI 可选

无 AI 时 Doctor **必须**仍可完成确定性诊断。

### FR-9 计划新鲜度

执行修复前 **必须**验证 Plan 未过期且环境未改变。

### FR-10 验收

每个 RepairAction **必须**定义可机器验证的验收方式。

### FR-11 授权

CONSENT Action **必须**获得明确授权。

### FR-12 人工边界

涉及凭据、sudo、UAC、Git merge 的动作 **不得自动执行**。

### FR-13 Desktop 恢复

Desktop backend 无法启动时，Electron **必须**提供独立恢复页。

### FR-14 远程 Web

远程 Web **不得默认拥有高风险主机修复权限**。

### FR-15 更新回滚

更新 **必须**支持 staged validation 和失败回滚。

### FR-16 所有权验证

系统 **不得**停止、替换或删除无法证明属于当前 Argus 安装的进程和文件。

### FR-17 兼容入口

现有 `argus --doctor` **必须**保留兼容。

---

## 30. 非功能要求

### NFR-1 性能

- Quick Doctor 本地检查目标：5 秒内完成；
- 每个外部命令有超时；
- Deep Doctor 默认总时间不超过 60 秒；
- 网络失败不得无限等待。

### NFR-2 安全

- 禁止 `shell=True`；
- 禁止拼接未验证命令；
- 不打印 Secret；
- 不上传原始日志；
- 不终止未知进程；
- 不修改未知安装。

### NFR-3 可靠性

- 单项检查异常变为 `CHECK_UNAVAILABLE`；
- Doctor 不因单项失败整体崩溃；
- 修复动作尽量幂等；
- 配置写入原子化；
- 防止 TOCTOU。

### NFR-4 兼容性

- 兼容现有 `argus --doctor`；
- JSON Schema 带版本；
- 新 CLI 能识别旧 API；
- 新 API 不误接管旧进程。

### NFR-5 可审计性

- 每个动作有记录；
- 日志脱敏；
- 支持 Support Bundle；
- 分享前提示用户复核。

### NFR-6 可测试性

- Platform Adapter 可注入 fake；
- 外部命令可注入 runner；
- 每个 Finding 规则可独立测试；
- 每个 RepairAction 有故障注入测试。

---

## 31. Edge Cases

### EC-1

Rescue Runtime 本身缺失：只能通过官方签名恢复包恢复。

### EC-2

存在多个 Argus 安装：报告全部候选，不自动删除或接管。

### EC-3

端口占用者身份不可确认：不得终止，只提供更换端口或人工处理。

### EC-4

PID 已复用：必须使用 PID + 启动时间 + executable path 等联合身份。

### EC-5

网络离线：本地检查继续，网络 Finding 标记 unavailable，不误判配置错误。

### EC-6

backend auth probe 本身会消费模型额度：Quick Doctor 不执行该探测。

### EC-7

Plan 生成后环境变化：拒绝执行并重新规划。

### EC-8

修复进行中断电或进程崩溃：下次启动从 Journal 判断状态，进入 reconciliation。

### EC-9

更新候选通过单项检查但完整 smoke test 失败：回滚。

### EC-10

Web 浏览器与目标主机平台不同：UI 显示目标主机而非浏览器平台。

### EC-11

容器文件系统只读：修复动作降级为外部镜像/部署建议。

### EC-12

macOS App 被 quarantine：不得通过未解释的方式静默移除安全属性。

### EC-13

Windows 文件被 Defender 隔离：不得关闭 Defender，只报告和提供人工恢复路径。

### EC-14

dirty/diverged Git：禁止自动更新。

### EC-15

活动 mission：禁止强制切换运行时，除非用户明确请求中止且已有安全机制。

---

## 32. 验收标准

### AC-1：Core 无法导入

Given：`argus_skill` 无法 import  
When：运行独立 Rescue Doctor  
Then：报告 Python/package Finding，而不是崩溃。  
关联：FR-1、FR-5。

### AC-2：Doctor 只读

Given：任意可诊断故障  
When：运行 `argus doctor`  
Then：文件、配置、进程和服务状态保持不变。  
关联：FR-2。

### AC-3：WebAPI 端口占用

Given：端口被未知进程占用  
When：运行 Doctor  
Then：报告占用事实，不自动终止进程。  
关联：FR-11、FR-16。

### AC-4：Desktop backend 丢失

Given：Electron 可启动但 bundled backend 缺失  
When：打开 Desktop  
Then：进入 Bootstrap Recovery Screen。  
关联：FR-13。

### AC-5：配置 backend 损坏

Given：Codex 未登录但 Claude 可用  
When：使用 `--advisor auto`  
Then：可由 Claude 解释报告，但不得自动切换 Execution Backend。  
关联：FR-8、FR-11。

### AC-6：无 AI

Given：没有任何 AI CLI  
When：运行 Doctor  
Then：确定性报告仍完整输出。  
关联：FR-8。

### AC-7：Plan 过期

Given：Plan 生成后 PATH 或进程变化  
When：执行 Plan  
Then：拒绝执行并要求重新诊断。  
关联：FR-9。

### AC-8：活动任务

Given：daemon 正在执行 mission  
When：计划要求更新 daemon  
Then：等待任务边界或请求用户选择，不直接强杀。  
关联：FR-11、FR-15。

### AC-9：Dirty Git

Given：源码仓库存在本地修改  
When：检查更新  
Then：阻止自动更新，不 stash/reset/merge。  
关联：FR-12、FR-15。

### AC-10：远程 Web

Given：macOS 浏览器连接 Linux Argus  
When：打开 Health Center  
Then：明确显示修复目标是 Linux 主机。  
关联：FR-7、FR-14。

### AC-11：Secret Redaction

Given：日志和环境包含 Token  
When：生成 JSON 或 Support Bundle  
Then：不包含原始 Secret。  
关联：NFR-2、NFR-5。

### AC-12：修复后验收失败

Given：RepairAction 命令成功但目标状态未恢复  
When：运行 verification  
Then：操作判定失败并按定义回滚。  
关联：FR-10、FR-15。

### AC-13：多入口一致

Given：同一模拟环境故障  
When：分别通过 CLI JSON、API、Web、Desktop 查看  
Then：Finding ID、严重度、根因和 RepairAction 一致。  
关联：FR-7。

---

## 33. 测试矩阵

### 33.1 Tier 1

- Windows 10/11 x64；
- Ubuntu 22.04/24.04 x64；
- macOS Apple Silicon arm64。

### 33.2 Tier 2

- Windows arm64；
- macOS Intel x64；
- Debian；
- Linux arm64。

### 33.3 Tier 3

- Fedora；
- Arch；
- Alpine/musl；
- 其他桌面发行版。

### 33.4 测试类别

1. Schema Contract Tests；
2. Finding Rule Unit Tests；
3. Adapter Contract Tests；
4. RepairAction Preconditions；
5. RepairAction Idempotency；
6. Rollback Tests；
7. Secret Redaction Tests；
8. CLI/API/UI Consistency Tests；
9. Native Platform E2E；
10. Packaging Smoke Tests；
11. Update Failure Injection；
12. Crash/Reconciliation Tests。

---

## 34. 与 Windows 适配工作的边界

另一个终端当前负责 Windows 适配时，应继续聚焦：

```text
Windows Process / Path / Port / File Lock
Daemon lifecycle
Desktop backend ownership
Windows packaging
Windows native tests
```

Doctor 系统负责：

```text
调用结构化 Windows primitives
→ 生成 Finding
→ 选择 Windows RepairAction
→ 规划、授权、执行、验收和回滚
```

Windows 适配最好提供：

- 结构化进程身份；
- 端口占用者；
- 文件锁状态；
- PATH 候选；
- 权限状态；
- Desktop backend ownership；
- 安全启停能力；
- 默认只读的检查函数。

建议 Git 分支/工作树边界：

```text
fix/windows-compat
feat/health-recovery
```

合并顺序：

```text
main
→ merge fix/windows-compat
→ rebase health-recovery
→ 接入 WindowsPlatformAdapter
```

---

## 35. 推荐实施阶段

### Phase 0：批准设计规范

确认：

- 命令；
- 数据模型；
- API；
- 风险政策；
- 平台边界；
- Rescue Runtime 范围。

### Phase 1：统一 Full Doctor Finding Schema

在现有 Core 可运行前提下扩展只读诊断。

### Phase 2：独立 Rescue Runtime

解决 Argus Core 无法进入的问题。

### Phase 3：Repair Plan 与 SAFE Action

首批只实现无争议、安全、可验收的动作。

### Phase 4：Windows CLI/Web/Desktop

接入 Windows primitives，验证完整架构。

### Phase 5：Linux CLI/Web

优先支持 headless、SSH、systemd。

### Phase 6：macOS CLI/Web/Desktop

支持 Apple Silicon、Rosetta、Gatekeeper 和签名应用。

### Phase 7：跨平台安全更新与回滚

完成 Desktop/Runtime staged update。

### Phase 8：AI Advisor 与外层 Agent 工具

在确定性规则和 Repair Registry 稳定后加入。

---

## 36. 上线门槛

首版进入 Preview 前必须满足：

- Doctor 全路径只读证明；
- 无 Secret 泄漏；
- 无任意 Shell 执行；
- 无未知进程终止；
- Plan freshness 生效；
- 至少 Windows Tier 1 E2E 通过；
- CLI/API Finding 一致；
- 修复失败可回滚或明确标记不可回滚；
- Support Bundle 脱敏测试通过；
- 文档明确哪些动作仍需人工完成。

---

## 37. 待批准的关键决策

1. 正式命令是否采用 `argus doctor` 并保留 `argus --doctor`。
2. Rescue Runtime 的实现形式与发行方式。
3. 是否在首版提供 Recovery Gateway。
4. 首批 SAFE RepairAction 清单。
5. 远程 Web 是否永远禁止 CONSENT Action，还是允许加强认证后开启。
6. Desktop 更新渠道和签名策略。
7. Linux 首批支持的发行版与打包格式。
8. macOS 首批是否同时支持 Intel 与 Apple Silicon。
9. Repair Plan 默认有效期。
10. Operation Journal 保留周期。

---

## 38. 最终设计结论

完整能力由两个系统组成：

### Bootstrap Doctor

在 Argus 主程序、Python 环境、WebAPI 或 Desktop backend 无法正常进入时仍然可以使用。

### Full Doctor & Recovery

在 Core/API 可用时提供完整诊断、AI 辅助解释、修复规划、授权执行、验收、更新与回滚。

最终原则：

> AI 可以帮助理解和编排，但底层环境事实必须由确定性检查得出；修复必须来自注册动作；不同平台、安装方式和目标对象必须路由到不同 Repair Provider；Doctor 本身永远只读。
