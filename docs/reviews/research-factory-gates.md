# Review: c6b11d3 "Add research-factory gates (F3/F4/F5)"

- 评审人: Argus 维护方
- 提交: `c6b11d3` — *Add research-factory gates: evidence-chain (F4), anti-mediocrity (F3), project lifecycle (F5)*
- 合著者: `nssmd <nssmd@noreply.local>`
- 日期: 2026-06-01

## 结论（TL;DR）

| Gate | 裁决 | 一句话理由 |
|------|------|-----------|
| **F4 `evidence_chain`** | ✅ **保留** | 查的是造假/断链，约束的是作弊，不是科研品味。这是 harness 正当的诚信护栏。 |
| **F5 `project_lifecycle`** | ⚠️ **可留，但要降级为建议** | 超时/预算是调度管道，属 harness。但 7d/14d/21d 这些常数不该 harness 单方面强制 agent。 |
| **F3 `anti_mediocrity`** | ❌ **拒绝合并（要求回退/重构）** | 把"科研做得好不好"写成了硬编码 Python 阈值。这是本系统**最核心的禁忌**：harness 替 agent 下科研判断。 |

核心分歧只有一句话：**判断"造假"是 harness 的活；判断"科研够不够好"是 agent 的活。F3 越过了这条线。**

---

## 评审基准：Argus 的设计哲学

> **harness 没有 agent 自己聪明。**

系统严格切成两类职责：

- **科研判断**（idea 平不平庸、提升够不够格、证据够不够广、能不能投）→ 交给 **agent**（L2 reviewer 对照 stage checklist 裁决，reviewer 是项目完成的**唯一**事实来源）。
- **领域无关的管道**（预算、持久化、调度、结构化 I/O、**防造假护栏**）→ 留给 **harness**。

判别一条规则该不该进 harness，就问一句：**它约束的是"作弊"，还是"品味"？** 约束作弊（用真 benchmark、不许重复行灌水、claim 要能溯源）= 正当；替 agent 决定"科研好坏"= 越界。

本仓库刚刚（本 session 的 `7a7d8ab`）才把残留的 harness 关键词/相关性判断删干净，README 也已重写明确写下"**反平庸是 reviewer 的判断，不是 harness 的规则**"。`c6b11d3` 与这条线正面冲突。

---

## F3 `anti_mediocrity` — 为什么不对（核心问题）

文件：`argus_skill/skills/anti_mediocrity.py`、`argus_skill/skills/automated_gates.py`、`argus_skill/tools/stage_check.py`

它自己的 docstring 就是"罪证"：

> *"Replaces prompt-level 'anti-mediocrity' checklist judgments with deterministic Python validators … refuse to advance a project when the bar is not met."*
> （`anti_mediocrity.py:3-5`）

把本该 reviewer 读了产物去**判断**的 checklist，换成 harness 用 `if` 替它**判定**——这正是我们要消除的"harness 比 agent 聪明"。具体四点：

### 1. 硬编码 magic number 决定科研价值
`anti_mediocrity.py:42-43`：

```python
DEFAULT_MIN_DELTA = 0.02       # proposed - baseline >= 0.02 reward 才算"有提升"
DEFAULT_MIN_FAMILIES = 3       # 必须 >=3 个 benchmark 家族
```

- 凭什么是 **0.02**？不同任务、不同 reward 尺度下，0.015 的提升可能极显著，0.05 也可能毫无意义。"这个提升够不够成为论文卖点"恰恰是 reviewer 的判断，不是一个写死的常数能卡的。docstring 还自证"<2% 提升落在 trial 噪声里、不可发表"（`:14-15`）——这是**领域结论**，正是 agent 该掌握、harness 不该硬编码的东西。
- 凭什么是 **3 个家族**？有的方向 2 个高质量 benchmark 足够强，有的 5 个还嫌弱。把"证据广度"降成计数，是把科研品味替换成阈值。

### 2. `benchmark_diversity` 是**无条件**硬门
`anti_mediocrity.py:366-369`：`check_benchmark_diversity(...)` **不依赖任何 env var**，无条件执行。也就是说在 run/analysis/review/submission 每个阶段，只要 <3 家族就硬失败——和 reviewer 怎么看无关。这是一条永远在线、agent 无法说服的结构性约束。

### 3. 实际是 **hard block**，不是它声称的"advisory"（最严重，且自相矛盾）
`automated_gates.py:11-12` 声称这些只是"reviewer 读作额外证据的 finding"。但接线方式不是这样——`stage_check.py` 把 gate 失败计入退出码：

