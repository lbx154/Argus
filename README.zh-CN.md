<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/svg/argus-logo-horizontal-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/svg/argus-logo-horizontal.svg">
  <img src="docs/assets/brand/svg/argus-logo-horizontal.svg" width="420" alt="Argus">
</picture>

### 面向科研与工程的持久、可审查自主运行时

让长期 Agent 能够规划、执行、验证、暂停，并在一次模型调用之后继续推进。

**Argus v0.1.8**

[![GitHub Stars](https://img.shields.io/github/stars/lbx154/Argus?style=flat-square)](https://github.com/lbx154/Argus/stargazers)
[![License](https://img.shields.io/github/license/lbx154/Argus?style=flat-square)](LICENSE)
[![Node.js](https://img.shields.io/badge/Node.js-22.12%2B-5FA04E?style=flat-square&logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![arXiv](https://img.shields.io/badge/arXiv-2608.05144-b31b1b?style=flat-square&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2608.05144)

[官方网站](https://argusbot.cn) · [入门指南（英文）](docs/getting-started.md) · [视频演示](https://www.youtube.com/watch?v=i8Qy9HCboQE) · [技术报告 · arXiv:2608.05144](https://arxiv.org/pdf/2608.05144) · [微信群](#微信群) · [English](README.md) / **简体中文**

`Manager` → (`Planner` →) `Engineer` ⇄ `Reviewer`

</div>

---

> [!IMPORTANT]
> **仓库渠道：**官方源码维护在
> **[microsoft/ArgusAgent](https://github.com/microsoft/ArgusAgent)**。
> **[lbx154/Argus](https://github.com/lbx154/Argus)** 是开发预览仓库。
> 开发更新通过同步进入官方仓库。

本仓库的 **`main` 是稳定源码分支**，**`dev` 是日常开发分支**。
开发 PR 提交到 `dev`，经过审查和验证后再合入 `main`。详见[贡献说明](CONTRIBUTING.md)。

## Driver–Harness 模型

**模型**是发动机：它烧掉算力，输出 token。**Harness** 是传动系统，把这些 token 耦合到文
件、shell、编译器、GPU 和测试上。**Driver** 是方向盘后面那个位子——决定接下来做什么、判断
上一个结果好不好、以及知道什么时候该停下来问人。在其他所有 Agent 系统里，坐在那个位子上的
都是一个人；所以那个人一睡觉，工作就停了。

**Argus 坐上 Driver 的位子**，把这份活拆给四个角色。角色的每一轮都是对你已经在用的某个
编程 Agent CLI 的一次调用；Argus 负责在角色之间交接工作、送达消息、记录发生了什么、守住权限边界。

| 角色 | 做什么 | 项目文件 |
|---|---|---|
| **Manager（经理）** | 你唯一的对接人。自己回答你、自己做小活；大一些的活整个交给一个工程师，一个任务装不下时交给规划者；最后把结果报告给你。 | 可读写 |
| **Planner（规划者）** | 把一个任务装不下的活切成几块大的，每块都以一个你会打开看的成果收尾。互不依赖的几块同时派出，每块一个工程师；全部结束后，把它们的信放在一起读，决定下一步。 | 只读 |
| **Engineer（工程师）** | 把一个任务从头做到尾，然后交给审查者。 | 可读写 |
| **Reviewer（审查者）** | 打开成果，按"你打开它时会不会打回"来审：你会打回的，退给工程师返工；审查结论交给派这个任务的人。关键的检查自己重跑一遍。 | 只读 |

**每次交接都是一封信。**只有收件人是结构化的——由后端 CLI 把模型的最终回答约束成这个
格式——信的内容是自由文本，原样送达。收信人看到的是项目文件和这封信，看不到写信人的对话，
所以一封交出工作的信就是这项工作的全部说明。每一封信你都能读到，合起来就是项目的时间线。
团队干活时你也能随时跟经理说话：它在下一轮回复你，团队的工作照常进行。某个角色需要只有你
能做的决定时，会写信给你，工作等你回答。Argus 从不判定一个任务是否成功，信里会写。

正因为干活的人不能给自己打分，没有人需要盯着它：在早先 Python 运行时的 27 场战役、1,548 小时里，它平均**每约
310 小时**才需要人做一次研究判断，占空比 **95–99%**。其余内容都在
**[技术报告](https://arxiv.org/pdf/2608.05144)**里。

**技能。**Argus 自带四个技能，在 [`packages/core/skills/`](packages/core/skills)：
`research`、`software`、`argus-maintenance` 和 `learning`。一个技能就是一个文件夹，它的
`SKILL.md` 开头的 frontmatter 写明它适用于哪类工作（和 Codex、Claude Code 的技能格式相同）；每个角色的提示词里都列出了全部技能。经理和工程师
（还有你）可以在 `~/.argus/skills/` 里写新技能、改进已有技能，同名时这里的技能取代自带的；
网页的**知识与技能**里能看到它们的变化。插件是一个带 `plugin.json`（`name`、`description`、
`version`、`url`）的技能文件夹：谁装的谁启动，网页列出它并打开它的 `url`。Argus 不复盘、
不打分、不整理；什么时候写技能、改哪里，由角色自己决定。

**沙箱。**在装有 bubblewrap（`bwrap`）的 Linux 上，每次后端调用都在沙箱里运行：系统目录
只读，项目目录可写（对规划者和审查者只读），家目录是私有的，只带入该 CLI 的登录信息和设置。没有
bubblewrap 时（macOS、Windows），Argus 使用 CLI 自带的沙箱或权限设置（如果它有的话）。

**后端：** `OpenAI Codex CLI` · `Claude Code` · `GitHub Copilot CLI` · `Pi`

## 微信群

扫码加入 Argus 交流群；点击图片可以查看原图。二维码有效期以图片中的提示为准；
如果已经过期，请在 Issue 中联系维护者更新。

<p align="center">
  <a href="docs/assets/argus-wechat-group-2.jpg?v=20260924">
    <img src="docs/assets/argus-wechat-group-2.jpg?v=20260924" width="360" alt="Argus 微信交流 2 群二维码">
  </a>
</p>

<p align="center"><strong>交流1群已满，请进入2群。</strong></p>

## 安装

需要从 [nodejs.org](https://nodejs.org/en/download) 安装 Node.js **22.12+**，并装好、登录
至少一个后端 CLI。Argus 直接用这个 CLI 的登录，没有单独的 Argus 账户。Linux 上还要装
bubblewrap（包名 `bubblewrap`）用于沙箱。

| 后端 | `--backend` | 安装 | 登录 |
|---|---|---|---|
| OpenAI Codex CLI | `codex`（默认） | `npm install -g @openai/codex@latest` | `codex login` |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | 运行 `claude`，再执行 `/login` |
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` | `copilot login` |
| Pi | `pi` | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` | 运行 `pi`，再执行 `/login` |

**通过 npm 安装。**包还没有发布；发布后，安装只需这一条（等拿到 `argus` 这个名字后会改名）：

```bash
npm i -g @lbx154/argus
```

**从源码安装**（发布之前用这个）：

```bash
git clone https://github.com/microsoft/ArgusAgent.git
cd ArgusAgent
npm ci
npm --prefix frontend/web ci
npm pack -w packages/core          # 构建核心和网页，生成 lbx154-argus-<版本>.tgz
npm i -g ./lbx154-argus-0.1.8.tgz
```

要用开发预览版，改为克隆 `https://github.com/lbx154/Argus.git`。

## 运行

```bash
argus
```

它会打印网页地址，默认是 `http://127.0.0.1:8799/?token=…`，用浏览器打开即可。没有子命令，其余
一切都在网页里完成。

| 选项 | |
|---|---|
| `--port N` | 换端口（默认 `8799`；`0` 表示自动选一个空闲端口） |
| `--backend codex\|claude\|copilot\|pi` | 新项目用的后端（默认 `codex`）；每个项目可以在自己的设置里改 |
| `--model M` | 新项目用的模型，写后端自己的模型名；不指定时用该 CLI 的默认模型 |
| `--host H` | 监听其他地址，例如 `0.0.0.0` |
| `--web DIR` | 使用别处构建的网页，而不是包里自带的 |
| `ARGUS_WEB_TOKEN` | 访问令牌；不设则首次启动时生成一个，存在状态目录里，重启后不变，开着的页面不用重新登录。打印出的地址里已带上它 |
| `ARGUS_HOME` | 状态目录（默认 `~/.argus`） |

在网页里新建项目，告诉经理你要什么。项目默认在 `~/.argus/workspaces/` 下自己的文件夹里
工作，也可以指定一个已有目录。团队干活时你可以继续跟经理说话、看进度地图（每个任务一张卡片，
上面有关于它的每一封信）、浏览项目文件、打开和下载成果，并在**知识与技能**里看技能库。

工作在 `argus` 进程里运行：关掉浏览器不会停，停掉 `argus`（Ctrl-C）才会停，之后在网页里
重新启动项目即可。在远程服务器上，在服务器运行 `argus`，再用
`ssh -L 8799:127.0.0.1:8799 user@server` 转发端口。完整的上手流程见
**[入门指南（英文）](docs/getting-started.md)**。

**桌面版**随包带着 Argus，用本机的 Node.js 22+ 运行它，再打开它服务的网页工作台；见
**[桌面版文档](docs/windows-desktop.md)** 和 [Releases](https://github.com/lbx154/Argus/releases) 页面。

## 升级与卸载

先停掉 `argus`；正在进行的工作会被中断，之后在网页里重新启动即可。

```bash
npm i -g @lbx154/argus@latest      # 源码安装：git pull，再重做一遍上面的源码步骤
npm rm -g @lbx154/argus
```

卸载不会删除 `~/.argus`：其中有项目和它们的记录、角色写的技能，以及 `workspaces/` 下
没有指定目录的项目文件夹。只有确定这些都不要了，才删除它。

## 用 Python 运行时开始的项目

早先的 Python 运行时（`argus/`）还留在仓库里，只是为了让用它开始的项目跑完；之后会删除，
不要再用它做新安装。它的状态在 `~/.argus-skill`，和 `~/.argus` 互不相干。这些项目请继续用
当初的安装来跑。两种安装都会提供名为 `argus` 的命令；同一台机器上两者都有时，先确认实际
运行的是哪一个（`type -a argus`，PowerShell 里用 `Get-Command argus -All`）。

## 仓库布局

- `packages/core/` —— npm 包本体：角色之间的交接（`src/flow.ts`）、后端调用与沙箱
  （`src/agent/`）、网页服务（`src/serve/`）、角色提示词（`prompts/`）和自带技能（`skills/`）。
- `packages/contracts/`、`packages/runtime/` —— 共享类型和工具代码，构建时打包进核心。
- `frontend/web/` —— 网页源码；包里带的是它的构建产物。
- `desktop-tauri/` —— 桌面外壳。
- `argus/`、`tests/` —— Python 运行时及其测试，保留到用它开始的项目结束。

## 延伸阅读

- **[TypeScript 核心](docs/typescript-core.md)**（英文）—— 核心如何工作，以及有意不做的事。
- 运行 Python 运行时期间写下的记录（本核心就是从它精简而来）：
  **[Argus 会出什么问题，我们怎么修的](docs/failure-modes-and-fixes.zh-CN.md)** —— 六种真实失效，
  以及一次修错了的修法；
  **[系统审计](docs/system-audit.zh-CN.md)** —— 六条抱怨，逐条拿代码核对；
  **[避免局部爬山](docs/exploration-without-local-hill-climbing.zh-CN.md)** —— 把探索和项目最终的结论分开。

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
