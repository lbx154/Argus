# 精简计划

一份把 Argus 变小的计划。**这是减重问题，不是重写**：系统是能用的，目标是删掉那些没有在承重
的东西。

配套文档：**[系统审计](system-audit.zh-CN.md)**（本计划所依据的实测数字）、
**[失效模式](failure-modes-and-fixes.zh-CN.md)**（本计划想改变的行为）。

English version: [simplification-plan.md](simplification-plan.md)

---

## 规则

> **绝不用"加一个机制"去修 bug。修根因，或者删代码。**

下面每一条都由它推出。本计划经另一个实验室的模型复核；凡是它对代码树的读法与我们不一致的地
方，我们都重新实测过，此处用的是修正后的数字。

## 核心发现

昂贵的不是某一个门，而是一个**循环**：

> **门产出产物 → 产物产出修复状态 → 修复状态被注回提示词 → 重试空转。**

`skills/research_gates.py` 就是这个循环的单文件版：一次咨询性检查每轮最多写四份产物
（`RESULT.json`、`REVIEW.md`、`FAILURES.json`、`REPAIR_TASKS.md`），而 `REPAIR_TASKS.md`
是一组会被写回下一轮的指令。**删掉这个循环，让只读 Reviewer 去判真证据。**

---

## 排序后的计划

自上而下做。每一步在下一步开始前都可验证。

### 1. 删掉那个空转的 Wiki 生命周期 API —— 可证明安全

`wiki/lifecycle.py:54` 的 `maintain_wikis_after_mission()` 接收七个参数、丢掉五个，而且自己
就写着：`"""Do nothing: Agents maintain pages and INDEX.md during the mission."""`
**它没有任何调用方。**

保留 `:22` 的 `ensure_project_wiki()`。
**验证：** 全仓引用搜索；`pytest tests/test_wiki_bootstrap.py tests/test_minimal_skill_wiki.py`。

### 2. 删掉 31 个未被引用的事件类型

129 个 `EventType` 成员里，**31 个在任何地方都未被引用**——既没有通过符号，也没有通过字符串
值；其中 **20 个是 `SKILL_*` 和 `WIKI_*`**，正是这个系统本该借以学习的那两个知识面。连同它
们的 payload schema、前端目录条目和生成的类型一起删。

**单独处理：另有 6 个是用裸字符串而不是枚举发射的**（`LIFE_MISSION_SKIPPED`、
`LIFE_MISSION_REQUEUED`、`LIFE_VERTICAL_RESOLVED`、`LIFE_INBOX_QUEUED`、`SKILL_OUTCOME`、
`OPERATOR_ALERT`）。**它们不是死的**，把它们改走枚举，让这份目录重新成为可靠的索引。

**风险：** 旧事件日志的外部消费方。**验证：** 回放一份旧 `events.jsonl`；
`pytest tests/core/test_event_catalog.py`。

### 3. 砍掉无条件追加的渲染块 —— 每删一行收益最高

`manager_rendering_prompt` 给**每一个** vertical 的**每一次**阶段决策贡献 **2,923 字符**，无
论这次决策跟渲染有没有关系（追加处 `manager/_stage_ops.py:779`）。**它比 vertical banner 的
中位数（1,318 字符）还大，几乎和它所伴随的那个决策提示词一样大。**

这是唯一一项**每个 vertical 都在付**的提示词开销。让它以"这次决策确实涉及呈现"为条件，或者
直接删掉。

**验证：** 在两个 vertical 上，阶段决策仍能产出有效判决。

### 4. 移除用散文正则决定归属权的逻辑

`core/role_handoff.py:12-79` 用一条包含 `access`、`release`、`production`、`delete`、`pay`
的正则在散文上决定一个决定归谁。**一句"删掉临时目录"就会被路由给人类。**

删掉 `_OPERATOR_AUTHORITY_RE`、`_REVIEW_ACTION_RE` 和 `_runtime_owned_review_request()`。让
`NEXT_OWNER` 成为权威：显式写了 `reviewer` 就是 reviewer，不管句子里用了什么词；没有归属的
遗留 `OPERATOR_QUESTION` 仍归操作者。

**不可逆操作边界上的真实权限检查保留。一张词表不是权限边界。**

**验证：** `pytest tests/test_structured_decisions_are_not_reparsed.py`，外加文本里含
"release"、"production"、"delete" 的交接用例。

### 5. 删掉门 / 修复生态 —— 行为价值最高的一刀

