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

_待工作流落地后填写:任务简报、实现简报模板、固定 claim 与诊断梯子、主机检查进 research vertical、锚点与评审包、wiki 面板、自评工具。_

## 3. 对照实验设计

同一实例(8985)、同一目标"写个iclr论文"、同一模型配置,新建项目运行;用 `python -m argus.verticals.research.capability_report` 每 15 分钟采样。比较维度:各阶段时长、调用次数与费用、METHOD.md 是否存在且组件被测试证明的比例、参考实现克隆、种子数与数据集、Reviewer 的 continue/done 与时长、图检缺陷数、沉淀的 skill/wiki 数量。

## 4. 结果

_待填写。_
