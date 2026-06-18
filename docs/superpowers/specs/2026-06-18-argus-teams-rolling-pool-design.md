# Argus Agent Teams — 滚动池 / Coordinator 时序系统（M2 设计文档）

- **日期**: 2026-06-18
- **状态**: Draft（brainstorming 产出，待写 implementation plan）
- **作者**: msra + Claude
- **主题 slug**: `argus-teams-rolling-pool`
- **续自**: `2026-06-17-argus-agent-teams-design.md`（M1：shared task list + mailbox + teammate + 两层验收 + time-box）
- **里程碑**: **M2**

---

## 0. 一句话

把 team 的调度从**“一批一批的 barrier”**升级成**“滚动池”**：lead 不再 `form→wait(等整批)→synthesize→下一批`，而是把**“保持 N 个 teammate 在飞”这件纯机械的事**交给一个**哑 coordinator 进程**持续 enforce，lead 解放成**纯判断**——只维护一条优先级 backlog（写）和读 shard 记录 best（读）。**“补位时钟”脱离 lead 的 reasoning 节拍**，slot 一空即补，最大化在飞的 engineer 脑子数。

---

## 1. 问题 / 诊断

M1 上线后实测：GPU（B200 卡 2/3/4/5）几乎 0% util，真正的吞吐瓶颈是**并发的 engineer 脑子数 × 它们的忙碌占比**，不是卡。而当前 objective 白纸黑字写的是 `waits with a bounded timeout` + `keeps launching new teams`，即：

```
form 8 个 → wait(等 8 个全收完，barrier) → synthesize → form 下一批 8 个
```

根因是 **lead 一个脑子同时承担了两个职责**：

1. **大脑（判断）**：哪些 kernel 值得打、广度还是深度、候选有没有刷过 best。
2. **时钟（机制）**：`form / spawn / wait / spawn` 的节拍。

这俩耦合在**一个串行 reasoning loop** 里，于是：

- **refill 速度 = lead 思考速度**。某 teammate ~9min time-box 自退、slot 空了，但 lead 正埋头读另一个 shard / 想下一批，这个 slot 就一直空，直到 lead 下一轮想起来补 → 卡空转。
- 一旦 `wait`（barrier），N 个 slot 直接清零等最慢的那个，空档更大。

**结论**：要“协同加速”，必须把**大脑和时钟解耦**——让一个无 LLM 的机械循环专职补位，lead 专职判断。

---

## 2. 目标 / 非目标

### 目标（M2）
- **滚动池**：一个独立 **coordinator 进程**持续 enforce “恰好 N 个 teammate 在飞”，slot 一空**立刻**补，**完全脱离 lead 的 turn 节拍**。
- **大脑/时钟解耦**：lead 只“排优先级 backlog + 读 shard 验收”，**不再 spawn、不再 wait(barrier)**。
- **backlog = 现有 task board**：给 pending task 加 `priority`，coordinator 挑优先级最高的 pending。广度=追新题、深度=同题再 form 一条，**同一机制**，不特判。
- **唯一 spawner**：只有 coordinator spawn → 从结构上消除 M1 的 claim-race 那类 bug。
- **孤儿安全**：lead 被 time-box 砍掉也不会留下一个还在 spawn 的野进程。
- **复用 M1 全部**：teammate 本体 / time-box watchdog / shard / 两层验收 / worktree 一律不改。

### 非目标（YAGNI / 留给后续）
- **不**把 coordinator 提到 **daemon/supervisor 级**让它跨 mission 常驻（pool 永远热）——那会碰 supervisor “一次一个 mission” 的不变量，归 **M3**。本 M2 的 coordinator **作用域 = 一个 lead mission**。
- **不**做自适应路由探测来自动定 N；N 由 lead 给、coordinator enforce，lead 可手调。
- **不**做 Web 面板；沿用 event/`_watch.py` 文本视图 + `team status`。

---

## 3. 架构：生产者 / 消费者

