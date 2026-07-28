# Argus North-Star 架构审查与改进方案

> **历史设计评审，不是当前运行规范。** 本文以文中标注的旧提交为审查基线，保留当时的
> 判断和待决问题作为设计 provenance。当前权威层级见
> `docs/DESIGN_AUTHORITY.md`，当前架构见 `docs/ARCHITECTURE.md`。后续 main 已实现
> `core/project_contract.py` 的 GoalContract、`core/project_api.py` 的统一完成写入口，
> 将论文 gate 统一为 `full_paper`，删除未生效的 Dynamic Plan mode/signal/streak，并把
> 正常 mission round 收敛为固定 `Engineer -> independent Reviewer`。因此本文关于
> “GoalContract 尚未统一”“保留低风险 Engineer self-review”及末尾待决问题的表述只代表
> 当时状态，不能用于指导当前代码。

## 0. 文档目的与审查基线

本文总结并校正
`Argus-Current-System-vs-North-Star-Architecture(1).pdf`，提取其中值得采纳的
领导意见，并对照最新主分支说明：

1. 哪些判断准确；
2. 哪些判断需要限定或已经过时；
3. 最新代码已经实现了哪些 North-Star 能力；
4. Argus 应如何在不破坏现有执行骨架的前提下继续改进。

本次核对基线：

- 仓库：`/home/argustest/dev/boxiu/argus-skill`
- 分支：`main`
- HEAD / `origin/main`：
  `8d4d47b2c686628bf3d186a2f9480daafddaa584`
- PDF 标注的审计 commit：
  `eb57a6aa164421cb7ffc09505a720977283b4798`
- 当前仓库无法解析 PDF 标注的该 commit，因此 PDF 的 line-level audit 不能作为
  当前主分支的可复现事实来源。

本文只新增设计文档，不修改 Argus 运行代码。

---

## 1. PDF 的核心观点

PDF 的总判断是：

> Fan 的模型适合作为 Argus 的 North-Star / 元认知控制层，但不应推翻当前保守、
> 证据驱动的执行骨架，也不应变成无约束目标重写或无治理自我修改系统。

### 1.1 PDF 对当前 Argus 的概括

PDF 认为当前 Argus 已有：

- Manager、Planner、Engineer、Reviewer 四角色；
- LifeSupervisor、backlog、DAG 和长周期执行；
- vertical stage/checklist；
- durable project state；
- evidence-gated completion；
- budget/cost guard；
- skill/wiki 演化；
- operator-only blocker 和 HITL；
- fresh provider sessions 与 checkpoint continuity。

### 1.2 PDF 希望新增的元控制能力

#### GoalContract

将自然语言目标变成结构化、可版本化的目标契约，包括：

- 原始请求；
- 语义意图；
- 精确约束；
- 成功条件；
- 排除结果；
- 歧义；
- 修订历史。

#### Precise / Semantic clauses

区分：

- `precise`：必须精确满足、可机械验证的约束；
- `semantic`：需要 Reviewer 结合 rubric、artifact 和 evidence 判断的要求。

#### RolePlan

根据任务风险、不确定性、成本和验证深度选择最简单但足够安全的执行组合。

#### Hierarchical Memory

明确区分：

```text
不可变原始请求
→ Manager 认证目标
→ Planner 计划/DAG
→ Reviewer 认证证据
→ 临时 scratch/checkpoint
```

#### Typed HITL

对歧义、不可逆操作、protected change、预算扩张、隐私安全等场景使用结构化人工确认。

#### Governed self-improvement

允许系统产生修复、分支、测试和 PR，但必须保留审查、权限、回滚和人工治理。

### 1.3 PDF 的优先级建议

P0：

1. Typed GoalContract；
2. precise/semantic clause；
3. memory hierarchy；
4. typed HITL。

P1：

1. 动态角色/执行框架组合；
2. guarded replanning；
3. 区分 task-local tactic 与 reusable skill。

P2：

1. self-improvement PR 保持 gated；
2. source promotion/autocommit 保持 default-off 或受严格治理。

拒绝或推迟：

