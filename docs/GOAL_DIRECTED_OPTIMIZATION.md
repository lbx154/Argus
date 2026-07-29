# 目标导向优化合同

本合同统一 Planner、Engineer、Reviewer 与 Skill memory 在可量化任务中的决策顺序，
防止系统把“做了很多工作”误当成“接近 operator 目标”。

## 1. 指标权威顺序

从高到低：

1. operator 明确要求的成功目标与外部 completion gate；
2. 合法公开的 reference baseline、竞赛方案或 SOTA；
3. 当前 worktree 的本地 incumbent；
4. 运行时间、kernel 数、序列化、文档完整度等次级指标。

本地指标变好但没有缩小第 1 层差距，不自动构成 forward progress。只有当次级指标被
实测证明会阻塞下一次主指标实验时，它才是高影响 prerequisite。
存在 controller-owned external completion gate 时，框架保留 vertical 的 stage/checklist，
但不注入可能重定义目标的 vertical role banner；例如 accuracy 竞赛不得被 speedrun banner
改写成“必须发明 kernel、禁止 task-specific 公开资料”。

## 2. 公开资料边界

operator 允许公开研究时，task-specific 论文、竞赛 discussion、公开 notebook 和源代码
属于合法方法知识。禁止的是导入答案、私有标签、现成预测、gold submission 或越权私有
反馈。Skill 只能提供方法，不能自行缩窄 operator 已允许的资料范围。

## 3. Planner -> Engineer 合同

每个可量化任务必须给出：

- `TASK_IMPACT_SCORE=1..5`；
- `TASK_IMPACT_AREA`，说明作用于主指标还是已证明的 enabling bottleneck；
- `TASK_EVIDENCE`，说明为什么该任务有能力缩小当前 target gap。

主目标差距较大时，Planner 优先复现更强公开基线或切换数据、表示、架构、训练策略，
不得用 profiling、kernel、校准、状态抄写或“新颖机制”填满 backlog。
提议新任务前必须索引已有实验结果并做语义去重；改名、换目录或轻微参数变化不构成新机制。
所有 validation、OOF、校准和 blend 选择预测必须由未见该行标签的模型产生；全量训练后的
final refit 只能用于 test inference，不能回头充当 holdout 证据。

## 4. Reviewer 合同

Reviewer 分开判断两件事：

- bounded implementation 是否正确完成；
- 这轮是否让 operator 目标前进。

前者可以是 `done`，后者仍可为 `planner_report.forward_progress=false` 与
`plan_signal=reconsider`。这会保留真实工程结果，同时要求 Planner 更换低杠杆路线。
该字段必须贯通 `ReviewDecision` 与 mission settlement；它是一次 mission 内部的控制
信号，不扩张公开 review event。Supervisor 只统计 Reviewer 明确给出的布尔判断，不从
分数或文本关键词自行推断。

## 5. 状态与上下文

controller 写入的 gate/feedback 文件是动态成绩的权威事实源。`CHECKPOINT.md`、
`RESULTS.md`、`GROUND_TRUTH.md` 等文档只保存结论、失败机理和重放方法，不为每一次成绩
变化重复抄写相同状态。这样可以减少 stale-fact 修复任务和长线程上下文腐烂。

## 6. Skill 负反馈

Skill matcher 必须看到成功与无效 reuse 计数。失败多于成功时不得把该 Skill 当作默认
高匹配；至少两次失败且失败多于成功的非 protected Skill 不进入正常匹配池。修订或人工
维护仍可恢复它。Skill 的复用证据不能覆盖 operator 目标和合法来源政策。
