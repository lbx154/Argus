<div align="center">

# Argus

### 面向长周期研究的通用智能体运行时

**长周期推理不是沿着固定路线一路狂奔的竞速。**<br/>
证据支持当前路线时，Argus 持续推进；当测量结果证明路线是错的，它会留下记录、按规则转向。

[![Website](https://img.shields.io/badge/官网-argusbot.cn-315BCE?style=flat-square)](https://argusbot.cn/zh.html)
[![Technical Report](https://img.shields.io/badge/技术报告-PDF-B31B1B?style=flat-square)](technical_report/argus-technical-report.pdf)
[![Results](https://img.shields.io/badge/实验结果-7%20个赛场-24465D?style=flat-square)](https://argusbot.cn/zh/results.html)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-6F9B86?style=flat-square)](LICENSE)

**[官网](https://argusbot.cn/zh.html)** ·
**[工作原理](https://argusbot.cn/zh/how.html)** ·
**[实验结果](https://argusbot.cn/zh/results.html)** ·
**[研究](https://argusbot.cn/zh/research.html)** ·
**[应用场景](https://argusbot.cn/zh/use-cases.html)** ·
**[快速开始](https://argusbot.cn/zh/start.html)** ·
**[技术报告](technical_report/argus-technical-report.pdf)**

[English](README.md) · [简体中文](README.zh-CN.md) · [English site](https://argusbot.cn/)

<img src="docs/assets/argus_teaser.png" alt="Argus 运行时与广度证据：Manager、Planner、Engineer、Reviewer 在共享持久化工作区上协作，四周为七个任务原生评测的结果卡片。" width="100%"/>

<sub><b>Argus 运行时及其广度证据。</b>Manager 掌握任务、研究垂直方向与 Stage 迁移的权限；Planner、Engineer、Reviewer 在共享工作区上工作，其知识、事件日志、产物、backlog、预算、daemon 与记忆都跨 bounded mission 持久化。四周卡片给出七个任务原生评测的结果，各自使用独立标度——这是广度证据，而非单一归一化榜单。<a href="docs/assets/argus_teaser.pdf">矢量 PDF</a></sub>

</div>

---

## 目录

- [为什么是 Argus](#为什么是-argus)
- [实验结果](#实验结果)
- [工作原理](#工作原理)
- [安装](#安装)
- [第一个任务](#第一个任务)
- [使用指南](#使用指南)
- [支持的后端](#支持的后端)
- [模型 Backbone](#模型-backbone)
- [配置](#配置)
- [仓库结构](#仓库结构)
- [技术报告与引用](#技术报告与引用)
- [参与贡献](#参与贡献)
- [许可证](#许可证)

---

## 为什么是 Argus

大多数 agent 框架优化的是单轮：一个 prompt、一个工具循环、一个答案。但研究不是这样运转的。真实的研究会持续数天，产出必须跨进程重启存活的产物，并且经常会发现——最初的计划本身就是错的。

Argus 建立在一个并不舒服的事实之上：**除非被治理，否则「转向」和「为失败找借口」在外部看来毫无区别。** 因此 Argus 里的每一次转向都必须有证据支撑、经过角色分离的 gate 准入，并连同理由一起被记录下来。

这一条约束推导出了整个设计：

| 长周期 agent 的问题 | Argus 的做法 |
| --- | --- |
| agent 忘记自己试过什么，反复重读同一批文件 | **持久化项目状态**——记忆、skill、checkpoint、事件日志、backlog 与产物都在磁盘上，而不在上下文窗口里 |
| 上下文无界增长直到 session 退化 | **全新 session + 精选交接**——自主角色调用从不 resume 线程；由 Reviewer 审计过的工作记忆 checkpoint 只携带下一个 session 真正需要的内容 |
| agent 自己给自己判卷 | **角色分离**——由独立的 Reviewer 对照产物与检查结果裁决 `done` / `continue` / `blocked`，永远不是 Engineer 自己 |
| 错误路线一条道走到黑，或者凭感觉半途放弃 | **可验证的转向**——只有在有证据且有授权时，Manager 才准入对目标的实质性变更，理由必须写下来 |
| session 一重置，学到的全没了 | **固定模型下的运行时自进化**——被审查通过的结果沉淀进记忆、skill、流程、验证状态与路由，而模型参数保持不变 |
| 需要有人一直盯着 | **7×24 daemon**——初始指派之后，常规轮次无人值守运行；只有需要你授权的决策才会上报 |

四个持续存在的角色在这个共享、持久的工作区上协作：

- **Manager**——理解 operator 意图、选择工作流、掌管 Stage 迁移，以及变更目标所需的「证据 + 授权」gate。
- **Planner**——把目标拆解为可执行任务，以及每个任务必须产出的证据。
- **Engineer**——调研、实现、跑实验、产出产物。
- **Reviewer**——独立检查正确性、证据、局限与完成度，并审计交给下一个 session 的记忆。

---

## 实验结果

以下所有数字均以 **GPT-5.5** 为 backbone、使用 **Codex** 后端，并以各赛场的原生单位报告。它们是**跨七个任务原生赛场的广度证据**，而不是单一归一化榜单——各图的标度相互独立。

| 赛场 | 协议 | Argus | 参照 | 差值 |
| --- | --- | --- | --- | --- |
| **SWE-Bench Pro** | 731 个任务 | **≈78%** | Direct Copilot ≈59% | **+19 pp**，Token 为 1.41× |
| **NVIDIA SOL-ExecBench** | B200 · 101 kernels | **全球第 6** · 2 项第 1 · 7 项前三 | — | 对 Recursive 两场正面交锋取胜 |
| **nanochat · B200** | 5 分钟 · 1×B200 · 426 次尝试 | **0.9636 BPB** | 人类 SOTA 0.9646 | 低 0.0010 ↓ |
| **nanochat · H100** | 5 分钟 · 1×H100 · 37 种机制 | **0.9855 BPB** | 人类 SOTA 0.9879 | 低 0.0024 ↓ |
| **nanoGPT speedrun** | 8×H100 · N=10 | **79.77 秒** | 同设备人类第 83 名：80.18 秒 | 快 0.41 秒 ↓ |
| **AARRI-Bench** | 82 个科研实习生任务 | **63/82 · 76.8%** | 论文最佳 68.3% | +8.5 pp ↑ |
| **Arbor · 人大 NLPIR** | 数学推理数据合成（AIME 风格） | **28.0** | Arbor 20.83 · Claude Code 8.33 · Codex 6.25 | pass@4−pass@1 gap ↑ |

<div align="center">
<img src="docs/assets/public_results.png" alt="六个赛场的公开结果，使用原生单位与直接标注，不做跨赛场归一化。" width="88%"/>

<sub>以原生单位呈现的公开结果——直接标注，不做跨赛场归一化。<a href="docs/assets/public_results.pdf">矢量 PDF</a> · <a href="https://argusbot.cn/zh/results.html">在线结果页</a></sub>
</div>

### 固定模型下的运行时自进化

SWE-Bench Pro 这一次运行同时也是一项观察性研究：当**权重不变**时，究竟是什么在变好。跨 22 个已完成 Wave，被审查通过的更新沉淀进记忆、skill、流程、验证状态与路由：

<div align="center">
<img src="docs/assets/swebench_evolution.png" alt="SWE-Bench Pro 的结果与审查路由、每任务 solve token、每任务活跃时间随 Wave 的变化。" width="92%"/>

<sub><a href="docs/assets/swebench_evolution.pdf">矢量 PDF</a></sub>
</div>

- 从 W1–6 启动窗口到 W19–22 成熟窗口，每任务 solve 输入 token **减少 21%**（2.95M → 2.33M）。
- 同样两个窗口之间，每任务活跃工作流时间**减少 15%**（8.52 分钟 → 7.25 分钟）。
- **这条轨迹并不单调。** 任务构成变化与后期高难度 Wave 会带来可见的回退——W23–24 上升到 3.72M token 与 9.01 分钟。我们如实报告，而不是截取到最好的窗口为止。

### Reviewer 究竟拦下了什么

731 个任务中，Reviewer 被调用 **466 次（63.7%）**，其余由自身路由完成。

| 结果 | 数量 |
| --- | --- |
| 首轮直接通过 | 388 |
| 要求修改 | 43 |
| 判定阻塞 | 35 |
| → 修改后通过**官方 verifier** | **34** |
| → 完成严格 review-loop 救回 | **22** |

Review 并不免费：进入 Reviewer 路由的工作量要多花 **2.75×** token 和 **1.80×** 时间。这 34 次 verifier 救回，就是这笔开销买回来的东西。

<div align="center">
<img src="docs/assets/reviewer_mechanism.png" alt="Reviewer 路由（466 次调用 vs 265 次自审）与修改救回：388 通过、43 修改、34 verifier 通过、22 严格救回。" width="92%"/>

<sub>Reviewer 路由与修改救回。<a href="docs/assets/reviewer_mechanism.pdf">矢量 PDF</a></sub>
</div>

### 六项目论文生产案例研究

一次从运行时状态重建出的长周期战役：**640 战役小时**、**254 个 mission**、**576 轮**、**89 次 session roll**、**16 次回滚**、**436 份 review 快照**，**6/6** 篇稿件完成。其中具有代表性的 163.6 小时轨迹，把*七条被否决的方法路线*变成了一份 4,500 行的负结果研究，随后在投稿阶段扛过两次回滚才最终完成。

> 这些被保留下来的轨迹，同时构成了面向未来监督学习与强化学习的结构化训练数据。

---

## 工作原理

<div align="center">
<img src="docs/assets/horizon_mountain.png" alt="一个 mission 从 research 出发，经 plan、run、benchmark、analyze、draft、review 到 submit，图中包含回滚、被否决分支，以及底部的持久化状态。" width="94%"/>

<sub>一个 mission，从调研到投稿——包含回滚、被否决的分支，以及比任何单个 session 都活得更久的持久化状态。<a href="docs/assets/horizon_mountain.pdf">矢量 PDF</a></sub>
</div>

一个 mission 会经过若干 Stage（**research → plan → run → benchmark → analyze → draft → review → submit**）。在每个 Stage：

1. **Planner** 把当前目标变成具体任务，以及每个任务必须产出的证据。
2. **Engineer** 执行一轮：读取状态、动手、跑检查，并提出一份工作记忆交接。
3. **Reviewer** 对照产物与检查输出，独立给出 `done` / `continue` / `blocked`，并把提出的交接*审计*成下一份 canonical checkpoint。
4. **Manager** 决定该 Stage 是推进、保持还是回滚——并且它是唯一有权准入目标实质性变更的角色。

比这张图更重要的是两条性质：

- **每一次自主调用都从全新 session 开始。** Engineer 与 Reviewer 的线程从不被 resume。连续性来自磁盘上的持久状态加一次显式、被审计的交接——而不是来自一个不断膨胀、还会被有损自动压缩悄悄侵蚀的上下文窗口。
- **删除即解毒。** 工作记忆 checkpoint 有一个在代码里强制执行的硬上限，而不只是写在 prompt 里。地面真相保存在磁盘产物里、随时可以重新读回；checkpoint 只携带下一个 session 真正需要的东西。

---

## 安装

### 环境要求

| | 要求 |
| --- | --- |
| **Python** | 3.11 或更高 |
| **Node.js** | 22 LTS 或更高（agent CLI 需要；Argus 启动器本身 ≥ 18 即可） |
| **操作系统** | 源码安装：Linux / macOS · npm beta：Linux / Windows x64 |
| **后端** | 至少一个受支持的 agent CLI，且已**安装并登录** |
| **Git** | 必需——Argus 在真实 worktree 中工作 |

> **你必须自行安装并登录一个 agent CLI。** Argus 不自带模型，它*驱动*你已有权限的编码 agent CLI。参见[支持的后端](#支持的后端)。

### 方式 A —— npm beta（最快）

面向 Linux x64 与 Windows x64 的纯二进制构建，无需 Python 工具链。

```bash
# 1. 安装并登录一个后端（以 GitHub Copilot 为例）
npm install -g @github/copilot
copilot login

# 2. 安装 Argus
npm install -g @argusevolve/argus@beta

# 3. 配置并启动
argus --setup --non-interactive --backend copilot --accept-house-rules
argus
```

### 方式 B —— 源码安装（推荐用于开发）

```bash
git clone https://github.com/lbx154/Argus.git
cd Argus

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

然后安装并登录一个后端，运行配置向导：

```bash
npm install -g @github/copilot
copilot login

argus --setup --non-interactive --backend copilot --accept-house-rules
```

去掉 `--non-interactive --backend ... --accept-house-rules` 即可进入引导式向导：

```bash
argus --setup
```

向导会配置作者身份、共享的 agent CLI 后端及其鉴权契约、可选的 model API 路由，以及可选的 GPU 资源。除非你显式传入 `--set-git-global` / `--configure-codex`，它不会改写你的全局 Git 身份或 Codex 配置。

### 验证安装

```bash
argus --doctor     # 后端/鉴权、能力、daemon 与状态诊断
argus --version
```

### 升级

```bash
# 源码安装
cd Argus && git pull --ff-only
. .venv/bin/activate && pip install -e .

# npm beta
npm install -g @argusevolve/argus@beta
```

> **daemon 正在跑的时候升级？** 先在 mission 边界停下来，绝不要中途打断：
> ```bash
> argus --daemon-stop --drain    # 静默、跑完当前 mission，然后退出
> ```
> `argus --daemon-runbook` 会打印完整的 daemon 安全升级/重启手册。

---

## 第一个任务

```bash
cd /path/to/your/project     # Argus 与 cwd 绑定：cwd → 项目指纹 → 项目状态
argus
```

这会打开 **cockpit**。没有需要背诵的子命令——用自然语言描述你要什么：

```
> 帮我分析这个仓库的热点路径，把 p99 延迟降低 30%，并给出前后对比 benchmark
> 继续上次的任务
> 现在在干什么？
> 换成 copilot 后端
> engineer 用 claude-sonnet-5
> 当前这轮跑完就暂停
```

Manager 对请求分类，Planner 拆解，Engineer 逐轮执行，Reviewer 把关完成度。任何需要**你的授权**的事情——目标的实质性变更、不可逆操作——都会上报，而不是替你拍板。

---

## 使用指南

### Cockpit

```bash
argus                    # 全新 session（默认）
argus --continue         # 恢复最近活跃的 session
argus --resume           # 从最近的 session 中挑选
argus --resume <ID>      # 直接跳到某个 session
argus --no-daemon        # 进入 cockpit 时不自动拉起后台 daemon
```

### 7×24 无人值守运行

```bash
argus --daemon                     # 后台常驻 worker，持续清空 backlog
argus --daemon-fg                  # 前台 worker（systemd / 调试用）
argus --daemon-stop --drain        # 在下一个 mission 边界安全停止
argus --daemon-stop --force        # 超时不退出则 SIGKILL
argus --status                     # 打印 daemon + backlog 状态后退出
argus --daemon-runbook             # daemon 安全升级/重启手册
```

**continuous 模式**让 Planner 在 backlog 清空时自动生成新工作：

```bash
# 开放式（默认）：永远持续生成新工作
argus --daemon --continuous --objective "提升推理服务吞吐，并给出证据"

# 有界：Planner 认证 project_done 后硬停
argus --daemon --continuous --bounded --objective "为模块 X 交付一套可复现的 benchmark"
```

> **关于入口门禁。** cockpit 与 daemon 需要*受信任的机器 house rules*——这正是 `--setup` 时 `--accept-house-rules` 所建立的东西。**不要求**一开始就给出 objective：Manager 会路由你的第一条实质性 prompt，判断这项工作是 **bounded** 还是 **standing**，并为 standing 战役撰写持久化的执行目标。缺少 house rules 会以退出码 `2` 终止，并给出可操作的指引。

仓库中附带了一份 systemd `--user` unit 模板：[`deploy/argus-skill.service`](deploy/argus-skill.service)。它负责崩溃/重启后的拉起，并在停止时 drain 到 mission 边界：

```bash
cp deploy/argus-skill.service ~/.config/systemd/user/argus-skill.service
# 把 WorkingDirectory 改成你的项目 worktree，然后：
systemctl --user daemon-reload && systemctl --user enable --now argus-skill
loginctl enable-linger $USER      # 跨登出/重启存活
```

### 观察它在做什么

```bash
argus --follow                     # 实时流式输出 daemon 事件（类似 tail -f）
argus --watch                      # 只读实时 cockpit
argus --status                     # 一次性状态
argus --notify "优先走向量化路径，不要用多线程"
argus --notify "检查一下图注" --notify-stage draft
```

`--notify` 会把 operator 指引追加到 supervisor 收件箱，下一轮 Engineer 会读到。加上 `--notify-stage` 则会一直挂起，直到 pipeline 到达该 Stage 才投递。

### Web 与终端界面

```bash
argus --web                        # 在 127.0.0.1:8799 提供共享后端 API
argus --web --web-port 9000
```

React Web UI（`frontend/web`）与 Ink 终端 UI（`frontend/tui`）都对接这套 API。它默认只绑定回环地址——**绑定 `0.0.0.0` 之前，务必先设置 `ARGUS_SKILL_WEB_TOKEN`。**

### 研究工厂 gate

```bash
argus --evidence-chain-check --project-root .          # claim ↔ evidence ↔ bundle 链路完整性
argus --anti-mediocrity-check --proposed-condition A --baseline-condition B
argus --lifecycle-status                                # incubating/running/writing/quarantined/done/archived
argus --lifecycle-archive / --lifecycle-resume
```

### 知识库

```bash
argus wiki init <project>                               # 初始化 .autors/<project>/wiki/
argus wiki ingest --wiki .autors/<project>/wiki --refs paper/refs.bib
argus learn --material notes.pdf --material spec.md     # 用你自己的材料教 Argus
```

### 日常维护

```bash
argus --config-help                    # 所有面向 operator 的 ARGUS_* 开关、默认值与当前值
argus --config-snapshot                # 把解析后的 backend/model/effort 快照写入文件
argus --export-builtin-skills          # 把内置 skill 复制到 ./argus_builtin_skills
argus --gc --gc-dry-run                # 预览过期项目清理
argus --model-api-status               # 能力状态，不打印任何密钥
```

---

## 支持的后端

Argus 不捆绑模型。它驱动的是你自己安装并登录的 agent CLI，并且**所有角色共享同一个后端**——除非你单独覆盖某个角色。

| 后端 | 安装 | 登录 | 说明 |
| --- | --- | --- | --- |
| **GitHub Copilot CLI** `copilot` | `npm install -g @github/copilot` | `copilot login` | 需要有效的 Copilot 订阅。默认推荐。 |
| **OpenAI Codex CLI** `codex` | `npm install -g @openai/codex@latest` | `codex login` | **唯一**支持 `--auth-mode model_api` 的后端。所有公开结果均由它跑出。 |
| **Claude Code** `claude` | `npm install -g @anthropic-ai/claude-code` | `claude auth login` | 订阅制鉴权。 |
| **OpenCode** `opencode` | `curl -fsSL https://opencode.ai/install \| bash` | `opencode auth login` | 也会从 `~/.opencode/bin` 解析。 |
| **Pi** `pi` | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` | 运行 `pi`，执行 `/login`，然后退出 | 裸 model id 会自动加上 `ARGUS_SKILL_PI_PROVIDER` 前缀（默认 `github-copilot`）。 |
| `memory` | — | — | 仅用于测试与 smoke 的确定性假后端。 |

在 setup 时选择：

```bash
argus --setup --non-interactive --backend codex --accept-house-rules
argus --setup --non-interactive --backend codex --auth-mode model_api --accept-house-rules
```

也可以随时切换——在 cockpit 里说「换成 claude 后端」，或用环境变量：

```bash
export ARGUS_SKILL_LIFE_BACKEND=copilot
```

### 按角色分配后端

不同角色可以跑在不同 CLI 上——例如便宜的 planner 配上昂贵的 engineer：

```bash
export ARGUS_SKILL_ENGINEER_BACKEND=codex
export ARGUS_SKILL_REVIEWER_BACKEND=claude
export ARGUS_SKILL_PLANNER_BACKEND=copilot
export ARGUS_SKILL_MANAGER_BACKEND=copilot
export ARGUS_SKILL_CURATOR_BACKEND=copilot
```

未设置的角色继承 `ARGUS_SKILL_LIFE_BACKEND`。

Argus 驱动后端时会完全接管角色 prompt 与工具策略——例如 Pi 会以 `--no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files` 启动，这样你本地的交互式配置就绝不会悄悄改变一次自主运行。

---

## 模型 Backbone

model id 会**原样透传给后端 CLI**，因此可用集合取决于你登录的那个 CLI 暴露了什么。开关接受裸 model id（`gpt-5.5`、`gpt-5.6-sol`）或带 provider 前缀的 id（`copilot/opus-5`）——不接受自由文本。

**所有已公开的 Argus 结果均使用 Codex 后端上的 `gpt-5.5`。**

### 按角色分配模型

优先级：**角色专用覆盖 → `ARGUS_SKILL_MODEL` → 内置默认值。**

| 开关 | 默认值 | 角色 |
| --- | --- | --- |
| `ARGUS_SKILL_MODEL` | `gpt-5.5` | 所有未单独覆盖角色的共享默认值 |
| `ARGUS_SKILL_MANAGER_MODEL` | `gpt-5.5` | Manager——意图、Stage 迁移、目标 gate |
| `ARGUS_SKILL_PLAN_MODEL` | `gpt-5.5` | Planner——任务拆解 |
| `ARGUS_SKILL_ENGINEER_MODEL` | `gpt-5.5` | Engineer——执行 |
| `ARGUS_SKILL_REVIEWER_MODEL` | `gpt-5.5` | Reviewer——裁决与记忆审计 |
| `ARGUS_SKILL_CURATOR_MODEL` | `gpt-5.5` | Curator——策略蒸馏 |
| `ARGUS_SKILL_MANAGER_REPLY_MODEL` | `inherit` | 面向 operator 的 Manager 回复 |
| `ARGUS_SKILL_FRONTDOOR_MODEL` | `auto` | 廉价请求分类——codex/copilot/pi 上为 `gpt-5.4-mini` |
| `ARGUS_SKILL_PLAN_PREVIEW_MODEL` | `auto` | 交互式 `/plan` 预览——codex/copilot/pi 上为 `gpt-5.4-mini` |
| `ARGUS_SKILL_REWRITE_MODEL` | `gpt-5.5` | 交互式 prompt 改写 |
| `ARGUS_SKILL_BOUNDED_DAG_MODEL` | `auto` | 用于 bounded 任务 DAG 拆解的紧凑模型 |

```bash
export ARGUS_SKILL_ENGINEER_MODEL=gpt-5.5
export ARGUS_SKILL_REVIEWER_MODEL=copilot/opus-5
```

或者直接在 cockpit 里说：「reviewer 用 claude-sonnet-5」。

### Reasoning effort

effort 按角色分别调优——在能改变结果的地方多想，在不能的地方求快。可选值：`low` · `medium` · `high` · `xhigh`。

| 开关 | 默认值 |
| --- | --- |
| `ARGUS_SKILL_MANAGER_REASONING_EFFORT` | `xhigh` |
| `ARGUS_SKILL_PLANNER_REASONING_EFFORT` | `xhigh` |
| `ARGUS_SKILL_ENGINEER_REASONING_EFFORT` | `xhigh` |
| `ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT` | `high`（直接任务的第一轮） |
| `ARGUS_SKILL_REVIEWER_REASONING_EFFORT` | `high` |
| `ARGUS_SKILL_CURATOR_REASONING_EFFORT` | `high` |
| `ARGUS_SKILL_PLAN_PREVIEW_REASONING_EFFORT` | `low` |

### 私有 model API 路由

Codex 用户可以走自己的 model API，而不是订阅鉴权：

```bash
argus --init-model-api        # 把 OPENAI_* / Codex 配置导入能力保险库
argus --model-api-status      # 查看能力状态，不打印密钥
```

凭据保存在 `~/.argus-skill/capabilities/model_api.json`，权限 `0600`，且绝不会被写进 prompt、日志或稿件。

---

## 配置

`argus --config-help` 会打印每个面向 operator 的开关及其默认值与当前值。最常用的几类：

### 预算与成本控制

| 开关 | 默认值 | 含义 |
| --- | --- | --- |
| `ARGUS_SKILL_COST_CONTROL` | `on` | 宿主机全局的已结算成本准入与对账 |
| `ARGUS_SKILL_UNPRICED_COST_POLICY` | `block` | 无法解析调用成本时的处理：`block` \| `allow` |
| `ARGUS_SKILL_CODEX_DAILY_CALL_CAP` | `300` | 宿主机级 Codex provider 每自然日调用上限 |
| `ARGUS_SKILL_COPILOT_DAILY_CALL_CAP` | `10000` | 宿主机级 Copilot 每自然日调用上限 |
| `ARGUS_SKILL_COPILOT_HOURLY_CALL_CAP` | `10000` | 宿主机级 Copilot 每滚动小时调用上限 |

### Mission 执行

| 开关 | 默认值 | 含义 |
| --- | --- | --- |
| `ARGUS_SKILL_MAX_ROUNDS` | `500` | 每个 mission 的最大 Engineer 轮数 |
| `ARGUS_SKILL_ENGINEER_FILE_READ_BUDGET` | `12` | 首轮文件浏览的软预算 |
| `ARGUS_SKILL_ENGINEER_TEST_RUN_BUDGET` | `3` | 最终 verifier 之前聚焦验证运行的软预算 |
| `ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS` | `2700` | 流空闲多久后仅终止当前 provider 进程组 |
| `ARGUS_SKILL_ROUND_CHECKPOINT` | `off` | 在 Reviewer 推荐的 checkpoint 处记录私有 git ref |

### 通知

```bash
export ARGUS_SKILL_ENABLE_TELEGRAM=on
export ARGUS_SKILL_TELEGRAM_BOT_TOKEN=...
export ARGUS_SKILL_TELEGRAM_CHAT_ID=...
```

Telegram 桥接支持远程查看状态与控制，包括 `/backend [codex|claude|copilot|opencode|pi|memory]`。

### 磁盘上的状态

```text
~/.argus-skill/
├── identity.md                          # 作者身份卡
├── skills/                              # skill 库（由内置 skill seed）
├── capabilities/model_api.json          # 私有能力保险库，权限 0600
└── projects/<fingerprint>/
    ├── project.md                       # 项目章程
    ├── backlog.jsonl                    # 任务队列
    ├── memory.jsonl                     # 本项目日志
    ├── events.jsonl                     # 事件日志
    └── continuous.json                  # 已装载的 continuous 战役
```

项目状态以工作目录指纹为键——**同一个 cwd 永远恢复同一个项目**。用 `--life-dir` 可覆盖根目录。

---

## 仓库结构

```text
argus_skill/          运行时：角色、loop、planner、reviewer、skill、daemon、web API
├── apps/             CLI、cockpit、运行时装配
├── engineer/         L1 执行循环、检查、checkpoint
├── reviewer/         L2 结构化裁决
├── planner/          L4 前向调度
├── manager/          意图、Stage 迁移、目标 gate
├── life/             supervisor、backlog、预算、记忆、通知
├── roles/            角色 prompt
├── agent_cli/        后端 CLI 驱动（codex、copilot、claude、opencode、pi）
├── skills/           skill store、蒸馏、pipeline contract
├── builtin_skills/   打包的 playbook（调研、benchmark、论文生产）
├── verticals/        垂直领域 pipeline
├── wiki/             按项目组织的 idea wiki
└── webapi/           web 与终端 UI 共享的 API
frontend/             基于共享 API 的 web（React）与 tui（Ink）客户端
technical_report/     技术报告 PDF
docs/assets/          本 README 使用的图（PNG + 矢量 PDF）
deploy/               systemd unit 模板
packaging/            npm 与单二进制打包
tests/                测试套件
```

---

## 技术报告与引用

完整的方法、评测协议与局限性都在技术报告中：

**[📄 Argus: A General-Purpose Agentic Runtime for Long-Horizon Reasoning](technical_report/argus-technical-report.pdf)**

每个头条数字对应的可复现证据包与图表源码随报告一同维护；公开快照见[研究页](https://argusbot.cn/zh/research.html)与[结果页](https://argusbot.cn/zh/results.html)。

```bibtex
@techreport{argus2026,
  title  = {Argus: A General-Purpose Agentic Runtime for Long-Horizon Reasoning},
  author = {{Argus Team}},
  year   = {2026},
  type   = {Technical Report},
  url    = {https://github.com/lbx154/Argus},
  note   = {Project page: https://argusbot.cn/}
}
```

---

## 参与贡献

欢迎提 issue 与 PR。

```bash
pip install -e '.[dev]'
pytest
```

请把改动放在正确的层：CLI 行为改 `argus_skill/apps/`，执行可靠性改 `argus_skill/engineer/`，验收标准改 `argus_skill/reviewer/`，调度改 `argus_skill/planner/` 与 `argus_skill/life/`。永远不要为了让 gate 变绿而手改生成的 review JSON 或证据产物；请修源头再重新生成。

---

## 许可证

以 [MIT 许可证](LICENSE)发布。

`@argusevolve/argus` npm beta 是纯二进制分发，适用其自身的单独条款。

---

<div align="center">
<sub><b>Argus</b> · <a href="https://argusbot.cn/zh.html">argusbot.cn</a> · <a href="technical_report/argus-technical-report.pdf">技术报告</a> · <a href="https://argusbot.cn/zh/results.html">实验结果</a></sub>
</div>
