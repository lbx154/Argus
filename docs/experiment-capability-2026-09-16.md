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
10. **Planner 的多行实现简报完整送达(v2)。** 两处 `TASK_OBJECTIVE=` 行式解析器(`argus/planner/planner.py`、`argus/planner/bounded_dag.py`)此前只取第一行。v1 运行里 Planner 按模板写了 40 行简报(claim、组件的 file:Symbol、接口、必过测试、数据规模、命令、环境、完成定义、范围外),Engineer 收到的任务正文只剩标题 `## Claim`。现在目标后面直到下一个字段(任何形状,含未登记的 `TASK_*`)的行都属于目标;上限 8000 字符。
11. **普通 mission 也带阶段(v2)。** `loop.py` 只在非 mission 操作时给 Engineer 传 stage,而研究垂域的 Experiment 阶段映射到 mission,于是 Engineer 横幅里的阶段块(权威手册指针、本机算力、"方法卡与可执行规格"、持久研究学习)从未渲染过——基线和 v1 的 Engineer 都是靠 Planner 的任务文本和技能目录自己摸到 METHOD.md 的。现在每种操作都带当前阶段,轮次上下文同样;阶段内横幅不变,不影响 provider 前缀缓存。同批小修:METHOD.md 尚不存在时任务简报引用路线原文而非选题理由;环境行写明 `.venv/bin/python -m pytest tests/spec`(v1 评审阶段的 Engineer 裸跑 `python3 -m pytest` 失败后对全盘 `find / -name pytest`);图检跳过 third_party/ 与虚拟环境。
12. **运行真实性由主机派生(v3)。** v2 的一个项目在 4 分钟内"跑完" 350 个 WebArena/WorkArena 任务 × 3 种子 × 4 方法:`runtime.py` 在没有模型时退回 `mock_worker_llm`,评估器生成合成 DOM,论文却写成基准评测,Reviewer 只在 limitations 里提了一句就 accept。测试全绿——它们证明的是代码符合方法卡,不是实验真实。现在方法卡派生多两项零 token 事实:`src/`(不含 tests、third_party)里名字含 mock/fake/stub/synthetic/oracle 的定义及其调用点,以及结果目录的足迹(文件数、写入跨度分钟数);它们以"Run reality"出现在任务简报与评审包里。提示文本各加一句:Engineer——替身只许在 tests/spec,真实系统跑不了就写进 METHOD.md 的 Deviations 并说明,不许把模拟当基准报;Reviewer——经替身产生的结果对该组件记 NOT_IMPLEMENTED,除非 METHOD.md 与论文都声明为模拟;Planner——路线需要托管模型或环境时,claim 任务依赖一个"官方样例端到端跑通"的搭建任务;论文标准——写明评测基底。协议本身点名的合成数据会被标注"the protocol names it",不算替身。
13. **方法图只走 PPT Master 路线 D;数据图 matplotlib 可用(v3.1–v3.3,操作者指令)。** v2 的 Engineer 先跑了 `ppt_master status`(ready),转身用 matplotlib 画架构图,文字压框、无可编辑源。操作者要求方法图必须走内置的 PPT Master 路线 D;数据图用 matplotlib "很扎实",保留。现在主机从树上派生的事实:论文里的方法图(文件名/label 含 architecture/overview/pipeline/mechanism 等,或首图且 caption 无度量词)其导出 PDF 的 producer 是否为 matplotlib;`paper/` 下是否有同名 `.pptx`;**该 pptx 是否真是导出源**——比对 PDF 的路径段数与 pptx 的形状/自定义几何数、PDF 文本与 pptx 文本的重叠率(演示中 Engineer 用 SVG→Ghostscript 出了 594 段路径的 PDF,旁边放了个 13 个矩形、零路径的 pptx 凑同名,被这条抓出)。三方各一句话规则与论文清单同步;matplotlib 画的框图脚本单独点名。数据图新增一条备选的内置路线:`figure_spec_scripts/echarts_figure.py`(ECharts option → 论文主题 → 浏览器渲染 → 带内嵌字体的 PDF/SVG + 图溯源,本机实测可用),与 matplotlib+`paper_chart_style` 并列,一篇论文选一条。

## 3. 对照实验设计

同一实例(8985)、同一目标"写个iclr论文"、同一模型配置,新建项目运行;用 `python -m argus.verticals.research.capability_report` 每 15 分钟采样。比较维度:各阶段时长、调用次数与费用、METHOD.md 是否存在且组件被测试证明的比例、参考实现克隆、种子数与数据集、Reviewer 的 continue/done 与时长、图检缺陷数、沉淀的 skill/wiki 数量。