- 无约束持续目标重写；
- 仅靠 self-verification 宣告高风险完成；
- 无人治理的自动自我修改。

---

## 2. PDF 中准确且值得保留的判断

### 2.1 当前执行骨架的总体描述准确

以下描述与最新 main 基本一致：

- Manager 是 operator front door；
- Manager 负责 vertical 和 stage transition；
- Planner 负责 forward planning；
- Engineer 使用真实文件、工具和实验执行任务；
- Reviewer 在需要独立审查时判断证据；
- LifeSupervisor 负责 backlog、预算、依赖、pending question 和调度；
- provider session 在不同 round/item 之间保持隔离；
- CHECKPOINT 和持久化 Project 状态承担连续性；
- skills/wiki 存在 protected floor 和 promotion 约束；
- 当前成本控制强于 token-aware role composition。

### 2.2 对当前能力边界的判断基本准确

当前 Argus：

- 并不是完全通用的“任意工作引擎”；
- 没有统一的 precise/semantic typed goal representation；
- 没有成熟的 token-budget role optimizer；
- 没有统一、正式的 hierarchical memory type system；
- 动态 team/subagent 能力存在，但主流程仍有固定骨架。

### 2.3 风险判断准确

PDF 强调的风险值得直接吸收：

- Goal drift：修改后目标可能削弱 operator 原始要求；
- Circular self-verification：角色独立不等于真正的认识论独立；
- Weak token planning：有花费限制，不代表会优化上下文和角色调用；
- Self-modification governance：skill、overlay、source change 会影响未来行为；
- 原始请求、protected policy、预算和安全约束不能被 agent 偷偷弱化。

### 2.4 Skill / Tactic / Finding / Guardrail 分类很有价值

建议保留该分类：

| 类型 | 含义 | 建议生命周期 |
|---|---|---|
| Skill | 可迁移、可复用的方法 | 多次真实验证后跨项目传播 |
| Tactic | 当前任务的局部方法 | 默认留在 Project 内 |
| Finding | 带 provenance 的事实或结果 | 进入 evidence/event/wiki |
| Guardrail | 保护性政策或边界 | 不允许普通 agent 弱化 |

这与当前 project skill、shared skill、wiki source 和 protected floor 的方向一致。

### 2.5 “保留现有骨架、增加元控制层”是正确总方向

不应重写 Manager/Planner/Engineer/Reviewer 和 LifeSupervisor。

North-Star 应解决的是：

- 目标怎么稳定；
- 计划怎么保持忠于目标；
- 角色怎么按风险选择；
- 记忆怎么分层；
- 什么情况下必须问人；
- 项目何时真正完成。

---

## 3. PDF 中不准确、过时或需要限定的判断

### 3.1 审计 commit 与 remote 证据无法复现

PDF 标注的 `eb57a6a...` 在当前仓库无法解析。

PDF 还称 public/remote HEAD 无法认证，但当前仓库已经正常跟踪
`origin/main`，本次核对的 HEAD 与 `origin/main` 相同。

因此：

- PDF 可以作为设计建议；
- 不能把它当成当前 main 的权威 source audit；
- 所有“已实现/未实现”结论应以当前 main 重新核对。

### 3.2 “Planner 普遍将工作拆成 DAG”表述过强

当前确实存在：

- L4 Planner；
- bounded DAG；
- plan id/version/dependencies；
- backlog CAS 替换。

但并非所有任务都会经过同一 DAG 分解路径。DIRECT、STAGED、continuous Planner、
bounded DAG 和 supplemental task 仍是不同入口。

更准确的表述是：

> Planner 具备 forward planning 和 DAG 能力，但当前规划入口与执行 topology 尚未统一。

### 3.3 “Reviewer 是统一完成权威”需要限定

当前代码允许：

- 低风险任务由 Engineer 显式 self-review；
- vertical、stage-closing 或显式请求触发独立 Reviewer；
- Manager 提交 stage transition；
- LifeSupervisor、vertical certificate 和 lifecycle 共同参与 Project 完成。

所以 Reviewer 不是所有任务的唯一完成状态写入者。

