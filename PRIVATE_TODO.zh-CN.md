# 私有仓库 TODO

> 适用范围：私有仓库 `lbx154/argus-skill`。其 `main` 分支已于 2026-08-06
> 与公有仓库 `lbx154/Argus:main` 同步。
>
> 公有 `main` 是代码权威来源。私有 `main` 只能保留经过明确批准、列入白名单的
> 私有叠加内容，例如本 TODO。不要把旧私有历史整体合并回来。

# 团队产品与架构待办

这份清单汇总了 Shan、Xuchuan、Jinlang 和现有 Argus 用户发现的问题。
优先级看的是用户能否真正完成目标，而不是实现起来是否方便。

## 协作原则

- 下列每一项都要有一位直接负责人（DRI）、一个 issue 和一个独立主题分支。
  存储、提示词、会话和 vertical 重构不要混在同一个改动里。
- 从真实项目里可复现的轨迹开始。只看到代码味道，不足以证明需要改变行为。
- 每个 PR 都要说明当前行为、目标行为、迁移与回滚方案、针对性测试，以及希望
  改善的可观测指标。
- 不要用关键词规则替代 Agent 的判断。权限和状态转换应当明确编码，语义判断
  仍交给负责该环节的角色。
- 不要强迫角色输出严格 JSON，也不要要求它们遵循面向模型的输出 schema。
  必需语义可以通过工具调用、运行时持有的状态，或对自然回答的宽容提取来保存。
- Planner 写出的 mission 是工作计划，不是不可修改的合同。用户目标以及安全、
  权限边界保持不变；后续证据指向更好路线时，技术选择可以调整。
- 每个 gate、assertion、wrapper、fallback 和兼容路径都必须有明确职责。不要因为
  “以防万一”不断加层；一旦明确了检查归属，就删除重复检查。除非证明有等价保护，
  不得削弱安全、权限、数据完整性和进程隔离机制。
- 状态标签：`unassigned`、`investigating`、`design-review`、`implementing`、
  `experimenting`、`blocked`、`done`。

## 优先级总览

| ID | 优先级 | 严重程度 | 紧迫度 | 建议负责人 | 依赖 |
| --- | --- | --- | --- | --- | --- |
| ARGUS-P0-01 | P0 | 严重 | 已完成 | Runtime/state | 无 |
| ARGUS-P0-02 | P0 | 严重 | 已完成 | Mission loop | P0-01 checkpoint invariants |
| ARGUS-P0-03 | P0 | 严重 | 已完成 | Manager/contract | 无 |
| ARGUS-P0-04 | P0 | 高 | 已完成 | Planner/goal | P0-03 |
| ARGUS-P0-05 | P0 | 严重 | 调查中 | Runtime/Planner | 真实 FLA 低效轨迹 |
| ARGUS-P1-01 | P1 | 高 | 已完成 | Mission progress/evaluation | 持久 frontier + 回退 envelope |
| ARGUS-P1-02 | P1 | 高 | 已完成 | Agent/session integration | canary 否决 mission 默认化，保留 fresh |
| ARGUS-P1-03 | P1 | 中 | 已完成 | Skill system | 自然触发 10/10，旧字段已迁移 |
| ARGUS-P1-04 | P1 | 中 | 已完成 | Architecture/verticals | core-owned contract |
| ARGUS-P1-05 | P1 | 高 | 已完成 | Role prompts/UX | 独立盲评 10/10 偏好新版 |
| ARGUS-P1-06 | P1 | 高 | 已完成 | Runtime/architecture | 删除无行为兼容层 |
| ARGUS-P1-07 | P1 | 高 | 未分配 | 专业角色/vertical | P0-05 |
| ARGUS-P1-08 | P1 | 高 | 未分配 | 实验生命周期 | P0-05、P1-07 |
| ARGUS-P2-01 | P2 | 中 | 后续试验 | Persistence | P0 状态语义稳定后 |
| ARGUS-P2-02 | P2 | 中 | 持续进行 | Evaluation/observability | 支撑全部事项 |
| ARGUS-P2-03 | P2 | 中 | 未分配 | 执行效率评估 | P0-05、P1-07、P1-08 |

---

## 执行效率专项 — 让 Argus 成为工程师，而不是证据收集员

FLA campaign 暴露了系统性问题：Planner、Engineer、Reviewer 和 Manager 重复读取
同一仓库与证据，真正应该执行的动作却迟迟没有派发。当前控制层对“审计完整”的奖励
高于对“首次有效动作、实验吞吐和交付速度”的奖励。本专项必须在保持正确性和权限边界
的同时，让立即行动成为默认路径。

### 不可违反的设计约束

- 不增加哈希绑定证据链、面向模型的严格 JSON schema、关键词路由或重复 gate。
- 下述预算是可配置的执行默认值，不是科研价值阈值。不能可靠预测收益的想法，只要机制
  可证伪，也可以进入低成本 decisive probe；不得要求统一收益百分比。
- 每个决策只有一个 owner。Reviewer 已完成的结论，除非出现新证据、冲突或用户拥有的
  变化，否则不能再让另一个模型从头审理。
- 只增加少量端到端回归场景；不能用庞大合成测试矩阵代替真实 canary。

## ARGUS-P0-05 — 让控制层以行动为先