## 4. 结果

### 4.1 v1(ad1a50631…eb860a1f9)对基线

同一实例、同一目标、同一模型配置;v1 项目 `s-3d1dbf72`,12:39 创建,15:29 由 Planner 宣告完成(REVIEW.md:accept)。数字来自 `capability_report`(state 目录 usage)与工作区 usage(团队工人);快照在 `/data/v-boxiuli/argus-eval-20260916/snapshots/`。

| 指标 | 基线 s-009c3ec3 | v1 s-3d1dbf72 |
|---|---|---|
| 总时长 | 2.97 h | 2.85 h |
| Idea / Experiment / Paper / Review 时长 | 2.16 / 0.47 / 0.19 / 0.10 h | 1.22 / 1.01 / 0.14 / 0.47 h |
| 任务数(平均轮数) | 8(1.5) | 6(1.0) |
| 评审次数与判定 | 10(done 6,continue 4) | 6(done 6) |
| 评审中位时长 | 35 s | 32 s |
| 模型调用(state + 团队) | 140 + 47 = 187 | 50 + 39 = 89 |
| 费用(state + 团队) | $12.02 + $9.43 = $21.45 | $5.94 + $7.38 = $13.32 |
| METHOD.md / 组件 / 被测试证明 | 无 / 0 / 0 | 有 / 4 / 4 |
| tests/spec 文件 / 绑定组件的测试 / 最近主机检查 | 0 / 0 / 无 | 5 / 14 / 17 passed, 0 failed |
| 参考实现克隆 | 无 | third_party/SRFF @ 692e958 |
| 种子 | 1(42) | 5(42–46) |
| 数据集 | synthetic, california_housing | synthetic, covtype, mnist(论文另报 ijcnn1, w8a) |
| 图检缺陷 | 6 | 0 |
| 配置超参数(带 `# why`) | 0(0) | 14(0) |
| wiki 页 / 项目 skill / 决策记录 | 1 / 0 / 0 | 1 / 0 / 0 |
| 论文 | 13 页 | 13 页 |

过程上看到的变化:

- **方法先于代码。** Experiment 第一个任务(6 分钟)产出 METHOD.md(4 个组件、协议、可证伪点)、钉住版本的参考克隆、5 个规格测试文件(oracle、differential、knockout、claim-shape、parity,全部带 component 标记),主机在轮末跑通 14 条并写入 `.argus/round-checks/`。第二轮 Engineer 的任务简报里"Components now"四个组件全部 `proven` 并指向 `src/sr_sh_rff.py` 的锚点行。
- **Reviewer 读的是主机证据。** 每次评审的提示里都有"评审包"(组件状态 + 锚点代码片段 + 主机测试计数 + 本轮改动文件)与"Host-run project checks";评审文本逐组件引用了这些结果。评审仍在 30 s 左右、零工具调用——这是设计(不给 Reviewer 加工具),但速度没有变化说明它仍是"读证据下判断"而不是"复现"。
- **claim 被固定并带着走。** 合成阳性对照 5 种子,SR-SH-RFF 的相对 Frobenius 误差比 ORF 低约一个量级(d=8, M=128:0.00088 对 0.0081);真实数据上准确率与 ORF/RFF 持平(covtype M=128:0.7813 对 0.7807/0.7804,均在标准差内)。论文摘要写的是"reduces error by more than an order of magnitude … matches or exceeds ORF test accuracy",以最强结果开篇,没有防御性写作;"至少 2× 更少特征即达到同等下游精度"这半句 claim 在真实数据上只做到"持平",论文没有缩写它,也没有把它写成负结果。
- **全否即重生成第一次在生产触发。** 三条路线各被一篇 2023–2025 年论文抢先;重生成后三条都换了方法但又各被新的抢先论文或数学缺陷否决;选题器按"最可修复"选了 route-01 并把三条致命意见原文写进 RESEARCH_NOTES.md 的"未解决实现义务"。这是设计行为,但也说明选题空间本身(随机特征/KRR 求解器)已经很拥挤,重生成一次不足以跳出;论文的新颖性风险由此而来。
- **时间与钱花在哪。** Idea 阶段少了 0.94 h(基线的 12 路组合改为 3 路);Experiment 多了 0.54 h,因为真的跑了 5 种子 × 3 数据集 × 4 方法 × 4 预算并写了规格测试;Review 多了 0.37 h(其中约 25 分钟是评审阶段 Engineer 裸跑 `python3 -m pytest` 失败后对全盘 `find / -name pytest`,操作者手工结束了该进程)。全口径调用数减少 52%,费用减少 38%。

### 4.2 v1 暴露的交接缺陷(已在 v2 修)

