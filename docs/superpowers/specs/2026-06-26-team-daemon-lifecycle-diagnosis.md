# Argus Teams × 新 main — 生命周期 / 集成诊断报告

> **状态（2026-06-26 更新）**: 本报告诊断的问题**已实现并合并到 main（PR #78）** —— M1 daemon-resident Curator（止漏，live codex 跑通验证）+ M2 确定性 leaderboard + Curator agent 角色。**尚未部署**：线上 daemon 仍跑分叉 fork `c51d0dd`（installed package），部署需把 install 指向 main 或走 reconcile。**M3（near-death resume）** 待 dive/handoff port（见 `../plans/2026-06-26-team-lifecycle-reconcile-onto-main.md`）。

- **日期**: 2026-06-26 ｜ **基线**: `origin/main @ 823dfcf`（真·最新；分支 `dev/team-latest`）
- **方法**: systematic-debugging（先复现/理解/定位根因，**本报告不含修复**）
- **范围**: `argus_skill/team/*` + `tools/team.py` + `agent-team-lead.md` 全读；daemon 侧集成接缝 grep；**daemon 实机（`/data/yijia/argus-skill` + 活进程）运行时核实**
- **测试基线**: `tests/team` + `test_team_cli.py` = **43 passed**（重构未打挂 team 代码）

---

## 0. TL;DR

**「teams 和新 main 是两种形态、daemon 控不住 teammate 周期」——确诊，而且是两层叠加，都已实锤：**

- **实锤 A｜代码分叉（最直接的"两种形态"）**：daemon 实际在跑的 `/data/yijia/argus-skill` 是一条**和 main 分叉**的 team 代码（HEAD `c51d0dd`，06-25），有 **main 里根本不存在**的生命周期工作（`3bb068a` dive-mode + near-death-handoff、`c51d0dd` dive-fix），同时**缺** main 分叉点之后的框架/team 工作（Manager stage 决策权、`regime_jump` 改名、`4d9c4ac`/`815acb2`…）。两边**各自独立演化了 team**。
- **实锤 B｜运行时泄漏（周期失控的活样本）**：同一个 team root 上**2 个 coordinator 已经跑了 ~18 小时**（远超 max_wall 6h）；多个 teammate 存活 **114–117min > 100min 硬死线**却没被回收；member seq 已到 **w10500+**。
- **实锤 C｜架构缺口（即便代码统一也还在）**：M2 设计**显式**把 team 做成进程外、文件协调、单 mission 作用域、靠心跳软超时自终止的 swarm，并把 daemon 级生命周期所有权**留给 M3**。daemon 对 swarm 没有进程所有权、没有回收钩子、甚至不知道它的 root。

**重构本身是干净的**（team 代码已随主线改名，无悬空引用，43 测试绿）。问题不在"改名打挂"，在 **(A) 两条 team 代码分叉未 reconcile + (C) M2→M3 架构缺口**，并在常驻 daemon 下爆发成 (B) 的泄漏。

> **结论：这不是单点 bug，是一个"把 daemon 的分叉 team 工作 reconcile 到重构后的 main + 补上 daemon 级生命周期控制"的合并型大活。**

---

## 1. 实锤 A：代码分叉（你说的"两种形态"，字面版）

daemon checkout `/data/yijia/argus-skill`：分支 `merge/teams-onto-latest`，HEAD `c51d0dd`（06-25）。分叉点 = `5774103`（两边都有）。其后：

| | daemon 独有（main ❌ 无此对象） | 改了什么 |
|---|---|---|
| `11332c2` | measured-bench（**main 有等价但不同 hash 的 `a5c92ee`** → 重复演化） | reviewer/loop + **team/roster +45, task_board +28, teammate_entry +197, tools/team +25** |
| `3bb068a` | **progress-aware lifecycle：dive mode + near-death handoff（env-gated）** ← **生命周期，main 完全没有** | reviewer +23, loop +52, **teammate_entry +26**, `docs/progress-aware-lifecycle-design.md` +119 |
| `c51d0dd` | **fix(team): dive = engineering deeper, NOT a param sweep** ← main 没有 | reviewer +29, loop +33 |

