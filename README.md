<h1 align="center">Argus</h1>

<p align="center"><strong>面向学术论文全流程的自主研究智能体系统</strong></p>
<p align="center"><em>Autonomous Research Generation & Understanding System</em></p>

<p align="center">
从选题到投稿，Argus 是一个 7×24 自主运行的学术研究 agent，<br>
能独立完成文献调研、实验设计、基准测试、结果分析、论文撰写和投稿审查全流程。
</p>

```text
 █████╗ ██████╗  ██████╗ ██╗   ██╗███████╗
██╔══██╗██╔══██╗██╔════╝ ██║   ██║██╔════╝
███████║██████╔╝██║  ███╗██║   ██║███████╗
██╔══██║██╔══██╗██║   ██║██║   ██║╚════██║
██║  ██║██║  ██║╚██████╔╝╚██████╔╝███████║
╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚══════╝
```

---

## 核心能力

Argus 不是一个"帮你润色论文"的工具——它是一个**完整的自主研究系统**：

- 🔍 **自主选题**：从 arXiv、Semantic Scholar、机器之心等多源搜索，发现真实研究空白
- 📐 **实验设计**：生成可执行的实验计划，含假设、基线、消融、预算
- 🧪 **自动实验**：在真实公开 benchmark 上运行全部条件（proposed + baselines + ablations）
- 📊 **结果分析**：从原始数据生成 claim-evidence 映射、统计检验、图表
- ✍️ **论文撰写**：ACL/EMNLP 格式的完整 LaTeX 论文，含 image-2 概念图生成
- 🔬 **多层审查**：分阶段 reviewer checklist + 最终 EMNLP peer review 模拟
- 📮 **投稿就绪**：结构检查 + 科学质量审查全部通过才停止

## 架构：三层 Agent

```
┌─────────────────────────────────────────────┐
│                  Planner                     │
│  读 PIPELINE_STATE + gate snapshot           │
│  决定下一个任务，分配给 Engineer              │
└──────────────┬──────────────────────────────┘
               │ 任务 + skill
               ▼
┌─────────────────────────────────────────────┐
│                 Engineer                     │
│  执行任务：搜论文、写代码、跑实验、写论文     │
│  工作目录和 Reviewer 共享                    │
└──────────────┬──────────────────────────────┘
               │ 输出 + check_commands 结果
               ▼
┌─────────────────────────────────────────────┐
│                 Reviewer                     │
│  按当前 stage 的 checklist 审查              │
│  done / continue(附具体修复指令) / blocked   │
└─────────────────────────────────────────────┘
```

三个 agent 都是 codex agent（gpt-5.4），Reviewer 有 shell 访问权，可以自己读文件、跑 validator。

## 8-Stage 研究 Pipeline

```
research → plan → benchmark → run → analysis → draft → review → submission
```

| Stage | Engineer 做什么 | Reviewer 审什么 | 严格度 |
|-------|----------------|----------------|--------|
| **research** | 文献搜索（arXiv + Semantic Scholar + 机器之心）、写 brief | 问题清晰度、文献覆盖、趋势转化 | 中等 |
| **plan** | 实验计划、下载参考代码、反平庸筛选 | 方法竞争力、基线强度、代码调研、可行性 | 中等 |
| **benchmark** | 准备 benchmark 数据、验证 gold answer | 来源真实性、覆盖度≥3族、可复现 | 中等 |
| **run** | 跑全部条件、复现至少1个强基线 | 统计显著性、ablation 公平性、效果量 | 中等 |
| **analysis** | 生成结果报告、claim 映射、图表 | 数字一致性、claim 溯源、图表质量 | 中等 |
| **draft** | 写 LaTeX、生成 image-2 概念图、编译 PDF | 结构完整性（宽松，能推进就行） | 宽松 |
| **review** | 学术语言审查、排版审查、基础设施泄露检查 | layout/语言/引用/页数/infra泄露 | 较严 |
| **submission** | 最终 gate 检查、submission assurance | **完整 EMNLP peer review**（score 5+ 才过） | 最严 |

### 反平庸机制

Argus 不会生产"灌水论文"：

