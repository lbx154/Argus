<h1 align="center">Argus：自主研究生成与理解系统</h1>

<p align="center"><a href="README.md">English</a> · <strong>简体中文</strong></p>

<p align="center">
  <strong>面向长周期实证研究的持久化基础设施：规划、执行、验证与持续运行。</strong>
</p>

<p align="center">
  <img src="technical_report/figures/argus_architecture.png" alt="Argus 系统架构：operator 目标进入持久化研究运行时，Manager、Planner、Engineer 与 Reviewer 协同执行并积累证据" width="100%">
</p>

## 系统概览

Argus 是一个自主科研运行时。它把高层研究目标转化为持续的文献调研、代码实现、
实验执行、结果评估和技术写作，并能在真实工具与硬件上 7×24 运行。系统状态落盘，
进程重启后可以恢复；operator 可通过 TUI/Web cockpit 查看当前任务、角色活动、
证据、预算和失败原因。

系统由四个职责明确的角色组成：

- **Manager** 理解 operator 意图，决定任务 lifetime 与 vertical，并独占 pipeline
  stage 的迁移权；
- **Planner（L4）** 把目标组织成可执行 backlog，并根据新证据继续规划；
- **Engineer（L1）** 搜索文献、修改代码、运行实验并产生研究产物；
- **Reviewer（L2）** 独立核查真实产物，是 mission 是否完成的唯一裁决者。

运行时负责调度、状态管理、资源治理、故障恢复和证据溯源，但不替这些角色判断
idea 是否新颖、基线是否充分或研究是否应该结束。主要产品形态是 KernelBench、
nanochat、nanoGPT speedrun 等公开 benchmark 上的实证研究；可选的 `research`
vertical 使用同一套运行时执行从 idea 到投稿材料的完整流程。

## 公开结果

