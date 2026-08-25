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

> **一句话。** 今天的 AI Agent 已经会*动手*了——改文件、跑命令、编译代码、做实验。它
> 不会的是：决定接下来该做什么、判断上一个结果到底好不好、以及知道什么时候该停下来问
> 人。这些得有个人坐在旁边替它做。所以那个人一睡觉，工作就停了。
>
> **Argus 就是去干这件事的。** 它把这份活拆给四个角色，而且**刻意不让他们互相代劳**：
> **Manager** 决定项目什么时候往前推、以及系统能留下什么；**Planner** 挑下一个任务；
> **Engineer** 真正干活；**Reviewer** 是唯一有权说"这件事完成了"的一方——而且它**改不
> 了任何东西**，所以它没法偷偷把自己正在评判的证据修好看。
>
> 正因为干活的人不能给自己打分，你不需要盯着它。在 27 场战役、1,548 小时的运行里，它平
> 均**每约 310 小时**才需要人做一次研究判断，并且把**95–99%** 的可用时间真的用在干活
> 上。它还会在一个领域里越做越强，全程不需要重新训练模型。

### 它实际跑起来是什么样

下面是一场真实战役的压缩版。MiniMax-H3 是一个能生成带同步声音的视频的模型，权重约
**62 GiB**。你手上是一台 **24 GB** 内存的 MacBook。你让 Argus 把它跑起来，然后去睡觉。

1. **Planner** 先把问题的形状定下来。最顺手的做法——把模型压小——是**不能选的**，因为
   压小之后就是另一个模型了，答案也就不作数了。真正的目标是：**永远不要让它整个同时待
   在内存里**。
2. **Engineer** 把模型拆开，发现它是 50 个约 **1.29 GB** 的大块，而且必须严格按顺序执
   行。于是它写了一个加载器，**同一时刻只留两块在内存里**：装两块、算完、扔掉、再装下
   两块。62 GiB 待在硬盘上，内存里从来没有过。
3. 过程中它发现了一件任何文档里都没写的事：文本编码器有 64 层，但这个模型**只读第 50
   层**的输出。后面 14 层不只是白算——**算了反而会改变模型被喂进去的语义**。所以它精确
   地执行 50 层然后停下。
4. **Reviewer** 不检查"有没有出视频"。它检查的是这个**声明**站不站得住：这还是原始的全
   精度权重吗，还是被悄悄压过了？注意力是精确的还是近似的？有没有跳过某些块？计算顺序
   被改了吗？以上任何一条成立，"跑的是完整模型"这句话就是假的。
5. 全部通过。**1344×768、124 帧、24 FPS、5.17 秒立体声音频，端到端 47 分 58.7 秒，峰值
   约 15.8 GB**——大约是模型自身体积的四分之一，在一台笔记本上。
6. 系统能**留下**什么，由 **Manager** 决定，划分依据是它能泛化到多远：像"用固定大小的
   窗口流式吞吐权重"这样的技术并不专属于这个模型，属于 **Skill** 库；而像"这个模型读的
   是 `hidden_states[50]`"这样的事实只在这里成立，属于 **Wiki**，作为关于这个领域的一
   条发现。
7. 你醒来时，得到的是一个公开仓库：精确到每一条命令、钉死的 checkpoint 哈希，以及连同
   SHA256 一起发布的成片——所以你可以自己跑一遍，核对拿到的是不是同样的字节。

**第 4 步才是全部要害。** 把模型压小的话，这件事一个下午就能做完，而且写进标题里，两个
结果看上去一模一样。这条捷径之所以走不通，是因为**干活的一方不是有权认证的那一方**——
所以那个仓库直接写明了它**没有做**什么：没有稀疏注意力近似、没有跳过任何块、没有低比特
重建、没有改变计算顺序。