- **Anti-mediocrity gate**：5 条拒绝标准（minor variant / 无理由组合 / manufactured gap / <2% improvement / trivial baseline 能达到）
- **必须下载参考代码**：对 top-3 相关论文 `git clone` 并读代码，不是只读摘要
- **强基线要求**：至少复现 1 个已发表方法作为 baseline（不是只比 random）
- **真实 benchmark**：≥3 个独立 benchmark 家族，禁止合成数据作为主证据

## 31 个内置 Skill

Skills 按角色分两个目录：

### Engineer Skills（25 个）

| 类别 | Skills |
|------|--------|
| 编排 | `auto-research-pipeline` (主入口), `emnlp-paper-skill-router` |
| 文献 | `arxiv-paper-search`, `semantic-scholar-search`, `research-ideation` |
| 规划 | `research-brief-to-experiment-plan`, `ablation-planner`, `training-infrastructure-guide` |
| 实验 | `agent-research-benchmark-runner`, `experiment-audit` |
| 分析 | `research-results-analysis-and-figures`, `result-to-claim`, `claims-evidence-audit` |
| 写作 | `emnlp-paper-drafting`, `paper-exemplar-pdf-learning`, `paper-illustration-image2`, `paper-framework-figure-studio-pro` |
| 审查 | `emnlp-format-preflight`, `emnlp-paper-infrastructure-review`, `emnlp-academic-language-review`, `paper-review-revision-loop` |
| 提交 | `research-submission-assurance-gate` |
| 角色 | `argus-engineer-role`, `argus-planner-role` |

### Reviewer Skills（6 个）

| Skill | 用于 Stage |
|-------|-----------|
| `experiment-plan-review` | plan |
| `experiment-results-review` | run |
| `academic-paper-peer-review-benchmark` | draft (宽松) / submission (严格) |
| `emnlp-academic-language-review` | review |
| `argus-reviewer-role` | 全局 |
| `reviewer-engineer-handoff` | 全局 |

## 快速开始

### 1. 前置依赖

**Python ≥ 3.11** + **Codex CLI**（OpenAI 官方非交互推理 CLI）：

```bash
# Codex CLI 是 npm 全局包
npm install -g @openai/codex
codex --version   # 验证可用
```

> 注意：ArgusBot 的 `codex_autoloop` 监督循环模块已经**内置**在本仓库里
> （见 `argus_skill/codex_autoloop/`），**不需要**再单独安装 ArgusBot
> 或使用任何 `[codex]` extra —— `pip install argus-skill`（下一步）就已经包含 codex 后端。

### 2. 安装

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

### 3. 初始配置（交互式向导）

```bash
argus-skill --setup
```

向导会依次引导你配置：

1. **三个 Agent 的 API**（Planner / Engineer / Reviewer）— 支持共享或独立配置
2. **实验 API 授权** — 询问是否允许实验中调用配置好的模型 API（例如当 LLM
   reward 模型 / judge、合成数据生成），而不仅用于 agent 自身推理。开启后写一个
   operator special prompt；凭证运行时从环境变量读取，绝不写进代码/论文/日志。
3. **GPU 资源分配** — 自动检测所有 GPU，选择分配给 Argus 的设备
4. **GPU Keep-Alive（防回收）** — 询问这台机器是否会回收空闲 GPU、需要占用几张。
   托管/云主机常会回收空闲 GPU，导致长论文跑被回收、进度丢失。开启后 Argus
   会用一个低占空比的 keep-alive 加载器在安静期（只调 API/写作）占住显卡；真正
   跑实验时由 `gpu_lease` 自动让位、跑完再 re-park。向导会写好
   `~/.argus-skill/capabilities/gpu_keepalive.json` 和一个 operator special
   prompt（同时满足 daemon 启动门禁所需的 special prompt）。
5. **Codex CLI 配置** — 用你刚输入的同一把 API key/base_url 自动写好
   `~/.codex/config.toml` 和 `~/.codex/auth.json`（已存在的文件会备份成
   `*.bak` 后才覆盖；不想覆盖直接回车）

