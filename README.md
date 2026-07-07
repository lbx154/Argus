<h1 align="center">Argus</h1>

<p align="center"><strong>一个完全独立于人类、自己做科研的 research assistant</strong></p>
<p align="center"><em>Autonomous Research Generation & Understanding System</em></p>

<p align="center">
给它一个目标和这台机器的规则，剩下的它自己来：<br>
在一个真实的公开 benchmark 上（nanochat / nanogpt-speedrun / KernelBench / …），<br>
选题、设计实验、在真机上跑、分析、改进——把指标推到目标，由 reviewer 判定做完没。<br>
没有人在回路里逐步审批。
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

## 我们在做什么

Argus 不是一个"帮你润色论文"的工具，也不是一条把 prompt 串起来的流水线。它是一个 **7×24 自主运行的 research agent**：你只需要告诉它**做什么**（objective）和**这台机器的规则**（special prompt——GPU、路径、调度约束），它就独立地在一个真实公开 benchmark 上把指标从基线推到目标——而且做科研所需的每一个**判断**都是 agent 自己下的，不是 harness 替它下的。完成与否由 reviewer 判定，没有硬编码的完成门。

> 活的产品是 **benchmark 复现 agent**（nanochat / nanogpt-speedrun / KernelBench 等 metric vertical）。"从 idea 写到投稿"的论文流水线是一个**可选模式**（`research` vertical），不是默认身份。

## 设计哲学（先读这一节）

整个系统建立在三条核心理念上。

**第一条：**

> **harness 没有 agent 自己聪明。**

所以我们严格区分两类东西：

| | 谁负责 | 例子 |
|---|---|---|
| **科研判断** | **Agent**（engineer / reviewer / planner） | 这个 idea 平不平庸？这段过往工作相不相关？实验做完了没？基线够不够强？该投了吗？ |
| **领域无关的管道** | **Harness** | 预算/限速、磁盘持久化与记忆、调度与 daemon、结构化 I/O、防造假的诚信护栏 |

**Harness 只做后者，而且只做后者。** 它是一根又粗又笨的管子，把任务喂给 agent、把产物存下来、按预算和节奏调度——它**不**用关键词/正则去猜"这是不是论文任务""这个目标是不是要永续跑""哪段记忆相关""这篇 idea 够不够格"。每一次"用词面去二次判断 agent"的诱惑，我们都把它删掉，换成**结构化信号**或**交还给 agent**。因为只要 harness 开始替 agent 做科研判断，它就成了天花板——而它远没有 agent 聪明。

这条哲学的几个直接后果：

- **没有关键词分类。** operator 的自由文本是 chat 还是 task，由一次模型调用判断，不是 60 字符上限 + 中英文正则。任务类型（论文/有界/永续）由**显式参数和结构化 tag** 决定，不从 objective 文本里猜。
- **没有单独的 validator / 硬编码完成门。** "项目做完了没"是 **L2 reviewer 对照 stage checklist 的裁决**，不是 harness 跑一串正则去判定。退役的 EMNLP validator 已删除——reviewer 是唯一的事实来源。
- **"反平庸"是 reviewer 的判断，不是 harness 的规则。** checklist 里写的是"至少显式否决一个平庸/已有的 idea 并给出理由"这类**要求 reviewer 去核**的陈述，harness 不去数 improvement 百分比、不去匹配关键词。
- **记忆是纯 recency 的中立管道。** 注入给 agent 的"过往工作"上下文按时间倒序给最近 N 条（按项目隔离、标注 non-authoritative），**不**用关键词 Jaccard 替 agent 猜"哪条相关"——相关性是 agent 读完自己判断的。
- **代码/任务产物和 Argus 内部状态必须分开。** Agent 可以在任务需要的代码工作目录里读代码、改代码、跑测试，也可以把用户要的报告、实验结果、benchmark output 写到目标项目或用户指定目录；但 `events.jsonl`、`backlog.jsonl`、`PIPELINE_STATE.json`、`CHECKLISTS.json`、`DOMAINS/` 这类 harness 调度状态必须写到当前 session 的 artifact root（`~/.argus-skill/projects/<session>/`），不能污染 repo 里的 `research/`。判断标准很简单：**用户想要的结果放项目里；Argus 为了调度自己写的内部状态放 session 里。**
- **唯一正当的"硬规则"是防造假护栏。** 必须用真实公开 benchmark、不许重复行灌水、要留审计包——这些约束的是**作弊**，不是科研选择，所以它们留在 harness 里是合理的。
- **入口必须显式配置。** 启动 daemon / 进 cockpit 前，必须同时给出 (1) mission objective 和 (2) 至少一个受信任的 special prompt。缺任何一个直接 `exit 2`——我们绝不让 agent 在"不知道目标、不知道机器规则"的情况下空跑或靠猜。