**状态：调查中。** 真实 FLA 轨迹显示：Planner grounding 过长、每个角色重复读文件、
旧 workdir/stage 泄漏、合法任务被错误过滤，以及 Reviewer 完成后 Manager 再次审计。

**问题。** 控制层没有清楚区分“信息已经足够，可以行动”和“也许还能再读一个文件”。
它在每个角色里重新建立仓库认知，以 stage artifact 数量替代决策，并让普通阶段转换再次
经过昂贵语义判断。

**工作项**

- [ ] 由 runtime 在内存中生成一个 `MissionBrief`，包含权威 workdir、stage、Git 状态、
      相关变更路径、硬件/工具、原生测试与 benchmark 命令、最近决定性结果、当前交付物
      和缺失 gate。它只是上下文，不写进项目，也不形成证据账本。
- [ ] 任务实际解析后的 workdir 必须是 vertical、stage、policy 和 artifact 解析的唯一
      来源；增加“父 campaign 仓库 + 嵌套目标 worktree”的真实回放。
- [ ] 增加可配置的 Planner grounding 预算（工具调用和墙钟）。Brief 与已读事实足够产生
      合法任务后必须派工；延长预算时必须指出具体缺失事实。
- [ ] 在单个 planning cycle 内缓存不变的只读工具结果；重复读取时返回
      `unchanged since last read`，不再重新加载全文。
- [ ] 每个 stage 只定义一个主要交付物和一种直接任务形状。Scope 缺 artifact 时，host
      应直接生成一个 bounded Scope Engineer 任务，而不是开放式 Planner 调研。
- [ ] Stage checks 通过且必要 Reviewer 返回 `done` 时，框架直接推进。只有 rollback、
      证据冲突、scope/权限变化或用户决策才调用 Manager 做语义裁决。
- [ ] Reviewer 只检查改动面和决定性 acceptance evidence；没有新证据时不得重新研究或
      重开已解决问题。
- [ ] 修复仓库本地 Skill 发现：匹配的 `.agents/skills/**/SKILL.md` 应能直接加载，不得先
      native lookup 失败再手工读取。
- [ ] 建立轻量 exploration/candidate/delivery 三级证据：失败 probe 只保留紧凑实验卡；
      完整 correctness、profiling 和报告只用于晋级候选及最终交付。

**验收标准**

- 在冻结的 FLA scope 回放中，一个短 planning turn 就能派发缺失 scope 工作，不再重复读
  文件或因父目录旧 stage 错误拒绝任务。
- Reviewer `done` 能直接推进无争议 stage，不再触发第二次模型证据审理。
- 首次有效动作时间、重复读取比例、Planner Tokens 和墙钟相比基线有实质改善，同时
  held-out correctness 不下降。
- 安全、权限、隔离和用户批准边界不变。

---

## ARGUS-P1-07 — 增加专业执行角色和真正的 Discover 流程

**问题。** 同一个通用角色同时承担数学研究、环境安装、kernel 编码、测量和发布，最终
自然退化为最安全的公共行为：收集证据和做局部实现调整。

**工作项**

- [ ] Scope Engineer 在一个 mission 内固定 API、可改范围、硬件、oracle、benchmark 和
      只读参考边界。
- [ ] Discover 路由给 Algorithm Scientist：推导公式与数据流，比较至少三种本质不同的
      重构，说明消除的工作/存储/通信、正确性或误差、primary prior art、未知量和最便宜
      decisive probe。
- [ ] 不设置统一预期收益阈值。按照项目 leverage、测量噪声、部署频率、实现成本，以及
      延迟、内存、通信、扩展性、数值或覆盖价值决定是否做 probe。
- [ ] Environment Engineer 只安装所选算法需要、且项目原生支持的 lockfile/extras，
      不安装所有可能工具。
- [ ] Kernel Engineer 接收已选算法与明确 probe 后才编码；Release Engineer 只负责最终
      交付，不能重新开启优化。
- [ ] Continuous 项目必须完成一次 delivery 后，才能开始下一轮 Discover。

**验收标准**

- Discover 在生产 kernel 修改前产出经 Reviewer 接受的算法决策，普通 tiling/autotune
  不会被包装成算法创新。
- 收益未知但机制合理的想法可进入低成本 probe；广泛热路径上的 1% 改善不会被全局阈值
  拒绝。
- Delivery 任务绝不修改算法或启动新实验。

---

## ARGUS-P1-08 — 使用候选组合与分级实验信息

**问题。** 增量 attempt 使用不断变化的 baseline，最早 supported 结果可能被误当成最佳
交付；失败想法也承担了与晋级候选相同的流程成本。

**工作项**

- [ ] 保留小型候选组合：机制、正确性、可比较指标、最差情况、适用范围、实现成本和
      未解决风险。不能按 attempt 名、完成时间或文件顺序选 winner。
- [ ] 每个 delivery cycle 固定一个 clean baseline。增量测量可指导探索，但最终候选必须
      对同一 baseline 比较。
- [ ] 用明确的 `continue`、`probe`、`select` 或 `deliver` 决策替代 first-winner 自动交付。
- [ ] 失败探索只保存紧凑实验卡：假设、消除工作、decisive probe、观察和结论；晋级后才
      增加完整矩阵。
