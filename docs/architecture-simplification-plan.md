# Argus 精简架构规划

Argus 不需要更多角色和 Gate，而需要按任务形状决定投入多少系统。

普通、单仓库、单交付物的工程任务走短链：Manager 只确定权限、Vertical
和 direct workflow；Engineer 完成工作；只有独立判断确有价值或操作者明确要求时
才调用 Reviewer。单阶段 direct 任务中，Reviewer 的 `done` 连同 deterministic
completion check 直接完成任务，不再让 Manager 对同一证据做第二次语义裁决，也不再
让 Planner 重复申请认证。开放研究、宽搜索、长实验和论文任务才使用完整的
Manager → Planner → Engineer ↔ Reviewer 链，并允许只读调研并行；共享代码和论文
主稿始终保持单写者。

这与公开经验一致。Anthropic 的多智能体研究系统在宽搜索任务上明显获益，但也报告
多智能体约为普通聊天 15 倍 token，并明确指出多数紧耦合编码任务不适合并行多写者。
Cognition 的实践同样支持“单写者 + 干净上下文 Reviewer”。格式方面，
[*Let Me Speak Freely?*](https://arxiv.org/abs/2408.02442) 发现严格格式约束会损害
推理；因此角色先用自然语言判断，末尾只留下 Host 真正执行所需的少数字段。旧 JSON
继续兼容，但不再出现在新 prompt 中。

## 共同上下文

不让四个角色并发编辑同一个 Markdown。目标形态是 Host 生成的
`handoffs/<mission>/MISSION.md`，它只是现有 `mission.json`、frontier、最新
Engineer delta 和 Reviewer verdict 的紧凑投影：

- Contract：操作者目标、不可变约束和验收；
- Strategy：Manager/Planner 当前路线、假设与改变路线的条件；
- Work：Engineer 最近的实质变化、实验反馈和仍缺什么；
- Review：Reviewer 的结论、矛盾、下一步或 programme-level reconsider。

每一节只有一个权威来源，角色不直接修改汇总文件。Host 在对应事件落盘后原子重建，
读取时只给角色需要的节，超长内容截断而不是拒绝任务。这样保留共享语境，同时避免
读改写竞态、四份重复摘要和角色互相继承盲点。迁移完成后再删除重复表达同一事实的
`CHECKPOINT.md`、frontier remaining-work、round handoff next-action 和 role capsule
字段；在证明等价前不删除 canonical event log。

## Vertical 拆库

先冻结现有 `VerticalContract`，让 core 只认识版本化接口、stage/profile、prompt
fragment、completion hook 和 iteration hook。随后增加 Python entry-point
发现机制，使内置 Vertical 与外部 Vertical 走同一加载路径。先迁移一个依赖少的
Vertical 做 shadow loading 与兼容测试，再迁移其余 Vertical；research 最后迁移。
`argus-verticals` 独立版本发布，Argus 保留一个薄兼容包和明确的 core/vertical
版本矩阵。迁移期间禁止 core 新增任何具名 Vertical import。今晚不移动仓库、不改
发布边界，只继续削减 core 对 Vertical 语义的硬编码。

## 对照基线

2026-08-25 的同题普通用户实验要求修复一个 6-test Python 函数：

- 直接 Copilot：27.79 秒，7 次模型 turn、6 次工具调用、1,982 output tokens、
  1 个 premium request，测试通过；
- Argus：代码同样一次修好，但 15 分钟后仍未自然结束；19 次模型调用、
  1,924,943 input tokens（其中 1,588,736 cached）、30,911 output tokens、
  约 7.5 个 premium request。Reviewer 已运行同一测试并判定 `done`，Manager
  又因摘要里没有重复测试输出而 HOLD，Planner 随后循环申请同一个认证。

本次把 direct final `done` 改为 deterministic completion，有限 direct 任务由
Manager 直接入队，并按 Vertical 契约决定是否需要 Reviewer；Planner 不再在每轮内联
写 Skill。相同任务的普通默认路径降到 **53.35 秒、2 次模型调用、167,558 input
tokens、2,512 output tokens**；显式要求独立 Reviewer 时为 99.55 秒、3 次调用。
直接 Copilot 仍快约 1.9 倍，因此短链还有固定启动/分类开销要降。后续比较不使用单个
漂亮案例下结论：按 METR
[time horizon](https://arxiv.org/abs/2503.14499) 思路覆盖不同人工时长任务，并按
[Scaffold Effect](https://arxiv.org/abs/2607.22585) 的建议同时报告成功率、墙钟、
token、premium request、模型调用、工具调用、追问次数和置信区间。