**第二条：**

> **过程数据 比 结果数据 值钱。一个能力较弱的 model，只要参考一段高质量的"过程"，就能在同一个任务上逼近强 model 的惊艳表现。**

强 model 的优势，很大一部分不在"它知道答案"，而在"它知道怎么一步步逼近答案"——遇到反常怎么定位、卡住了去哪查、怎么把别的领域的 idea 迁过来、什么时候该重写 mechanism 而不是继续调参。这段**推理轨迹（过程数据）是可以被抽出来、被复用的**。所以 argus 把"过程"当一等公民：

- **Skill = 蒸馏过的过程数据。** 一条 kernel 优化 skill 写的不是"答案是 0.02ms"，而是"先算 roofline 定瓶颈 → memory-bound 就砍 DRAM 流量 → 去 CUTLASS/Triton 找 mechanism → 用 online 算法把两遍 stats 压成一遍"。弱 engineer model 读到这条，就能像 kernel 专家一样**推理**，而不是靠记答案。
- **轨迹被记录、被回放。** 完整的 engineer/reviewer 回合（怎么诊断、怎么改、为什么改、哪次失败教会了什么）是资产，不是噪声——它们沉淀进 skill、wiki、memory，下一个任务、下一个更弱的 model 都能站在上面。
- **诚实是过程的一部分。** 失败的尝试、被官方 scorer 打回的"假赢"、走过的弯路，都如实记进过程数据——因为"哪条路不通"和"哪条路通"一样值钱。

推论：harness 的另一项正当职责，是**忠实地捕获、持久化、回放高质量过程**（skill 蒸馏、轨迹日志、wiki/memory）。它**不评判**过程好不好（那是 agent 的判断），但它必须把过程完整存下来，让一个更弱的 model 也能复用一段专家级的推理，做出令人惊讶的结果。

**第三条：**

> **别把 agent 想成无所不知 —— 它必须自己进化。** 一个 agent 的内置知识永远撑不起真实的难题；真正的能力来自它会**自己上网搜、自己总结经验、自己自进化**。

别闭门造车，别把 model 想得那么聪明。再强的 model，它脑子里那点训练时的知识也支撑不了一个真正难的问题——B200 上某个 kernel 今天的 SOTA 写法、某个库的最新 API、某篇上周的 arXiv，它**不可能凭空知道**。所以真正能干活的 agent，第一反应不是硬凑，而是：

- **先去搜，站在巨人肩上。** 优化一个 kernel，先 `web_search` 这个 op 在 B200/Hopper 上的 SOTA 怎么写（CUTLASS / FlashAttention / FlashInfer / 最新 arXiv / NVIDIA blog / GPU MODE），拿到已知最好的 mechanism 再动手——而不是从零瞎试。**联网调研是强制的 grounding，不是可选项。**
- **自进化 = 搜 + 总结 + 沉淀的闭环。** 这一轮搜来的资料、自己跑出来的过程数据（踩的坑、走通的路），蒸馏成下一轮能用的 skill；下个 mission（哪怕是更弱的 model）继承它，再搜、再跑、再沉淀——**每一轮都比上一轮多知道一点，系统自己越跑越强。**

推论：harness 必须给 agent **真·联网能力**（web_search 作为强制研究步骤）+ **把经验自动蒸馏成 skill** 的管道，让"搜→学→沉淀"这个自进化闭环转起来。它不替 agent 判断搜什么、学什么（那是 agent 的判断），但它必须保证 agent **能**搜、**会**沉淀。

三条理念是一体的：**第一条**——判断交给 agent；**第二条**——把 agent 的优质判断过程沉淀下来，让弱者也能复用；**第三条**——agent 必须靠自己上网搜 + 总结经验持续自进化，因为没有任何内置知识能一劳永逸。

## 架构：三层 Agent + 笨管道