读 v1 的 Engineer 提示原文时发现两处与"交接"直接相关的缺陷,均不在提示措辞而在管道:

1. Planner 确实按 `implementation-brief.md` 写出了 40 行实现简报(claim、四个组件的 `src/...py:Class` 入口、接口签名、必过的 tests/spec 文件、数据规模、命令、环境前提、完成定义、范围外),但 `TASK_OBJECTIVE=` 的两处行式解析器只保留了第一行,任务记录里的 objective 字面上是 `## Claim`。Engineer 是靠验收句、任务简报和技能目录把方法卡、参考克隆和规格测试做出来的。
2. Experiment 阶段的 Engineer 以普通 `mission` 操作运行,`loop.py` 对这种操作不传 stage,研究垂域为 Engineer 准备的阶段块(权威手册、本机算力、"方法卡与可执行规格"、"持久研究学习")从未进入 Engineer 提示——基线亦然。这解释了 `# why:` 注释为零、没有沉淀 skill/决策记录:Engineer 从未看到要求它这样做的那段话。

两处都在 v2(`f62c654b9`,产物 `19629fb29`)修复并部署;v2 对照项目 `s-e2a29d20` 16:03 启动。

### 4.3 v2(f62c654b9 / 19629fb29)

v2 项目 `s-e2a29d20`,16:03 创建,16:57 完成(0.92 h),题目换成了 web agent 的提示注入防御(PBIS)。交接修复在生产中得到验证:首个 Experiment Engineer 提示 4921 词,含权威手册、本机算力、"方法卡与可执行规格"三块;任务正文是 Planner 的完整 2990 字实现简报(claim、组件 file:Symbol、接口、必过测试、命令、完成定义、范围外);任务简报的 claim 引用路线原文。工作区:METHOD.md 3 组件全 proven、tests/spec 9 条主机跑通、third_party/webarena 钉 dce0468、configs 里 14 条 `# why`(v1 为 0)、wiki 3 页(v1 为 1)。全口径 68 次调用、$6.72。Idea 阶段 20 分钟即有合格路线,没有触发重生成。

| 指标 | 基线 | v1 | v2 |
|---|---|---|---|
| 总时长 | 2.97 h | 2.85 h | 0.92 h |
| 调用 / 费用(全口径) | 187 / $21.45 | 89 / $13.32 | 68 / $6.72 |
| 组件被证明 / 规格测试 | 0 / 0 | 4 / 14 | 3 / 9 |
| 参考克隆 | 无 | SRFF@692e958 | webarena@dce0468 |
| `# why` 超参数注释 | 0 | 0 | 14 |
| wiki 页 | 1 | 1 | 3 |
| 评审判定 | done 6 / continue 4 | done 6 | done 6 |

**但 v2 的实验不是真的。** 路线 03 明确写了托管本地开放权重模型(GPU 0–1 上以推理引擎服务 14B 级模型)作为执行策略与内容工作器;Engineer 写的 `runtime.py` 在没有模型函数时退回 mock 解析器,评估器用 `mock_worker_llm` 与合成 DOM,"350 任务 × 3 种子 × 4 方法"的 results/ 在 4.7 分钟内写完;METHOD.md 的 Deviations 写 "none",论文摘要写 "We evaluate PBIS across 350 comprehensive benchmark tasks across WebArena and WorkArena",Reviewer 只在 limitations 里提到"合成 DOM 结构"便给了 accept(8/10)。方法与代码一致、测试全绿、claim 未漂移——机制都按设计工作,却对"实验是否真跑了"一无所知。这是 v3 的目标(第 2 节第 12 条):主机把替身与结果足迹作为事实交给 Engineer 与 Reviewer,并把"替身不算结果"写进三方的规则。

### 4.4 v3(a3bcc4fad…8ac7a1a01;对照项目 s-793a4918,19:37–23:02 PDT,已完成)

v3 在 v2 的交接修复之上加了两件事:主机从树上派生的"Run reality"(替身函数扫描 + results/ 足迹)进 Reviewer 包与任务简报;方法图必须有同名 `.pptx` 且导出确实来自它(figure_lint 比对形状/路径/文字)。题目是 GQA KV-cache 的 sub-4-bit 量化(Decoupled-RotKV),参考克隆 kivi@876b4d2。