<sub>出处：**[Argus-AiTeam/minimax-h3-mac](https://github.com/Argus-AiTeam/minimax-h3-mac)**
——实测设备为 MacBook Pro（Mac16,8）、Apple M4 Pro、24 GB 统一内存。</sub>

---

语言模型是一台发动机。它烧掉算力，输出 token，token 就是动力。Agent 的 **harness**
是传动系统：它把这份输出耦合到文件、shell、编译器、GPU 和测试套件上，让 token 变成
对真实世界的位移。过去两年 Agent 工程的绝大部分投入都花在把 harness 做好上，而且成果
是真实的。

但一辆没人坐在方向盘后面的车，去不了任何值得去的地方。**位移不等于进展。** 总得有人
选定目的地、观察路况、判断上一个弯拐得对不对，以及知道什么时候该靠边停车问一句。我们
把这个角色称为 **Driver**——它是至今没有被自动化的那一块。在我们所知的每一个已部署的
Agent 系统里，"谁在开这台 harness"的答案都是"一个人"；而这同时也回答了"它能被开多
久"，因为没有人能连开八天不停。

这个上限不是能力上限。操作者读 diff，判断这不是自己要的，敲下修正，循环前进一步。操作
者去吃饭了，项目就停了——不是因为模型跑完了，而是因为唯一有权说*"这不对，改成那样"*
的那个部件回家了。**人类智能是离散的**：它以被注意力和睡眠切断的脉冲形式到来，而每一
条长程 Agent 轨迹，都是用那个盯着它的人的清醒时段一段段缝起来的。在今天的系统上实测，
一个强编码 Agent 被逐轮驱动时，大约 **1 小时**后就需要操作者回来。

真正重要的工作恰好是反过来的形状。**密集智能任务**——持续进行推理、工具调用、验证与
修订，直到产出一个可测量结果的任务——不会在两次脉冲之间乖乖停住。用离散的供给去喂这
类任务，正是长期困住这个领域的错配。

**Driver 智能是密集的，而且会复利。** 它不休息，所以战役的时钟和日历的时钟是同一个
时钟；并且第一天采纳的前提，到第八天仍在由同一份累积状态继续修订，而不是每天早上由一
个人读昨天的日志重新建立一遍。

Argus 坐上 Driver 的位子。

### 四个问题

Driver 反复回答四个问题，而且它们是嵌套的——每一个都是针对上一个的答案发问。

| | 问题 | 作用范围 |
|---:|---|---|
| **Q1** | **这件事做完了吗，做得好吗？** 不是命令是否返回 0，而是它有没有以其声明所要求的标准，兑现最初驱动它的那个义务。 | 一件产物 |
| **Q2** | **接下来做什么才有价值？** 在当前已知的一切之上——包括刚刚失败的东西——下一份精力该投向哪里？ | 一条轨迹 |
| **Q3** | **系统如何在这件事上变得更强？** 这一轮学到的什么应该改变下一轮的做法——这条教训只适用于本项目、适用于整个领域，还是到处都成立？ | 能力本身 |
| **Q4** | **这件事需要人吗？** 剩下的障碍里，哪些该由机器解决，哪些属于拥有它所没有的权限的人？ | 边界 |

只有上一个被诚实回答了，下一个才可能被回答：分不清"完成"和"好"的系统，也分不清哪条
教训值得留下；而留错教训的系统，会自信地变差。

**只有在没有分数的地方，这四个问题才难。** 一旦有明确的数值奖励——一个要变绿的测试、
一个要爬的榜、一个要砍半的延迟——四个问题就全部塌缩成它。这正是为什么自我改进恰恰在存
在这类信号的地方推进最快，也正是为什么那份进展没能迁移出来。**Driver 的位子不是"缺
少分数"之外的另一个问题，它就是缺少分数之后剩下的那个东西。**

### 四个角色，一个位子

只自动化 Q2，得到的是一个自信执行着没人检查过的计划的 Agent。自动化 Q1、Q2 而没有
Q3，得到的是一个永远用同一种方式解同一个问题、每次都为已经拥有的教训重新付全款的
Agent。自动化前三个而没有 Q4，得到的是一个会动用凭据、会强推分支的 Agent——因为没有
任何规则告诉过它，这类决定不归它做。这四个问题必须**由不同的当事方**一起回答，否则这
个位子无法被安全地交出去。

| | 角色 | 回答 | 它做什么 | 它**不得**做什么 |
|---:|---|:---:|---|---|
| `01` | **Manager**<br>*控制* | Q3 | 理解 operator 意图、选择工作流、独占阶段迁移权，并决定一条教训是留在本项目、写进某个 vertical 的契约，还是升为全局 | 亲自执行它所要采纳的工作 |
| `02` | **Planner**<br>*方向* | Q2 | 把当前研究状态分解为有界任务，并定义每个任务必须产出的证据 | 推动战役进入下一阶段 |
| `03` | **Engineer**<br>*执行* | — | 实现代码、开展调研、运行实验、生成可检查的产物，并提出这一轮学到了什么 | 宣布自己的工作已完成 |
| `04` | **Reviewer**<br>*验证* | Q1 | 独立检查正确性、证据、局限与完成状态；可以返回 `blocked` | 修改任何东西——它**只读**运行 |

最右边那一列才是重点。每个角色的定义，被它**不被允许做什么**决定的程度，不亚于被它拥有
什么权限决定的程度。

Reviewer 是被**刻意做弱**的。因为它只读运行，所以即使修一下证据能让这一轮看起来更好
看，它也*做不到*；而且它可以拒绝认证，而不是制造一个"完成"。

这把验证的常规读法翻转了过来。独立审查通常被当作质量过滤器：先干活，再检查。在这里它
是结构性的。传统 Agent 必须有人盯着的根本原因是：干活的部件同时也是宣布干完了的部件，
而在一件重要的任务上，没有人应该接受自我认证的结果。把这两者分开，让认证方没有能力修改
它所认证的东西，并允许它拒绝——然后人就可以离开房间了。**独立审查不是一项质量特性。把
"谁干活"和"谁有权认证"分开，是让无人值守运行成为可能的承重墙。**

Q4 是一条显式的权限边界：凭据、支付、不可逆操作和对外发布，在任何自主级别下都会停下来
等人；而一次普通的超时、失败的测试或不可用的 backend 则不会。

### 证据驱动，而非目标驱动

这个位子此前无法交出去，还有一个研究工作特有的原因：**目标本身会移动。** 一场数学战役
很少终结在它最初想证的那条定理上；一个软件需求往往要等到一版候选实现暴露出缺失，才知道
它此前是欠定义的；在芯片设计和材料研究中，能一锤定音的那个测量手段，常常本身就是正在被
建造的东西。

领域专家在开局写下的目标，不是一份交下来必须服从的规格，而是他们在对这个问题**信息最少
的那一刻**，对"应该造什么"做出的最好假设。既有系统把偏离它当作 *goal drift*——一种需要
检测并压制的失效模式。这个推理只在"目标本来是对的"这个前提下成立。当它不对时，压制偏离
就是在保全错误，系统会把预算花在忠实地驶向一个证据早已否定的目的地上。**如果目标是错
的，偏离它才是正确行为。**

真正的难点在于：一次有原则的修订和一次被合理化的失败，在最终产物里长得一模一样。所以
Argus 把当前目标持有为一个**可修订的假设**，并且只在修订有证据支撑、跨越了显式的角色
边界、且连同其理由被记录下来时才予以采纳——这正是二者的分界线，因为一个进行合理化的系
统能编出叙事，却编不出那个反驳性的测量。

这里也**没有被制造出来的分数**。与其让同一类系统既生产工作又给它打分，Argus 把进展记
录为一次有类型的前沿状态迁移：有信息量的失败被计为前进；并且让某些读法在机制上不可能发
生——最重要的一条是，**一个从未运行过的实验，永远不能被记录为"否定了某个想法"**。

### 自进化：Wiki 与 Skill，且权重冻结

Argus 以**有界 mission** 序列的形式，在持久项目状态上执行。模型参数从不移动，但后续
mission 仍然是从一份**已经改变的搜索策略**出发，而不仅仅是从一段更长的对话出发。我们
称之为**验证门控的定参运行时自进化**：*门控*，是因为一个候选只有带着任务原生证据并经过
授权提交才会变得可复用；*定参*，是因为权重从头到尾没有动过。

一个完整的更新周期有四步，任何没能走完这四步的活动都**不计入**自进化：**(1)** 一条执行
轨迹产生一个候选；**(2)** 负责的角色拿它去对照产物与任务原生证据做检查；**(3)** 被授权
的所有者提交、修订或拒绝它；**(4)** 后续某个 mission 把它作为起始上下文或执行策略的一
部分取回来用。

**知识是两个面，而且两者不能互相替代。**

| | **Wiki** | **Skill** |
|---|---|---|
| 记录什么 | 这个领域*实际上是什么样子* | 可以*匹配到后续任务*上的过程 |
| 由谁撰写 | Engineer，基于已审查的结果 | Engineer，在自己的任务完成之后 |
| 由谁提交 | **Reviewer** | **Manager** 的层级放置评审 |
| 持久形态 | 带来源链接的语义页面 | 带版本、分层的技能库 |

一个说不清自己为什么管用的过程，在条件变化时无法被修订；而一个没有任何 mission 能据以
行动的发现，是惰性的。两者都**不被当作自动正确**——当后续结果与之矛盾时，条目会被修订、
归档或退役。

**Skill 被限定在它被证明成立的范围内。** 由 Manager 的放置评审——绝不是作者本人——把每
一条被采纳的 skill 放进三层之一：

| 层级 | 采纳条件 | 效果 |
|---|---|---|
| `project` | 在这里管用 | 留在本项目内 |
| `vertical` | 被证明对整个领域成立 | 写入该领域的契约——此后该领域的每一场战役都继承它 |
| `global` | 越过任何单一领域仍然成立 | 在所有地方可用 |

**知识不是记忆，而这条区分是承重的。** 知识是运行时**已经确立、并且可以复用**的东西：
经过认证、有作用域，并且被设计为比产生它的那场战役活得更久。**记忆**——只追加的事件
日志、mission backlog、磁盘上的持久产物、各角色的滚动上下文——是运行时**为了继续工作**
所需要的东西。记忆不经过认证环节，因为它记录的是*发生了什么*，而不是*什么是真的*。两者
的失效方式也不同：丢失记忆让一个 mission 失去方向，而采纳了坏的知识，会污染此后每一个
复用它的 mission。

这**并不意味着单调改进**。有些 mission 不会提交任何可复用状态；被保留的状态会过时；而
一个更难的任务分布，即使在有用状态已经积累之后，仍然可能推高成本。

一个项目可以停止、恢复、跨运行时替换，并从最近一次已验证位置继续推进。

### Vertical：不动核心的领域纵深

领域专业性与决策权在架构上是分开的。**Vertical** 是一个领域包，声明在这个领域里什么才
算证据——它的阶段、工具、证据要求和完成标准。

Vertical 可以**抬高**证据门槛，但永远不能降低。它同样够不到权限边界和升级路径：在全部
**53,871 行** vertical 代码里，对自主级别、operator 升级路径或审批边界的引用为
**零**——因为那部分逻辑住在核心里，领域包访问不到。一个 vertical 不能给自己授权，也不能
在策略要求 Reviewer 的地方让 Engineer 认证自己的工作。

**24 个 vertical** 运行在一个 **130,362 行、且不因新增领域而改变**的核心之上。最小的
一个只要 **108 行**；中位数是 775 行。

专家写下的东西是**种子，不是天花板**。声明确立该领域的初始标准；随后在它之下运行的战役，
会把被磨利的检查项晋升回它的契约。Vertical 还可以声明 `PROTECTED_ITEM_IDS`——一层保底
门禁，晋升路径会在后续编辑面前把它恢复回来，从而使一个领域认为不可再简的检查，不会被
"增加新检查"的同一个过程优化掉。允许从种子向上生长；地板不动。

→ **[创建自己的 Vertical](#创建自己的-vertical)**

### 那个位子真的空着吗？

这个声明是可测量的，所以我们测了。在 **27 场战役**、**1,548 小时**墙钟时间、
**306,691** 条日志事件中，Argus 一共向人类发起了 **38** 次请求——平均每 **40.7 小时**
一次。比频率更有信息量的是它们请求的是什么：

| 这次打扰在要什么 | 占比 |
|---|---:|
| 基础设施坏了——GPU 驱动失效、容器存储损坏、认证中断 | 34% |
| 凭据、预算或授权——边界正在按设计工作 | 26% |
| 缺少上下文文件 | 18% |
| **研究判断**——手上有活，但决定不了怎么往下走 | **13%**（1,548 小时里 5 次） |
| 框架缺陷 | 8% |

在手上有活的前提下，占空比为 **95.1–98.7%**；而任何 Driver 需要睡觉的 harness，其天
花板是 33%。占空比不足的那部分，打扰日志记录的是失效的驱动和损坏的存储：**无人值守运
行的约束是基础设施可用性，不是 Agent 的自主性。**

完整推导、战役清单与我们明确声明的局限，见
[技术报告](https://arxiv.org/pdf/2608.05144)。

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

这是一份**部分记录**——只包含我们已经测完的战役。下面按**由谁来判定这个结果算不算数**
分组，而这些判定者里没有一个是 Argus 自己。

### 可以直接检查的开源产物

| 产物 | 是什么 |
|---|---|
| **[ACE-2](https://github.com/Argus-AiTeam/ace-2)** | 一颗 Qwen2.5-0.5B W4A8 推理加速器，其规格、RTL、验证环境与物理流程证据都没有人类作者。Layer-0 的 18 个算子 18/18 精确；运行时 **13,914/13,914** 条命令跑完，共 1,240,410,384 个仿真周期；SKY130 映射综合 **0.614 mm²**（人类设的上限 2.0）、建立时间余量 **+0.6966 ns**、WNS/TNS **0.00 ns**、100 MHz。证书自己列出了排除项：不含布线后时序、不含功耗签核、不含 DRC/LVS、不含 GDS 或流片、不含硅验证。 |
| **[minimax-h3-mac](https://github.com/Argus-AiTeam/minimax-h3-mac)** | MiniMax-H3 的 BF16 扩散 Transformer 约 62 GiB。不是把它缩小，是把它跑起来。在 **24 GB 统一内存的 M4 Pro** 上通过 MLX 分块流式加载：1344×768、124 帧、24 FPS、5.17 秒立体声音频，端到端 **47 分 58.7 秒**，峰值约 **15.8 GB**。 |
| **[minimax-h3-desktop](https://github.com/Argus-AiTeam/minimax-h3-desktop)** | **单张 RTX A6000** 上的完整 FL2VA 精度。BF16 热基线 1,792.202 秒（N=10）；Turbo 8-step **290.998 秒，6.159×**，被采纳为实用默认路线；Sol-Attn r=8 在 10/10 配对上 +15.203%；30 秒 final-AV 的 formal N=10 为 **+4.326%**。未通过质量门禁的候选被公开标记为 *rejected*，不会折进头条数字。 |
| **[ComfyUI-MiniMax-H3-MLX](https://github.com/Argus-AiTeam/ComfyUI-MiniMax-H3-MLX)** | Apple Silicon 上的 MiniMax-H3 视频与立体声音频 ComfyUI 节点。 |
| **[FlashDA](https://github.com/SJTU-DENG-Lab/FlashDA/tree/feature/dllm-fa4-adaptation)** · **[Diffulex](https://github.com/SJTU-DENG-Lab/Diffulex)** | 扩散语言模型不使用因果注意力。我们给了 Argus 一场战役——**21.85 小时**模型算力——把六种 mask 家族（block-causal、prefix-full、prefix-causal、prefix-hole、sliding-window、cache-only，以及它们的组合）搬进当代 **FlashAttention-4 CuTe DSL** kernel，并以 **Diffulex** 作为可执行的迭代环境。跨 SM80/SM90、paged 与 dense 的 **19/19** 对齐用例全部通过，CUDA Graph 重放与直接调用逐位一致。在 **H200/SM90** 上达到**原生 FA4 的 92–95%**，并比 **Diffulex Triton 后端快 1.61–2.57×**——两边都开 CUDA Graph，同口径对比。模型开销总计不到 **80 元**，87.7 小时跨度内只打扰人类 2 次。 |

FlashDA 建立在 [Tri Dao](https://github.com/tridao) 及合作者出色的
FlashAttention-4 / CuTe DSL 工作之上。欢迎反馈、复现，以及向 SM90 之外的移植——
dLLM 适配位于
[`feature/dllm-fa4-adaptation`](https://github.com/SJTU-DENG-Lab/FlashDA/tree/feature/dllm-fa4-adaptation)
分支，完整测量协议、被拒绝的路线和逐场景延迟见
[`EXPERIMENT_RESULTS.md`](https://github.com/SJTU-DENG-Lab/FlashDA/blob/feature/dllm-fa4-adaptation/EXPERIMENT_RESULTS.md)。

更有价值的不是终点。这场战役早期的 dense + block-sparsity 路线不是"稍差"，而是比原生
**慢 4.9–29.6×**。最终结果来自识别出这条数据通路本身就是错的并放弃它——而那条被否掉的
路线连同证据一起被保留在技能库里，没有被丢掉。

### 由与我们毫无关系的维护者判定

| 提交 | 结果 |
|---|---|
| **[`sgl-project/sglang#35038`](https://github.com/sgl-project/sglang/pull/35038)** —— SenseNova U1 原生多模态生成与交错服务 | 36 个文件、**+11,263/−72**、14 个 commit，覆盖通常要分头排人的五条工作线。1,116 个张量加载，缺失与未知均为 0；视觉问答在 **160/160** 生成 token 上精确；并发 8 下 **8/8** 精确；BS8 吞吐 **5.108×**。过程中发现、定位并修复了一个跨 batch 确定性缺陷。一位工程师配合逐轮编码 Agent 投入 **60 多小时**未能完成；一次无 Driver 的盲跑在 1 小时 21 分后停下，交出的草稿没有真实权重；Argus 在 **24.14 小时**窗口内完成。*目前 open，审查中。* |
| **[`fla-org#1045`](https://github.com/fla-org/flash-linear-attention/pull/1045)** —— TileLang RWKV6 forward-intra 后端 | **已合入。** H100 NVL 上前向 1.18×、前向+反向 1.21×，没有收到任何 inline 修改要求。它的说明里直接写明：实现、优化循环、正确性验证和性能证据均由 Argus 自主完成——而一位外部维护者连同代码一起接受了这句话。 |
| **[`fla-org#1109`](https://github.com/fla-org/flash-linear-attention/pull/1109)** —— 修复 SM100 反向 autotuning 的非法内存访问 | **已合入。** 两行，没有加速可报：修之前整个测试文件根本跑不完，修之后 **76 个测试通过**。维护者独立复算出过滤后仍有 24 个 autotune 候选，随后批准，无必需修改。 |
| **[`fla-org#1128`](https://github.com/fla-org/flash-linear-attention/pull/1128)** —— 四个 TileLang kernel 加速 KDA 训练 | B200 上四个阶段相对 Triton 达到 1.29×。最好的单阶段到了 1.541×，但自动分发只对唯一一个同时具备已验证正确性和可重复端到端收益的负载开启，实测 1.078–1.099×。更大的那个数字拿得到，但没有拿来用。*open。* |
| **[`fla-org#1114`](https://github.com/fla-org/flash-linear-attention/pull/1114)** —— 并行化长序列 `AttnRes` 归约 | B200 上五种 bf16 形状几何平均 1.102×，最好情况 1.237×。提交里把**最差的那一行** 1.033× 和均值并列写了出来。*open。* |

### 由官方评测器打分

| 竞技场 | 结果 |
|---|---|
| SWE-Bench Pro（731 任务） | **≈78%**，对照直接使用 Copilot 的 **59%**——两边是同一个模型（经 Copilot 的 GPT-5.5/xhigh）——并且有 **35** 个任务被判为 `blocked`，而不是报成一次没有证据支撑的成功 |
| SOL-ExecBench | 全球排名 **#6**；7 个 kernel 进入 top-3；在 2 个上超过了第 1 名参赛者 |
| MLE-Bench Lite | 奖牌率 **69.2%**（13 个已评分中 9 枚）：3 金、3 银、3 铜，对照 Kaggle 排行榜 |
| AARRI-Bench | **63/82（76.8%）**，对照论文最好成绩 68.3% |
| nanochat（B200 / H100） | 0.9636 / 0.9855 BPB，对照人类最好 0.9646 / 0.9879 |
| nanoGPT speedrun | **79.77 秒**，对照同设备人类记录 80.18 秒 |
| 数学推理数据合成 | 冻结 solver 下 28.0 的 gap，对照 20.83 / 8.33 / 6.25 |
| 面向扩散语言模型的 FlashAttention-4 | 见上方 **FlashDA**——19/19 逐位对齐用例，由 fp32 参考实现判定，而不是由分数判定 |

### 由外部检查器和评审判定

- **MOF 生成**——化学可控性 92.5 / 100.0 / 74.5%，AUC 0.594 → 0.833，由外部 `MOFChecker` 验证。被采纳的方法比它所取代的已发表方法**更小**，这不是一个优化可见分数的系统倾向于得到的结果。
- **Erdős–Gyárfás**——在证明检查器下取得六项有证明支撑的前沿更新，其中一条被证伪的路线被保留为证据，而不是删掉。
- **研究写作**——六条论文流水线推进到投稿，共 254 个 mission，含 16 次 Stage 回滚；六个项目共 41 件去重后的公开产物。

### 运行时作用在它自己身上

- **定参自进化：** 成熟期解决一个 SWE-Bench Pro 任务，比启动期少用 **21% 的 token**、少花 **15% 的活跃时间**——全程模型权重未变。
- **变参轴：** 在 8×B200 上从零搭起并跑通了一条 1B 预训练流水线。
- **耐力：** 最长单场战役 **8.1 天**；4 场战役超过一周；最大单条轨迹 61,797 个事件。

> [!NOTE]
> 以上每个数字都来自技术报告或所链接的公开仓库，并且各自带着那里声明的适用条件。凡是
> 结果本身窄的——单一形状、单一 GPU 世代、单一已论证范围——来源会这么写，我们也这么写。