```
═══════════════════════════════════════════════════════════════
  Argus — Autonomous Research Generation & Understanding System
═══════════════════════════════════════════════════════════════

  Step 1: API Configuration
  Do all 3 agents share the same API endpoint? (y/n) [y]: y
  API Base URL: https://api.openai.com/v1/
  API Key: sk-...
  Planner model [gpt-5.4]:
  Engineer model [gpt-5.4]:
  Reviewer model [gpt-5.4]:

  Step 1b: Experiment API access
  ── Experiment API access ──
  Allow API use inside experiments (reward/judge/etc.)? (y/N) [n]: y
  ✓ Operator prompt   → ~/.argus-skill/special_prompts/30-experiment-api.md

  Step 2: GPU Resources
  Available GPUs:
    [0] NVIDIA B200 (179 GB)
    ...
    [7] NVIDIA B200 (179 GB)
  Devices to allocate (e.g. 6 or 0,1,2) [6]: 6
  ✓ Allocated: device(s) 6 (1 GPU, 179 GB total)

  ── GPU Keep-Alive (anti-reclaim) ──
  Does this machine reclaim idle GPUs? Enable keep-alive? (y/N) [n]: y
  How many GPUs to hold (of allocated [6]) [1]: 1
  VRAM % to hold per GPU [10]:
  Best-effort GPU utilization % [20]:
  Python interpreter for the loader (needs torch+CUDA) [/opt/conda/envs/ptca/bin/python]:
  ✓ Keep-alive config → ~/.argus-skill/capabilities/gpu_keepalive.json
  ✓ Operator prompt   → ~/.argus-skill/special_prompts/20-gpu-keepalive.md
  Start the keep-alive now (hold the cards)? (y/N) [n]: y
  ✓ Keep-alive started (pid 12345).

  Step 3: Codex CLI Configuration
  ✓ codex config → /root/.codex/config.toml
  ✓ codex auth   → /root/.codex/auth.json

  ✓ Setup complete!
```

> Keep-alive 细节：加载器是独立脚本 `argus_skill/tools/gpu_load.py`（仅依赖
> `torch`，与 Argus 框架解耦，可用单独的 torch 环境解释器运行）。它把
> `--gpus` 当作**物理 GPU id**，在 import torch 前自行设好 `CUDA_VISIBLE_DEVICES`。
> 真正的 GPU 任务务必通过 `python -m argus_skill.tools.gpu_lease run -- <cmd>`
> 运行，切勿手动 kill 加载器。`--util` 只是尽力而为的活动目标，若仍被回收可调高
> `--util`/`--mem` 或调低 `--interval`。

### 3. 创建研究项目

```bash
python -m argus_skill.tools.new_auto_research_project \
  --parent ~/research \
  --objective "World Model for Agent Action Selection"
```

系统自动创建项目目录、导出内置 skill、初始化 PIPELINE_STATE，并启动 7×24 daemon
（默认即启动；如只想建目录不启 daemon 加 `--no-start`）。

常用参数（完整列表见 `python -m argus_skill.tools.new_auto_research_project --help`）：

| 参数 | 说明 |
|------|------|
| `version` | 位置参数，例如 `15` 或 `v15`；省略则自动选下一个可用版本号 |
| `--parent` | 版本化 workspace 的父目录 |
| `--project-dir` | 直接指定项目目录，跳过 `parent + version` 命名 |
| `--objective` | 项目主目标（写入 `AGENTS.md` 和 daemon objective） |
| `--non-goals` | 显式 non-goals |
| `--compute-budget` | 项目特定的算力 / API 预算和停机条件 |
| `--template` | 自定义 AGENTS 模板，默认使用内置 |
| `--domain` | 仅加载指定 domain 的 skill 包（当前内置 domain 注册表为空，等同于不传） |
| `--no-start` | 创建项目但不启动 daemon |
| `--no-git` | 跳过 `git init/add/commit` |
| `--dry-run` | 仅打印将要创建的路径/版本号，不落盘 |

### 4. 监控进度

```bash
argus-skill --status    # 当前状态
argus-skill --watch     # 实时 cockpit
argus-skill --follow    # 事件流

# 或通过 Telegram
export ARGUS_SKILL_TELEGRAM_BOT_TOKEN="123:abc"
export ARGUS_SKILL_TELEGRAM_CHAT_ID="123456789"
```

## Daemon：7×24 自主运行