- [ ] 把不确定性作为一等信息：有证据时给区间；没有时写 `unknown` 和可消除未知的实验。
- [ ] 保存诚实负面结果，但不把原始 profiler 日志注入后续角色 prompt。

**验收标准**

- 多个候选对统一 baseline 比较时，较晚的更强候选能够胜过较早 supported 候选。
- 负面 probe 保持低成本，不触发完整交付证据流程。
- 不依靠不透明身份元数据，也能从工程取舍理解候选选择。

---

## ARGUS-P2-03 — 测量并自适应执行效率

**工作项**

- [ ] 记录可安全披露的控制层指标：首次任务/写入/测试时间、派工前 Planner reads、重复
      读取比例、角色墙钟、每个接受候选的 Tokens/成本、每小时实验数、Reviewer 重审次数、
      winner 到 PR 的耗时。
- [ ] 在事件中区分有效执行和控制层工作，使产品无需检查私有推理也能统计 action ratio。
- [ ] 从真实软件、kernel、研究和证明轨迹建立基线；测量分布后再设 canary 目标。
- [ ] 按 stage 复杂度与实测价值调整 Planner/Reviewer 执行预算，不能把 runtime 预算变成
      科研价值阈值。
- [ ] 每周发布简洁对比：基线、候选策略、正确性、延迟、成本、重复工作和
      ship/revise/stop 决策。

**验收标准**

- 效率改动按端到端目标完成和 held-out 质量评估，不只看 Tokens 或文件数下降。
- 即使 action ratio 改善，只要正确性、权限处理或恢复能力回退，就不能上线。
- FLA 回放证明派工更快、有效实验吞吐更高，且不再退回证据收集循环。

---

## ARGUS-P0-01 — 保证人工批准与恢复在事务上保持一致

**状态：已完成。** 实现在 `e9bfae30caf7`（release
`0.1.1+ef1ffc08e1f034b6`）。全量测试套件通过（共收集 4,396 项，原有 skip
保持不变）。

**问题。** 用户批准一个 Argus 决策并恢复运行后，backlog、campaign、daemon、
decision card 或 lifecycle 状态曾可能停在不同版本，导致目标无法继续。

**已完成工作**

- [x] 新决策绑定 project/session id、campaign generation、backlog item id、
      decision id 和预期 revision。
- [x] 在调用 Manager 或修改状态前执行 compare-and-swap；过期决策返回 `stale`，
      不修改当前状态。
- [x] 在一次原子 backlog 更新中保存决策完成、continuation 创建、依赖重接线、
      resolution identity 和重启意图。
- [x] 重试和并发提交保持幂等：第一次返回 `accepted`，相同请求返回
      `already_applied` 并复用原 continuation。
- [x] 增加 continuous state 幂等校准，并为接受或停止决定写入持久 transcript、
      UI 和审计记录。
- [x] 增加过期 revision、旧 campaign generation、并发/重复提交、重新打开状态、
      注入写入失败和 stop replay 测试。
- [x] 重建 Web/TUI release 产物并运行全量测试。

**验收标准**

- 同一个决策可以重复提交，但只产生一次 mission 转换。
- 过期批准绝不修改当前状态。
- 重新打开项目后，可从持久化状态得到同一张已完成决策卡、continuation 和 campaign
  意图。
- API 明确报告 `accepted`、`already_applied` 或 `stale`，不再只给笼统的
  state mismatch。

---

## ARGUS-P0-02 — 用进展判断取代固定的 24 轮中断

**状态：已完成。** 已在 `e9bfae30caf7` 实现并通过测试。

**问题。** 当 Reviewer 一直返回 `continue` 时，
`SupervisedConfig.hard_escalate_rounds=24` 过去会强制结束 mission。但长程任务即使
超过这个边界也可能仍在有效推进，而且可观测指标通常不是单调变化的：加强证明
invariant 可能暂时破坏已经通过的 obligations；一次完整重构可能在接口收敛前增加
失败测试；一次失败实验也可能否定中间假设、减少不确定性。固定轮数会切断同一条
任务路线，把有限且有价值的局部回退变成另一个无关 mission。

**已完成工作**

- [x] 复用 Reviewer 提供的 `planner_report.forward_progress` 判断，不从文件数、
      通过数、benchmark 分数或关键词推断进展。
- [x] 在持久化 round review 事件中增加 `forward_progress` 和 `plan_signal`。
- [x] 有明确进展判断的 mission 可以超过 24 轮；尚未达到停滞阈值的有限局部回退
      也可以继续。
- [x] 保留用户停止、预算、backend failure、decision timeout、semantic stall、
      无输出和最终 `max_rounds` 保护。
- [x] 继续使用 `CHECKPOINT.md` 传递同一个 mission；不能只因为轮数到了就替换目标。
- [x] 更新 Reviewer 指引，区分仍有产出的内部工作和真正外部阻塞，并明确写出进展判断。
- [x] 增加真实 26 轮回归测试：第 24 轮局部回退，后续恢复，第 26 轮通过 Reviewer。
- [x] 删除 bounded DAG node 的固定三轮 override、2–8 clamp，以及依赖任务标题中
      `matrix` 关键词启用的伪无限轮数例外；bounded node 现在复用同一套进展、停滞和
      全局 emergency `max_rounds` 策略。