| 指标 | 基线 | v1 | v2 | v3 |
|---|---|---|---|---|
| 总时长 | 2.97 h | 2.85 h | 0.92 h | 3.43 h(其中 21:17 我在阶段中途重启服务,Engineer 会话被切,重试会话 `find /` 空转 66 分钟) |
| 调用 / 费用(state 口径) | 140 / $12.02 | 50 / $5.94 | 68 / $6.72 | 54 / $7.79 |
| 评审判定 | done 6 / continue 4 | done 6 | done 6 | done 6 / continue 0(1 次后端失败跳过) |
| 组件被证明 / 规格测试 | 0 / 0 | 4 / 14 | 3 / 9 | 3 proven + 1 partial / 9 passed |
| 想法阶段 | 1 条路线 | — | — | 3 条一手文献路线 + 独立评审后选 route-02,RESEARCH_NOTES 写资源尺寸 |
| METHOD.md Deviations | 无 | — | "none"(实为全 mock) | 如实写"单元验证在合成激活分布上",但没写检索扫描也是合成的 |
| 真实模型是否跑过 | 否 | 否 | 否(mock_worker_llm) | **是**:Qwen2.5-7B-Instruct 与 Mistral-7B-Instruct-v0.3 在 GPU 2 上各算 1 段 2048 token 的 Wikitext-2 困惑度 |
| 主打数字的来源 | 合成 | 真实数据(持平) | 4.7 分钟"350 任务基准" | "32k 检索召回 94.0%,比 KIVI +28":`evaluate_retrieval_at_scale` 在 `torch.randn` 合成 K/V 上做的机制扫描,10 次试验;结果文件把两个模型名写在这些数字上方;summary 在脚本最后一次编辑 57 s 后写出 |
| 方法图 | matplotlib | matplotlib | matplotlib 三面板 | TikZ standalone(pdfTeX)+ python-pptx 8 个形状的同名 pptx;三块文字框,文字被面板边裁掉(figures/v3/decoupled_rotkv_framework.png) |
| 数据图 | 6 处 lint 缺陷 | 0 | 0 | 3 张 paper_chart_style,Type 42 字体,0 缺陷,可读(figures/v3/retrieval_scaling.png) |
| 论文 / 评审结论 | 13 页 | 13 页 | — | 11 页,paper 阶段 10 分钟,review 阶段 3 分钟,Strong Accept |

**机制上确实起作用的部分。**
- 主机派生的 Run reality 第一次进包就起效:21:09 的 Reviewer 包列出 `retrieval_pilot.py:18 create_synthetic_retrieval_scenario (the protocol names it)`,Reviewer 把先导判为正对照,Planner 下一任务写明"在真实 transformer 权重上评测",Engineer 于是真去加载了 7B 权重——四个对照项目里第一次。
- 想法阶段的三路线 + 独立评审、方法卡 + 主机侧规格测试、参考克隆这几项在 v1–v3 稳定复现;费用比基线低 35%–64%。

**v3 暴露、已在 v3.5(7d3d0f937)修的两处。**
1. *没名字的替身。* 替身扫描按名字找 mock/synthetic,`evaluate_retrieval_at_scale` 没有这些词;它的输入却是 `torch.randn`,上层 `run_model_eval` 的 docstring 自己写着 "synthetic long-context retrieval"。Reviewer 拿到的是"任务要求真实权重"+"tests 9 passed"+"results 文件列了两个模型",就接受了。v3.5 让 Run reality 多给两条从树上直接读出的事实:每个结果文件"距代码最后编辑 N s 写出",以及"用随机张量喂输入的度量函数(带 docstring 首行)"。在 v3 的树上现在打出来是:
   - `src/eval/run_model_eval.py:229 evaluate_retrieval_at_scale — used from …:398; builds inputs with torch.randn, torch.randint; docstring: 'Evaluates multi-depth retrieval recall …'`
   - `src/eval/run_model_eval.py:352 run_model_eval — docstring: 'Executes end-to-end evaluation suite on real models and synthetic long-context retrieval,'`
   - `results/model_eval_summary.json was written 57 s after the last edit to src/eval/run_model_eval.py`
   Reviewer 提示只加一句:一分钟的运行或随机 key 不是协议里的评测,不管结果文件上写了什么模型。仍然不是门:一个物理仿真器就该用随机初值,由 Reviewer 判断。
2. *形式合规的 pptx。* v3 的 Engineer 查了 `ppt_master status`(ready)、`which soffice`(没有),然后用 TikZ 编译了框架图,再用 python-pptx 造了一个同名 pptx。文字相同、形状数在阈值边缘,原有的形状/路径比对没抓到。v3.5 加一条不需要阈值的事实:方法图 PDF 的 producer 是 pdfTeX/XeTeX/matplotlib 等——没有任何 PPTX 导出链会产出这些 producer,所以旁边的 pptx 只是陪衬。v3 的树上现在报 `was produced by pdfTeX-1.40.25, which no PPTX export chain produces`。

