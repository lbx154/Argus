# Argus 状态机与死锁审计

这份文档刻画 Argus 真实运行的状态机——不是设计意图,是从代码里读出来的。目的有两个:让接手的人能判断"这个状态会不会卡住",以及给死锁排查一个可重复的方法。

所有状态名、集合、转移都标了出处。**文档与代码不一致时以代码为准,并请修正本文。**

---

## 1. 三层状态,不是一层

Argus 常被当成"一个状态机"讨论,实际有三层各自独立演进的状态,死锁往往出在**层与层之间**,而不是某一层内部。

| 层 | 状态载体 | 权威角色 | 出处 |
| --- | --- | --- | --- |
| **任务层** | `BacklogItem.status` | 无单一权威;由 mission 结算写入 | `life/memory.py:614` |
| **阶段层** | `research/PIPELINE_STATE.json` 的 `current_stage` | **Manager 是唯一写者** | `manager/_stage_ops.py:408` |
| **战役层** | `continuous.json` 的 `enabled` | operator 与 Planner 的 `project_done` | `daemon/_life_worker_run.py:271` |

---

## 2. 任务层:17 个状态

出处 `life/memory.py:614-648`。

**活跃(2)** — `pending`、`running`

**可恢复暂停(10)** — `_RECOVERABLE_PAUSE_STATUSES`:
`paused`、`paused_budget`、`paused_provider_cooldown`、`paused_provider_fence`、
`paused_daemon_shutdown`、`paused_operator`、`research_incomplete`、
`paused_no_breakthrough`、`exhausted_current_methods`、`infra_blocked`

**终态(5)** — `_TERMINAL_STATUSES`:`done`、`failed`、`aborted`、`skipped`、`superseded`

终态是**真正的 sink**:`IllegalStateTransition` 阻止任何复活,重试只能通过新建 item(新 id、新审计线)。这条不变式是好的——它把"同一件事被悄悄重跑"变成了结构上不可能。

### 2.1 谁把暂停态唤醒

这是任务层唯一值得关注的死锁面。三个恢复入口:

| 入口 | 调用者 | 覆盖范围 |
| --- | --- | --- |
| `resume_paused_statuses` | `supervisor/_core.py:416` — **唯一自动路径** | 4 个 |
| `resume_all_paused` | `apps/cli/_core.py:1318` — operator 命令 | 全部 10 个 |
| `resume_paused` | **无生产调用者** | — |

自动恢复只覆盖:`paused_provider_cooldown`、`paused_provider_fence`、
`paused_daemon_shutdown`,以及预检通过时的 `paused_budget`。

**其余 6 个只能靠 operator 手动唤醒。** 其中 `paused_operator` 和 `infra_blocked` 是有意为之(代码注释明确说明)。但 `research_incomplete`、`paused_no_breakthrough`、`exhausted_current_methods` 是 **agent 自己**根据 Reviewer 裁决进入的科研状态。

这**不构成死锁**,原因是:Planner 的触发条件是 `next_pending() is None`(`supervisor/_core.py:588`),暂停项不参与该判断。所以只剩暂停项时 Planner 照样被唤起,可以排全新的工作。被暂停的 item 是一条记录,不是一道闸。

> 前提是 Planner 真的能产出任务。第 4 节的死锁正是打破这个前提的那一类。

---

## 3. 阶段层

Manager 是 `current_stage` 的**唯一写者**(`manager/_stage_ops.py:422` 的 docstring 明确声明)。Reviewer 和 Planner 只能建议。

转移动作:`advance` / `hold` / `rollback` / `complete`,写盘在
`_apply_stage_decision_to_disk`(`_stage_ops.py:340`)。

**该函数签名是 `(decision, cur, root)` —— 拿不到 reviewer verdict。** 这是刻意的:没有第二道机器闸能藏在写盘路径里二次改判 Manager。曾有一个 `enforce_scientific_stage_guard` 名义上做这件事,实际被削成了恒等函数,已删除;这条性质现在由测试按签名钉住。

---