```
        ┌──────────── Harness（领域无关的管道）─────────────┐
        │  预算/限速 · 持久化&记忆 · 调度&daemon ·           │
        │  结构化 I/O · 防造假诚信护栏                        │
        └───────────────────────┬────────────────────────────┘
                                 │ 喂任务 / 存产物 / 按预算调度
   ┌─────────────────────────────┼─────────────────────────────┐
   ▼                             ▼                             ▼
┌─────────┐   任务+skill   ┌──────────┐   产物+check   ┌──────────┐
│ Planner │──────────────▶│ Engineer │──────────────▶│ Reviewer │
│  (L4)   │               │   (L1)   │               │   (L2)   │
│ 排下一  │◀──────────────│ 搜文献/  │◀──────────────│ 对照 stage│
│ 个任务  │  done 后续派   │ 写代码/  │ done/continue/ │ checklist │
└─────────┘               │ 跑实验/  │ blocked        │ 裁决      │
                          │ 写论文   │ (+具体修复指令) └──────────┘
                          └──────────┘
```

- 三个 agent 都是 codex agent（默认 `gpt-5.4`）。Reviewer 有 shell 访问权，能自己读文件、跑检查，不依赖 harness 替它读。
- **Planner（L4）** 在 continuous 模式下 backlog 空了就排新任务；project 还差认证时把"完成"裁决改派成认证任务。
- **Engineer（L1）** 单轮执行：搜论文、写代码、跑实验、写 LaTeX；工作目录和 reviewer 共享。
- **Reviewer（L2）** 按当前 stage 的 checklist 审查，给 `done` / `continue`（附具体修复指令）/ `blocked`。**它是项目是否完成的唯一事实来源。**
- 历史上的 L3 critic 逐轮打磨层已移除——验收完全交给 L2 reviewer。

## 8-Stage 研究 Pipeline

```
research → plan → benchmark → run → analysis → draft → review → submission
```

每个 stage 的产物由 engineer 产出，由 reviewer 对照该 stage 的 checklist 裁决是否推进。下表的"reviewer 关注点"是 checklist 的**陈述**——由 reviewer agent 去核，**不是** harness 的正则：

| Stage | Engineer 做什么 | Reviewer 对照 checklist 核什么 |
|-------|----------------|--------------------------------|
| **research** | 文献搜索（arXiv + Semantic Scholar + 机器之心）、写 brief、写 idea 否决日志 | 问题清晰度、文献覆盖、是否显式否决了平庸 idea、GO/NO-GO |
| **plan** | 实验计划、下载参考代码并读、设计消融 | 方法竞争力、基线强度、代码确实读了、可行性 |
| **benchmark** | 准备真实 benchmark 数据、验证 gold answer | 来源真实性、覆盖度、可复现 |
| **run** | 跑全部条件、复现至少一个强基线 | 统计显著性、ablation 公平性、效果量 |
| **analysis** | 结果报告、claim→evidence 映射、图表 | 数字一致性、claim 溯源、图表质量 |
| **draft** | 写 LaTeX、生成概念图、编译 PDF | 结构完整性（能推进就行） |
| **review** | 学术语言审查、排版审查、基础设施泄露检查 | layout/语言/引用/页数/infra 泄露 |
| **submission** | 最终 gate 自查、submission assurance | 完整 venue peer review（按 `target_venue` 选 EMNLP/AAAI 标准），reviewer 判达标才停 |

> 注意：这张表里没有"严格度"数值，也没有"improvement < 2% 就拒"这类硬阈值。"够不够格"是 reviewer 读了产物之后的判断；harness 只负责把产物递过去、把裁决记下来。

## 内置 Skill

Skill 是给 agent 复用的横向能力（playbook），不是 harness 的判断逻辑。按角色分两个目录：

- **Engineer skills（60 个）**：编排（`auto-research-pipeline` 主入口、`emnlp-paper-skill-router` / `aaai-paper-skill-router`）、文献（`arxiv-paper-search`、`semantic-scholar-search`、`research-ideation`）、规划（`research-brief-to-experiment-plan`、`ablation-planner`、`training-infrastructure-guide`）、实验（`agent-research-benchmark-runner`、`experiment-audit`）、分析（`research-results-analysis-and-figures`、`result-to-claim`、`claims-evidence-audit`）、写作（`emnlp-paper-drafting` / `aaai-paper-drafting`、`paper-illustration-image2`、`paper-framework-figure-studio-pro` 等）、审查与提交（`emnlp-format-preflight` / `aaai-format-preflight`、`paper-infrastructure-review`、`research-submission-assurance-gate`）、角色（`argus-engineer-role`、`argus-planner-role`）。会议格式相关的 skill（drafting / format-preflight / skill-router / academic-language-review）按 `target_venue` 自动只暴露对应 venue 的那套。
- **Reviewer skills（12 个）**：`experiment-plan-review`（plan）、`experiment-results-review`（run）、`academic-paper-peer-review-benchmark`（draft 宽松 / submission 严格）、`emnlp-academic-language-review` / `aaai-academic-language-review`（review，按 venue）、`argus-reviewer-role`、`reviewer-engineer-handoff`。