**v3 暴露、还没修的。** 见 4.7 前三条。

### 4.5 重画演示:GPT-6 Astra 走路线 D/B(23:53–00:07 PDT)

用户要求用 GPT-6 Astra 重画 v3 论文的 Figure 1 并跑完整流程。做法:独立进程调用网页前门同一入口 `manager_message`(`argus-eval-20260916/redraw_astra/run_redraw.py`),只在该进程环境里把 `ARGUS_SKILL_ENGINEER_MODEL` 设为 `gpt-6-astra`,共享实例与 v4 对照的模型不受影响;Manager 前门分类后走 self-implement 路线。

| 项 | 结果 |
|---|---|
| 用时 / 费用 | 14.0 min / $4.61(gpt-6-astra 2.31M 输入、24k 输出 token) |
| 路线 | 本机无图像接口,按技能自动转 Method B:先下载 KIVI、QuaRot 原文并渲染其框架图页作参考,在项目 venv 装 Playwright,用导出命令先做 export_probe 验证链路,再用 python-pptx 造原生对象(335 个形状、80 条连接线、103 段文字,无 custGeom) |
| 导出 | 三轮"导出 → 看 PNG → 改 pptx"(export-1/2/3.log);PDF producer Skia/PDF;`figure_lint` 0 缺陷;main.pdf 重新编译,Figure 1 在第 3 页 |
| 构图 | 三行机制图:(a) RoPE 平面旋转与 O 逐块可交换、融进 W_K/W_Q、通道离群前后对比;(b) K 按通道、V 按 token 的 INT2 位格 + 32k 检索对照面板(KIVI 66 / Ours 94 / BF16 98);(c) 低频残差 2-bit 码 → SRAM 逐 query-head 修正。标题字号、留白、分组均达刊印水准,下标字距略松 |
| 诚实度 | 图与图注自己写明"synthetic attention retrieval test,不是 per-model RULER 分数"、"SRAM 边界是设计而非已验证的融合内核" |

对比 v3 自己产出的三块项目符号框(同一工作区、gemini-3.8-flash、并入写全文的任务、6 分钟):差别来自三件事——图单独成任务、导出链可执行、模型能看自己渲染的 PNG 并返修。产物在 `argus-eval-20260916/figures/v3-astra/`。

### 4.6 v4(v3.5/v3.6 = 8ac7a1a01…2254a98c0;对照项目 s-bed96846,23:13–03:19 PDT,已完成)

v4 是第一个从头到尾跑在"Run reality 带结果时间戳与随机输入函数、方法图导出链可用"版本上的对照。题目再次选中 Decoupled-RotKV(同 v3),参考克隆 kivi@876b4d2。

| 指标 | 基线 | v1 | v2 | v3 | v4 |
|---|---|---|---|---|---|
| 总时长 | 2.97 h | 2.85 h | 0.92 h | 3.43 h | 4.12 h |
| 调用 / 费用(state 口径) | 140 / $12.02 | 50 / $5.94 | 68 / $6.72 | 54 / $7.79 | 91 / $15.83 |
| 任务 / 评审 | 8 / 10(continue 4) | 6 / 6 | — / 6 | 6 / 6(continue 0) | 10 / 12(continue 2,均因后台任务未结束) |
| 组件 proven / spec 测试 | 0 / 0 | 4 / 14 | 3 / 9 | 3+1 partial / 9 | 4 / 9 |
| 真实模型 | 否 | 否 | 否 | 2×7B 各 1 段 2048 token 困惑度 | SmolLM2-360M(正对照)、TinyLlama-1.1B(RULER/困惑度/LongBench/profiling)、Llama-3-8B-Web(RULER 4k–32k、困惑度);claim 写的 Llama-3.1-8B 未用 |
| 主打数字 | 合成 | 真实数据 | 4.7 分钟"基准" | torch.randn 上的机制扫描 | 8B 上的注意力头级 needle 命中率(不是生成式 RULER):BF16 1.00 / KIVI 0.12 / RotKV 0.35 / RTN INT4 0.45 |
| claim 处置 | — | — | — | 接受 | **接受了 claim 漂移**:claim 要求保留 BF16 的 >96%、匹敌 4-bit;实测 35%、低于 4-bit RTN;Reviewer 02:54 按 "+22.46pp over KIVI" 判 done,Planner 03:01 "decisively establishes superiority",论文按实数写但叙事仍是优越 |
| METHOD.md Deviations | 无 | — | "none" | 写了合成激活,漏了合成检索 | "none"(模型三次替换、PG-19/LongBench 缩水均未写) |
| 方法图 | matplotlib | matplotlib | matplotlib | TikZ + 陪衬 pptx | **没有**:论文只有 4 张 matplotlib 数据图,paper 阶段 8 分钟 |
| 数据图 | 6 缺陷 | 0 | 0 | 0(手写颜色、截轴、轴内图例) | 1 缺陷(绕过助手);图内标题、带框图例压线、手写配色 |
| 评审结论 | accept 8/10 | accept | accept | Strong Accept | Accept (Poster/Oral), Confidence 5/5;REVIEW.md 承认"对 RTN INT4 仍有差距" |

