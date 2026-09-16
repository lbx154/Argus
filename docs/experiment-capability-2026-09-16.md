# Argus 实验能力升级与对照实验(2026-09-16 夜)

面向操作者的报告。目标:让 Argus 在研究垂域里做出的实验"方法与代码一致、结果真正 work、复用与效率优先、知识能自进化",并用一次对照运行证明改进。

## 1. 诊断:问题不在模型,在交接

基线项目 `s-009c3ec3`(目标"写个iclr论文",研究垂域,完整跑到 Review 并被内部评审接受)的记录说明了失败机制:

- **任务交接极薄。** 实现任务第一轮 Engineer 收到的提示共 3543 词,其中"当前任务"只有一句话("Implement the core streaming ridge-regularized RPCholesky feature map extractor ... alongside ... baselines on GPU 1; execute unit checks and a positive control test ..."),其余是角色政策、记忆、技能库路径和一份 4051 词手册的指针。没有方程、没有接口、没有要通过的测试、没有参考实现、没有环境状态。
- **方法由实现者自己定义。** Planner 先派了"Specify regularized Schur complement update equations and baseline benchmark protocol"给同一个 Engineer,写进 RESEARCH_NOTES.md;而选中的路线原文(route-03)里根本没有"正则化 Schur 补"这个方法(全文只出现一次 "regulariz"),它来自选题器的 rationale。实现是简化版(`d_j = sqrt(u_p + lambda)`,"流式"类内部 `V = zeros(k, N)` 全量分配),测试按结论写("Reg 优于 RFF"),没有 knockout。
- **Reviewer 结构上无法核实。** 两次实验评审分别 42 秒和 32 秒,单轮、零工具调用;集成 Reviewer 跑在只读沙箱里,shell 被剥掉。它读的是 Engineer 写的笔记,对照的是 Engineer 写的公式。
- **claim 跟着工程漂。** 基准任务(验收:至少 3 个数据集、k 到 1024、5 个随机种子、均值与标准误)因 provider 退出 143 失败后,Planner 重发时验收变成"运行器无异常跑完,产出 JSON",数据集缩成"例如 California Housing"。论文最终写的是单种子、一个真实数据集,并把 λ 无效包装成受限情况的结论。
- **没有复用。** 路线点名了公开实现(Epperly 等),没有克隆;RL 类基础设施技能里写死的框架名会随时间过期。

基线数据(来源:`state/projects/s-009c3ec3/usage.jsonl`;工作区另一份 usage 含团队工人,合计 187 次调用、$21.44):

| 指标 | 基线 s-009c3ec3 |
|---|---|
| 模型调用 | 140 |
| 输入 token(其中缓存命中) | 67.7M(59.7M) |
| 输出 token | 217k |
| 费用 | $12.02 |
| Experiment 阶段时长 | 约 31 分钟 |
| 实验评审时长 | 42 s / 32 s,零工具调用 |
| 参考实现克隆 | 无 |
| 种子数 | 1 |
| 组件级测试 | 无(5 条结论型测试) |

## 2. 改动(按机制,不是按文件)

原则:凡是能从代码、测试、配置、git 推出来的,都由主机零 token 派生,不让 agent 手写维护;凡是只有 agent 知道的(方法是什么、为什么这么设),只写一次、写在离代码最近的地方;没有任何一处是"门",证据交给有 hold 权的角色判断。

