<h1 align="center">Argus：自主研究生成与理解系统</h1>

<p align="center"><a href="README.md">English</a> · <strong>简体中文</strong></p>

> **每一次 run，都在拓展下一次研究的边界。**

<p align="center">
  <img src="technical_report/figures/master_spine.png" alt="Argus 技术主线：未知的分布外目标进入由 Manager、Planner、Engineer、Reviewer 驱动的持续密集智能运行时；经显式核验的工作通过证据门；证据门更新持久运行时状态——记忆、skill、工具、verifier、路由、评测；扩大后的运行时以更高的起点迎接下一个未知任务" width="100%">
</p>

Argus 是一个自主科研运行时，在长周期里让判断、执行与验证始终耦合。四个持久、
由模型驱动的角色在一个连续循环中维持**持续密集智能**；每一次 run 经显式
核验的**证据**都会更新持久的**运行时状态**——记忆、skill、工具、verifier、路由
与评测——而底层模型参数保持固定；扩大后的运行时随后以更高的起点迎接下一个分布外
目标。这条主线——持续密集智能、证据、运行时演化、不断拓展的边界——正是本项目被
构建与被度量的方式。

## 持续密集智能与长周期研究

强模型通常只在一次调用内保持连续，而长周期研究跨越数小时到数天。Argus 在持久项目状态上
运行四个模型角色：Manager 固定意图与 lifetime，Planner 调度，Engineer 构建和实验；任务、
vertical 或 Engineer 要求时，Reviewer 做独立审查。允许的低风险 bounded 工作可以由
Engineer 显式自审。

这种让判断、执行与验证持续耦合的机制称为**持续密集智能**。`rho_DI(T)` 只是对该设计目标的
概念性描述，不是可上报 benchmark，也不是普适优越性分数。

## 从工作到证据再到运行时演化

每次 run 都保存核验证据及完成来源：允许的低风险 bounded 工作可由 Engineer 自审；
vertical 策略、`stage_closing` / `review:required` 或 Engineer 主动请求会调用独立 Reviewer。
结果更新记忆、skill、工具、verifier、路由与评测：
`H(t+1) = U(H(t), trajectory, evidence)`。

所有权按组件划分。独立审查时由 **Reviewer 认证**未亲自产出的记忆与 skill；工具由
**operator 拥有**；verifier 由 **Planner 拥有**且 Reviewer **仅提供反馈**；路由由
Manager 提交，评测由 Planner 编写、scheduler 提交。

运行时能力演化**不依赖在线参数训练**，模型权重保持固定
（`theta_(t+1) = theta_t`）。这一设计也**不保证每次 run 都增加能力**：结果可能失败、
只留下负面发现，或随后被修订、归档。这里的 claim 仅限于证据门控、可复用的运行时状态，
不意味着能力单调增长。

## 来自前沿的证据