更准确的描述是：

> Reviewer 是独立审查路径上的证据事实来源；Manager 是 stage/state authority；最终
> Project 完成目前仍由多个子系统共同表达。

### 3.4 “step_back 和 default-off Dynamic Plan 已实现”已经过时

旧设计曾描述：

```text
Reviewer plan_signal=reconsider
→ 多轮确认
→ Dynamic Plan 重写
```

但旧的 mode/streak/signal 没有形成真正有效的运行链路。

当前真实路径是：

```text
Reviewer status=replan_requested
→ 当前 round 结束
→ LifeSupervisor 记录 replan
→ Manager / Planner 替换后续方向
```

Stage rollback 仍由 Manager 决定。不能重新把已经失效的 Dynamic Plan signal 作为
“已实现能力”写回文档。

### 3.5 “没有 autonomous self-PR”对最新 main 是错误的

最新 main 已实现：

- 私有 self-maintenance worktree；
- Engineer 修复；
- independent Reviewer；
- local canary/rollback；
- GitHub push 权限检查；
- 自动 push reviewed branch；
- 自动创建 PR；
- PR 状态追踪；
- `auto_merge=False`，永不自动 merge。

当前实际策略是：

> 可自动开 PR，但不能自动 merge。

PDF 建议的是：

> 人工批准后才允许开 public PR。

这是一个需要领导/operator 明确决定的产品政策差异，不是简单的“缺功能”。

### 3.6 “拒绝 self-verification”与当前低风险策略冲突

当前保留低风险 Engineer self-review。

建议不要一刀切，而是：

- 普通低风险中间节点可保留 self-review；
- final Project Goal Gate、高风险、stage-closing、protected change 必须独立 Reviewer；
- self-review 不能绕过最终 Goal Gate。

### 3.7 Typed HITL 不能泛化成“所有外部写入都问人”

否则会破坏 autonomous research。

应强制 HITL 的是：

- 不可逆外部写入；
- protected branch/policy；
- 发布和 merge；
- credential/privacy/safety；
- 超预算；
- 原始目标的实质歧义或修改。

普通公开数据下载、Git fetch、benchmark 执行不应全部阻塞。

---

## 4. 最新 main 已经实现了哪些 North-Star 雏形

### 4.1 GoalContract：部分实现

已有：

- `MissionContext.request_anchor` / `original_request`；
- Engineer prompt 明确区分 original request 与 current task；
- `mission.json` 已包含：
  - objective；
  - acceptance check；
  - non-goals；
  - context refs；
  - plan id/version；
  - dependencies/tags；
- campaign identity 使用 objective SHA-256 和 generation；
- objective replacement 能触发 stage reset。

缺少：

- Project 级统一 `GoalContract`；
- precise constraints；
- semantic criteria；
- exclusions；
- ambiguities；
- contract revision history；
- Manager 修改语义目标时的统一 preservation audit。

结论：**已有 GoalContract 的数据碎片，但没有统一类型和唯一权威。**

### 4.2 Precise / Semantic clauses：尚未一等实现

当前 hard constraints、acceptance check、non-goals 和 checklist prose 中已经隐含两类要求，
但没有：

```text
clause.kind = precise | semantic
```

也没有统一规定：

- precise 由什么 verifier 检查；
- semantic 由什么 Reviewer rubric 判断；
- 两类证据如何进入最终 certificate。

结论：**概念存在于 prose，类型系统尚未实现。**

### 4.3 RolePlan：尚未统一实现

已有：

- Manager 选择 `workflow_mode=direct|staged`；
- Planner DAG；
- team/subagent；
- per-role backend/model/effort；
- independent review policy；
- global budget。

缺少一个统一的 RolePlan，例如：

```json
{
  "topology": "direct|staged|compound",
  "risk": "low|medium|high",
  "review": "self_allowed|independent_required",
  "expected_cost": "...",
  "reason": "..."
}
```

结论：**能力分散存在，但没有统一控制 artifact。**

### 4.4 Hierarchical Memory：部分实现

已有的层次：

