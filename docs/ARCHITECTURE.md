# Argus Architecture

> 本文是当前运行架构图。文档权威层级见
> [`DESIGN_AUTHORITY.md`](DESIGN_AUTHORITY.md)。若本文与当前实现或行为测试冲突，
> 以实现和测试为准，并在同一变更中修正文档。

Argus 是一个长期运行的 agent harness。Harness 只负责预算、持久化、调度、结构化 I/O、
进程隔离和反造假边界；任务价值、方案选择和证据是否足够由模型角色判断。

## 1. 端到端主链路

```text
operator
  -> CLI / Ink / Web cockpit
  -> Manager front door
       CHAT/SELF -> 直接回复，不入 backlog
       TEAM + BOUNDED -> bounded DAG Planner -> backlog
       TEAM + STANDING -> continuous objective -> L4 Planner -> backlog
  -> LifeSupervisor
       claim backlog item
       -> MissionExecutor / SkillLoop
            match/adapt Skill
            -> Engineer executes one round
            -> independent Reviewer verdict
                 done | continue | blocked | replan_requested
       -> Manager stage decision when eligible
       -> persist events / outcome / next planning state
```

入口和装配路径：

```text
argus-skill / python -m argus_skill
  -> argus_skill/apps/cli/_parser.py
  -> argus_skill/apps/cli/_core.py
  -> argus_skill/webapi/manager_bridge.py 或 argus_skill/daemon/life_worker.py
  -> argus_skill/apps/_runtime.py
  -> argus_skill/life/supervisor/_core.py
  -> argus_skill/apps/_runtime_execute.py
  -> argus_skill/loop.py
  -> argus_skill/engineer/runner.py
  -> argus_skill/reviewer/_core.py
```

## 2. 角色与权威边界

| 角色 | 当前职责 | 无权做什么 |
| --- | --- | --- |
| Manager | operator 唯一前门；解析任务/lifetime/vertical/domain；维护 GoalContract；独占 stage 的语义决策；处理 operator-only 决策 | 不代替 Engineer 实现，不代替 Reviewer 验收 |
| Planner (L4) | 读取真实项目状态；生成 bounded DAG 或 continuous 后续任务；在 `replan_requested` 后替换剩余计划 | 不把 mission 判为完成，不直接写 stage |
| Engineer (L1) | 使用真实文件、工具、搜索、实验和硬件执行任务；更新 `CHECKPOINT.md`；交付可检查证据 | 不跳过 Reviewer，不写 stage，不静默放宽 GoalContract |
| Reviewer (L2) | 独立检查当前 artifact、必要日志和 checklist；返回 `done`、`continue`、`blocked` 或 `replan_requested`；最后编辑 `CHECKPOINT.md` | 不写 stage，不扩大 mission 范围，不替 Planner 创建新计划 |
| Curator（可选） | 团队/teammate 模式下维护 pool、leaderboard 和策略蒸馏 | 不参与普通单 mission 主链路 |

角色权限模式由 composition root 决定并结构化传入各 role config；角色实现不得静默
覆盖该值。特别是 headless daemon 的 Copilot Planner 必须保留已授权的非交互模式，
否则 `--allow-all-tools` 仍会请求终端确认，造成只读探测失败、重复规划和 token 浪费。

当前主链路没有 Engineer `review=skip` 自审旁路。代码中与
`engineer_self_review` 有关的历史事件值、兼容解析或旧测试数据不代表当前生产行为。

## 3. 控制平面

### 3.1 Manager front door

`manager/front_door.py`、`manager/_vertical_ops.py`、`manager/dispatch.py` 和
`webapi/manager_bridge.py` 共同完成：

- 模型判断 CHAT/SELF/TEAM，而不是关键词正则；
- 选择 BOUNDED 或 STANDING lifetime；
- 选择或创建 vertical/domain；
- 把 operator 约束记录到 GoalContract；
- 将任务持久化为 backlog 或 continuous objective；
- 将活跃任务的 operator steering 写入 inbox，而不是直接把原话当成 Engineer 微操。

普通 bounded-DAG 节点使用小型修复预算：默认最多 3 个 Engineer→Reviewer round，内部
兼容 knob 可在 2–8 间调整；progressive experiment matrix 不受该小预算强行截断。

### 3.2 GoalContract

`core/project_contract.py` 的 `goal_contract.json` 区分：

- `precise`：数值目标、硬件预算、命名 baseline、deadline 等；改变它会改变“done”的
  含义，必须有覆盖该变更的 operator confirmation。
- `semantic`：需要角色结合 artifact 判断的意图说明；Manager 可以澄清，但不能借此
  偷换 precise 约束。

Planner、Engineer、Reviewer 均会收到有效任务契约。GoalContract 当前约束目标漂移，
项目完成本身仍由 `core/project_api.py` 的 completion source/gate 机制裁决。

### 3.3 Planner 与计划替换