miss 的能力由 distiller 在线蒸馏（复用 engineer backend，不是独立 agent）。

## 快速开始

### 1. 前置依赖

**Python ≥ 3.11** + 一个受支持的 agent CLI 后端（三选一，装哪个由
`ARGUS_SKILL_RUNNER_BACKEND` 决定，见下）：

| 后端 | 值 | 安装 | 认证 |
|---|---|---|---|
| **Copilot**（当前团队默认） | `copilot` | `npm install -g @github/copilot`（需 Node.js ≥ 22） | 首次运行 `copilot` 走一次交互式设备授权（用你的 GitHub Copilot 订阅，无需单独配置 API key） |
| Codex | `codex`（省略此变量时的历史默认） | `npm install -g @openai/codex` | `codex --version` 验证；API key 走 `docs/API_CONFIG.md` 的 vault 配置 |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | 首次运行 `claude` 走一次交互式登录 |

```bash
export ARGUS_SKILL_RUNNER_BACKEND=copilot   # 三个都装了也没关系，这个开关决定用哪个
copilot --version   # 验证可用
```

> 后端和模型不需要提前想好——装好 CLI 之后，在 cockpit 里直接说"换成 copilot 后端"
> 或"把模型换成 claude-sonnet-5"就行，不用改配置文件。三个后端的细节（premium
> request 计费、per-role 独立配置等）见 `docs/API_CONFIG.md`。
>
> `codex_autoloop` 监督循环已 **vendored** 在本仓库（`argus_skill/agent_cli/`），无需单独安装 ArgusBot。

### 2. 安装

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

> 装好之后 `argus` 和 `argus-skill` 是同一个命令（`argus` 是简写别名），本文档下面
> 统一用 `argus-skill`，两个可以互换。

### 3. 初始配置（交互式向导）

```bash
argus-skill --setup
```

向导会依次引导你配置：

1. **作者身份**（Author identity）— 询问作者名 + 邮箱，写入
   `~/.argus-skill/capabilities/author.json` 并同步设到 `git config --global`
   `user.name/user.email`，让生成的研究 workspace 提交与论文 camera-ready
   作者块都用这个身份（EMNLP 投稿 PDF 仍保持匿名）。
2. **三个 Agent 的 API**（Planner / Engineer / Reviewer）— 支持共享或独立配置
3. **实验 API 授权** — 询问是否允许实验中调用配置好的模型 API（例如当 LLM
   reward 模型 / judge、合成数据生成），而不仅用于 agent 自身推理。开启后写一个
   operator special prompt；凭证运行时从环境变量读取，绝不写进代码/论文/日志。
4. **GPU 资源分配** — 自动检测所有 GPU，选择分配给 Argus 的设备
5. **GPU Keep-Alive（防回收）** — 询问这台机器是否会回收空闲 GPU、需要占用几张。
   托管/云主机常会回收空闲 GPU，导致长论文跑被回收、进度丢失。开启后 Argus
   会用一个低占空比的 keep-alive 加载器在安静期（只调 API/写作）占住显卡；真正
   跑实验时由 `gpu_lease` 自动让位、跑完再 re-park。向导会写好
   `~/.argus-skill/capabilities/gpu_keepalive.json` 和一个 operator special
   prompt（同时满足 daemon 启动门禁所需的 special prompt）。
6. **Codex CLI 配置** — 用你刚输入的同一把 API key/base_url 自动写好
   `~/.codex/config.toml` 和 `~/.codex/auth.json`（已存在的文件会备份成
   `*.bak` 后才覆盖；不想覆盖直接回车）

```
═══════════════════════════════════════════════════════════════
  Argus — Autonomous Research Generation & Understanding System
═══════════════════════════════════════════════════════════════

  Step 0: Author Identity
  Author name [lbx154]:
  Author email [lbxhaixing154@sjtu.edu.cn]:
  ✓ Author identity → ~/.argus-skill/capabilities/author.json
  ✓ git --global user.name/user.email set

  Step 1: API Configuration
  Do all 3 agents share the same API endpoint? (y/n) [y]: y
  API Base URL: https://api.openai.com/v1/
  API Key: sk-...
  Planner / Engineer / Reviewer model [gpt-5.4]:

  Step 1b: Experiment API access
  ── Experiment API access ──
  Allow API use inside experiments (reward/judge/etc.)? (y/N) [n]: y
  ✓ Operator prompt   → ~/.argus-skill/special_prompts/30-experiment-api.md

  Step 2: GPU Resources
  Available GPUs:  [0] NVIDIA B200 (179 GB) ... [7] NVIDIA B200 (179 GB)
  Devices to allocate [6]: 6

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
  ✓ codex config / auth written

  ✓ Setup complete!
```