```text
operator transcript / original request
→ mission context
→ plan/backlog
→ CHECKPOINT
→ reviewed handoff
→ events
→ project skills/wiki
```

`context_packet.py` 已经强调“引用 canonical source，不复制 prose”，这是正确方向。

缺少：

- 正式层级定义；
- 每层 owner；
- freshness/staleness 规则；
- retraction；
- 当前事实与临时推测的类型区分；
- 一个统一 Project projection。

结论：**物理层次存在，语义层次尚未正式化。**

### 4.5 Typed HITL：已经有较强局部实现

`manager/control_state.py` 已实现：

- immutable revision snapshot；
- objective hash；
- campaign epoch；
- state revision；
- typed `Authorization`；
- typed `RepairCapability`；
- allowed actions；
- allowed write paths；
- frozen evidence；
- forbidden mutations；
- expiry、nonce 和 source channel；
- stale authorization 防护。

当前 allowed actions 主要覆盖 validator/provenance/acceptance repair。

缺少：

- 通用 ambiguity trigger；
- irreversible external write；
- publication/merge；
- privacy/safety；
- budget expansion；
- 一个统一 HITL trigger schema 和 UI 表达。

结论：**typed HITL 不是从零开始，已有强控制底座，但适用面较窄。**

### 4.6 Fact/evidence ledger：部分实现

已有：

- canonical `events.jsonl`；
- reviewed handoff；
- artifact/context refs；
- immutable wiki sources；
- vertical completion certificate；
- objective/checklist fingerprint；
- 部分 vertical 自己的 evidence/search ledger。

缺少：

- 通用 fact/finding type；
- assertion status；
- superseded/retracted relation；
- Project 当前事实 projection；
- 跨 vertical 一致接口。

结论：**证据链真实存在，但没有统一 fact ledger schema。**

### 4.7 Replanning：真实实现存在

当前 Reviewer 可以返回：

```text
status=replan_requested
```

该状态会结束当前 round，并回到 Manager/Planner 重新规划。

旧的 `step_back` / Dynamic Plan signal 不应恢复。需要加强的是：

- replan 必须引用 GoalContract；
- precise constraints 不得在重规划时丢失；
- 新计划必须说明旧计划被什么新证据推翻。

### 4.8 Self-improvement PR：已经实现

最新 main 会在：

- 修复 reviewed；
- canary 通过；
- 当前身份有 push 权限；

之后自动 push branch 并创建 PR，且显式 `auto_merge=False`。

如果要严格遵循 PDF，需要新增：

```text
publication_pending_approval
```

但是否要求“开 PR 前人工批准”，必须由领导/operator 确认。

### 4.9 Unified Project completion：部分实现但仍分散

已有：

- `ProjectState` / `lifecycle.json`；
- final-stage completion status；
- versioned completion contract fingerprint；
- `vertical_has_current_completion_certificate()`；
- Planner project completion verdict；
- `continuous.json` generation/done reason。

但还没有：

- canonical `ProjectAPI`；
- 一个 `complete_project()`；
- SELF/TEAM 统一 Project completion；
- 单一完成事实来源。

结论：**完成 certificate 已有雏形，但状态提交仍分散。**

---

## 5. 推荐的目标架构

### 5.1 所有 operator 请求都创建 Project

```text
create Project
→ Manager: SELF | TEAM
```

#### SELF

- Manager-only Project；
- Manager reply/config/control 是 result；
- 不调用 Planner/Engineer/Reviewer；
- 通过统一 Project completion API 完成。

#### TEAM

- durable Project；
- Manager 选择 vertical 和 topology；
- topology 为 DIRECT / STAGED / COMPOUND；
- 最终由 vertical Goal Gate 决定完成。

不再需要 BOUNDED/STANDING 作为 Project lifetime 分类。

### 5.2 GoalContract 成为 Project 的一部分

建议结构：

```json
{
  "request_anchor": "immutable operator request",
  "semantic_intent": "...",
  "precise_constraints": [],
  "semantic_criteria": [],
  "success_criteria": [],
  "non_goals": [],
  "ambiguities": [],
  "revision": 1
}
```