公开结果发布在
[argusbot.cn/results.html](https://argusbot.cn/results.html) 与
[argusbot.cn/research.html](https://argusbot.cn/research.html)。比较首先采用人类记录、
人类作者基线或论文报告的最佳结果。机器可读快照位于
[`technical_report/evidence/website_results.json`](technical_report/evidence/website_results.json)
和
[`technical_report/evidence/paper_inventory.json`](technical_report/evidence/paper_inventory.json)。

| 赛道 | 协议 | Argus 结果 | 主要参考 | 证据层级 |
|---|---|---:|---|---|
| NVIDIA SOL-ExecBench | B200 · 101 个 kernel | 全球第 6 · 2 项第 1 · 7 项前三 | 公开排行榜 | 网站快照 |
| nanochat · B200 | 5 分钟 · 1×B200 · 426 次尝试 | **0.9636 BPB** | 人类 SOTA：0.9646 | 本地 artifact |
| nanochat · H100 | 5 分钟 · 1×H100 · 37 种机制 | **0.9855 BPB** | 人类 SOTA：0.9879 | 网站快照 |
| nanoGPT speedrun | 8×H100 · N=10 | **79.77 秒** | 同设备人类第 83 名：80.18 秒 | 本地 artifact |
| AARRI-Bench | 82 个研究实习任务 | **63/82 · 76.8%** | 论文报告最佳：68.3% | 网站快照 |
| Arbor · RUC NLPIR | Math-Reasoning Data | **28.0 gap** | Arbor：20.83 | 网站快照 |

公开研究组合包含六个 program、**41 篇去重后的论文产物**：35 篇 manuscript 与
6 篇 draft。方向包括 LLM 认知偏差（9）、多模态与视觉语言模型（16）、LLM Agent
方法（5）、效率/压缩/解码（7）、世界模型（2）、状态追踪与可审计性（2）。这是产物
清单，不是录用数量；Argus 不声称这些论文均已被接收。

目前两项结果有仓库内佐证。nanoGPT 的 79.77 秒来自 N=10 的 verifier-certified
测量（`valid=true`、`p=0.004007`、`79.77±0.06 s`、`seal=ok`）；nanochat B200
与冻结 scorer、单 seed 的 `MEAN_VAL_BPB=0.963634` 一致。其余四项明确标注为网站
快照，不包装成本地完成的复现。

## 三平面系统架构

| 平面 | 职责 | 主要组件 |
|---|---|---|
| **控制平面** | 意图解释、任务规划、stage 迁移、调度、预算与 daemon 生命周期 | Manager、Planner、`LifeSupervisor`、backlog、项目配置 |
| **执行平面** | 文献搜索、代码、实验、写作、独立审查与后台任务 | Engineer、Reviewer、agent CLI backend、工具与 GPU 能力 |
| **证据平面** | 持久化状态、类型化事件、artifact、用量核算、测量记录与发布溯源 | `events.jsonl`、`CHECKPOINT.md`、journal、evidence bundle、figure manifest |

Manager 是唯一前门和 stage 权威；Planner 只生成结构化任务与规划裁决；Engineer
执行一个有边界的真实工作回合；Reviewer 返回 `done`、`continue` 或 `blocked`，
系统没有第二个硬编码完成裁决器。历史上的 L3 critic 已移除，避免多个评价层重复
修改同一研究结论。可选 Curator 只在 team/subagent 模式维护 skill 池。

## Mission 运行时与状态

一个 mission 从 operator 请求开始，经 Manager 解释、Planner 生成 backlog、任务原子
claim、Engineer–Reviewer 多轮执行，最终进入完成、阻塞、暂停或继续规划状态。受控
重启后，只有持久化 identity 与 objective、vertical 和 lineage 一致时，daemon 才会
续接原 campaign。

关键状态不依赖 provider 对话。`events.jsonl` 是 append-only 的规范时间线；backlog、
journal、daemon status、项目配置和研究 artifact 都可以直接检查。Engineer 与 Reviewer
使用各自独立、可 resume 的短窗口 provider session。默认同一 role 的 thread 在连续
3 轮后滚动；若上一调用报告的输入达到 150 万 token，也会开启新 session。
`CHECKPOINT.md` 负责跨 session 保存当前目标、已验证工作、失败路线、blocker、证据与
下一步。

Skill 系统把真实任务中形成的可复用过程沉淀为项目层或共享层能力，并支持版本化、
更新、拆分、合并、归档和退役。独立的 evidence-cited wiki 保存稳定知识，避免把完整
事件历史反复注入模型上下文。

## 可靠性与资源治理

Argus 把长期运行中的执行漂移作为系统问题处理：

- Reviewer 对每轮标注 `decision`、`evidence`、`setup_only`、
  `artifact_sync_only` 或 `none`；连续两轮没有决策/证据增量时结束当前 mission。
- 1,800 秒 decision-progress 预算只在安全回合边界生效，不会粗暴中断仍在工作的
  单次模型调用；受独立 supervisor 管理的后台实验会暂停这只时钟。
- effective-progress 与 runner-idle 超时用于识别失去响应的子进程；backend 错误采用
  有界重试与退避，不会被伪装成成功结果。
- 长训练与评测使用持久化后台状态和独立 monitor cadence，前台无需反复轮询。
- mission、每日、provider call 和主机并发预算在调用前 reservation，结束后按实际
  usage 对账。
- 凭据在进入事件流、持久化日志和下游 Reviewer 上下文前进行脱敏。

这些机制只约束执行与资源，不用关键词判断科研质量。idea、novelty、baseline 与完成
状态仍由读取真实产物的 agent 负责。

## 测量与证据

每项 benchmark claim 都必须和协议一起解释：benchmark 版本、硬件/软件环境、baseline
定义、运行命令、退出状态、重复测量统计以及支撑 claim 的 artifact hash。若固定评测
输入会允许针对已知分布硬编码，评测应引入随机化。可复现的负结果和正式 NO-GO 同样
是有效研究产出。

当前事件目录包含 11 个类别、107 种事件和 75 个 payload schema。实时 cockpit 与后续
审计使用同一条类型化事件流，因此 operator 可以从公开数字追溯到 mission、round、
Reviewer verdict、命令记录和 artifact，而不是只依赖总结性文字。

## Operator Workbench 与部署

TUI 和 Web cockpit 是同一份持久化项目状态的操作视图，展示角色活动、backlog、
当前 stage、预算、事件、待回答问题以及每轮 artifact。实时更新与历史审计读取同一条
事件流，不维护第二套 UI 真相。operator 可以提交任务、回答 blocker、查看 transcript、
nudge 或 abort mission，并在干净的 mission 边界 drain daemon。

Argus 可交互运行、作为 detached project daemon 运行，也可交给用户级 service manager
长期托管。受控替换只在 campaign identity 兼容时恢复，不会在升级时静默重规划正在执行
的目标。配置 token 后，Web command surface 和 live stream 需要鉴权；日常项目检查仍可
通过只读 endpoint 完成。provider 凭据和机器策略保存在本地配置中，不复制进研究产物。

## 快速开始

Argus 要求 Python 3.11+ 和至少一个受支持的 agent CLI。

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv
. .venv/bin/activate
pip install -e .
argus-skill --setup
```

启动 daemon 前先配置至少一份受信任的机器策略：

```bash
mkdir -p ~/.argus-skill/special_prompts
printf 'Operational policy for this machine.\n' \
  > ~/.argus-skill/special_prompts/10-machine-policy.md
chmod 0644 ~/.argus-skill/special_prompts/10-machine-policy.md
```

从目标项目目录启动：

```bash
mkdir -p ~/research/world-models
cd ~/research/world-models
argus-skill --daemon --continuous \
  --objective "World Model for Agent Action Selection"
```

日常交互使用 `argus` TUI cockpit；运行状态可通过 `argus-skill --status`、
`--watch` 与 `--follow` 查看。

## 支持的 Backend

| Backend | 配置值 | 安装 | 鉴权 |
|---|---|---|---|
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot`（Node.js ≥ 22） | GitHub device authorization |
| OpenAI Codex CLI | `codex`（默认） | `npm install -g @openai/codex` | 见 [`docs/API_CONFIG.md`](docs/API_CONFIG.md) |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | 交互式登录 |

可设置 `ARGUS_SKILL_RUNNER_BACKEND`，也可直接在 cockpit 中切换 backend 与 model。

## 技术报告

完整的系统架构、角色接口、mission 状态机、部署模型、证据方法、六项公开成绩和
41 篇论文组合见：

**[Argus: Autonomous Research Generation and Understanding System —
Technical Report 0.2](technical_report/argus-technical-report.pdf)**

LaTeX 源码位于 [`technical_report/`](technical_report/)，使用
`make -C technical_report clean all` 构建。

## 局限与项目状态

Argus 仍在快速开发。研究质量受底层模型、工具、数据和算力限制；Reviewer 是单一且
可能犯错的完成权威；六项公开结果中有四项尚无仓库内复现 artifact；持续运行会产生
真实的 GPU 与 provider 成本；每个 benchmark 的完整性仍需按其协议单独设计。当前
证据系统提供内容 hash 与 provenance manifest，不提供密码学结果签名。

所有性能数字都应按其明确协议理解，不能外推为普适能力保证。

## License 与来源

项目 package metadata 声明 MIT License。主要来源包括：

- [skill-agent](https://github.com/lbx154/skill-agent)：skill matching 与蒸馏；
- [ArgusBot](https://github.com/waltstephen/ArgusBot)：Reviewer loop 与 CLI runner，
  vendored provenance 和 license 位于
  [`argus_skill/agent_cli/`](argus_skill/agent_cli/)。