- [x] 明确 round 不是 candidate Try；环境、命令、toolchain、benchmark 或 measurement
      infrastructure 修复轮次不消耗 Try，也不因轮数触发方向穷尽。

**验收标准**

- 即使一个或多个局部指标暂时回退，有产出的长程 mission 也能超过 24 轮而不被替换。
- 真正停滞的循环仍会按预算或无进展策略终止。
- continuation 保留原目标和持久 checkpoint，同时能说明为什么允许越过边界。
- bounded DAG node 不会在第三轮被强制替换；真实停滞仍由已有 no-progress、timeout、
  budget、backend 和最终全局上限终止。

---

## ARGUS-P0-03 — 让 Planner mission 保持可修改

**状态：已完成。** 实现在 `ec32c0c0ee28`（release
`0.1.1+52dd12145f5c7077`）。

**问题。** Planner 的方向写进 mission 后可能被误当成硬约束，让早期、信息较少的
选择压过后续反例和更好的路线。

**已完成工作**

- [x] 将用户目标以及安全、权限、trust、permission、resource 边界与 Planner 技术
      策略分开。
- [x] 在 backlog 和 mission context 中保存工作假设、目标贡献、允许的局部回退、
      改线条件和来源。
- [x] Reviewer 可以在不使用严格 JSON schema 的情况下报告被证伪假设、更优路线和
      涉及的 authority 层级。
- [x] `done` 或 `continue` 加 `PLAN_SIGNAL=reconsider` 会停止旧 mission，不再继续
      派发过期下游工作。
- [x] Planner 行动前，Manager 必须记录 `keep`、`revise`、`replace` 或
      `ask_operator` 决策。
- [x] 用户拥有的变化进入持久决策卡；技术替代方案不会被误报成用户 blocker。
- [x] 在持久事件中记录 challenge、处理、提交时间和 revision latency。
- [x] 增加 `0d-3` 回放：`no-gap` 替换 `skip-zero`，旧 plan nodes 在继续旧工作前被
      原子 supersede。

**验收标准**

- Planner 策略写进 mission 后仍是可修改的工作计划。
- 后续证据在争议工作继续前获得有记录的 Manager 决策。
- `0d-3` 按用户目标选择 alternative，不会重新运行过期 `skip-zero` 路线。
- 用户拥有的边界始终生效，revision latency 可测量。

---

## ARGUS-P0-04 — 提高 Planner mission 质量并衡量目标是否完成

**状态：已完成。** 实现在 `ec32c0c0ee28`；全量 Python 测试通过（共收集 4,451 项，
原有 skip 保持不变），Web 134/134、TUI 224/224 也全部通过。

**问题。** Planner mission 可能只优化方便的局部 checker，却不说明工作如何推进用户
真实目标、哪些内容可能回退，以及什么证据应当触发改线。

**已完成工作**

- [x] Planner mission 必须说明可修改假设、目标前沿贡献、预期临时回退、决策规则和
      决定性 acceptance check。
- [x] Planner 缺少 mission-quality 信息时会获得一次有边界的修复机会，而不是直接接受
      一个只追逐局部 checker 的任务。
- [x] continuous planning 和 bounded DAG planning 使用同一质量约定。
- [x] 质量信息写入 backlog、mission packet、Mission View 和 Web 任务检查器。
- [x] Planner 明确知道：checker 变绿只能验证产物，不能单独证明用户目标有进展。
- [x] Reviewer challenge、负面证据和 replacement 原因会进入下一轮 planning，同时保留
      现有重复工作保护。
- [x] 增加 mission acceptance、forward progress、replan、重复工作、首次有效进展时间、
      最终完成和未完成目标年龄等目标级指标。
- [x] 增加弱 mission 修复、plan replacement、持久化、metrics、prompt budget 和用户
      可见渲染的固定回放与回归测试。

**验收标准**

- 新 Planner 路径中的 mission 必须说明如何推进目标，以及什么证据会改变计划。
- checker 成功不会被单独当成目标级结果。
- 目标进展、浪费工作和 replanning 都能在持久 metrics 中查看。
- 固定 `0d-3` 回放会退出过期路线并提交连贯 replacement。

---

## ARGUS-P1-01 — 刻画长程任务中的非单调进展

**状态：已完成。** 通用 `TaskFrontier` 持久化目标/invariants、假设、证据、obligations、
代理变化、不确定性和下一决策点；Reviewer 用语义 transition 而非单一分数判断进展。
有局部回退时必须提供 cause/scope/budget/recovery/exit envelope，缺项会请求 replan。
三类非单调轨迹及跨重启恢复均有回归测试。

**问题。** 长程任务的进展有多个维度，而且往往不是单调的。同一条连贯路线中，某些
代理指标可能暂时变差，但整体状态仍在改善：加强证明会产生新的修复 obligations；
软件迁移可能暂时破坏中间测试，同时消除结构风险；研究或优化实验可能让主指标降低，
却排除了错误假设。要求少数计数器每轮都上升，或要求每个中间状态都变绿，会把有边界、
有解释的回退误判为失败。反过来，“非单调”也不能成为反复折腾的借口：临时回退必须有
明确原因、范围以及恢复或退出条件。

**工作项**

