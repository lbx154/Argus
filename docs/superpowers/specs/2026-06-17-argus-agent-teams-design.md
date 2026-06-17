# Argus Agent Teams — 设计文档

- **日期**: 2026-06-17
- **状态**: Draft（brainstorming 产出，待写 implementation plan）
- **作者**: msra + Claude
- **主题 slug**: `argus-agent-teams`
- **参考设计**: Claude Code [Agent Teams](https://code.claude.com/docs/en/agent-teams)

---

## 0. 一句话

给 Argus 加一个**原生的 agent-team 能力**：engineer 作为 **lead**，在一个 mission 内部把活拆成 N 个"文件所有权不相交"的子任务，**并发拉起 N 个会自己循环的 teammate-engineer**（各自独立 context / git worktree / GPU lease），它们从一张**共享 task list 自取（self-claim）**、通过 **mailbox 互相通信**，跑完后 lead 综合、**两层 reviewer** 验收。整套 **shared task list + mailbox** 是领域无关的笨管道；**"要不要组队、怎么拆"是 agent 判断，不是 harness 分类**。

---

## 1. 问题 / 诊断

用户观察："最近让 Argus 跑的多任务优化 benchmark（SLO / 类 SOL-ExecBench 的多 kernel 优化），它不会自己拉一个 subagent team 来并行自优化。"

不是单一 bug，是三个设计层面的缺口叠加：

1. **subagent 不是 agent，是"带看护的 shell 命令"。** `argus_skill/tools/subagent.py`（2557 行）里一个 subagent = `fork()` + `Popen(shell=True)` 跑一条固定命令。`supervised` 模式会起一个 codex，但它只是**只读看护**——能判断 RL 健康、能 early-stop，但**不能改代码、不能调参、不能重跑**（决策词汇只有 `continue/early_stop/save_checkpoint`）。所有"优化判断"最终回到那个**唯一的主 engineer**。

2. **现有的 fan-out 轴是"方法条件"，不是"任务"。** engineer 被教的并行方式是 `run_experiments.py` + `experiments/MATRIX.json`，按 **condition**（baseline vs proposed，3~6 个粗条件）拆 subagent；每个 condition 的命令**内部仍是串行 for 循环跑完所有 N 个 task**。没有"一个 task → 一个 worker"的抽象。

3. **daemon supervisor 是"故意单线程"的。** `argus_skill/life/supervisor.py:22` 明写 *intentionally synchronous: one mission at a time, no thread pool*。planner 只发一个 ≤3 条的**线性 stage checklist**（`research→plan→benchmark→run→analysis→draft→review→submission`），没有并行 task 的 DAG 概念。

4. **加之：SLO/SOL-ExecBench 在本 repo 里不是一等的 benchmark harness。** 它是被当成一个**自由文本 mission objective** 丢进 daemon 的，于是直接继承了上面这套"单 agent 串行"模型。（`benchmarks/swebench_pro` 那种**真有** per-task 并发的 `asyncio.Semaphore` 池是给人手动 benchmark Argus 系统用的，daemon 永远不会调它。）

### 用 Agent Teams 的对比表来定位

Argus 现在的 subagent 恰好是那张对比表的**左列**；用户要的是**右列**：

| | Subagents（Argus 现状） | Agent Teams（目标） |
|---|---|---|
| Context | 自己的 context，结果回主 agent | 自己的 context，完全独立 |
| 通信 | 只回报主 agent | teammate 之间直接互发 |
| 协调 | 主 agent 管所有活 | 共享 task list，自协调 |
| worker 本体 | 一条 shell 命令（+只读看护） | 会自己循环的完整 agent |

**核心洞察**：右列的两个 primitive（**shared task list + mailbox**）正好就是"领域无关的笨管道"——认领哪个 task、怎么协调、做完没，全是 agent 判断。所以这不违背 Argus "harness 笨、agent 聪明"的哲学，而是它的**自然下一步**：管道从"喂一个 agent"升级成"喂一支会自协调的 team"。

---

## 2. 目标 / 非目标

### 目标
- 一个 **engineer-as-lead** 在单个 mission 内并发拉起 N 个 **teammate-engineer**，每个是会自循环（edit→run→measure→improve）的完整 engineer loop。
- **shared task list**：可并发认领（flock）、带依赖、完成自动解锁、teammate 死掉时 task 退回 `pending` 重派。
- **mailbox**：teammate 之间直接互发消息（不是只回报 lead）。
- **两层验收**：teammate 自带 reviewer 门管自己那块 done；lead 综合后整体再过 mission 级 L2 reviewer（stage checklist + 防造假套在合并结果上）。
- **分布式文件/git 安全**：shared-nothing 的 work product + 单写者合并的 canonical。
- **doc-based continuity**：每个 teammate 在自己 session 里及时更新自己的 living doc，lead 派发时把这条写进 teammate 的 system prompt；daemon 重启能从 roster + doc 恢复。
- **触发 = agent 判断**，solo 是默认兜底，harness 只设资源上限。

### 非目标（YAGNI，留给后续里程碑）
- **不**在 v1 动 supervisor 的"一次一个 mission"不变量（并发 mission = team 是 M3）。
- **不**在 v1 做异构 runtime（codex / Claude Code / hermes / omh 混编）——v1 teammate 同构，都是 Argus engineer。
- **不**在 v1 做 Web 图形面板；先用现有 event/`_watch.py` 文本视图。
- **不**让 planner 上游拆并行任务（那等于让它从 objective 文本猜并行度，离"反对的分类"更近）。扳机放 engineer-as-lead。

---

## 3. 哲学对齐（Argus 特有，必须守住）

| | 谁负责 | 在本设计里 |
|---|---|---|
| **科研判断** | Agent | 要不要组队、拆成几块、每块给谁、文件所有权怎么切、teammate 的 sys prompt 怎么写、综合结论、验收 |
| **领域无关管道** | Harness | task_board 的原子写+flock、mailbox 的投递、roster 持久化、worktree/lease 生命周期、资源上限 |

三条硬规矩：
1. **触发不是 harness 分类。** harness 绝不用关键词/正则去判断"这是不是多任务 mission"。组队是一个 **agent 能调的工具**（跟 subagent 一样被动）。engineer 读完 mission、看过 repo/benchmark 后**自己**决定调不调。
2. **solo 是默认兜底。** 看不出并行性 / 线性 / 强依赖 → 不组队，照现在单 engineer 跑。组队 opt-in，阈值不由 harness 设。
3. **harness 唯一正当的硬约束是资源**（不超可用 GPU / 预算）——跟防造假护栏同类，管的是资源不是科研选择。N 的上限 = 资源决定。

---

## 4. 架构

### 4.1 落点（三层架构上这部分放哪）

- **笨管道（harness）→ 新模块 `argus_skill/team/`**
  - `task_board.py` — 可并发认领的共享 task list（原子 `tmp+os.replace` + `flock`）
  - `mailbox.py` — 每 member 一个信箱（泛化 `apps/_inbox.py` 的 append+offset 语义到 per-recipient）
  - `roster.py` — team manifest / 成员与 task 状态 / continuity 锚点
  - `worktree.py` — per-teammate git worktree + GPU lease 生命周期（封装现成件，见 §6）
- **判断（agent）→ `argus_skill/builtin_skills/engineer/agent-team-lead.md`**
  - lead 怎么拆活、怎么切文件所有权、怎么写 teammate 的 sys prompt、怎么综合、两层验收——全是 prompt 契约，住 agent 侧。
- **agent 调用的工具 → `argus_skill/tools/team.py`**
  - engineer 调它来组队（verbs 见 §5.5），就像今天调 `subagent`。复用 subagent 的 fork/registry/inbox 机制，但 teammate 的"命令"是**一个 Argus engineer loop**而非任意 shell。
- **接缝 → `argus_skill/apps/_life_repl.py::_CodexSkillLoopRunner.execute`（:767）**
  - 关键：team **不**在这里加一条 harness 编排分支去"检测多任务"。team 完全是**从 engineer loop 内部 tool-driven** 的——lead 在自己的 round-loop 里调 `tools/team.py`，poll、merge 也都是 tool 调用。execute() 基本不动（只需保证 teammate 子进程能拿到同样的 SkillLoop 执行路径）。

一句话：**管道在 `harness/team/`，扳机和拆活的脑子在 `agent/agent-team-lead.md`，中间用 `tools/team.py` 连。**

### 4.2 管线图（engineer-as-lead 内核，设计成可上移到 M3）

```text
  supervisor.tick ── 仍"一次一个 mission"（v1 不动 supervisor.py:22 同步不变量）
        │ objective + special prompt
        ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ LEAD engineer  (跑在 _CodexSkillLoopRunner.execute 的常规 loop)│
  │  ① 读 mission → 判断是否可并行；不可并行就 solo（默认兜底）     │
  │  ② 可并行 → 拆成 N 个"文件所有权不相交"的子任务               │
  │  ③ tools/team.py form → 写 shared task list（原子+flock）      │
  │  ④ tools/team.py spawn × N（各自 worktree / GPU lease / shard）│
  │  ⑤ poll mailbox + roster，等所有 task 到 done（teammate idle）  │
  │  ⑥ 读所有 shard → 综合 → 过 mission 级 L2 reviewer → HANDOFF   │
  └───┬──────────────────┬──────────────────┬───────────────────┘
      │ self-claim        │                  │   (认领=agent 判断，flock 防抢)
      ▼                   ▼                  ▼
 ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
 │ teammate #1 │    │ teammate #2 │    │ teammate #N │
 │ 完整 engineer│    │ 完整 engineer│    │   ...       │
 │ loop 自循环  │    │ loop 自循环  │    │             │
 │ edit→run→   │    │ edit→run→   │    │             │
 │ measure→改  │    │ measure→改  │    │             │
 │ 自带 reviewer│    │ 自带 reviewer│    │             │ ← 第 1 层验收
 ├─────────────┤    ├─────────────┤    ├─────────────┤
 │私有 worktree │    │私有 worktree │    │私有 worktree │ ← 物理隔离，永不写同一文件
 │私有 GPU lease│    │私有 GPU lease│    │私有 GPU lease│
 │私有 result   │    │私有 result   │    │私有 result   │
 │  shard       │    │  shard       │    │  shard       │
 │私有 living doc│    │私有 living doc│    │私有 living doc│ ← continuity（自己及时更新）
 └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
        │ 只 append 自己 shard / 发 mailbox     │
        └──────────────┬───────────────────────┘
                       ▼
        ┌──────────────────────────────────────┐
        │ 共享通道（唯一需要并发安全的三样）：    │
        │  • task_board  — 原子写 + flock 自取    │
        │  • mailbox     — 每 teammate 一个信箱   │
        │  • roster/ledger — append-only + lock   │
        │  canonical 产物 → 只有 LEAD 写          │
        └──────────────────────────────────────┘
```

### 4.3 映射（Agent Teams → Argus 现成件）

| Agent Teams | Argus 现成对应物 | 要做的 |
|---|---|---|
| Team lead | engineer（在常规 loop 里） | 加 `agent-team-lead.md` 角色 + `tools/team.py` |
| Teammate（自循环） | 一个完整 `SkillLoop`+reviewer | 让它能被并发拉起 N 份（teammate=subagent，其命令是 engineer loop） |
| Shared task list | `planner.TaskSpec` / backlog | 新 `team/task_board.py`，可并发认领+依赖+重派 |
| Mailbox | `apps/_inbox.py`（append+offset） | 新 `team/mailbox.py`，泛化到 per-recipient |
| Team config / roster | `.argus_subagents/` registry | 新 `team/roster.py`，team manifest + 续跑锚点 |
| 显示模式（tmux 分屏） | `apps/_watch.py` / event log | v1 文本视图；图形面板留 M3 |
| 每 teammate 独立 worktree | `RunnerOptions.worktree_name`（codex_runner.py:60）、`_life_repl` 的 `project_worktree` | 封装成 per-teammate worktree |
| `TeammateIdle/TaskCompleted` hook | reviewer 门 + roster 状态 | lead 用 roster/mailbox 判断 idle/done |
| Reviewer 门 / 防造假 | L2 reviewer + integrity guardrails | 天然 per-teammate + 合并结果再过一层 |

---

## 5. 组件详述（每个：做什么 / 接口 / 依赖）

### 5.1 `team/task_board.py` — 共享可认领 task list

- **做什么**：持有一支 team 的所有子任务；支持并发**自取**、依赖、完成自动解锁、死任务重派。
- **数据结构**（每 team 一个目录 `.argus_team/<team_id>/tasks/`，每 task 一个 JSON）：
  ```json
  {
    "task_id": "kernel-attn-fwd",
    "title": "...", "objective": "...",
    "owns_paths": ["kernels/attn_fwd/**"],   // 文件所有权边界（lead 切）
    "deps": [],                               // 依赖的 task_id（Agent Teams 同款）
    "state": "pending|claimed|running|done|failed",
    "owner": "",                              // 认领它的 teammate id
    "result_shard": "shards/kernel-attn-fwd.jsonl",
    "claim_ts": 0, "heartbeat_ts": 0, "attempts": 0
  }
  ```
- **接口**：`form(team_id, tasks)`、`claim(team_id, member_id)`（原子 CAS：flock→读→若 pending 且 deps 全 done 则翻 claimed→写→放锁；抢不到返回 None）、`complete(task_id, shard)`、`fail(task_id, reason)`、`reassign_stale(ttl)`（heartbeat 超时→退回 pending、`attempts++`）、`all_done(team_id)`。
- **并发**：原子 `tmp+os.replace`（Argus registry 现用）+ `fcntl.flock`（gpu_lease 现用）。认领走 compare-and-set，天然防双取。
- **依赖**：无业务依赖；纯文件系统 + flock。

### 5.2 `team/mailbox.py` — 每 member 一个信箱

- **做什么**：teammate↔teammate、teammate↔lead 直接互发消息。
- **结构**：`.argus_team/<team_id>/mailbox/<member_id>/inbox.jsonl` + 每个**读者**自己的 `inbox.offset`（直接搬 `_inbox.py` 的 `queue/drain/count` 三函数，把 `life_dir` 换成 `mailbox/<member_id>/`）。
- **接口**：`send(team_id, to, frm, text)`（append 到收件人信箱，单写者无竞争）、`drain(team_id, member_id)`、`count_pending(...)`、`broadcast(team_id, frm, text)`（给每个成员各发一份——Agent Teams 也是"每收件人一条"）。
- **依赖**：复用 `apps/_inbox.py` 的实现，只换路径维度。

### 5.3 `team/roster.py` — team manifest / 生命周期 / continuity

- **做什么**：记录 team 成员、各自 worktree/lease/doc 路径、task 状态快照；是 daemon 重启后**续跑**的锚点。
- **结构**：`.argus_team/<team_id>/roster.json`
  ```json
  {
    "team_id": "...", "mission_objective": "...", "lead": "lead",
    "created_ts": 0, "state": "forming|running|merging|done|dissolved",
    "members": [
      {"id": "tm-1", "pid": 0, "worktree": "...", "gpu_lease_owner": "tm-1",
       "doc": "<worktree>/TEAMMATE_STATUS.md", "task_id": "kernel-attn-fwd",
       "status": "running|idle|dead", "thread_id": "...", "heartbeat_ts": 0}
    ]
  }
  ```
- **接口**：`create/load/save`（原子+flock）、`mark(member, status)`、`reattach()`（重启时：活进程 reattach，死进程→其 task `reassign_stale`、可 respawn）。
- **续跑契约**（决策 4）：roster 记成员与 task 状态；**每个 teammate 的真相在它自己 worktree 的 living doc**（见 §5.7）。恢复 = 读 roster 找到半跑成员 → 读它的 doc → 重新 spawn 一个 teammate 接着干（task 仍 `claimed`/退回 `pending`）。

### 5.4 worktree + GPU lease 生命周期

- **worktree**（物理隔离主防线）：每个 teammate 一个 **git worktree**。primitive 已存在——`RunnerOptions.worktree_name`（`codex_runner.py:60`）会传 `--worktree`，`_life_repl.py` 已经在 thread `project_worktree`。封装：spawn 前 `git worktree add .argus_team/<team_id>/wt/<member_id> <base>`，teammate 的 `working_dir` 指它。
- **GPU lease**：`gpu_lease.py` 的 `_acquire(cfg, owner, pid, ttl, ...)`（:437）本就多 owner、flock 租约。封装一个 team-aware 分配器：spawn 时以 `owner=<member_id>` 抢一张/一组卡，注入 teammate 的 `CUDA_VISIBLE_DEVICES`，退出释放。这就把"caller 把 CUDA_VISIBLE_DEVICES 烤进命令"的旧模型换成真调度。
- **资源上限**：N ≤ 可用 GPU 数（或预算）。这是 harness 唯一正当的硬 gate。

### 5.5 `tools/team.py` — agent 面向的工具

仿 `subagent.py` 的 CLI 风格（verbs，registry-on-disk）：

| verb | 作用 |
|---|---|
| `form --team-id --tasks tasks.jsonl` | lead 落 task_board + roster |
| `spawn --team-id --member-id --task ... --sys-prompt-file ...` | 起一个 teammate（=detached Argus engineer loop in worktree，复用 subagent fork 机制） |
| `status --team-id` | 聚合 roster + task_board + 活 pid |
| `wait --team-id` | **阻塞**到下一个有意义事件（某 task done / 某 teammate idle / dead / 新 mail）。lead 用它**避免 token-spin**：teammate 完成/出事时复用 subagent 现成的 `_alert_engineer`→inbox 通知唤醒 lead，而不是 lead 在 round-loop 里反复 poll 烧 token |
| `send / drain --member-id` | mailbox 收发 |
| `reassign --team-id` | 触发 `reassign_stale`（死任务退回） |
| `dissolve --team-id [--keep-worktrees]` | 收尾：合并完成后清 worktree/lease/registry |

- **teammate = subagent，但命令是 engineer loop**：`spawn` 实际提交的"命令"形如
  `python -m argus_skill engineer-once --objective <子任务> --workdir <worktree> --sys-prompt-file <teammate_prompt> --team-id <tid> --member-id <mid>`
  （v1 需要一个"跑单个 engineer loop on 一个 objective"的瘦入口；plan 阶段定具体 entrypoint，复用 `_CodexSkillLoopRunner.execute` 的 SkillLoop 路径）。
- **复用**：fork/setsid/registry/inbox-alert 全用 subagent 现成机制，省一大块。

### 5.6 `builtin_skills/engineer/agent-team-lead.md` — lead 角色（判断住这）

写明 lead 的契约：
1. **何时组队**：仅当 mission 能切成**互相独立、文件所有权不相交、各自可单独完成**的子任务时才组（否则 solo）。明确"sequential / 强依赖 / 同文件 → 不要组队"。
2. **怎么拆 + 切文件所有权**：每个子任务声明 `owns_paths`，**两个 teammate 的 `owns_paths` 不得相交**（这是 §6 shared-nothing 的来源，决定权在 lead）。
3. **怎么写 teammate 的 sys prompt**：见 §5.7 模板（决策 4 强约束：必须把"持续更新你的 doc"写进去）。
4. **怎么综合**：只有 lead 读全部 shard、写 canonical 合并产物（单写者）。
5. **验收**：综合结果交 mission 级 L2 reviewer（第 2 层）。

### 5.7 teammate system-prompt 契约（lead 派发时构造，决策 4 落点）

lead `spawn` 每个 teammate 时，注入的 sys prompt **必须**包含：

- **身份**：`你是 teammate <id>，team <tid> 的一员，lead 是 <lead>`。
- **任务**：完整子任务 objective + `owns_paths`（**你只能改这些路径下的文件**）。
- **continuity 强约束**：`你必须在 <worktree>/TEAMMATE_STATUS.md 里及时记录你的进展/决定/当前状态——每完成一步就更新。这是你的续跑真相来源；daemon 若重启，会从这个 doc 恢复你。`
- **mailbox 协议**：`用 tools/team.py send 跟其他 teammate/lead 通信；用 drain 收信。`
- **自带验收**：`你跑完要过你自己的 reviewer 门（第 1 层）才算 done。`
- **防造假**：`必须用真实公开 benchmark、不许灌水重复行、留审计包`（per-teammate 套现有 guardrails）。
- **资源**：`你的 GPU 是 $CUDA_VISIBLE_DEVICES（已分配的 lease），别越界抢卡。`
- **idle 后**：`done 后回 task_board 自取下一个未认领、依赖已满足的 task；没有就上报 idle 给 lead。`

---

## 6. 并发 & 分布式文件/git 问题（用户特别点名）

**这不是分布式难题，是"按所有权切干净 + 少量加锁通道"的本地并发问题。** Agent Teams 文档原话：*"Two teammates editing the same file leads to overwrites. Break the work so each teammate owns a different set of files."* 本设计把这句话变成结构。

三层防御（对应 §4.2）：

1. **work product → shared-nothing（主防线，干掉 ~90%）**
   每个 teammate 一个**私有 git worktree** + **私有 result shard**。两个 teammate 在文件系统上**根本碰不到同一路径**——不是靠锁抢，是靠隔离不抢。`owns_paths` 不相交由 lead 保证（§5.6）。

2. **协调状态 → single-writer 或加锁**
   真正共享的只有三样小东西：`task_board` / `mailbox` / `roster`。
   - 认领/写：**原子 `tmp+os.replace` + `flock`**（Argus registry 现用原子写，gpu_lease 现用 flock 租约，primitive 都现成）。认领走 CAS：抢锁→若 pending 翻 claimed→放锁；抢不到跳下一个。
   - mailbox：**每收件人一个信箱文件**，发件人 append、收件人只读自己那只 → 天然无竞争。

3. **canonical 产物 → 只有 lead 写**
   teammate 永远只 append 自己 shard；**lead 是唯一合并者**，读全部 shard 合成唯一 canonical（`results.jsonl` / checkpoint）。单写者 → 一致性免费。

**git 专门留神**：N 个 worktree 各自 commit；因 ① 已保证每人 own 不相交目录，lead 合并基本是"并集"，不会真冲突。合并策略：lead 在主 worktree `git merge`/`cherry-pick` 各 teammate 分支，或直接收 shard（推荐后者：teammate 产出落 shard，代码改动落各自 worktree 分支，lead 择优 merge）。

**一句话规则**：`work product shared-nothing；coordination single-writer-or-locked；canonical 只 lead 写。`

### 失败模式 → 缓解

| 失败模式 | 缓解 |
|---|---|
| 两 teammate 改同一文件 | `owns_paths` 不相交（lead 拆活时切开） |
| 并发认领同一 task | flock + CAS（state 必须是 pending 才能翻） |
| teammate 崩溃/卡死 | heartbeat 超时 → `reassign_stale` 退回 pending（决策 3） |
| 合并冲突 | 不相交目录 → 并集；shard 优先 |
| GPU 争抢 | `_acquire(owner=member_id)` 独占 lease |
| ⚠️ **subagent 全局 discussion-block 死锁** | `subagent.py::_open_discussion_blockers`（:1231）会让"一个 supervised 子进程 parked 就阻塞**所有** submit"。一支 N teammate 的 team 会因此死锁。**必须把这条 block 改成 per-team/per-lane 作用域**（见 §10）。 |

---

## 7. 触发 & 生命周期（engineer-as-lead）

1. supervisor 照常发一个 mission 给 engineer（**不变**）。
2. engineer 读 mission、看 repo/benchmark，**自己判断**能否并行（harness 不插手）。
3. 不能并行 → solo 跑完（= 现状，默认兜底）。
4. 能并行 → 拆 N 个子任务（`owns_paths` 不相交）→ `tools/team.py form`。
5. `spawn × N`：各建 worktree + 抢 GPU lease + 构造 teammate sys prompt（含 doc 强约束）+ 起 detached engineer loop。
6. teammate 各自 `claim` 一个 task → 自循环 edit→run→measure→改 → 及时更新自己 doc → 跑完过**自己的 reviewer 门（第 1 层）** → `complete(shard)` → 回去自取下一个 / 报 idle。
7. lead poll roster+mailbox；teammate 死 → `reassign`（task 退回 pending，别的 teammate 或新 respawn 接手）。
8. `all_done` → lead 读全部 shard **综合**、写 canonical。
9. 综合结果过 **mission 级 L2 reviewer（第 2 层）** → reviewer 裁 done/continue → 正常 `HANDOFF`。
10. `dissolve`：清 worktree/lease/registry（保留 shard/doc 作审计）。

---

## 8. 验收（两层，决策 2）

- **第 1 层（per-teammate）**：每个 teammate 是完整 `SkillLoop`+reviewer，它自己那块"done"由它自带的 L2 reviewer 判。不过自己的 reviewer 门，task 不算 `done`。
- **第 2 层（mission 级）**：lead 综合出的合并结果，**仍走原 mission 的 L2 reviewer**，对照 `CANONICAL_STAGE_ORDER` 的 stage checklist + 反平庸 + 防造假裁决。team 只是把"run 这一阶段"并行化了，**不绕过任何既有质量门**。

---

## 9. Continuity / 续跑（决策 4）

- **每个 teammate 在自己 session 里维护一个 living doc**（`<worktree>/TEAMMATE_STATUS.md`），每完成一步就更新——这条由 lead **写进 teammate 的 sys prompt** 强制（§5.7）。
- **roster** 记成员 + task 状态快照（原子+flock）。
- **daemon 重启**：读 roster → 找半跑成员 → 读它的 living doc 拿到"干到哪了" → 重新 spawn 一个 teammate 从 doc 续上（task 维持 `claimed` 或退回 `pending` 重派）。
- 对比 Agent Teams：官方文档自承 *no session resumption with in-process teammates* 是已知短板；Argus 靠 **roster + per-teammate living doc** 反而能把续跑做得更稳——这正是 Argus "agent with continuity" 哲学的延伸（从单 engineer 的 checkpoint 扩到 team 维度）。

---

## 10. 集成点（要改的真实文件）

| 文件 | 改动 |
|---|---|
| `argus_skill/team/`（新） | `task_board.py` / `mailbox.py` / `roster.py` / `worktree.py` |
| `argus_skill/tools/team.py`（新） | agent 面向工具（§5.5），复用 subagent fork/registry |
| `argus_skill/builtin_skills/engineer/agent-team-lead.md`（新） | lead 角色契约（§5.6） |
| `argus_skill/builtin_skills/engineer/argus-engineer-role.md` | 加一段"何时可组队、solo 是默认"的指引（agent 侧，非 harness 规则） |
| `argus_skill/tools/subagent.py:1231`（`_open_discussion_blockers`）、submit guard ≈`:2035` | ⚠️ 把"parked 阻塞所有 submit"改成 **per-team/per-lane 作用域**，否则 N teammate 死锁 |
| `argus_skill/apps/_life_repl.py:767`（`execute`） | 提供/暴露"单 engineer loop on objective"的瘦入口给 teammate 子进程（teammate 复用同一 SkillLoop 路径） |
| `argus_skill/tools/gpu_lease.py`（`_acquire` :437） | 包一个 team-aware 多 owner 分配器（不改核心，加封装） |
| `argus_skill/planner/planner.py`（可选） | planner 可发**非权威软提示**"此 mission 看着可并行"；**不**做决策、不拆并行 task |

---

## 11. 里程碑 / 分期

- **M1（内核）**：`team/`（task_board+mailbox+roster+worktree）+ `tools/team.py` + teammate=detached-engineer + worktree 隔离 + 两层验收 + 重派 + doc-continuity，实例化在 engineer-as-lead、**同构**。修掉 subagent 全局 block。
- **M2（调度）**：GPU lease 真调度（N teammate 共享 8×B200 的 owner 分配 + 资源上限 gate）。
- **M3（上移 & 富化，后续）**：把同一 primitive 上移到 supervisor 级（并发 mission = team，松动 `supervisor.py:22`）；图形面板；异构 runtime（codex/CC/hermes/omh 混编）。

---

## 12. 测试策略

- **单元**：task_board 并发认领（多进程抢同一 task，断言只一个成功 / flock race）；mailbox per-recipient 投递与 offset；roster 重启 reattach；worktree 隔离（两 teammate 写不到对方路径）。
- **集成**：一个 3-task 玩具 mission 在单 lead 下 fan-out；杀掉一个 teammate → 断言 task 退回 pending 并被重派；两层验收（teammate reviewer + mission reviewer）都触发。
- **防造假**：断言每个 teammate 仍受 integrity guardrails 约束（真实 benchmark / 审计包）。
- **回归**：solo 路径（不组队）行为与现状一致——确认 team 是纯增量、默认不触发。

---

## 13. 风险 & 待解

- **Token 成本**：Agent Teams 文档明确警告 team 比单 session 贵得多；N 个并发 engineer 各自烧 context。需 per-mission 预算 gate 覆盖整支 team 的总花费。
- **subagent 全局 block**（§6 已记）：M1 必修，否则死锁。
- **合并仲裁**：若两 teammate 的产出在"逻辑上"耦合（即使文件不相交），lead 综合时要判断一致性——这是 lead 的判断活，写进 `agent-team-lead.md`。
- **瘦入口**：teammate 复用哪个 engineer-loop 入口需在 plan 阶段定准（`_CodexSkillLoopRunner.execute` 当前与 REPL/supervisor 耦合）。
- **续跑边界**：重启时"task 维持 claimed vs 退回 pending"的判定阈值（heartbeat TTL）需调。

---

## 附：决策记录（brainstorming 共识）

1. 扳机 → **engineer-as-lead**（现场判断；planner 顶多给非权威软提示）。
2. acceptance → **两层**（teammate 自带 reviewer + 合并结果再过 mission 级 L2）。
3. teammate 挂/卡 → **task 退回 `pending` 重派**。
4. continuity → **每 teammate 在自己 session 及时更新自己的 doc；lead 派发时把这条写进 teammate 的 sys prompt**。
5. 分布式文件/git → **shared-nothing 工作产物 + 单写者合并 canonical + per-teammate worktree**。
6. 范围 → v1 同构、不动 supervisor 同步不变量、无图形面板；这些留 M3。