规则：

- `request_anchor` 永不改写；
- Manager 可以澄清 semantic intent；
- precise constraints 不可被偷偷削弱；
- 实质目标变化产生新 revision；
- 修改 hard constraints 需要 operator confirmation；
- Reviewer 检查 revision 是否保持原始意图。

### 5.3 GoalContract 与 vertical Goal Gate 合并

```text
GoalContract：这个 Project 想实现什么

Vertical checklist：这个领域需要什么证据

Final Goal Gate：当前 Project 的最终完成条件
```

通用 runtime 不判断“什么是好软件/好研究/正确数学”，只读取 active vertical 的有效
Goal Gate。

### 5.4 RolePlan 保持简单

不建议创建任意动态角色编排器。建议统一为：

```json
{
  "topology": "direct|staged|compound",
  "risk": "low|medium|high",
  "review": "self_allowed|independent_required",
  "expected_cost": "...",
  "reason": "..."
}
```

Manager 提交 topology；Planner 可以提供建议。

### 5.5 统一 Project completion

一个原子 completion transaction：

1. 验证 Project generation；
2. 验证 completion source；
3. 写入 result/evidence refs；
4. 标记 Project DONE；
5. 发出 `project.completed`；
6. daemon/backlog 停止只是派生效果。

完成依据：

- SELF：Manager result；
- TEAM：当前 final Goal Gate certificate。

### 5.6 Memory hierarchy 作为现有状态的规范化，而不是新增多套真相

建议：

| 层 | 内容 | 现有基础 |
|---|---|---|
| L0 | 原始请求 | transcript/request anchor |
| L1 | GoalContract | Project projection |
| L2 | Planner plan/DAG | backlog + plan metadata |
| L3 | reviewed evidence/certificate | events + handoff + artifact refs |
| L4 | 临时工作记忆 | CHECKPOINT |
| L5 | 跨项目经验 | skills/wiki |

不要新增四个互相独立、都能宣告事实的 JSON 文件。

### 5.7 Typed HITL 扩展现有 control state

在现有 Authorization/RepairCapability 基础上增加：

```text
ambiguity
irreversible_write
protected_change
publication
budget_expansion
privacy
safety
```

普通公开数据访问、Git fetch、benchmark 运行不应一律要求人工确认。

---

## 6. 建议代码修改顺序

### P0：先建立 canonical Project/GoalContract

建议新增或收敛：

- `core/project_contract.py`：Project、GoalContract、revision/certificate type；
- `core/project_api.py`：create/send/add/complete/stop/get 命令；
- `webapi`、CLI、Telegram 只做薄 adapter。

兼容原则：

```text
旧接口
→ 单次翻译
→ canonical Project API
→ 唯一实现
```

不保留两套业务逻辑。

### P0：接入 Manager

修改范围：

- `manager/front_door.py`
- `manager/_vertical_ops.py`
- `roles/prompts/manager.py`

Manager 应：

- 创建/更新 GoalContract；
- 选择 SELF/TEAM；
- TEAM 选择 vertical/topology；
- 对 GoalContract revision 负责；
- 不直接替 Reviewer 宣告 evidence 成立。

### P0：统一 vertical Goal Gate 和完成事务

修改范围：

- `skills/stage_machine.py`
- `skills/checklist_store.py`
- `skills/vertical_select.py`
- `life/supervisor/`
- `life/project_lifecycle.py`

顺序必须是：

1. 先保证每个可选 vertical 都有合法 final gate；
2. 再统一 completion certificate；
3. 最后让 `complete_project()` 成为唯一 DONE 写入路径。

不能先强制 certificate，再保留空的 software final checklist，否则 Project 会永远无法完成。

### P1：接入 Planner / Reviewer

Planner：

- 始终读取 GoalContract；
- 输出计划时引用 contract revision；
- replan 必须说明新证据；
- precise constraints 不得丢失；
- checklist ops 继续由 Planner 独占。

Reviewer：

- precise clause 读取 verifier evidence；
- semantic clause 读取 artifact/rubric；
- final review 输出 completion certificate；
- scope change 使用 `replan_requested`。