- [x] 审查软件/重构、研究/优化和证明任务中的代表性 mission 与 Reviewer 判断。
      Verus 只保留为一个具体案例，不作为整体抽象。
- [x] 定义可持久化的通用任务状态，包含目标和 invariants、当前假设/策略、产物与证据、
      已解决/新增/回退的 obligations、剩余工作簇、相关代理指标、不确定性和下一决策点。
- [x] 按语义上的状态变化定义进展，而不是压成一个分数。改进产物、消除风险、减少
      不确定性，或一次会带来有限修复债务但有依据的改变，都可能算进展；不要求任何
      单个字段始终单调改善。
- [x] 局部状态变差时要说明回退边界：改了什么、为什么预期会回退、允许的范围/预算、
      如何认定恢复，以及什么证据会触发 replan 或放弃。
- [x] 规划完整的状态转换，例如“修改共享抽象并修复受影响的一组问题”，而不是
      “让下一个方便的 checker 变绿”。
- [x] 让 Reviewer 区分有限且预期的回退、无依据或持续扩大的回退、能带来信息的失败、
      真正恢复，以及重复不变的失败。
- [x] 跨新会话和进程重启保存准确诊断、因果假设、已接受的修复债务和任务状态。
- [x] 增加覆盖 invariant 加强、多模块重构和研究/优化搜索的端到端用例。每个用例都应
      包含临时回退、多轮恢复或有依据的放弃，以及目标级结果。

**验收标准**

- Planner 和 Reviewer 不会只因某个代理指标暂时变差就拒绝有效路线，也不会只因一个
  指标改善就接受路线。
- 每个被容忍的回退都有原因、有边界、写入持久化状态，并带有恢复和退出条件。
- 重复不变的失败或持续扩大且无法解释的回退仍会触发诊断、replan、升级或终止。
- 一条长轨迹在多轮之间始终是连贯、可检查的任务状态，包括局部受挫和恢复过程。

---

## ARGUS-P1-02 — 设计有边界的角色会话生命周期

**状态：已完成。** 受控实验结果保留；新增两个真实软件交付 canary 后，fresh 联合成功
1/2，mission 0/2。mission 虽更快、重复读取更少，但外部 correctness 不稳定，因此生产
默认明确保留 `fresh`，mission/rolling 只作 opt-in 诊断。重复矛盾、Reviewer confusion 和
质量下降现在是显式结构化信号，只轮换目标角色，不做关键词推断。结果见
`docs/evaluations/ARGUS_P1_02_P1_03_CANARY_2026-08-08.md`。

**问题。** Manager 会复用 session，而 Planner、Engineer 和 Reviewer 通常每次启动新
session。新 session 会重复探索仓库，浪费时间和 Tokens；无限增长的 session 又会累积
过期上下文，降低输出质量。

**工作项**

- [x] 在两类受控仓库任务、每类两次重复上按角色衡量 prompt、provider Tokens、墙钟、
      重复仓库读取、Reviewer verdict 和 held-out correctness；原始轨迹仅本地保存，仓库中
      保留可披露聚合结果。真实用户轨迹基线仍是下一阶段 canary。
- [x] 完成 fresh、mission、rolling 三策略 live 配对评估并作决定：mission 相比 fresh
      墙钟降低 14.6%、显式 prompt 降低 33.5%、重复仓库读取降低 41.9%，联合成功率
      4/4 对 2/4；原 rolling 联合成功率仅 1/4。轮换 handoff 修复后 smoke 2/2，仍需
      完整矩阵复测后才能恢复 rolling 候选资格。
- [x] 实现小型、按角色隔离的 session capsule：只保存目标版本、仓库地图、已检查路径、
      最新关键输出、开放问题、checkpoint 指针和会话计数，不保存完整对话。
- [x] 完成全部轮换触发：turn/Token 上限、目标/分支/model/backend 变化、resume/backend
      故障和路径变化；重复矛盾、Reviewer confusion 与质量下降使用显式结构化角色信号，
      不做关键词推断。
- [x] 保持角色隔离：Planner、Engineer、Reviewer 使用不同 capsule/thread；Reviewer
      不继承 Engineer 私有推理，角色 session 不写 Manager 管理的 pipeline 状态。
- [x] backend 无法恢复时丢弃对应 thread，并从持久 capsule、mission packet 和
      checkpoint 进入新 session；同一设计兼容可恢复和只能新建 session 的 provider。

**验收标准**

- [x] mission 在受控 live 匹配任务上减少重复探索、墙钟和显式 prompt；但 provider
      input Tokens 增加 43.8%、成本增加 4.7%，因此不能宣称总 Token/成本下降。
- [x] 受控配对中 mission 的 held-out correctness 和 Reviewer 联合接受为 4/4，未低于
      fresh 的 2/4；真实项目 canary 已完成并否决 mission 默认化。
- [x] 上下文轮换明确、可观测，并能从持久化状态和进程重启中恢复。
- [x] 设计同时支持可恢复和只能新建 session 的 coding-agent backend。

---

## ARGUS-P1-03 — 完成 coding-agent 原生、按需使用 Skill 的方案