Backlog 节点持久化 `plan_id`、`plan_version`、`node_key`、依赖和有界
`context_refs`。Reviewer 若判断当前方向在既定 mission 内不可修复，返回
`replan_requested`：

```text
Reviewer replan_requested
  -> 当前 item 结算为 replan 请求
  -> LifeSupervisor 调用 L4 Planner
  -> compare-and-swap 替换剩余 active nodes
  -> 已完成节点不变，旧 active nodes 进入 superseded
  -> 失败则保留旧计划可运行
```

不存在 `ARGUS_SKILL_DYNAMIC_PLAN_MODE=off|shadow|active`、连续
`plan_signal` 确认或 `plan_reconsider_streak`。`life.plan.signal` 仅作为遗留协议名保留，
当前没有生产者。

### 3.4 Stage 与 Project completion

- Stage 顺序和 checklist 由 `verticals/*/stages.py` 定义。
- 通用 stage 读写和证书在 `skills/stage_machine.py`。
- Manager 的 `_stage_ops.py` 是 `current_stage` 的唯一语义决策路径。
- Supervisor 有一个机械补偿写路径：同一 bounded DAG 尚有未完成节点时，
  `_apply_dynamic_plan_stage_guard` 可撤销提前发生的 `advance`，恢复到本 mission 起始 stage。
  它不能选择任意 target，也不能替代 Manager 的科研判断。
- `core/project_api.py::complete_project` 是 Project DONE 的统一写入口，并按照 vertical 的
  `completion_gate` 比较 completion source 强度。
- 论文型 gate 的当前名称为 `full_paper` / `full_paper_gate`。

## 4. 执行平面

### 4.1 SkillLoop

`loop.py` 负责单个 mission 的胶水：

```text
objective
  -> SkillStore / matcher
  -> miss 时 Scientist distill/adapt
  -> SupervisedEngineer.run
       Engineer round
       Reviewer.evaluate
       continue -> Reviewer next_action 进入下一轮
       done/blocked/replan_requested -> 返回 LifeSupervisor
  -> skill/wiki maintenance 与 outcome 结算
```

每个 Engineer 和 Reviewer 回合使用新的 provider session。跨回合连续性由普通 Markdown
文件 `CHECKPOINT.md` 承担，不继承 provider 私有 transcript。

### 4.2 长任务和等待

长实验由 `argus_skill.tools.subagent` 或统一 external-work registry 托管。Engineer 可在
最终回复的最后一个非空行输出精确 JSON：

```json
{"wait_for":"subagent","wait_id":"<registry-id>"}
```

Runner 验证 registry id 和健康状态后，只按 supervisor cadence 等待；不存在另一个
mission-scoped control file 作为等待事实源。

### 4.3 Backend 边界

应用层统一通过 `core/run_gateway.py` 调用 backend。实际 CLI 适配位于：

- `adapters/agent_cli_backend/`
- `agent_cli/`
- `adapters/memory_backend.py`（测试）

Provider 进程、事件解析、usage 提取、硬空闲终止和进程组清理由 adapter 层负责。

## 5. 证据与持久化平面

### 5.1 两个根目录

- **project workdir**：四个角色实际读写项目 artifact 的目录。
- **project state root**：`~/.argus-skill/projects/<fingerprint>/`，保存 Argus 内部状态。

恢复 session 不得把 `launch_cwd` 猜成新的 workdir。一个 daemon 在全生命周期持有
canonical-workdir lease，防止多个 session 同时写同一工作目录。

### 5.2 主要状态文件

```text
~/.argus-skill/
  identity.md
  config.json
  skills/
  cost-control.json
  projects/<fingerprint>/
    session.json
    goal_contract.json
    backlog.jsonl
    events.jsonl
    usage.jsonl
    continuous.json
    inbox.jsonl
    transcript.jsonl
    daemon.status.json       # 仅运行时存在
    daemon.pid               # 仅运行时存在
```

`events.jsonl` 是唯一历史事实源；`EventJournal` 只是从该文件投影短历史，不存在独立的
global journal 或 `journal.jsonl` 真相源。

## 6. 运行和发布身份

不同 daemon 可以从不同 source root 启动，因此 package version 相同不代表行为相同。
`daemon.status.json` 和 WebAPI meta 同时暴露 source root、revision、release id、manifest
digest 和 runtime digest。协议兼容见 `protocols.md`，release identity 见
`release-identity.md`。

开发部署可设置 `ARGUS_SKILL_REQUIRE_RELEASE_MATCH=1`，让 source 与已构建 release 不一致时
拒绝启动。默认关闭该门禁意味着允许 editable checkout 快速开发，也意味着 operator 必须
主动管理不同 daemon 的 source/revision 漂移。

## 7. 模块边界

完整维护边界见 `orchestration-modules.md`。核心原则：入口模块只编排，状态读模型、进程
适配、prompt、计划周期和 mission 结算分别位于专用模块；不要重新把职责堆回
`_core.py`、`server.py` 或 `life_worker.py`。