> **启动前置（硬门禁）**：进 cockpit 或启动 daemon 前，必须同时配好两样东西，否则 `argus-skill` 直接 `exit 2` 并打印指引：
> 1. **mission objective** —— 用 `--continuous --objective "<目标>"` 提供（会持久化到 `continuous.json`，之后再启动可省略）；
> 2. **至少一个 special prompt** —— 在 `~/.argus-skill/special_prompts/` 放一个 `*.md`（机器/部署的操作规则，比如 GPU、路径、调度），文件须属主本人且**不可 group/world-writable**：
>    ```bash
>    mkdir -p ~/.argus-skill/special_prompts
>    printf 'Operational house rules for this box.\n' > ~/.argus-skill/special_prompts/10-house-rules.md
>    chmod 0644 ~/.argus-skill/special_prompts/10-house-rules.md
>    ```
> 这取代了一切“从 objective 文本猜任务类型”的隐式逻辑：agent 必须被显式告知它的目标和这台机器的规则。只读 / admin 命令（`--status`、`--watch`、`--skill-stats`…）不受门禁限制。

```bash
# 启动（默认 open-ended：project_done 后继续生成新工作，永续运行）
argus-skill --daemon --continuous \
  --objective "Complete the EMNLP paper on world models for agent action selection"

# 有界一次性目标：planner 认证 project_done 后硬停
argus-skill --daemon --continuous --bounded \
  --objective "Add a unit-test suite for the data loader"

# 管理
argus-skill --status          # 查看状态
argus-skill --daemon-stop     # 优雅停止
argus-skill --daemon-runbook  # 升级清单
```

Daemon 特性：
- **POSIX double-fork** — 关闭终端、断开 SSH 不影响运行
- **自动 gate 检查** — 全部检查通过后自动停止，不浪费 API
- **预算控制** — 单任务/每日预算上限
- **Telegram 远程控制** — 发消息 nudge 当前任务或添加新工作

### systemd 部署

```ini
[Unit]
Description=Argus Auto-Research Agent
After=network.target

[Service]
Type=simple
User=researcher
Environment=ARGUS_SKILL_LIFE_BACKEND=codex
ExecStart=/usr/local/bin/argus-skill --daemon-fg
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ARGUS_SKILL_LIFE_BACKEND` | 后端：`codex`（生产） / `memory`（测试） | `codex` |
| `ARGUS_SKILL_RUNNER_BIN` | codex CLI 路径 | `$PATH` |
| `ARGUS_SKILL_PER_MISSION_CAP_USD` | 单任务预算上限 | `30` |
| `ARGUS_SKILL_DAILY_CAP_USD` | 每日预算上限 | `180` |
| `ARGUS_SKILL_TELEGRAM_BOT_TOKEN` | Telegram bot token | — |
| `ARGUS_SKILL_TELEGRAM_CHAT_ID` | Telegram chat ID | — |

## 项目结构

```
argus_skill/
├── builtin_skills/
│   ├── engineer/          # 25 个 engineer skills
│   ├── reviewer/          # 6 个 reviewer skills
│   └── *.md               # 项目模板
├── tools/
│   ├── stage_check.py     # 分阶段 shell 检查 + reviewer checklist
│   ├── image_tool.py      # image-2 概念图生成
│   ├── subagent.py        # 子 agent 系统
│   └── new_auto_research_project.py  # 项目创建
├── skills/
│   ├── pipeline_contracts.py  # manifest/freshness/policy artifact 构建-修复工具 (质量 gate 由 reviewer checklist 决定)
│   └── store.py           # skill 匹配器
├── engineer/
│   ├── runner.py           # SupervisedEngineer 轮次循环
│   ├── reviewer.py         # Reviewer agent
│   └── checks.py           # check_commands 执行器
├── life/
│   ├── supervisor.py       # Mission 编排 + Planner
│   └── memory.py           # 持久化状态
├── daemon/
│   └── life_worker.py      # 7×24 daemon worker
└── loop.py                 # SkillLoop — matcher × engineer × reviewer
```

## 测试

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).

## Provenance

- [skill-agent](https://github.com/lbx154/skill-agent): skill 匹配、distiller
- [ArgusBot](https://github.com/waltstephen/ArgusBot) (MIT)：reviewer 循环、codex runner —— `codex_autoloop` 模块已 **vendored** 到 `argus_skill/codex_autoloop/`（含上游 LICENSE 与 `_VENDORED.md` 注明 commit/sha；详见该目录）
- 新代码: auto-research pipeline, stage_check, builtin skills, image-2 集成