删掉咨询性的 literature、theory、numerical、novelty、novelty-seeking、paper-type、
manuscript-package 各门，然后删掉它们共享的 `skills/research_gates.py`、
`physics/gate_feedback.py`、capability trace、生成的修复产物，以及承载它们的 role-banner 注
入（`physics/stages.py`）。

`physics/gates/novelty_seeking.py:40` 是最清楚的例子：十个方向、十一列推理、六项打分、四份支
撑文件——**170 个表格单元，换取"可以做一个声明"的资格**——而**它从不评估新颖性，它只是数行
数**。

只保留针对**真实被请求的交付物**的结果检查——要论文，就检查论文能不能编译。删掉精确的 CSV 表
头、图数与引用数、措辞禁令和章节配额。

**这恰好恢复了 kernel Reviewer 里早就写好的姿态：**
*"忽略 GROUND_TRUTH/gate/marker/status/provenance 文件……以及产物卫生——评分器给出的那个数字
是唯一的证据。"*

**验证：** 在已完成的物理任务上，比较 Reviewer 判决质量、构建成功率和声明准确度——**而不是门
通过率**。

### 6. 不再对干净的 Reviewer 通过做复议

Manager 仍是阶段状态的唯一写入者，但不应该被叫来**重新裁定一次没有任何异议的 Reviewer 通
过**。把它留给战略变更、冲突、回滚、终止完成和权限。

**这正是那套分工在正常工作：** Manager 管战略、Planner 管拆解、Engineer 管本地迭代、
Reviewer 管独立判断。

### 7. 把 daemon 自维护降格为普通 mission

`ARGUS_SKILL_SELF_MAINTENANCE` 默认为 `"1"`（`daemon/_life_worker_run.py:46`），启用了一个
3,186 行的 worktree/canary/发布子系统。**对 Argus 自身的修改，完全可以是走正常
Engineer/Reviewer 回路的普通工程 mission。**

先在关闭它的情况下跑。**这是按行数最大、按行为改善最小的一刀**——放在第 5 步之后做，不要提
前。

**风险：** 会失去无人值守的框架自更新。那是一项真实能力，请刻意决定。

### 8. 最后才清理防御性处理与空转循环

放到最后。**在你即将删掉的子系统里清理异常处理，是白干。**

---

## 对付 2,277 个 `try:` 的机械判据

**不要逐个人工审。** 只有以下四条**全部**成立时才保留：

1. 它包住**一个**外部边界——网络、子进程、可选遥测、清理；
2. 它捕获的是该边界的**预期**失败，或者它是唯一的顶层 mission 边界；
3. 它返回**显式的非成功**——`blocked`、可重试失败、状态不变——或者只影响可选的可观测性；
4. 它记录了失败，并且有**唯一的、有界的**重试方。

**凡是为状态、提示词、路由、检查表、权限、证据、锁、预算或阶段迁移返回"成功形状"默认值**
（`""`、`[]`、`{}`、`False`、`pass`、`continue`）**的处理器，一律删掉或向上传播。**

| | 例子 |
| --- | --- |
| **好** | `reviewer/_core.py` 捕获后端失败并返回 `blocked` |
| **坏** | `skills/stage_machine.py` 悄悄替换成一个空检查表 |

**一个不会失败的运行时，也就是一个没法告诉你它坏了的运行时。**

---

## 不能删的

- **独立的只读 Reviewer。** `require_independent_review` 默认为 `True`
  （`engineer/round_config.py`），且 Reviewer 只读运行。**这是让无人值守成为可能的承重墙。**
- 单一的阶段写入者与原子的阶段状态。
- 持久的事件与 backlog 历史。
- 不可逆操作边界上的**真实**权限检查——相对于"在摘要上跑正则"而言。
- 供应商失败即阻塞的行为、成本核算、GPU 租约。
- `core/role_reply.py` 里的宽容散文解析。**它早就把本文档要讲的道理讲完了：**
  *"Harness 并不比 Agent 更聪明，而要求一种线格式，就是 harness 在决定 Agent 可以怎么说
  话。"*

---

## 陷阱

这件事最可能的失败方式，是**把删掉的东西换成一个"统一门系统"、"统一事件系统"或"韧性层"**。

**`research_gates.py` 本身就已经是这个错误了**：一次咨询性检查长成了四份产物、一个停滞跟踪
器、修复提示词，以及额外的控制流。

**用已经存在的所有者。删调用方和格式，而不是把它们合并起来。若某次精简暴露出一个失败，把它
交给 Reviewer——不要加一层。**