Argus 在 [argusbot.cn/results.html](https://argusbot.cn/results.html) 与
[argusbot.cn/research.html](https://argusbot.cn/research.html) 维护公开记录。比较首先
采用人类记录、人类作者基线或论文报告的最佳结果；机器可读快照已提交于
[`technical_report/evidence/website_results.json`](technical_report/evidence/website_results.json)
和
[`technical_report/evidence/paper_inventory.json`](technical_report/evidence/paper_inventory.json)。

| 赛道 | 协议 | Argus 结果 | 主要参考 | 证据层级 |
|---|---|---:|---|---|
| NVIDIA SOL-ExecBench | B200 · 101 个 kernel | Global #6 · 2× #1 · 7 top-3 | 公开排行榜 | 网站快照 |
| nanochat · B200 | 5 分钟 · 1×B200 · 426 次尝试 | **0.9636 BPB** | 人类 SOTA：0.9646 | artifact digest |
| nanochat · H100 | 5 分钟 · 1×H100 · 37 种机制 | **0.9855 BPB** | 人类 SOTA：0.9879 | 网站快照 |
| nanoGPT speedrun | 8×H100 · N=10 | **79.77 秒** | 同设备人类第 83 名：80.18 秒 | artifact digest |
| AARRI-Bench | 82 个研究实习任务 | **63/82 · 76.8%** | 论文报告最佳：68.3% | 网站快照 |
| Arbor · RUC NLPIR | Math-Reasoning Data | **28.0 gap** | Arbor：20.83 | 网站快照 |

每一行都是在其各自协议与单位下的有范围 claim；这些赛道度量不同的量，从不做跨赛道
归一化。其中两行——nanoGPT speedrun 与 nanochat B200——带有已提交的 artifact digest
记录：N=10 的 verifier line（`valid=true`、`p=0.004007`、`79.77±0.06 s`、`seal=ok`）
以及冻结 scorer、单 seed 的 `MEAN_VAL_BPB=0.963634`。本仓库保存它们的 logical
artifact ID 与 SHA-256，不保存 artifact 本体。其余四行为网站快照，并如实标注。每项
benchmark claim 都作为协议范围内的测量来处理，保留 benchmark 版本、硬件与软件环境、
baseline 定义、命令、退出状态、适用时的重复测量统计，以及支撑 claim 的 artifact hash。

另外，公开研究组合包含六个 program、**41 篇去重后的论文产物**：35 篇 manuscript 与
6 篇 draft，方向涵盖 LLM 认知偏差（9）、多模态与视觉语言模型（16）、LLM Agent
方法（5）、效率/压缩/解码（7）、世界模型（2）、状态追踪与可审计性（2）。这是仅与
人类作者文献比较的产物清单，不是录用数量；Argus 不声称这 41 篇中的任何一篇已被接收。

## Argus 如何让这个循环成真

运行时被组织为三个协作的平面：负责意图、规划、调度、预算与 daemon 生命周期的**控制
平面**；负责搜索、代码、实验与独立审查的**执行平面**；以及由持久状态构成的**证据
平面**（`events.jsonl`、`checkpoint.json`、journal、evidence bundle 与 figure
manifest）。四个由模型驱动的角色通过明确接口在这些平面上协作：

| 角色 · 平面 | 系统职责 | 决策边界 |
|---|---|---|
| **Manager** · 控制 | operator 意图的前门；决定 lifetime 与 vertical；独占 pipeline stage 迁移 | 其他角色可建议 stage 变更，但无权执行 |
| **Planner（L4）** · 控制 | 构建并修订工作 backlog；必要时排定认证任务 | 产出结构化任务与项目级规划裁决 |
| **Engineer（L1）** · 执行 | 用真实文件、工具、搜索与硬件执行一个有边界的回合 | 产出 artifact 并选择 `review=skip|required`；只有启用自审且任务不强制独立审查时才接受 `skip` |
| **Reviewer（L2）** · 执行 → 证据 | 在任务要求或 Engineer 请求时独立核查 artifact 与日志 | 返回 `done`、`continue` 或 `blocked`；stage-closing 与 vertical 强制审查路径必须经过 Reviewer |

append-only 事件流是规范时间线，operator 因此可以从一个公开数字追溯到它的 mission、
round、review verdict、命令记录与 artifact 集合，而不必依赖总结性文字。一个 mission
沿着持久生命周期推进——operator 请求被解释、规划为 backlog item、原子 claim、经
Engineer 自审或 Engineer–Reviewer 路径执行，最终返回完成、阻塞、暂停或继续规划——
受控重启后，只有当
持久化 identity 与当前 objective、vertical 和 lineage 一致时，daemon 才会续接原
campaign。运行时把可靠性作为一等问题处理：有界的 mission/每日/provider call/主机并发
预算；对 backend 失败使用有界重试与退避，而不是伪装成成功的兜底；一份由 Engineer 编辑、在 Reviewer 被调用时由其纠正的共享
`CHECKPOINT.md`，在 fresh role session 之间传递当前工作状态；以及在
证据与 artifact 进入审查前对凭据脱敏。当固定的已知输入分布会允许硬编码优化时，评测
输入可被随机化。这些机制只约束执行；它们从不给新颖性打分、不替 agent 选题、也不用
关键词推断完成——科研质量始终是一个由真实产物支撑的、结构化的 agent 判断。

## 快速开始

Argus 要求 Python 3.11+ 和至少一个受支持的 agent CLI。

GitHub Copilot 订阅用户先安装并登录官方 CLI：

```bash
npm install -g @github/copilot
copilot login
```

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv
. .venv/bin/activate
pip install -e .
argus-skill --setup  # PATH 中只有 Copilot 时自动选择并持久化 copilot backend
```

如果尚无机器策略，setup 向导会自动生成一份受信任的基础策略，并且绝不会覆盖 operator
已有的 prompt。机器有额外操作规则时，直接编辑生成的文件：

```bash
${EDITOR:-vi} ~/.argus-skill/special_prompts/10-house-rules.md
```

从目标项目目录启动一个持续运行的项目：

```bash
mkdir -p ~/research/world-models
cd ~/research/world-models
argus-skill --daemon --continuous \
  --objective "World Model for Agent Action Selection"
```

日常交互使用 `argus` TUI cockpit；运行状态可通过 `argus-skill --status`、`--watch`
与 `--follow` 查看。Argus 也可交给用户级 service manager 长期托管；受控替换会保留
campaign identity，绝不在升级时静默重规划正在执行的目标。

Argus 面向三种可互换的 agent CLI backend：

| Backend | 配置值 | 安装 | 鉴权 |
|---|---|---|---|
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot`（Node.js ≥ 22） | GitHub device authorization |
| OpenAI Codex CLI | `codex`（默认） | `npm install -g @openai/codex` | 见 [`docs/API_CONFIG.md`](docs/API_CONFIG.md) |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | 交互式登录 |

可设置 `ARGUS_SKILL_RUNNER_BACKEND`，也可直接在 cockpit 中切换 backend 与 model。

## 技术报告、局限与来源

完整的系统架构、角色接口、mission 状态机、证据方法、运行时演化形式化、六项公开成绩
与 41 篇论文组合见：

**[Argus: Autonomous Research Generation and Understanding System —
Technical Report 0.3](technical_report/argus-technical-report.pdf)**

LaTeX 源码位于 [`technical_report/`](technical_report/)，使用
`make -C technical_report clean all` 构建。

Argus 仍在快速开发，其保证是刻意有边界的。研究质量受底层模型、工具、数据与算力限制；
完成判定仍是可能犯错的模型判断：允许的低风险 bounded mission 可由 Engineer 显式自审，
要求或请求独立审查的路径则由 Reviewer 裁决；六项公开结果中有四项尚无 artifact digest 佐证，
另两项也只在本仓库保存外部项目 artifact 的 ID 与 hash，而非本体；每个 benchmark 的
完整性仍需按其协议单独设计；持续运行会产生真实的 GPU 与 provider 成本；当前证据系统
提供内容 hash 与 provenance manifest，不提供密码学结果签名。所有性能数字都应按其明确
协议理解，不能外推为普适能力保证。

项目 package metadata 声明 MIT License。它构建于
[skill-agent](https://github.com/lbx154/skill-agent)（skill matching 与蒸馏）与
[ArgusBot](https://github.com/waltstephen/ArgusBot)（Reviewer loop 与 CLI runner，
vendored provenance 与 license 位于
[`argus_skill/agent_cli/`](argus_skill/agent_cli/)）。
