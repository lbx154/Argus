<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/svg/argus-logo-horizontal-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/svg/argus-logo-horizontal.svg">
  <img src="docs/assets/brand/svg/argus-logo-horizontal.svg" width="420" alt="Argus">
</picture>

### 面向科研与工程的持久、可审查自主运行时

让长期 Agent 能够规划、执行、验证、暂停，并在一次模型调用之后继续推进。

**当前为 Preview v0.1.2 · 用于提前发布 Argus 的后续更新。**

[![GitHub Stars](https://img.shields.io/github/stars/lbx154/Argus?style=flat-square)](https://github.com/lbx154/Argus/stargazers)
[![License](https://img.shields.io/github/license/lbx154/Argus?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![arXiv](https://img.shields.io/badge/arXiv-2608.05144-b31b1b?style=flat-square&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2608.05144)

[官方网站](https://argusbot.cn) · [视频演示](https://www.youtube.com/watch?v=i8Qy9HCboQE) · [技术报告 · arXiv:2608.05144](https://arxiv.org/pdf/2608.05144) · [微信群](#微信群) · [English](README.md) / **简体中文**

`Manager` → `Planner` → `Engineer` ⇄ `Reviewer`

</div>

---

> [!IMPORTANT]
> **仓库定位：**这是 Argus 的 Preview 仓库；正式版维护在
> **[microsoft/ArgusAgent](https://github.com/microsoft/ArgusAgent)**。
> 两个仓库后续会保持同步更新，关注或 Star 任意一个仓库都可以持续了解项目动态。

## Driver–Harness 模型

**模型**是发动机：它烧掉算力，输出 token。**Harness** 是传动系统，把这些 token 耦合到文
件、shell、编译器、GPU 和测试上。**Driver** 是方向盘后面那个位子——决定接下来做什么、判断
上一个结果好不好、以及知道什么时候该停下来问人。在其他所有 Agent 系统里，坐在那个位子上的
都是一个人；所以那个人一睡觉，工作就停了。

**Argus 坐上 Driver 的位子**，把这份活拆给四个**刻意不让互相代劳**的角色：

| | 拥有 | **不得** |
|---|---|---|
| **Manager** | 阶段迁移权，以及被采纳的教训存放在哪一层 | 亲自执行它所要采纳的工作 |
| **Planner** | 下一个任务，以及它必须产出的证据 | 推动战役进入下一阶段 |
| **Engineer** | 实现、调研、实验、产物 | 宣布自己的工作已完成 |
| **Reviewer** | 判决——正确性、证据、局限；可以返回 `blocked` | 修改任何东西。它**只读**运行 |

凭据、支付、不可逆操作和对外发布，永远会停下来等人。

它还能不重训就变强：被采纳的 Skill 和带来源链接的 Wiki 发现，会按"它被证明成立的范围"放进
`project` → `vertical` → `global`；而新领域以 **vertical** 的形式接入一个不会改变的核心
——目前 24 个，全部 53,871 行领域代码里对权限边界的引用为零。

正因为干活的人不能给自己打分，没有人需要盯着它：在 27 场战役、1,548 小时里，它平均**每约
310 小时**才需要人做一次研究判断，占空比 **95–99%**。其余内容都在
**[技术报告](https://arxiv.org/pdf/2608.05144)**里。

**原生 Backend：** `GitHub Copilot CLI` · `Pi` · `OpenAI Codex CLI` · `Claude Code` · `OpenCode` · `Grok Build` · `Qoder` · `DeepSeek Harness`

**Harbor 评测：** Harbor Framework 可以把完整的有界 Argus
Manager/Planner/Engineer/Reviewer 运行时作为自定义 Agent 直接调用。配置和边界见
**[Harbor 接入说明](docs/harbor.md)**。

**Code Agent 插件：** 可通过打包的 MCP bridge 和宿主 Skills 使用 Argus，不修改
核心 runtime。参见 **[插件快速入门](docs/plugin.md)**。

## 微信群

扫码加入 Argus 交流群；点击图片可以查看原图。二维码有效期以图片中的提示为准；
如果已经过期，请在 Issue 中联系维护者更新。

<p align="center">
  <a href="docs/assets/argus-wechat-group.jpg">
    <img src="docs/assets/argus-wechat-group.jpg" width="360" alt="Argus 微信交流群二维码">
  </a>
</p>

## 快速安装

请只使用当前操作系统对应的一组命令，不要混用。所有平台都需要从
[nodejs.org](https://nodejs.org/en/download) 安装 Node.js **22.12+**，并准备一个
已完成鉴权的 Agent CLI。直接复用你日常使用的 CLI；Argus 没有单独账户。
普通 Argus 安装不需要 Docker；只有单独的 Harbor 评测集成可能把 Docker 作为可选
环境依赖。

> [!TIP]
> **推荐：让你正在使用的 Code Agent 代为安装并验证 Argus。**
> 复制下面“Agent 一键安装”中的 prompt 即可；希望逐步手工安装的用户仍可使用后面的
> 三系统命令。

| Agent CLI | Backend | 安装 | 鉴权 |
|---|---|---|---|
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` | `copilot login` |
| OpenAI Codex CLI | `codex` | `npm install -g @openai/codex@latest` | `codex login` |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | 运行 `claude`，再执行 `/login` |
| Pi | `pi` | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` | 运行 `pi`，再执行 `/login` |
| OpenCode | `opencode` | [官方安装说明](https://opencode.ai/docs/) | `opencode auth login` |
| Grok Build | `grok` | [官方安装说明](https://x.ai/cli) | `grok login` |
| Qoder CLI | `qoder` | `npm install -g @qoder-ai/qodercli` | `qodercli login` |
| DeepSeek Harness | `dsh` | `npm install -g @deepseek-ai/dsh` | 配置 `DEEPSEEK_API_KEY` 或 dsh Models 页面 |

正式 PyPI 首发前，公共 Preview 直接从 GitHub archive 安装。

### 推荐：使用 Agent 一键安装

把下面整段发送给已安装的 Code Agent：

```text
请阅读 https://github.com/lbx154/Argus/blob/main/docs/agent-install.md，
使用当前操作系统对应的方式安装 Argus。优先复用当前 Agent CLI 作为 backend。
Windows 和 macOS 不创建手工 venv；Linux 保留文档中的 venv。必须让 setup 完成真实
Agent turn 验收，再运行 argus doctor --deep --advisor auto。需要登录、sudo 或修改
全局配置时先说明原因并等待确认。不要要求我在对话中粘贴密码、token 或 API Key。
```

Agent 将遵循 **[安装执行规范](docs/agent-install.md)**。

### Windows 10/11：直接 pip 安装，不创建虚拟环境

从 [python.org](https://www.python.org/downloads/windows/) 安装 Python 3.11+
并勾选 **Add Python to PATH**。重新打开 PowerShell 后执行：

```powershell
py --version
node --version
py -m pip install --upgrade pip
py -m pip install --upgrade --force-reinstall "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$Argus = Join-Path $Scripts "argus.exe"
if (-not (Test-Path $Argus)) { throw "Argus entry point not found at $Argus" }
$env:Path = "$Scripts;$env:Path"
& $Argus --version
& $Argus --setup
& $Argus doctor --deep --advisor auto
& $Argus --status
& $Argus
```

使用 `$Argus` 绝对路径可以证明 setup 没有误调用旧安装。`$env:Path` 会让当前
PowerShell 同时支持普通 `argus` 命令；新窗口的持久 PATH 修复见后面的排障章节。

`argus doctor` 是主动修复命令：默认会在真实 Argus 目录中启动用户电脑上已安装的
Agent CLI，开放工具让 Agent 直接检查并修复机器，然后重新运行确定性检查验收。
只有需要“不调用模型的确定性验证”时才使用
`argus doctor --advisor none --verify`。
主动修复会执行一次真实 Agent turn，可能需要几分钟；它不是快速版本检查。

Windows 当前支持安装、Manager 对话、配对、Web/TUI、终端作用域 daemon 控制和
原生 durable subagent。Native Windows 使用独立 worker 承载 direct 或 supervised
长命令，持久化任务注册与日志，并进行有界进程树清理；此路径不再强制依赖 WSL2。
图形安装见 **[Windows Desktop](docs/windows-desktop.md)**。

### macOS：uv tool 管理安装，不手工创建虚拟环境

按需安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后执行：

```bash
uv --version
node --version
uv tool install --force --python 3.12 \
  "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
ARGUS_BIN="$(uv tool dir --bin)/argus"
test -x "$ARGUS_BIN"
"$ARGUS_BIN" --version
uv tool update-shell
"$ARGUS_BIN" --setup
"$ARGUS_BIN" doctor --deep --advisor auto
"$ARGUS_BIN" --status
"$ARGUS_BIN"
```

即使 uv 的 tool bin 尚未加入 PATH，`ARGUS_BIN` 也能立即工作。
`uv tool update-shell` 会让新终端可以直接使用 `argus`。隔离环境已经由 uv 管理，
不要再套一层 venv。

### Linux：保留隔离源码 venv

Linux 服务器继续显式使用 venv，保证 Python、CUDA 工具链和长任务进程环境可复现。
先安装 Python 3.11+、Git、Node.js 22.12+ 和发行版的 `python3-venv` 包：

```bash
git clone https://github.com/lbx154/Argus.git "$HOME/Argus"
cd "$HOME/Argus"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
ARGUS_BIN="$HOME/Argus/.venv/bin/argus"
"$ARGUS_BIN" --version
"$ARGUS_BIN" --setup
"$ARGUS_BIN" doctor --deep --advisor auto
"$ARGUS_BIN" --status
"$ARGUS_BIN"
```

私有 Preview 协作者在 Linux clone 命令中改用
`https://github.com/lbx154/argus-skill.git`。Windows/macOS 应安装私有 wheel
或经过认证的私有 archive，不要把 GitHub token 写进 shell history。

Linux 新终端不要依赖全局 `argus`；请使用
`$HOME/Argus/.venv/bin/argus`（或显式激活该 venv）。如果创建 venv 时提示缺少
`ensurepip`，安装发行版的 `python3-venv` 包后重试。

### Backend 说明

`--backend` 可使用 `copilot`、`pi`、`codex`、`claude`、`opencode`、`grok`、
`qoder` 或 `dsh`。setup 会优先采用所选 CLI 自己目录中的模型；无法确定时保留
该 CLI 的原生默认值，不会把 OpenAI 模型 id 注入 Claude Code、Pi、OpenCode、
Grok、Qoder 或 dsh。
如果已有 OpenAI-compatible URL，setup 会在需要时自动安装 Pi 并完成配置：

```bash
ARGUS_SETUP_API_KEY=... argus --setup --non-interactive \
  --api-url https://api.example.com/v1 \
  --api-model model-id
```

使用 Grok Build 时，请先安装并登录 xAI 官方 CLI：

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok login
argus --setup --non-interactive --backend grok
```

无界面环境也可以使用 `XAI_API_KEY`。Argus 通过 Grok 原生 headless JSON
流运行、按 Session ID 续接，并避免把角色 prompt 放进进程参数。
PowerShell 多行续行符为反引号，不是 `\`。

#### 为多 provider 的 CLI 指定 provider

Pi 与 OpenCode 是与 provider 无关的前端：具体走哪个账户，取决于你给它认证了什么
（原生 DeepSeek key、Anthropic、Azure、本地 vLLM、Copilot 代理）。Argus 会把你配置
的 model id 原样透传，因此 `deepseek-chat` 这样的裸 id 由 CLI 自己解析。

只有在裸 id 有歧义、或 CLI 本身要求限定时才需要指定 provider：

```bash
# Pi —— 仅当两个已认证目录里存在同名 model 时才需要
export ARGUS_SKILL_PI_PROVIDER=deepseek

# OpenCode —— 必需：`opencode run --model` 只接受 provider/id
export ARGUS_SKILL_OPENCODE_PROVIDER=deepseek
```

两者也可以在座舱 `/config` 里设置，在那里设置后会持久化、重启依然生效。

`argus --doctor` 会读取 CLI 的已认证目录：配置的 provider 你并没有 key，或选定的
model 不在目录中时，会直接告诉你。

用 `argus --config-help` 查看每个角色最终使用的模型及配置来源。模型目录查询命令
因 backend 而异，例如 `pi --list-models`、`opencode auth list` 和
`qodercli --list-models`。

完整说明（含对依赖旧的隐式 `github-copilot` 前缀的 Pi 部署的不兼容变更）：
**[后端 provider 说明](docs/backend-providers.md)**。

### 启动

Windows 和 macOS 配好 PATH 后可直接使用 `argus`。Linux 如果没有激活 venv，
请把下面的 `argus` 替换成 `$HOME/Argus/.venv/bin/argus`。

```bash
argus
```

```bash
argus doctor                         # 调用 Agent 检查并修复
argus doctor --advisor none --verify # 不调用模型的确定性验证
argus --status                       # 查看当前运行状态
```

## 交互界面

### Windows Desktop

Windows x64 源码包含一个 Electron 桌面宿主：它监管由同一套 Argus 运行时冻结得到的
本地后端，并直接打开现有 Web Cockpit；Manager、Workbench 与 WebAPI 不存在单独的
Desktop 分叉。源码运行、安全边界、验收和打包命令见
**[Windows Desktop 文档](docs/windows-desktop.md)**。

### Terminal Cockpit

```bash
argus
```

通过终端 Cockpit 与 Manager 对话、跟踪实时工作、检查状态并恢复项目。
未显式指定 `--port` 时，Argus 会复用兼容后端；若默认端口被其他程序或旧后端占用，
则从 `8799` 开始选择首个可用端口。在 Windows 上，普通 `argus` 启动会同时打开
Web UI；使用 `argus --no-open` 可只保留终端 Cockpit。

### Web UI

启动 Argus，并在默认浏览器中打开 Web UI：

```bash
argus --web
```

首选地址：[http://127.0.0.1:8799](http://127.0.0.1:8799)；被占用时会自动顺延。

```bash
argus --web --web-port 8800  # 使用其他端口
```

#### 通过 SSH 使用远程服务器

在服务器上：

```bash
argus --web
```

在自己的电脑上：

```bash
ssh -L 8799:127.0.0.1:8799 user@server
```

然后在本机打开 [http://127.0.0.1:8799](http://127.0.0.1:8799)。

<details>
<summary><strong>直接通过局域网访问</strong></summary>

非本机监听始终受 Bearer Token 保护：设置了 `ARGUS_SKILL_WEB_TOKEN` 就用它，没设置则为本次运行自动生成一个。

```bash
argus --web --web-host 0.0.0.0 --web-port 8799
```

命令会打印其他设备可达的地址、Token，以及一个二维码。想让 Token 在重启后保持不变，自己设置即可：

```bash
export ARGUS_SKILL_WEB_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

如果确实要在没有 Token 的情况下提供服务（仅在你自己有鉴权代理的前提下），设置 `ARGUS_SKILL_WEB_ALLOW_INSECURE=1`。

</details>

### 在手机上使用

Telegram、飞书 / Lark 和网页版都可以在手机上使用。两个聊天机器人都是**向外拨号**的长连接，所以位于 NAT 后面的守护进程不需要内网穿透，也不需要公网地址：

```bash
# 飞书 / Lark —— WebSocket 长连接，无需配置请求地址
pip install 'argus-skill[feishu]'
export ARGUS_SKILL_ENABLE_FEISHU=1
export ARGUS_SKILL_FEISHU_APP_ID=cli_xxx ARGUS_SKILL_FEISHU_APP_SECRET=xxx

# Telegram
export ARGUS_SKILL_ENABLE_TELEGRAM=1
export ARGUS_SKILL_TELEGRAM_BOT_TOKEN=... ARGUS_SKILL_TELEGRAM_CHAT_ID=...
```

两个机器人提供完全相同的命令（`/add`、`/status`、`/nudge`、`/backlog` 等）。网页版可以添加到手机主屏幕，扫描 `argus --web --web-host 0.0.0.0` 打印的二维码即可完成配对。

完整配置见 **[docs/mobile.md](docs/mobile.md)**。

## 高级使用

Argus 的设计目标不是“只能配置”，而是“可以被你改变”。

### 自主程度

默认 `pragmatic` 模式会自行处理超时、失败测试、benchmark 规模和技术路线等可恢复问题；只有凭证、预算增加、不可逆操作、对外发布或改变你定义的验收边界时才会询问。

```bash
# 谨慎：每个明确问题都询问
export ARGUS_SKILL_AUTONOMY_MODE=cautious

# 务实（默认）：技术问题自动恢复，权威边界询问
export ARGUS_SKILL_AUTONOMY_MODE=pragmatic

# 主动：最大化可逆技术执行，仍保留凭证/金钱/不可逆边界
export ARGUS_SKILL_AUTONOMY_MODE=autonomous
```

也可以从 Web 配置页或 `/config` 修改该选项。

### 改造整个运行时

如果你是 Agent 的狂热爱好者，我们推荐你在本地部署 Argus，让完整闭环真正适合自己的工作方式。你可以调整角色 Prompt、工作流边界、审查策略、工具与运行约定，对接已有基础设施，并用测试固定自己重视的行为。

一个完整工程案例是 **[避免局部爬山](docs/exploration-without-local-hill-climbing.zh-CN.md)**：MI300X serving 任务暴露出过度保守激励后，Argus 如何把纯报告研究、高风险机制组合、单次探索筛选与严格最终声明分开。

更完整的一份是 **[Argus 会出什么问题，我们怎么修的](docs/failure-modes-and-fixes.zh-CN.md)**：记录了六种真实失效——一个测的其实是自己 token 上限的实验、冻结在训练截止时刻的世界知识、满足于交差而不是做成一件事、在想法还没配得上时就施加的仪式、局部爬山，以及不愿意报喜。它也记录了一次**我们修错了的修法**：加一个门去逼出进取心——而诚实的答案是，防御性检查不产出好工作，它只产出能通过检查的工作。

### 创建自己的 Vertical

Vertical 可以为你的领域提供专属阶段、Skill、数据集、工具、证据要求、评测方法与完成标准。规划与审查将遵循该领域真正重要的规范，而不是一套通用流程。

`math` vertical 是已实现的完整范例：三阶段流程、内容寻址的证据库、Lean 机械验证，以及"哪一类检查才有资格判定哪一类问题"的明确规则。详见 **[mathematical research](docs/research-mathematics.md)**（英文）。

### 让其他 Agent 成为外层入口

你可以通过 GitHub Copilot、Pi、Codex、Claude Code、OpenCode、Grok Build、OpenClaw 或 Hermes 调用 Argus、检查状态、操作本地 CLI 或 Web/API，并继续迭代自己的部署。

- **Argus 原生 Backend：** GitHub Copilot CLI、Pi、Codex CLI、Claude Code、OpenCode、Grok Build、Qoder、DeepSeek Harness
- **外层 Agent：** OpenClaw、Hermes，或任何能够使用 Shell / HTTP API 的 Agent

如需运行持久任务，可安装或适配可移植的
[`argus-runtime-orchestration` Agent Skill](integrations/agent-skills/argus-runtime-orchestration/SKILL.md)。
该 Skill 明确定义了双方操作模型、主动检查 `Needs you` 的干预闭环、
各宿主适配器、证据边界与收尾检查。

常用入口：

```bash
argus doctor
argus --status
argus --web
```

最强大的 Argus 往往是一套被你认真改造成更适合自己伟大领域与工作方式的 Argus。

## 更新

Windows：

```powershell
py -m pip install --upgrade --force-reinstall "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
$Argus = Join-Path (py -c "import sysconfig; print(sysconfig.get_path('scripts'))") "argus.exe"
& $Argus --version
& $Argus doctor --advisor none --verify
```

macOS：

```bash
uv tool install --force --python 3.12 \
  "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
"$(uv tool dir --bin)/argus" --version
"$(uv tool dir --bin)/argus" doctor --advisor none --verify
```

Linux 源码 checkout：

```bash
"$HOME/Argus/.venv/bin/argus" update
"$HOME/Argus/.venv/bin/argus" --version
"$HOME/Argus/.venv/bin/argus" doctor --advisor none --verify
```

Linux 源码更新会拒绝 dirty/detached checkout，只做 fast-forward 并刷新 editable
安装。更新后 Argus 会识别过期的本地 WebAPI 与 daemon，并在受控任务边界完成替换。
这里的更新验收是确定性的，不消耗模型调用。

## 卸载

```powershell
# Windows
py -m pip uninstall argus-skill
```

```bash
# macOS
uv tool uninstall argus-skill
```

Linux 请先停止 Argus、保留所需工作，再删除 `$HOME/Argus` checkout 及其中的
`.venv`。所有平台卸载 package 时都会保留 `$HOME/.argus-skill` 运行状态；只有在
确定项目、配置和日志也不再需要时才删除该目录。

## 安装排障

- PowerShell 用 `Get-Command argus -All`，macOS/Linux 用 `type -a argus`
  确认 shell 实际调用哪个 executable；更新后 `argus --version` 的 release id
  应发生变化。
- macOS 可立即使用 `"$(uv tool dir --bin)/argus"`；执行一次
  `uv tool update-shell` 并重新打开终端后才能稳定使用普通 `argus`。
- Windows 用
  `$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"`
  找回准确 Scripts 目录，再用 `$env:Path = "$Scripts;$env:Path"` 修复当前窗口。
  新窗口请在 Python 安装器的 **Modify** 中启用 **Add Python to PATH**，不要为此
  创建 venv。
- Linux 使用 `$HOME/Argus/.venv/bin/argus`；全局 `argus` 可能属于旧安装。
  `python3 -m venv` 缺少 `ensurepip` 时先安装 `python3-venv`。
- `argus doctor --advisor none --verify` 只做确定性诊断；需要本机 Agent 直接检查和
  修复 Argus 时使用 `argus doctor`。
- 用 `argus --config-help` 检查实际 backend/model，再判断 setup 或鉴权是否失败。

## Argus 目前取得的成果

一份部分记录，按**由谁来判定这个结果算不算数**分组——而这些判定者里没有一个是 Argus 自己。

### 开源代码

| 仓库 | 结果 |
|---|---|
| **[ace-2](https://github.com/Argus-AiTeam/ace-2)** | 一颗 Qwen2.5-0.5B W4A8 推理加速器，其规格、RTL、验证与物理流程都没有人类作者。运行时 **13,914/13,914** 条命令跑完；SKY130 面积 **0.614 mm²**（上限 2.0）、余量 **+0.6966 ns**、WNS/TNS 0.00 ns、100 MHz。证书自己公布了排除项：不含 DRC/LVS、不含 GDS、不含硅验证。 |
| **[minimax-h3-mac](https://github.com/Argus-AiTeam/minimax-h3-mac)** · **[-desktop](https://github.com/Argus-AiTeam/minimax-h3-desktop)** · **[ComfyUI 节点](https://github.com/Argus-AiTeam/ComfyUI-MiniMax-H3-MLX)** | 一个约 62 GiB 的模型，不是被压小而是被跑起来：24 GB 的 M4 Pro 上 **47 分 58.7 秒**，峰值约 **15.8 GB**。单张 RTX A6000 上，Turbo 8-step 相对 BF16 的 N=10 基线达到 **6.159×**。未通过质量门禁的候选被公开标记为 *rejected*。 |
| **[FlashDA](https://github.com/SJTU-DENG-Lab/FlashDA/tree/feature/dllm-fa4-adaptation)** · **[Diffulex](https://github.com/SJTU-DENG-Lab/Diffulex)** | 六种扩散语言模型 mask 家族被搬进 **FlashAttention-4 CuTe DSL** kernel，耗时 **21.85 小时**模型算力、不到 **80 元**、87.7 小时内只打扰 2 次。跨 SM80/SM90 **19/19** 对齐；H200/SM90 上达到**原生 FA4 的 92–95%**，并比 Diffulex Triton 后端快 **1.61–2.57×**（两边都开 CUDA Graph）。早期路线比原生**慢 4.9–29.6×**；正是"识别出这条数据通路本身就是错的并放弃它"才产出了最终结果，而那条被否掉的路线连同证据一起被保留。 |

FlashDA 建立在 [Tri Dao](https://github.com/tridao) 及合作者出色的 FlashAttention-4 /
CuTe DSL 工作之上。欢迎复现，以及向 SM90 之外的移植；完整协议与逐场景延迟见
[`EXPERIMENT_RESULTS.md`](https://github.com/SJTU-DENG-Lab/FlashDA/blob/feature/dllm-fa4-adaptation/EXPERIMENT_RESULTS.md)。

### 由外部维护者判定

| 提交 | 结果 |
|---|---|
| **[sglang#35038](https://github.com/sgl-project/sglang/pull/35038)** —— SenseNova U1 原生多模态生成与交错服务 | 36 个文件、**+11,263/−72**、14 个 commit。1,116 个张量、0 缺失；视觉问答 **160/160** 精确；并发 8 下 **8/8** 精确；BS8 吞吐 **5.108×**。一位工程师配合逐轮 Agent 投入 **60 多小时**未能完成；Argus 在 **24.14 小时**内完成。*open。* |
| **[fla-org#1045](https://github.com/fla-org/flash-linear-attention/pull/1045)** —— TileLang RWKV6 后端 | **已合入**，H100 NVL 上前向+反向 1.21×，无任何 inline 修改要求。它的说明里写明这项工作由 Argus 自主完成——而外部维护者连同代码一起接受了这句话。 |
| **[fla-org#1109](https://github.com/fla-org/flash-linear-attention/pull/1109)** —— SM100 autotune 崩溃 | **已合入。** 两行，没有加速可报：修之前整个测试文件跑不完，修之后 **76 个测试通过**。 |
| **[fla-org#1128](https://github.com/fla-org/flash-linear-attention/pull/1128)** · **[#1114](https://github.com/fla-org/flash-linear-attention/pull/1114)** | KDA 训练相对 Triton 1.29×，`AttnRes` 在 B200 上几何平均 1.102×。两者都把**最差的那一行**和均值并列写出；#1128 交付的是它能验证的 1.078–1.099×，而不是它测到的 1.541×。*open。* |

### 由官方评测器打分

| 竞技场 | 结果 |
|---|---|
| SWE-Bench Pro（731 任务） | **≈78%**，对照直接使用 Copilot 的 **59%**（两边同一个模型）——并且 **35** 个任务被判为 `blocked`，而不是报成没有证据支撑的成功 |
| SOL-ExecBench | 全球排名 **#6**；7 个 kernel 进入 top-3；在 2 个上超过第 1 名 |
| MLE-Bench Lite | 奖牌率 **69.2%**（9/13）：3 金、3 银、3 铜，对照 Kaggle 排行榜 |
| AARRI-Bench | **63/82（76.8%）**，对照论文最好成绩 68.3% |
| nanochat / nanoGPT speedrun | B200 上 0.9636 vs 人类最好 0.9646 BPB；**79.77 秒** vs 同设备人类记录 80.18 秒 |

### 由外部检查器判定

- **MOF 生成**——化学可控性 92.5 / 100.0 / 74.5%，AUC 0.594 → 0.833，由外部 `MOFChecker` 验证。被采纳的方法比它所取代的那个**更小**。
- **Erdős–Gyárfás**——六项有证明支撑的前沿更新，其中一条被证伪的路线作为证据被保留而不是删掉。
- **研究写作**——六条论文流水线推进到投稿，共 254 个 mission，含 16 次 Stage 回滚。
- **作用在它自己身上**——成熟期解决一个 SWE-Bench Pro 任务，比启动期少用 **21% 的 token**、少花 **15% 的活跃时间**，全程权重未变。最长单场战役 **8.1 天**。

> [!NOTE]
> 以上每个数字都来自[技术报告](https://arxiv.org/pdf/2608.05144)或所链接的仓库，并各自带着
> 那里声明的适用条件。