```
            ┌──────────────────────────────────────────┐
   LEAD ───▶│  共享 task board (flock)  =  优先级 backlog │◀── COORDINATOR(哑循环, 无 LLM)
 (判断/大脑) └──────────────────────────────────────────┘    每 ~5s 一 tick:
   │ 写: backlog 追加/重排 (广度=新题 / 深度=同题再来一轮, 带 priority)   1. reassign_stale(心跳超时→pending)
   │ 读: shard 落地 → 比 MEASURED SOL vs best → 记录改进              2. in_flight = #claimed+#running
   │ 调: pool-set 改 width N / 置 draining (兼作 lead 心跳)            3. free = N - in_flight; 取 free 个
   └───────────────────────────────────────────────────────────────►   最高优先级 pending, 各发新 member-id
        teammate w1 w2 … wN  (各自 time-box 自退, 退了即空 slot) ◀───────  claim_top + spawn 补满 N
```

**契约**：lead 与 coordinator 之间只有两个文件（都在 team root，flock 保护）：
- **task board**（`tasks/*.json`）= 工作队列（backlog + 状态）。
- **`pool.json`** = 控制面 `{ width, state, lead_heartbeat_ts }`。

### 7 个决策各归各位（回答“上层怎么编排”）

| 决策 | 谁定 | 怎么定 |
|---|---|---|
| 跑哪个 kernel | **lead（判断）** | backlog 队头（priority 最高的 pending）；lead 随时重排 |
| 开几个（宽度 N） | lead 设、coordinator enforce | `pool.json.width`，lead 随路由容量调 |
| **何时补位** | **coordinator（机制）** | slot 一空即补，不等 lead 想 ← 吞吐增益在此 |
| 广度 vs 深度 | **lead（判断）** | 纯靠 backlog 内容：追新题 / 同题再 form 一条 |
| 收不收（beat best） | **lead（判断）** | 读 shard 比 MEASURED SOL，异步，从不卡池子 |
| 死 / 卡 | coordinator（机制） | 心跳 TTL → 退回 pending 重派 |
| 何时收手 | lead | 停止追 backlog + `pool-set --state draining`，coordinator 排空后自退 |

---

## 4. 组件 / 接口（新增面，全是小件）

### 4.1 `argus_skill/team/task_board.py`
- **task 增字段** `priority: int`（默认 `100`，**数字越小越优先**）。`form()` 从 spec 读取 `spec.get("priority", 100)` 写入 task 记录。
- **新增 `claim_top(root, member_id, *, now) -> dict | None`**：在 flock 内，从所有 `state=="pending"` 且 `deps` 全 done 的 task 中，取 **priority 最小（同 priority 以 task_id 升序 tiebreak）** 的那个，CAS 置 `claimed` + `owner=member_id` + 时间戳，返回该 task；无可领则 `None`。语义同 `claim()`，仅排序规则不同。
- **新增 `count_in_flight(root) -> int`**：`snapshot` 里 `state in ("claimed","running")` 的计数（claim 在 spawn 瞬间原子置 `claimed`，故新 spawn 立即计入 → 不会 double-spawn）。
- 保留 `claim` / `claim_specific` / `reassign_stale` / `complete` / `fail` 不变。

### 4.2 `argus_skill/team/roster.py`
- **新增 `next_member_id(root, *, prefix="w") -> str`**：flock 内自增 `roster.json.member_seq`（缺省从 0 起），返回 `f"{prefix}{seq}"`（如 `w1`,`w2`,…）。保证 coordinator 连续发号、永不撞号。

### 4.3 `argus_skill/team/pool.py`（新文件，控制面）
- `read(root) -> dict`：读 `pool.json`，缺省 `{"width":0,"state":"running","lead_heartbeat_ts":0.0}`。
- `set(root, *, width=None, state=None, now) -> dict`：flock 内合并写；**每次 set 都刷新 `lead_heartbeat_ts=now`**（所以 lead 调 `pool-set` 兼作心跳）。原子写。

### 4.4 `argus_skill/tools/team.py`（CLI，新增 2 个 verb + 复用 spawn 逻辑）
- **`refill_once(root, *, width, cwd, member_prefix, ttl, now, spawn_fn=None) -> dict`**（纯逻辑，可单测）：
  1. `task_board.reassign_stale(root, ttl=ttl, now=now)`；
  2. `in_flight = task_board.count_in_flight(root)`；
  3. `free = max(0, width - in_flight)`；
  4. 循环最多 `free` 次：`mid = roster.next_member_id(root)`；`task = task_board.claim_top(root, mid, now=now)`；`task is None` → `break`（backlog 空）；`spawn_fn(member_id=mid, task_id=task["task_id"], cwd=cwd)`（默认 = 现有 `cmd_spawn` 的进程启动逻辑，抽成可注入函数；测试传 stub）；`roster.add_member`；
  5. 返回 `{"spawned":[...], "in_flight":..., "free":...}`。