```python
total_failed = failed + gate_failed
return 0 if total_failed == 0 else 1
```
（`stage_check.py` 的 `main()`，本次新增）

`stage_check` 是 daemon 默认的 `check_commands` 目标。gate 失败 → 退出码 1 → **整个 check_commands 失败 → 这一轮被判失败**，与 reviewer 的判断无关。于是"proposed 比 baseline 只高 0.019" → 自动 exit 1 → 轮次失败，**reviewer 根本没有否决它的权力**。这与 `automated_gates.py` 自己写的"absence of a gate is NOT a pass / agent 是唯一裁决"直接矛盾：实现上是 harness 把 agent 的裁决权拿走了。

### 4. 用 env var **绕过显式信号原则**
`stage_check.py` 从 `ARGUS_SKILL_PROPOSED_CONDITION` / `ARGUS_SKILL_BASELINE_CONDITION` 读条件名来开启完整 gate（commit message 自述"without re-plumbing through every supervisor call site"）。等于在管道层**偷偷**给科研结论盖章，既不经 planner 的结构化 scope，也不经 reviewer。这正是我们一直在拆的隐式旁路。

### 5. 与刚完成的清理工作正面冲突
本 session 的 `7a7d8ab` 删除了 harness 的关键词/相关性判断；README（`5c169b0`）明确写"反平庸是 reviewer 的判断"。F3 把它又加了回来，且换成更硬的形式（确定性阈值 + 退出码 block）。两者不能共存。

---

## F4 `evidence_chain` — 这个是对的，保留

文件：`argus_skill/skills/evidence_chain.py`

它查的是：`paper/claims_to_evidence.tsv` 里每条 claim 必须指向**真实存在**的路径、每个 bundle 必须有 `BUILD_INFO.md`、非历史 claim 不得引用**被污染**的 bundle。这些约束的全是**造假/断链**，不是科研选择——属于 harness 正当的防造假护栏。它甚至已经在仓库里抓到一个真实断链（缺 `BUILD_INFO.md` 的 bundle），说明它有效。

**保留建议**，仅一个 caveat：确认它只查"溯源完整性/真实性"，不夹带任何"证据够不够好"的品味判断。目前看是干净的。

---

## F5 `project_lifecycle` — 可留，但常数要降级

文件：`argus_skill/life/project_lifecycle.py`

状态机（incubating/running/writing/quarantined/done/archived）+ 超时/预算上限，本质是**调度管道**，属 harness 该管的事，且是纯函数、无副作用、不调 LLM——干净。

**Caveat**：`incubating 7d / running 14d / writing 21d / 80% 预算` 这些常数若用来**硬性 quarantine** 一个项目，就有"harness 替 agent 决定该不该放弃这条科研路线"的味道。预算上限（防烧钱）是正当 plumbing；而"21 天还没写完就隔离"更接近科研判断。建议：超时作为**给 planner/reviewer 的信号**，由 agent 决定是否 pivot/放弃，而不是 harness 直接转 `quarantined`。commit 说"deeper supervisor integration is a follow-up"，正好在接进去之前定清楚这条边界。

---

## 建议的落地改法

1. **F3 删除 `improvement_threshold` 和 `benchmark_diversity` 的硬阈值判定**（连同 `DEFAULT_MIN_DELTA` / `DEFAULT_MIN_FAMILIES` magic number 和 env-var 旁路）。这些"够不够格"的判断交还 reviewer。
2. 若想保留 F3 抽取的**事实**（best reward、baseline 是否复现成功、覆盖了几个家族），可以把它降级成**只读的结构化 finding**喂给 reviewer prompt——**绝不计入 `stage_check` 退出码**。让 reviewer 看着这些数字自己判断，而不是 harness 用 0.02 卡死。
3. **F4 保留**，维持其 hard-block 语义（造假就该 block）。
4. **F5 保留状态机与预算上限**；把时间超时改成**建议信号**而非自动 quarantine，等到接 supervisor 时再定边界。
5. 修掉 `automated_gates.py` 文档与 `stage_check` 实现的矛盾：要么真的只做 advisory（那就别进退出码），要么诚实承认是 hard gate（那 F3 就更不该在）。

> 一句话给作者：不是反对加 gate，是反对**用写死的阈值替 agent 判断科研好坏**。把 F3 的"品味判断"拆掉、把"事实抽取"留作 advisory，F4/F5 基本就对了。