**状态：已完成。** 在两种模型、十次无显式 Skill 提示的 normal mission 中，相关正文
自然打开 10/10、held-out 通过 10/10、错误 Skill 0/10。另做四次包含真实 provider
selection 成本的 matcher+injection 基线，质量 4/4；自然按需路径平均墙钟略低，单次已知
成本也更低。旧 Reviewer `skill_ops` 字段和 knob 已删除；迁移 fixture 证明旧 verdict
仍可读取并安全忽略该字段。结果见 2026-08-08 canary 报告。

**工作项**

- [x] 审计 Manager、Planner、Engineer、Reviewer prompt 和 backend adapter；删除 Manager
      固定角色 Skill、software-grounding Skill 正文的直接注入，并去掉重复路径包装。
- [x] 定义最小发现约定：project → active vertical/domain → global；同层 OWN 优先、
      cross-role 仅 REFERENCE；只有 description 明确高匹配时才读取正文，不设 harness
      matcher/scorer。
- [x] 覆盖 Codex、Claude、Copilot、OpenCode、Pi adapter：Pi 使用显式 `--skill` 且关闭
      ambient discovery，其余 backend 使用同一 role-path fallback；已有参数化 contract
      tests。
- [x] 完成受控 live 按需测量：记录实际打开文件、读取字节、Tokens、成本、墙钟、
      有用/错误复用、Reviewer verdict、visible tests 和 held-out tests。相关 Skill 正文
      4/4 被读取并使 held-out 从 0/4 提升到 4/4；出现 1/4 错误复用。后续自然触发
      probe 为 0/2，说明“能按需读取”已经成立，但“会自然按需读取”尚未成立。
- [x] Agent 新建的角色 Skill 可从稳定 library root 立即发现；prompt 只持有路径，
      不需要重建 Skill 正文列表或重启 daemon。
- [x] 删除旧 `skill_ops` 字段与 knob；旧 event/session fixture 迁移回放确认历史 verdict 仍可读取。

**验收标准**

- [x] 普通 mission prompt 默认不包含完整的非角色 Skill 正文。
- [x] Agent 能通过原生 Pi loader 或可移植文件工具路径按需找到并加载相关 Skill。
- [x] 自然触发 10/10、held-out 10/10、错误复用 0/10；包含真实 selection 成本的公平
      matcher 基线已完成。早期 oracle 仍只作为不可部署的理论下界。

---

## ARGUS-P1-04 — 将 vertical 语义从 core 中拆开

**状态：已完成。** Core 只定义 `VerticalContract`，不 import 具体 vertical；paper
integrity policy 已移入 research，stage order、completion strength、role guidance、evidence
和 mission hook 都由 contract 声明。未知/不完整 plugin 明确失败，最小非 research fixture
证明新增 vertical 不需要中央条件分支。兼容分类与静态数据见 P1-04/P1-06 audit。

**问题。** Core 仍包含特定 vertical 的概念和命名，包括 paper/research target 和
full-paper completion。依赖方向因此反了，新 vertical 会被迫继承另一个 vertical 的假设。

**工作项**

- [x] 列出 `argus_skill/core`、`life/supervisor`、共享 prompts 和 event payloads 中
      vertical 特定的 import 和 symbol。
- [x] 把每处归为通用约定、兼容 adapter 或真实的 vertical 泄漏。不要机械重命名通用
      research 概念。
- [x] 明确依赖方向：core 负责通用 lifecycle/authority/event 协议；各 vertical 通过
      窄接口声明 stages、完成强度、角色指引、evidence schema 和可选扩展。
- [x] 把 paper/venue/full-paper policy 从 core 移到 research vertical 或注册能力中。
      只有多个 vertical 确实共用时，core 才保留通用完成来源排序。
- [x] 用注册或协议调用取代直接 vertical import；插件缺失或不兼容时明确失败。
- [x] 先提交保持行为不变的移动并配 contract tests，后续 PR 再删除兼容 adapter。
- [x] 增加最小的非 research 测试 vertical，证明 core 不依赖 paper、venue 或
      research-target symbol 也能运行。

**验收标准**

- Core 不 import 任何具体 vertical package。
- 新增 vertical 只需实现一个有文档的接口，不需要修改中央条件分支。
- 现有 vertical 行为和持久化状态保持兼容。

---

## ARGUS-P1-05 — 让用户看到的输出清楚、自然

**状态：已完成。** Manager、CLI review、mission completion 和 Mission View 现在先给结果，
再给具体原因与下一步；单独一个标签不再作为完整用户消息。五组 matched
样本的独立盲评为 10/10 偏好新版，事实内容无下降。证据见 P1-05 output review JSON。

**问题。** 有些 Argus 消息像自动生成的过程记录：重复用户请求、堆很多标题、使用抽象
措辞，却把真正结果埋在后面。用户不应该先解码输出，才能知道发生了什么或 Argus 需要
什么。单独输出拒绝标签就是常见例子：它作为内部状态也许有用，但用户看不出哪里失败，
也不知道下一步是什么。

Argus 应像一个靠谱队友那样表达：先说结果，用普通语言解释关键取舍，引用具体证据；
不确定或判断发生变化时也直说。用户看到的判断过程应容易跟上：什么变了、哪些选项重要、
为什么选了这一项。目标不是添加虚假的人格，也不是暴露原始思维记录。

**工作项**