- **`team coordinate`**（`cmd_coordinate`，detached 长循环；**lead 用 `nohup ... &` 起一个**）：
  - 参数：`--root --team-id --cwd --width(默认值) --poll(5s) --ttl(teammate stale, 180s) --lead-ttl(300s) --max-wall(兜底, 如 6h) --member-prefix(w) --exec-cmd(测试 stub 透传给 spawn)`。
  - 每 tick：`now=time()`；`p = pool.read(root)`；`width = p["width"] or args.width`；
    - **终止判定（满足任一即退，绝不留野 spawner）**：① `p["state"]=="draining"` 且 `count_in_flight==0`；② `p["lead_heartbeat_ts"]>0` 且 `now-lead_heartbeat_ts>lead_ttl`（lead 没了）；③ `now-start>max_wall`；
    - 否则 `refill_once(width=width, ...)`；
    - `sleep(poll)`。
  - 日志 → `root/logs/coordinator.log`。
- **`team pool-set`**（`cmd_pool_set`）：`--root --width --state` → `pool.set(...)`（兼作 lead 心跳）。lead 每轮判断循环调一次。
- 现有 `form/spawn/status/wait/send/drain/claim/reassign/dissolve` 全保留（`wait`/`spawn` 仍在，供测试与回退；滚动模式下 lead 不用 `wait`、不直接 `spawn`）。

---

## 5. 生命周期与安全

- **作用域 = 一个 lead mission（M2）**。lead 起 coordinator → 跑判断循环 → 收尾 `pool-set --state draining` → coordinator 排空在飞的再自退 → lead `dissolve`。下一个 mission 起一个全新的。
- **孤儿保护**：见 §4.4 终止判定 ② —— lead 被 time-box 砍掉、没来得及置 draining，coordinator 在 `lead_ttl` 后凭“lead 心跳过期”自退，**不会撞上下一个 mission 的新 lead**。③ `max_wall` 再兜一层。
- **唯一 spawner**：只有 coordinator spawn，`in_flight` 含 `claimed`（spawn 瞬间原子置位）→ 不 double-spawn；M1 的 claim-race 从结构上消失。
- **board 并发**：所有 mutate 走 `_store.locked`（flock）；coordinator / lead / teammate 三方安全。
- **coordinator 无状态**：每 tick 全部从 board+pool 重算 → 崩溃可直接重启续跑。

---

## 6. 数据流（一个 campaign）

1. **lead**：选 K 个 untouched kernel → `team form`（带 `priority`）建初始 backlog + roster。
2. **lead**：`nohup python -m argus_skill.tools.team coordinate --root R --team-id T --cwd WS --width N ... &` 起**一个** coordinator。
3. **coordinator**：每 5s 补满到 N（`claim_top`+spawn 新 member），回收 stale。
4. **teammate**（M1 不变）：领到的 task = 一个 kernel，跑 `_CodexSkillLoopRunner.execute` 时间盒 → 写 `shards/<member>.jsonl` → `complete`/`fail` → 退出 → 空出 slot。
5. **lead 判断循环**：`pool-set --width N --state running`（心跳）→ 读新落地 shard → 比 MEASURED SOL vs best → 记录改进 → **给 backlog 补料**（广度：新 untouched kernel；深度：把有苗头的 kernel 再 form 一条“换机制再来一轮”的 task，给更高 priority）→ 视路由忙闲调 N → sleep。
6. **收尾**：backlog 打得差不多 / 够好 → `pool-set --state draining` → coordinator 排空自退 → lead 综合 best-per-kernel → 过 mission 级 L2 reviewer → `dissolve`。

---

## 7. 错误处理

| 情况 | 处理 |
|---|---|
| teammate 卡死 / 心跳超时 | coordinator `reassign_stale(ttl)` 退回 pending，下 tick 重派（time-box 仍是第一道防线） |
| backlog 空 | `claim_top` 返 None，coordinator 这轮少补，pool 自然小于 N（优雅降级），等 lead 补料 |
| lead 死 / 被 time-box 砍 | coordinator 凭 lead 心跳过期自退（§5），不留野 spawner |
| coordinator 自己崩 | 无状态，重启即续；最坏情况 pool 暂时不补，teammate 各自 time-box 收尾 |
| double-spawn | 结构上不可能（唯一 spawner + in_flight 含 claimed） |
| 测量造假 | 不变：候选只有 **MEASURED-on-B200 beat best** 才算改进；lead 验收门 + 防造假沿用 M1 |