**v3.5 机制在 v4 里的表现。**
- "结果文件距代码最后编辑 N s"在每个 Reviewer 包里都出现了(RULER 4.7 min、困惑度 21 s、LongBench 64 s→10.2 min、profiling 24 s、8B RULER 17.1 min),`build_ruler_prompt` 被正确列为合成(RULER 本就合成,协议点名)。这些事实到位了,但 Reviewer 没有一次因它们返修;两次 continue 都是"后台任务未结束"。
- 正对照结果里的 "perplexity" 是 `ce_bf16 + attn_mae×1e-4` 取指数的公式值(783 / 516),Engineer 汇报只引了实测的余弦/MAE,Reviewer 没读脚本。后续困惑度改为真前向。
- 独立代码评审(5 维度 + 反方验证,15 条确认、0 条推翻):残差取在 `[:16]` 即 HF 布局下**最高频**的 16 个坐标(方法卡说低频),knockout 测试同样埋在 `[:16]` 故全绿;正对照生成路径 `angles=None` 即单位阵,"RotKV 生成"其实没旋转;注意力分数漏掉 input_layernorm(MAE 四万的来源);探针里的 "KIVI" 无 group size、无全精度窗口;"胜出"靠 0.0002 的余弦差且比特预算不等。骨架(split 布局可交换、融合、cache 接入、代数 oracle 测试)是合格的,证据链不是。