- [x] 收集用户认为难读或明显机器化的 CLI、Web、通知和 decision-card 实例，并为每个
      实例配一版简短的人工改写。
- [x] 内部角色通信默认不出现在用户消息中，除非它能帮助用户做决定或理解故障。
- [x] 不要只展示 `GO`、`REVISE` 或 `BLOCKED` 等内部结论。状态确实有用时，
      后面必须跟普通语言的原因和下一步。例如：“暂时不能继续：validator 在 X 上仍然
      失败。Argus 接下来会尝试 Y，目前不需要你操作。”
- [x] 先说答案或当前状态；原因、证据和下一步只有在有帮助时再补充。
- [x] 用具体事实代替笼统表述：改了哪个文件、哪个测试失败、在等什么决策、找到了什么
      结果、还剩什么不确定性。
- [x] 减少重复总结、模板化转折、过多标题、夸张称赞和虚假的确定感。不能把这件事做成
      关键词黑名单。
- [x] Argus 做出或修改选择时，说明真正决定选择的取舍，以及什么新证据会改变判断。
- [x] 问题要具体：一次只问一个决定，说明为什么该由用户决定，并写清各选项后果。
- [x] 按不同界面调整长度和细节，不要所有地方共用一种回答模板。
- [x] 对匹配输出做独立盲评预筛，检查理解程度、偏好、事实准确性和找到下一步所需时间；
      两种独立模型对五组乱序样本均选择新版（10/10），未发现事实回退。

**验收标准**

- 用户无需阅读内部日志，就能找到结果、支持证据和下一步。
- 盲评者更偏好修改后的输出，理解题正确率提高，事实正确性不下降。
- 不确定性和判断变化用直白语言说明，不藏在自信的总结后面。
- 用户消息不能停在一个拒绝标签上；必须说明什么被阻塞、原因是什么，以及
  Argus 或用户下一步要做什么。
- 用户不需要理解 Argus 内部角色术语也能看懂问题。

---

## ARGUS-P1-06 — 减少 runtime 中不必要的复杂度

**状态：已完成。** 已审查 dispatch、review、resume 和 Web/API commands：删除 Manager
55 行 alias/re-export、两个 pending-question wrapper、八个 inert session config 字段、两个
死 knob，以及 research fallback；completion 与 vertical 依赖改为单 owner。认证、权限、
secret、sandbox、幂等和 crash recovery 保持不变。量化见 P1-04/P1-06 audit。

**问题。** Argus 已积累了重复检查、旧兼容分支、只做转发的 wrapper、gates、assertions
和 fallback 路径。其中一些确实保护真实边界；另一些只是在重复工作、遮住主路径、把可
恢复情况变成崩溃，或让一个小改动穿过很多层。仅因为“以防万一”保留的代码通常难以理解，
也很少有真正有用的测试。

**工作项**

- [x] 先选几条重要路径：mission dispatch、review、resume 和 Web/API commands，列出
      其中的 gates、assertions、wrappers、fallbacks 和兼容分支。
- [x] 对每一项记录它保护的故障或边界，以及证明它有用的测试。没有当前 caller、producer
      或失败案例的，标记为待删除。
- [x] 每个检查放在真正负责它的层。除非边界确实可能在 caller 处被绕过，否则不要每层
      重复检查同一个条件。
- [x] Assertion 只用于不可能出现的内部状态，不能用于错误用户输入、工具缺失、过期状态
      或其他 runtime 可以报告和处理的问题。
- [x] 删除只改参数名或转发调用的 wrapper。只有当 wrapper 负责 policy、translation、
      lifecycle 或真实兼容边界时才保留。
- [x] 审查会掩盖第一次错误的宽泛 catch、retry 和 fallback 链。宁可保留一条清楚路径并
      返回有用错误，也不要给出看似可用但实际错误的 fallback。
- [x] 简化 gate 链。留下的 gate 必须有一个 owner、一个存在理由和一个针对性测试。
- [x] 确认持久化状态和受支持版本要求后，用小 PR 删除过期兼容代码。
- [x] 对每条清理路径记录分支数、调用深度、删除代码量和回归结果；不能只用代码行数证明
      改进。

**验收标准**

- 已审查路径上的每个 gate、wrapper、fallback 和兼容分支都有明确用途，并有测试或已知
  边界支撑。
- 在不改变预期行为的情况下，已审查路径减少重复检查和调用间接层。
- 可恢复问题返回有用错误，不再触发 assertion failure 或静默 fallback。
- 清理不能削弱认证、沙箱、secret 处理、权限检查、数据完整性、幂等性或崩溃恢复。
- 维护者可以直接跟踪正常路径，不必穿过无行为的 wrapper 或过期分支。

---

## ARGUS-P2-01 — 评估混合持久化，而不是先假定“全文件”或“全数据库”

**问题。** `~/.argus-skill` 的文件组织复杂，并有很多 sidecar locks。数据库可能简化事务
和锁，但不透明存储会影响人工检查、调试、Git 式恢复和 Agent 工具访问。

**工作项**

- [ ] 盘点每个状态文件、writer、reader、lock、写入频率、大小、事务关系，以及人或
      Agent 是否需要直接检查。