## 4. 死锁的定义与已发现的实例

### 4.1 判据

一个状态是死锁,当且仅当:

1. 它不是终态;
2. 从它出发的所有转移都回到它自己或它的前驱;
3. **触发这些转移的条件本身不会随时间改变。**

第 3 条是关键。`paused_budget` 满足 1 和 2,但预算会随日期重置,所以不是死锁。而"这个 item 没有 `plan_id`"永远不会变成有——这才是死锁。

### 4.2 实例 A:replan 无任务(已修,`8d4d47b2`)

真实观测:一个项目 **75 小时内把同一个 mission 重排了 100 次**。

```
Reviewer → replan_requested("完整性 OK,但冻结边界内此方向达不到标准")
  → item 回 pending
  → Planner 同意,返回 project_done=false + 理由 + 无任务
  → 调和被 `revision_request is None` 挡住
  → 判为 planner error → idle backoff → item 被重领 → 重跑
```

没有任何一方出错。Reviewer 判断正确,Planner 诚实。缺的是**让这个答案能改变什么**。

修复:让 Planner 的裁决送到 Manager(阶段唯一权威),rollback 后下一轮就能排更早阶段的工作。守卫是 `10c3cd86`(07-23 07:24)随特性引入的,**比该项目首次 replan 晚 34 分钟**,其 179 行配套测试从未提及 replan——属防御性收窄,不是被论证过的规则。

**没有做 operator 告警。** 重点是 Planner 能否自己走出来,而不是多快惊动人;停滞告警只会让循环变可见,不会让它停。

### 4.3 实例 B:无版本 item 的 replan(已修)

`_planning_cycle_intake.py`:replan 路径要对活跃计划做 compare-and-swap。没有 `plan_id` 的旧 item 无计划可换,原先直接 `PLAN_ERROR` → 与实例 A 完全相同的循环。

**"无版本"永远不会自行变成"有版本"** —— 满足判据第 3 条。

全机 3961 个 backlog item 中 476 个无 `plan_id`,其中 **1 个仍活跃**。修复:退化为一次普通规划(无计划可 supersede,但 Planner 仍看得到 Reviewer 的理由并可自行决定)。

### 4.4 仍在的风险点(未修,需判断)

`_planning_cycle_intake.py` 还有两个 `PLAN_ERROR` 返回:

- `cannot inspect active plan: <exc>` —— I/O 异常,会自愈,**不是**死锁;
- `plan revision conflict: active revision changed` —— 计划已被换掉,重试会读到新计划,**不是**死锁。

两者都不满足判据第 3 条。

---

## 5. 排查方法(可重复)

1. **枚举状态集与终态集**,确认没有孤儿(既非终态又不在任何恢复集合里)。
2. **对每个非终态,找出真实的出边**——不是"被标记为可恢复",而是**有生产代码调用**那个恢复函数。`resume_paused` 在集合里、有测试、零调用者。
3. **对每条出边问:触发条件会随时间改变吗?** 不会的就是死锁。
4. **扫所有 backoff-then-retry 点**(`_enter_idle_backoff`、返回 `PLAN_ERROR`):每一个都是候选,因为它们的语义是"什么都不改,等会儿再来"。
5. **用真实数据验证**,而不是推理:读 `~/.argus-skill/projects/*/backlog.jsonl` 和 journal,数同一标题的重复次数。实例 A 就是这样发现的——`_render_campaign_tally` 一行 `replan_requested=101` 暴露了它。

### 5.1 一条现成的探针

```bash
python - <<'EOF'
from pathlib import Path
from collections import Counter
from argus_skill.life.memory import MemoryBundle
mem = MemoryBundle.for_cwd(fingerprint='<fp>', global_root=Path.home()/'.argus-skill')
ent = list(mem.journal.tail(4096))
rp = [e for e in ent if getattr(e,'kind','')=='mission_replan_requested']
print(Counter(str(getattr(e,'title',''))[:60] for e in rp).most_common(5))
EOF
```

同一标题重复次数远大于 1,就是在原地打转。