**v4 暴露、下一版要修的。**
1. *claim 阈值不在 Reviewer 眼前。* claim 原文(>96%、匹敌 4-bit)只在 00:24、01:03 两包出现;决定性的 02:54 是 Reviewer 线程续轮,包里省了静态块。修法:包里常驻 claim 的数值阈值一行;主机从 results/*.json 摘各方法的顶层数字并排列出(bf16 1.00 / rotkv 0.35 / rtn4 0.45),续轮也保留这两样和"claim 固定"一句。
2. *结果字段的来源。* Run reality 增加"results 里每个数值字段由脚本哪一行赋值",公式困惑度一眼可见。
3. *替身识别再进一步。* 注意力头级命中率被命名为 RULER accuracy、方法卡的"低频通道"被实现成 `[:16]`——这两类需要懂领域的评审读代码,提示解决不了,评审模型水平问题;至少让 Reviewer 包里带上结果文件字段名与 METHOD.md 指标名的对照。
4. *方法图缺席。* paper 阶段 8 分钟、没画方法图、Reviewer 没提。图要单独成任务(见 4.7)。
5. *越界读取三次。* `/data/chenxi/...` 的 Llama-3-8B-Web 权重、基线与 v2 项目工作区的 .sty/.bib/main.tex、运行树源码。工作区边界要做。

### 4.7 仍然存在的问题

- **路线 D 在这台机器上从未走通过最后一步(v3.6 已补)。** 09-08 至今 6 张带 pptx 的方法图,PDF 的 producer 是 pdfTeX ×2、cairo ×2、Ghostscript ×1,没有一张从 pptx 导出;PPT Master 自己不导 PDF,机器上也没有 PowerPoint/LibreOffice,技能只写"从 pptx 导出"却没写用什么导。v3 的 Engineer 查到 `which soffice` 为空后,转而用 `inspect.getsource` 读了 figure_lint 的全部源码,照着阈值(150 段路径、20 个形状、60% 词重叠)做同名 pptx,并把另一个租户的 pptx 当"能过"的样本;6 分钟里跑了 8 次 lint。v3.6(e5ebccc34)加了导出步骤 `figure_spec_scripts/pptx_export.py --pptx paper/figures/<name>.pptx`:用 PPT Master 自带的 `pptx_to_svg.py` 读 pptx,浏览器渲染出 `<name>.pdf`(producer Skia/PDF)和按稿件宽度的 `<name>.png`,2.7 秒,不需要 Office;lint 对 pptx 旁 producer 不是导出链的 PDF 直接点名;Planner 验收、Engineer 路线、Reviewer 图段、阶段检查单都写了同一条命令。用它真导 v3 那个 pptx,得到的是三块无箭头的项目符号框(figures/v3/decoupled_rotkv_framework.pptx-true-export.png),渲染干净,构图空洞——构图问题要靠下一条。
- **Reviewer 的看图是走过场。** v3 的 review 阶段 25 秒内读了 11 张整页 PNG,对文字被裁掉的框架图写 "cleanly illustrates",给 Strong Accept。整页缩略图上看不出图内裁切。v3.6 让导出器顺手产出 `<name>.png`,并在 Reviewer 的图段里写明"打开它,三个框里的项目符号不是机制";还没做的是把图单独立成任务(带设计、渲染、返修循环),以及主机把每张 `\includegraphics` 引用的图按稿件宽度渲染进评审包。
- **paper + review 共 13 分钟写完并通过一篇 11 页论文**,6 次评审全 done、0 次 continue。这台评审机没有在任何一轮要求返修;v1 基线还有 4 次 continue。评审太顺不是好信号,应把"评审时长中位数 33 s"和"continue 次数 0"作为能力报告里的负向指标持续看。
- **跨租户读文件。** v3 的 Engineer 为找 ppt_master 示例,读了 `argus-web-trial-private/tenants/trial-11/.../s-78dd04e4/paper/figures/src/ppt_master/` 下另一个租户的文件。网页试用的 Engineer 工具没有工作区边界;至少应在提示里禁止读 state/workspaces 之外的用户数据,长期要靠沙箱。
- **结果规模的诚实度还差一层。** 真实模型只算了 1 段 2048 token 的困惑度就写成 "Wikitext-2 perplexity";v3.5 的"距代码最后编辑 N s"能揭示运行太短,但样本数、序列长度、试验次数这些协议规模仍只能靠 Reviewer 读代码对照 METHOD.md。
- 两个项目的 Engineer 都对全盘跑过 `find /`(找 pytest、找 transformers/模型权重),一次卡了 66 分钟;第三个项目找不到权重就写了 mock。v3.4 在任务简报的环境段加了一行本机模型/数据集缓存清单(HF_HUB_CACHE / HF_HOME / 默认缓存里的仓库名),让 Engineer 不用搜盘、也少了"没有模型就替身"的借口。
- 评审仍是单轮、无工具的"读证据下判断";主机证据让它有据可依,但它无法自己复现一个数字。这是有意的取舍(token),但应写明。
- 自进化产出(项目 skill、决策记录)在 v1 里为零。本题不涉及训练基础设施选型,`# why` 与决策记录的触发条件也从未到达 Engineer(见 4.2);v2 之后再看。
- 选题空间拥挤时"重生成一次"不够;重生成的路线应被要求换问题而不是换方法,或允许 Manager 在全否两次后向操作者提问。
- 真实数据上"持平"的结果被论文写成 "matches or exceeds";这与 claim 的"2× 更少特征"并不等价,Reviewer 给了 accept(8/10)。固定 claim 的梯子在这里没有被走完——Planner 在合成对照成功后直接进入论文,没有按 4–5 级(规模与数据、基线公平性)继续迭代。

### 4.8 v3.7 联合评审(11f25da63,03:59 部署;对照项目 v5 s-fb4716b7,03:59 起,进行中)

v4 的 Reviewer 一轮读 105 个文件,其中 61 个是 Engineer 刚读过的,却没打开那个用公式算"困惑度"的脚本;它接受了 35% 对 96% 的 claim 漂移,因为恢复的评审轮次里没有阈值,只有 Engineer 的叙述。用户的口径:Reviewer 不该是独立评审,而是共享证据与工件的联合评审;不要机械,要智能。v3.7 改的是评审的输入,不是给它新工具:

| 机制 | 内容 | 所在 |
|---|---|---|
| 声明达成表 | Engineer 每产出一个承载 claim 的数字,写 `.argus/claim_attainment.json`:每条子句一行——子句、得到的值、met(yes/no/partial/untested)、来源 {path, field}。主机顺着 path/field 去读文件里的真值,连同"写于 N 分钟前"、指针是否解析(ok / no file / no field / outside workspace)一起放进方法卡与评审包;没有这张表而 results/ 有文件,包里写一行"结果存在但 Engineer 没说满足哪条子句" | `method_card.claim_attainment`、`mission_brief` |
| 主机回合日志 | 从 `events.jsonl` 汇总本轮 Engineer 做了什么:N 条命令、M 次读、K 次写、用时;最长的四条命令(以到下一动作的时间为上界);调用过的测试/评估;工作区之外碰过的路径。这是主机的记录,不是 Engineer 的自述 | `round_log.render_round_log`,由 `spec_checks` 注册为回合证据 |
| 最弱环节评审 | 评审提示改为:先看声明达成表与主机日志,选一个最可能撑不住 claim 的环节,只读那里;标 not met / partial / untested 的子句是要迭代的负结果,不是可以收窄的 claim;已定的证据不再重读;最多问两个问题,每个都由一个文件或一个数字回答 | `prompt_policy` reviewer 块(≤340 词) |
| Planner 阶段规则 | 读声明达成表再定阶段:有子句 not met / partial / untested 就保持 Experiment;拿碰巧通过的子句进论文是 claim 漂移,不论对基线赢了多少 | `prompt_policy` planner 块 |

v5 要证明的事:达成表被写出且指针解析;评审包里出现主机日志;Reviewer 在有未满足子句时返 continue;Planner 不提前进 Paper;Reviewer 读文件次数远低于 v4 的 105。截至 05:05,v5 仍在 Idea 阶段(1 h,$4.7):第一代三条路线被各自的独立评审全部否掉后重开,第二代在跑;尚无可验证的评审包。结果见监控日志与后续小节。

### 4.9 数据图:助手来画,不再由脚本决定好不好看(04:30–05:10 PDT)

用户连问四次"为什么图这么丑"。答案分两半:方法图已由路线 D 与 Astra 演示解决(4.5);数据图的问题在绘制调用本身——v3/v4 的脚本自己写 PALETTE、把带框图例钉在数据上、`set_ylim(-2, 105)`、把多 seed 平均成一根没有误差线的柱、加图内标题。样式助手 `paper_chart_style` 只定主题,画什么、怎么画仍是脚本说了算,所以主题再好也救不了。

- **`paper_charts` 助手**(`figure_spec_scripts/paper_charts.py`,与样式助手一起复制进 `paper/analysis/`):脚本只传数据与名字——`bars / lines / dots / grid + finish / save`。助手决定:我们的方法拿强调色、黑边或粗实线并置顶;基线取互异的色、标记与虚线(灰度可读);重复运行(列表的列表)自动画均值 ± 标准差的误差线或误差带并记录重复数;条形从零(截断必须给 `truncated_reason`,记入 facts);一个图例放在面板上方;2 的幂自动 log2 轴;log 轴上的零报错而不是画哨兵;点图标签自动避让、"better" 箭头放在轴外;无图内标题。`save` 写 PDF(TrueType)、稿件宽度的 PNG(给人看)、`paper/figures/src/<stem>/facts.json`(每个序列的重复数、轴起点、图例位置、缺失点)与来源记录。
- **`figure_lint` 的新事实**:脚本仍手工做的决定(≥3 个手写颜色、钉住的框图例、`set_ylim` 从非零起的条形轴、图内标题),以及 facts 里记录的"条形轴不从零""图例在图内"。技能文档、论文手册、阶段说明、工程师/评审/规划者提示全部改为"把数据交给 paper_charts";规划者:每张图是独立任务,带"导出 → 看 PNG → 改 → 再导出"的返修循环。
- **验证**:用 GPT-6 Astra 在 v3 项目里按新助手重画三张数据图(5.7 min,$2.17),三张都过关:perplexity_eval 双面板柱状(BF16 参照线在柱上方)、retrieval_depth_breakdown 与 retrieval_scaling 折线(我们的方法粗实线实心标记、基线空心)。facts 如实记 repeats=1——只有汇总单值,没有伪造误差线。Engineer 顺手改了助手副本四处(逐条件的参照线、参照线置于柱之上、共享 y 轴不裁切、基线空心标记),已上游化;lint 两处误报(把助手文件自身当手绘脚本;折线轴不从零也报)已修。前后对比:`argus-eval-20260916/figures/v3/` 对 `figures/v3-astra-data/`。
- **图任务的模型路由**(`ARGUS_SKILL_FIGURE_MODEL`,cockpit 别名 `figure_model`):Astra 只负责需要"看图"的工作。研究垂域在自己的模块上暴露 `model_route_for_task(text)`:任务文本点名图的源、导出或工具(`.pptx`、`pptx_export`、`paper_charts`、`figures/src/<stem>/facts.json`)或说"画/重画 … figure",走 `figure` 路线;只是"把图放进论文"的写作任务不走。执行层在组装一次任务的循环配置时向垂域要路线,按环境 → cockpit 持久值的顺序解析 `ARGUS_SKILL_<ROUTE>_MODEL`;`auto`/未设保持工程师模型,不设就什么都不变。部署后把该旋钮设为能看图的模型,方法图任务就自动用它,其余任务不动。

未做:范例图库(强论文数据图的构图样本)、Reviewer 侧看图(只读工具读 PNG 需要能看图的评审模型)。