> Keep-alive 细节：加载器是独立脚本 `argus_skill/tools/gpu_load.py`（仅依赖
> `torch`，与 Argus 框架解耦，可用单独的 torch 环境解释器运行）。它把
> `--gpus` 当作**物理 GPU id**，在 import torch 前自行设好 `CUDA_VISIBLE_DEVICES`。
> 真正的 GPU 任务务必通过 `python -m argus_skill.tools.gpu_lease run -- <cmd>`
> 运行，切勿手动 kill 加载器。`--util` 只是尽力而为的活动目标，若仍被回收可调高
> `--util`/`--mem` 或调低 `--interval`。

### 4. 创建研究项目

不再有独立的"建项目"启动器（旧的 `python -m argus_skill.tools.new_auto_research_project` 已退役）。现在创建并启动一个项目 = 在一个**项目目录**里直接拉起 argus：daemon 的 bootstrap 会从 objective 自动分类 vertical、生成对应的 `AGENTS.md` 契约并初始化 `PIPELINE_STATE`。

```bash
mkdir -p ~/research/world-models && cd ~/research/world-models
argus-skill --daemon --continuous \
  --objective "World Model for Agent Action Selection"
```

daemon 是 **cwd-bound** 的（项目状态绑在当前目录）。启动前须满足下方「Daemon 启动硬门禁」——① 用 `--continuous --objective` 提供 mission objective；② `~/.argus-skill/special_prompts/` 至少放一个机器规则 `*.md`。也可直接跑 bare `argus-skill`（不带 flag）进 REPL 跟 Manager 对话，由它驱动同一套 bootstrap。

> **会议格式（research vertical）**：论文排版契约由 `PIPELINE_STATE.json` 的 `target_venue` 决定（默认 `emnlp`）。`emnlp` = ACL/EMNLP 8 页正文、References 第 9 页起、强制 Limitations/Ethics、acl.sty；`aaai` = AAAI-2026 两栏 7 页正文、References 后接 Reproducibility Checklist、aaai2026.sty。所有格式 gate、stage checklist 与 reviewer skill 都按 `target_venue` 自动切换。

### 5. 监控进度

```bash
argus-skill --status    # 当前状态
argus-skill --watch     # 实时 cockpit
argus-skill --follow    # 事件流

# 或通过 Telegram
export ARGUS_SKILL_TELEGRAM_BOT_TOKEN="123:abc"
export ARGUS_SKILL_TELEGRAM_CHAT_ID="123456789"
```

## Daemon：7×24 自主运行

> **启动硬门禁**：进 cockpit 或启动 daemon 前，必须同时配好两样东西，否则 `argus-skill` 直接 `exit 2` 并打印指引——
> 1. **mission objective**：用 `--continuous --objective "<目标>"` 提供（持久化到 `continuous.json`，之后可省略）；
> 2. **至少一个 special prompt**：在 `~/.argus-skill/special_prompts/` 放一个 `*.md`（这台机器/部署的操作规则：GPU、路径、调度），文件须属主本人且不可 group/world-writable。
>    ```bash
>    mkdir -p ~/.argus-skill/special_prompts
>    printf 'Operational house rules for this box.\n' > ~/.argus-skill/special_prompts/10-house-rules.md
>    chmod 0644 ~/.argus-skill/special_prompts/10-house-rules.md
>    ```
> 这取代了一切"从 objective 文本猜任务类型"的隐式逻辑——agent 必须被**显式**告知目标和机器规则。只读 / admin 命令（`--status`、`--watch`…）不受门禁限制。

```bash
# 默认 open-ended：planner 认证 project_done 后继续生成新工作，永续运行
argus-skill --daemon --continuous \
  --objective "Complete the EMNLP paper on world models for agent action selection"

# 有界一次性目标：planner 认证 project_done 后硬停
argus-skill --daemon --continuous --bounded \
  --objective "Add a unit-test suite for the data loader"

argus-skill --status              # 查看状态
argus-skill --daemon-stop --drain # 优雅停止：排空到 mission 边界再退（不中途 SIGKILL）
argus-skill --daemon-runbook      # 升级清单
```