- [ ] 收集真实的竞争、损坏、部分写入和恢复事故；不能只因 lock 文件多就重做存储。
- [ ] 比较三个原型：强化的 append-only 文件；SQLite/WAL；数据库负责索引/协调并导出
      人可读权威产物的混合方案。
- [ ] 为每类数据定义唯一权威来源，避免两个都可写的来源。
- [ ] 试做迁移、回滚、备份、导出和灾难恢复。
- [ ] 测试并发 daemons、崩溃注入、查询延迟和用户检查流程。
- [ ] 等 P0 lifecycle 事务语义稳定后，再用 ADR 做决定。

**验收标准**

- 选定方案在需要时提供原子的多对象更新。
- 人和 coding agent 都保留有文档的检查/导出路径。
- 迁移可逆，旧项目状态仍可恢复。
- 不能把 lock 数量作为唯一成功指标。

---

## ARGUS-P2-02 — 建立共享评估与可观测性矩阵

- [ ] 建立版本化、可安全披露的失败轨迹集，覆盖批准/恢复、非单调证明、软件重构、
      研究/优化轨迹、不明确目标、早期计划锁定（包括 `0d-3`）、过期 Planner 目标、
      重复探索和 Skill 加载。
- [ ] 统一定义公共指标和事件字段，避免每个问题做一套独立 dashboard。
- [ ] 分开运行组件 A/B 测试和端到端目标回放。只有组件改善但目标级结果没有改善，
      不能算产品成功。
- [ ] 每周发布表格，包含事项负责人、实验状态、回归状态和 ship/revise/stop 决定。
- [ ] 保留负面结果和已放弃设计，避免团队重复尝试。

---

## 建议执行顺序

1. **立即执行：** 用冻结的 FLA 轨迹实施 P0-05 action-first 控制层；把权威 workdir/stage、
   MissionBrief、重复读取复用、有限 grounding、直接 scope 派工和无争议阶段确定性推进拆成
   独立聚焦改动。
2. **P0-05 canary 成功后：** 实施 P1-07 专业角色路由，并回放 FLA 的 Scope → Discover →
   Prototype 路线。
3. **随后：** 实施 P1-08 候选组合和分级证据，让探索保持低成本，交付选择可比较的最强候选。
4. **持续进行：** 使用 P2-03 指标和现有 P2-02 评估矩阵，对比端到端质量、延迟、Tokens、
   成本、重复工作与交付情况。
5. **已完成基础：** P0-01 到 P1-06 作为回归约束保留，没有真实轨迹不得重开；P2-01 存储
   工作继续放在状态语义与执行行为稳定之后。

## P0 — 保证同步后的基线可运行

- [ ] 有控制地重启当前绑定 `127.0.0.1:8799` 的私有 Web 服务；它在仓库同步前启动，
      内存中仍是旧 Python 代码。
- [ ] 在同步后的 `main` 上验证干净安装：新建 venv，按公有说明安装，运行公有 smoke
      tests，并验证 CLI/Web 启动。
- [ ] 按同步后的公有配置 schema 检查本地未跟踪的 `config/`。凭据和机器本地设置不得
      进入 Git。
- [ ] 在运维文档中记录同步后的公有代码基线：
      `public main = 7db07ce1259d51391e0df2b79f00a1706ea255d8`；私有 `main`
      只增加获批的 `PRIVATE_TODO.md` 叠加。

## P0 — 保护历史和后续同步

- [ ] 把私有分支 `202686` 视为旧私有 `main` 的不可变备份，对应
      `f3439e8c2afdaa5e0f0ce6155edfdb47a6f3d300`。
- [ ] 在 GitHub branch rules 中保护 `202686`，禁止 force-push 和删除。
- [ ] 私有改动必须使用主题分支和 PR；私有 `main` 与公有仓库的差异只能来自明确的
      私有叠加白名单。
- [ ] 自动化公有到私有的同步：拉取公有仓库、备份观察到的私有 head、用
      `--force-with-lease` 更新私有代码基线，然后重新应用并验证白名单叠加。比较代码树时
      排除 `PRIVATE_TODO.md`。

## P2 — 仓库卫生

- [ ] 清理运行 checkout 中过期的 build/cache 目录，但不要碰 `config/` 或 `202686` 备份。
- [ ] 生成的 frontend bundles 和 release manifests 必须能从源码复现，不能手工修改。
- [ ] 定期验证：

  ```bash
  PUBLIC=$(git ls-remote https://github.com/lbx154/Argus refs/heads/main | cut -f1)
  PRIVATE=$(git ls-remote https://github.com/lbx154/argus-skill refs/heads/main | cut -f1)
  git merge-base --is-ancestor "$PUBLIC" "$PRIVATE"
  git diff --name-only "$PUBLIC..$PRIVATE"
  ```

  私有 head 必须直接继承声明的公有基线；差异只能包含白名单私有叠加（当前为
  `PRIVATE_TODO.md`）。

## 完成标准

- 私有 `main` 直接继承公有 `main`；排除获批叠加白名单后，两边代码树一致。
- 远端 `202686` 已受保护且可恢复。
- 私有服务运行在同步后的源码上。
- 任何恢复的私有叠加都应最小、经过测试、有文档，并留在主题分支；只有明确批准进入
  私有 `main` 并加入叠加白名单，或明确向公有仓库提交时例外。