而 **main（823dfcf）独有、daemon 没有**：Manager stage 决策权（`a344c1f`/`bf9a605`）、`argus_skill/meta→regime_jump` 改名、`4d9c4ac`(team load-bearing)、`815acb2`(Codex→中立) 等分叉点之后的一切。

→ **两条 team 代码已各自演化**：daemon 有生命周期工作（dive/near-death-handoff）main 缺；main 有框架重构 daemon 缺。要修必须先 **reconcile**，不是改一个文件。
（注：`docs/progress-aware-lifecycle-design.md` 只在 daemon clone，是那套生命周期模型的设计文档，**下一步必读**。）

---

## 2. 实锤 B：运行时泄漏（"daemon 控不住周期"的活样本）

实机进程（2026-06-26 探测）：

- **2 个 coordinator，同一个 root** `…/teams/solopt43-20260618T201956Z`，各 **alive ~64782s ≈ 1079min ≈ 18h**。
  - 正常每个 lead mission 只该有 **1 个** coordinator；出现 2 个 = **重复/孤儿 coordinator**。
  - 18h ≫ max_wall(6h) = 终止判定根本没把它们带走（孤儿，见 §4 BUG-1 / §3）。
- **teammate 多个 114–117min**（w10538…w10543）> **100min 硬死线**（90min 软 + 10min 硬 grace）→ `teammate_entry` 的硬 SIGKILL time-box **在 daemon 跑的代码上没生效**（很可能 daemon 那条分叉里这块逻辑不同）。
- **member seq w10500+** → `roster.next_member_id` 每 spawn 全表扫的 O(N) 成本（§4 BUG-5）已是真实规模。

→ 泄漏的老 teammate + 重复 coordinator 持续抢 GPU/CPU（代码注释自录"300+ teammates → load 256/128 cores"）→ 在飞 teammate 变慢 → **mean SOL 低分**。这就是你说的"逻辑不对的低分"的最大来源。

---

## 3. 实锤 C：架构根因（为什么即便代码统一，daemon 仍控不住 teammate 周期）

来源：M2 设计 `…/2026-06-18-argus-teams-rolling-pool-design.md` §2/§5（原文）：
> **不**把 coordinator 提到 daemon/supervisor 级…那会碰 supervisor "一次一个 mission" 的不变量，**归 M3**。本 M2 coordinator **作用域 = 一个 lead mission**。

- **形态①（daemon 拥有）**：进程内、一次一个、Manager 管 stage/teardown 的 supervised mission。
- **形态②（daemon 不拥有）**：`nohup`+`start_new_session=True` 脱离（`tools/team.py:59`、`teammate_entry.py:389`）、只靠 `pool.json` 心跳 + task board 文件协调的 coordinator+teammate 群。
- **唯一纽带 = `pool.json` lead 心跳**（`agent-team-lead.md:36`、`pool.py:202-212`），一个 30min 软超时，**不是进程所有权**。
- **daemon 侧零感知零回收**：`grep -rE 'teammate_entry|coordinate|killpg|pkill' apps/ life/` = 空；daemon 从不被传 `team_root`，想回收也不知道回收谁。
- **跨 mission 重叠**：`pool.py:371` 注释——"the daemon lead is a **sequence** of bounded missions"。每个 lead mission 起自己的 coordinator，旧的要等心跳过期/max_wall 才退 → 与新 mission 的 swarm 重叠（= §2 看到的 2 个 coordinator）。
- **新 Manager 只治形态①**：teammate 直接 `_SkillLoopRunner.execute`（`teammate_entry.py:185,228`）**绕过 Manager stage 治理**。

---