> open-ended vs `--bounded` 是**显式开关**（`LifeSupervisorConfig.open_ended`），取代了过去从 objective 里猜 `7×24`/`ongoing`/`perpetual` 关键词的做法。

Daemon 特性：POSIX double-fork（断 SSH 不影响）、预算控制（单任务/每日上限，纯管道）、Telegram 远程 nudge / 加任务。

### systemd 部署（7×24 持久化）

仓库自带一个可直接用的 unit 模板 **`deploy/argus-skill.service`**（systemd `--user`）：crash/reboot 自动重启（`Restart=on-failure`），停止时用 `--daemon-stop --drain` 排空到 mission 边界(`TimeoutStopSec=1800`)，不会 SIGKILL 在跑的 eval / 正在 bank 的 win。

```bash
cp deploy/argus-skill.service ~/.config/systemd/user/argus-skill.service
# 编辑 WorkingDirectory 为你的项目 worktree（daemon 是 cwd-bound 的）
systemctl --user daemon-reload && systemctl --user enable --now argus-skill
loginctl enable-linger $USER   # 登出/重启后仍存活 = 真 7×24
```

> 持久性由 systemd 负责（不是手搓的外部守护）。非 systemd 部署没有自动重启——`--daemon` double-fork 只断 TTY，不自带 respawn。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ARGUS_SKILL_LIFE_BACKEND` | 真实 agent 循环（`codex`，命名沿用历史上第一个支持的 CLI，实际会走 `RUNNER_BACKEND` 选的那个） vs 确定性测试替身（`memory`） | `codex` |
| `ARGUS_SKILL_RUNNER_BACKEND` | 实际驱动哪个 CLI：`codex` / `claude` / `copilot`（也可以直接在 cockpit 里说"换成 xx 后端"，无需改环境变量重启） | `codex` |
| `ARGUS_SKILL_RUNNER_BIN` | 对应 CLI 可执行文件的路径 | `$PATH` |
| `ARGUS_SKILL_PER_MISSION_CAP_USD` | 单任务预算上限 | `30` |
| `ARGUS_SKILL_DAILY_CAP_USD` | 每日预算上限 | `180` |
| `ARGUS_SKILL_TELEGRAM_BOT_TOKEN` | Telegram bot token | — |
| `ARGUS_SKILL_TELEGRAM_CHAT_ID` | Telegram chat ID | — |

## 项目结构

```
argus_skill/
├── builtin_skills/
│   ├── engineer/          # 60 个 engineer skill（agent 的 playbook）
│   ├── reviewer/          # 12 个 reviewer skill
│   └── *.md               # 项目模板
├── tools/
│   ├── stage_check.py     # 分阶段 shell 检查 + reviewer checklist
│   ├── image_tool.py      # 概念图生成
│   ├── subagent.py        # 子 agent 系统
│   └── new_auto_research_project.py
├── skills/
│   ├── pipeline_contracts.py  # manifest/freshness artifact 构建-修复（不是质量 gate）
│   ├── stage_checklists.py    # reviewer 对照的 stage checklist（裁决在 reviewer）
│   └── store.py               # skill 匹配器
├── engineer/
│   ├── runner.py          # L1 engineer 轮次循环
│   └── checks.py          # check_commands 执行器
├── reviewer/
│   └── _core.py           # L2 reviewer（唯一完成事实来源）
├── life/
│   ├── supervisor/        # backlog / 预算 / L4 planner（领域无关编排）
│   ├── memory.py          # 持久化状态 + 纯 recency 记忆注入
│   └── special_prompts.py # 受信任的机器规则加载
├── daemon/
│   └── life_worker.py     # 7×24 daemon worker
└── loop.py                # SkillLoop — matcher × engineer × reviewer
```

> 想改"什么时候算完成 / 还差认证时派什么"，改的是 reviewer checklist 和 `supervisor.py` 的 planner 改派分支——**不要**在 harness 里加关键词判断。

## 测试

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).

## Provenance

- [skill-agent](https://github.com/lbx154/skill-agent)：skill 匹配、distiller
- [ArgusBot](https://github.com/waltstephen/ArgusBot) (MIT)：reviewer 循环、codex runner —— `codex_autoloop` 已 **vendored** 到 `argus_skill/agent_cli/`（含上游 LICENSE 与 `_VENDORED.md`）
- 新代码：auto-research pipeline、stage_check、builtin skills、image-2 集成