1. **任务简报(SWE-bench 化的交接)。** 研究垂域接管 mission 前言(`prepare_mission`),每个 Experiment 任务开头多一份派生的 `## Task brief`(不超过 70 行):冻结的 claim 原文(METHOD.md 陈述,并注明只有操作者能改);各组件当前状态与 `# @component` 锚点位置;环境事实(解释器与版本、已装依赖与代码导入的包是否在位、third_party 克隆与钉住版本、数据目录、GPU、上一轮主机检查结果);本任务的验收、决策规则、非目标原文,以及"完成的定义";上次以来的 git 改动。零模型 token。
2. **实现简报模板。** Planner 写每个实现任务必须按 `engineer/implementation-brief.md`:claim 原文、本任务要实现的组件(来自 METHOD.md,含 file:Symbol 入口与方程/路线章节)、接口、必须通过的 tests/spec、数据与规模(照抄路线)、命令、环境前提、完成定义、范围外。验收是可执行检查,不是形容词;一个任务一份简报;基础设施或 provider 失败重发时简报与验收逐字不变。"定义方法"不再单独派给别人:方法由同一个 Engineer 在写代码前写进 METHOD.md。
3. **固定 claim,迭代到 work。** 手册、检查清单、Planner/Reviewer/Manager 片段、论文手册里所有"从证据重推论题"的措辞替换为六级诊断梯子:实现忠实度 → 设置与评估器(阳性对照)→ 超参与配方(一次一因)→ 规模与数据(路线的数据集与规模,不是缩水 pilot)→ 基线公平性 → 仍满足 claim 的方法变体。至少三次有诊断的尝试才允许升级,升级是带证据的操作者问题,不是缩小 claim,不是"受限情况"或"负结果"论文;Reviewer 把 claim 漂移和少于三次尝试的负结果当作修复请求;Manager 不把建立在缩小 claim 上的评审通过当作阶段完成;论文只在 claim 如述被支持时才写,以最强结果开篇,不做防御性写作。
4. **方法卡写一次,其余派生。** METHOD.md 只有方法陈述、组件表(组件 | 想法规定(引用路线原文)| 备注)、协议、可证伪点。实现位置、测试状态、复用代码、超参数、改动记录由主机从 `# @component` 锚点、tests/spec 的 component 标记与主机跑测试的结果、src 导入扫描(映射到 third_party 钉住版本与已装包版本)、configs 的 YAML/JSON/TOML 与 `# why:` 注释、git log 派生。Atlas 研究简报里的方法卡面板展示派生结果。
5. **主机检查进 research vertical。** 核心层只留 `argus/engineer/round_evidence.py` 注册钩子("每轮 Engineer 结束后,垂域可提供证据");研究垂域 `spec_checks.py` 在导入时注册自己,负责跑 tests/spec(隔离 pytest 配置与环境变量、超时即杀)、按组件 join、写 `.argus/round-checks/`。别的垂域不受影响。
6. **让代码好 review。** `# @component <名>` 放在组件入口上方,`# @simplified <名>: 原因`、`# @reuses <库> <符号>` 类推,非显然决定写 `# why:`。主机据此生成"评审包":每个组件的代码片段、测试结果、超参变化、本轮改动文件(含删除)。Reviewer 的阅读顺序:评审包 → METHOD.md → 原始验证证据 → 测试 → 代码 → 最后才是 Engineer 自述。Reviewer 没有新增工具或权限。
7. **基础设施知识自进化。** 技能只写流程(活的调研含"继任者发现"、候选隔离环境钉版本站起并测阶段表与 rollout 引擎开关 A/B、锚定当时配方的一次一因调参),不写任何框架名;当前答案在项目当时产出,存成带 "Surveyed 日期 / re-verify after 日期" 的项目 Skill,沿既有传播机制进研究垂域共享层;研究角色的动态上下文加一行今天日期,声明记得的框架名只是待验证的过期假设。
8. **wiki 实时可见。** 左侧栏底部新增知识库面板(页数、最近 5 页、15 秒刷新、点开阅读),对应只读接口。
9. **自评工具。** `python -m argus.verticals.research.capability_report --state-dir … --workspace …` 输出每个项目的过程指标(阶段时长、评审时长与判定、token 与费用、方法卡与组件状态、规格测试、参考实现、种子与数据集、图检、skill/wiki),支持 `--baseline` 对照。

## 3. 对照实验设计

同一实例(8985)、同一目标"写个iclr论文"、同一模型配置,新建项目运行;用 `python -m argus.verticals.research.capability_report` 每 15 分钟采样。比较维度:各阶段时长、调用次数与费用、METHOD.md 是否存在且组件被测试证明的比例、参考实现克隆、种子数与数据集、Reviewer 的 continue/done 与时长、图检缺陷数、沉淀的 skill/wiki 数量。

## 4. 结果

_待填写。_