## 4. 逻辑 / 低分 Bug 审计（基于 main 的 team 代码）

| # | 位置 | 问题 | 严重 | 置信 |
|---|---|---|---|---|
| 1 | `pool.py:15,207-211,287` | lead 未首次心跳(hb==0)→孤儿 coordinator 狂 spawn 至 max_wall(6h)；`hb>0` 守卫把"从没心跳过"当"别停"（语义反） | **高** | 高（§2 实锤） |
| 2 | `pool.py:287` | `width or a.width`：`pool-set --width 0`(暂停)被当 unset→回落 8，无法暂停 | 中 | 高 |
| 3 | `teammate_entry.py:208-212` | 硬 SIGKILL 绕过 finally→不写 shard、任务卡 running；无活 coordinator 则永久泄漏+丢已测好结果 | 中-高 | 高 |
| 4 | `teammate_entry.py:183,206` vs `tools/team.py:371` | teammate time-box ~100min ≫ lead-ttl 30min→lead 死后 teammate 无人回收→压垮机器→低分 | **高** | 高（§2 实锤） |
| 5 | `roster.py:75-84,99-108` | `next_member_id` 每 spawn 全表扫 tasks(O(N))持锁→长 campaign refill 变慢→在飞<N→低吞吐 | 中 | 中（w10500+ 实锤） |
| 6 | `task_board.py:167-193`+`pool.py:110-142` | `reassign_stale` 的 live_owners 取自 roster PID；roster 压缩/丢失→误判 stale→同 task 双开 | 中 | 中-低 |
| 7 | `task_board.py:133-135` | 硬杀后任务仍 running 占 in_flight→该 slot 暗 ≥ttl(180s) 才补 | 低 | 中 |

详述见各位置；**BUG-1 / BUG-4 与 §2 的运行时泄漏直接对应**，是"周期失控 + 低分"的主因。

---

## 5. 待你拍板的方向（本报告不实现）

这是 **reconcile 两条分叉 + 修生命周期** 的活。需要你定：

1. **以谁为准 reconcile？**（推荐：以重构后的 main 为基，把 daemon 的生命周期工作 `3bb068a`(dive/near-death-handoff)+`c51d0dd` port 过来，对齐 main 的 Manager/regime_jump 重构；`11332c2` 与 main 的 `a5c92ee` 去重。）
2. **现在要不要先止血？** daemon 上 2 个 coordinator 跑了 18h、teammate 超时未回收——是否先 `pool-set --state draining` + 清掉超 100min 的泄漏进程，止住低分出血。
3. **生命周期真修方案**：
   - **A2（推荐，较小）**：给 daemon 一个回收钩子——lead 把 `team_root` 写进约定文件，daemon 在 mission teardown/Manager rollback 时对该 root `draining`+宽限后 `killpg` teammate_entry。给 daemon 真正的所有权，不碰 supervisor 不变量。
   - **A1（M3 大改）**：coordinator 提升为 daemon 拥有的跨 mission 常驻池。
   - **B 短期止血**：BUG-1（hb==0 即停）、BUG-2（width=0 语义）、BUG-4（lead-ttl≥teammate 硬死线）、BUG-3（SIGTERM→宽限→SIGKILL 给 finally 机会）。

**下一步我建议**：读 daemon 上 `docs/progress-aware-lifecycle-design.md` + diff `3bb068a`/`c51d0dd` 的实际改动，做一份"reconcile 到 main"的 plan。等你点头方向。

---

## 附：本次调查动作（可复核）
- 读全 8 个 team 文件 + CLI + lead skill + M2 设计文档；43 测试绿
- grep 实证 daemon 侧零引用 team；teammate detached；daemon=mission 序列
- 实机核实：daemon checkout 分叉（c51d0dd vs 823dfcf）、3 个 commit 文件 stat、main 无此对象；活进程 2×coordinator@18h、teammate>100min、w10500+