---

## 8. 测试

**单元**：
- `claim_top`：优先级最小者先领；同优先级按 task_id；deps 未 done 不领；并发 flock 下两 member 不撞同一 task。
- `count_in_flight`：claimed+running 计数正确。
- `roster.next_member_id`：连发唯一、单调、并发安全。
- `pool.set/read`：合并写 + 每次刷 heartbeat；缺省值。
- `refill_once`（注入 stub `spawn_fn`）：① in_flight<N 时正好补到 N；② in_flight>=N 时不补；③ backlog 不足时补到耗尽即停；④ 先 reassign 再补；⑤ 幂等（连调两次、状态不变则第二次 free=0）。
- coordinate 终止判定：draining+0 在飞 / lead 心跳过期 / max_wall 各自触发退出。

**集成**（用 `--exec-cmd` 跑 stub teammate，不烧 LLM）：
- 起 coordinator，width=4，backlog=10：稳定维持 4 在飞；stub 随机退出后被秒补；10 个全 done 后 draining 自退；全程无 double-spawn、member-id 唯一。

**回归**：M1 现有 team 测试全绿（form/claim/heartbeat/complete/reassign/wait、lane-scoped subagent 等）。

---

## 9. Objective / AGENTS.md 改写（lead 契约）

把 lead 从 “form→wait→synthesize→下一批” 改成 **滚动池**：

> 作为 LEAD，**默认跑滚动 teammate-engineer 池**，不要用 per-task subagent 做跨 kernel fan-out，也**不要 `wait` 整批**：
> 1. `team form` 选一批 untouched kernel 建初始 backlog（带 priority）。
> 2. `nohup python -m argus_skill.tools.team coordinate --root R --team-id T --cwd WS --width 8 --poll 5 --ttl 180 --lead-ttl 300 --max-wall 21600 &` 起**一个** coordinator（默认 N=8、max_wall=6h，可调）。
> 3. 进入**判断循环**（你只做判断，**绝不自己 spawn / 绝不 wait barrier**）：`team pool-set --width N --state running`（兼心跳）→ 读新落地 shard → 比 **MEASURED-on-B200 SOL vs 当前 best**、只归档**真超过 best** 的改进 → 给 backlog 补料（广度：新 untouched kernel；深度：把有苗头的 kernel 再 form 一条换机制的 task，给更高 priority）→ 视路由忙闲调 N → sleep 后重复。
> 4. 收尾：`team pool-set --state draining` → 等 coordinator 排空自退 → 综合 best-per-kernel → 过 L2 reviewer → `team dissolve`。
> 约束沿用：只用 B200 卡 2/3/4/5；新机制来自 idea-wiki + 自身理解并经实验验证（**禁调参 / 禁锚定最强 baseline / 禁用 Yuandong Tian team 方法**）；correctness before speed；**绝不报未在 B200 实测的 SOL**。

把这能力**写进 Argus 系统知识**（engineer/benchmark-runner skill 文档）：多 task 优化型 benchmark **默认起滚动 teammate 池**。

---

## 10. Rollout / 部署

1. 在 `/data/yijia/argus-merge`（=origin/main）实现 §4，跑 §8 测试全绿。
2. commit 到 **main**（作者 `waltstephen <1016013662@qq.com>`），push。
3. deploy 到 daemon checkout `/data/yijia/argus-skill`：fetch + 对齐 main（reconcile 现有 2 个脏文件），确认 `argus-skill` 可执行指向该 checkout。
4. 改 `/data/yijia/sol-execbench-argus/AGENTS.md` + daemon objective 为 §9 的滚动池版。
5. **重启 daemon 跑 SOL-ExecBench**（caps=99999、无预算上限、卡 2/3/4/5），观测：coordinator 持续维持 N 在飞、slot 秒补、覆盖率与 mean SOL 增速。

---

## 11. 开放问题 / 待定

- `width N` 初值与 `max_wall` 具体值：实现时取保守默认（如 N=8、max_wall=6h），上线后按路由实际并发上限手调。
- coordinator 与 lead 同写 board 的极端竞争：已由 flock 串行化；若 tick 太密（poll 过小）可能与 teammate 心跳争锁，poll=5s 足够稀疏，必要时退避。