不要恢复旧 Dynamic Plan signal/streak。

### P1：RolePlan 与执行 topology

在现有 direct/staged 基础上加入 compound，但三者只决定工作组织方式：

- DIRECT：一个 Engineer workflow；
- STAGED：顺序 stage；
- COMPOUND：Planner DAG。

三者共享同一个 final Goal Gate。

### P1：规范化 Memory / Evidence

- `events.jsonl` 继续作为历史事实；
- Project projection 表示当前状态；
- mission context、checkpoint、wiki 继续使用引用；
- 增加 typed Finding/retraction 关系；
- 不增加平行 completion authority。

### P1：扩展 typed HITL

复用 `manager/control_state.py` 的：

- objective hash；
- epoch/revision；
- nonce/expiry；
- frozen evidence；
- allowed paths/actions；
- stale authorization 防护。

扩展 trigger 类型，而不是新写第二套授权系统。

### P2：治理 self-maintenance 发布

保留：

- isolated worktree；
- independent review；
- canary；
- rollback；
- never auto-merge。

待确认政策：

- A：允许自动开 PR，人工 merge；
- B：人工批准后才能 push/open PR。

PDF 选择 B，当前 main 实现 A。

---

## 7. 测试建议

测试必须少而有效。

### Deterministic contract tests

1. GoalContract revision 不得丢失 precise constraints；
2. stale generation 不得完成新 Project；
3. final Goal Gate certificate 才能 TEAM DONE；
4. 旧接口和新 Project API 进入同一个 canonical command。

### Real backend tests

1. 真实 Manager 创建 SELF Project 并完成；
2. 真实 Manager 创建 TEAM Project 并选择 vertical/topology；
3. 真实 L4 Planner 读取 GoalContract 生成计划；
4. 一个临时小仓库跑完整 Manager→Planner/Direct→Engineer→Reviewer→completion 链。

真实 backend tests 必须显式 opt-in、限制调用次数、使用临时 workdir，不能只靠 Mock。

---

## 8. 不建议做的事情

- 不要新增 GoalContract、RolePlan、FactLedger、HITL 四套互相独立的真相文件；
- 不要让 Planner `project_done` 直接等于 Project DONE；
- 不要恢复旧 Dynamic Plan signal；
- 不要用关键词/正则判断领域质量；
- 不要全局搜索删除 `bounded`；
- 不要让目标 revision 偷偷削弱 operator 原始约束；
- 不要把普通 external read/fetch 都变成人工 blocker；
- 不要自动 merge self-improvement PR；
- 不要用全 Mock 测试证明真实 provider 链路可用。

---

## 9. 需要领导/operator 确认的产品决策

1. 最终 Project Goal Gate 是否一律需要 independent Reviewer？
2. 低风险 DIRECT 中间节点是否继续允许 Engineer self-review？
3. GoalContract semantic intent 修改是否只需 Manager+Reviewer，还是必须 operator 确认？
4. COMPOUND 由 Manager 直接选择，还是 Planner 建议、Manager 提交？
5. self-maintenance 是“自动开 PR、人工 merge”，还是“人工批准后才能开 PR”？
6. 旧的持续优化 Project 如何迁移，避免新 Goal Gate 让它们意外提前结束？

---

## 10. 最终结论

领导方案的核心判断值得采纳：

> Argus 的主要短板不是执行能力，而是缺少统一、typed、可审计的元控制层。

推荐的落地不是推翻当前系统，而是将现有分散能力收敛为：

```text
Canonical Project
  + GoalContract
  + RolePlan(topology/risk/review)
  + Vertical Goal Gate
  + Typed HITL
  + Unified Completion
```

同时保留当前 Argus 最重要的底线：

- domain judgment 仍由 agent/vertical Reviewer 完成；
- harness 不替 agent 猜科研质量；
- original request 不可被偷偷重写；
- protected constraints/gates 不可被普通任务削弱；
- 高风险完成必须有独立证据；
- self-improvement 必须可审计、可回滚、永不自动 merge。
