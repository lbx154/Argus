# Argus Skill Agent 修改地图

这份说明给后续接手的 agent 看。目标不是替代代码阅读，而是让你先知道“哪里管哪里”，避免在 EMNLP 论文 pipeline 或 7x24 harness 里乱改错层。

设计文档的权威层级和历史材料边界见 `docs/DESIGN_AUTHORITY.md`。

## Git 发布边界

融资、BP、VC deck、路演文案及其配图不得进入主分支。它们应保存在仓库外的私有工作区；
`.gitignore` 钉住已知路径，避免被 `git add -A` 重新带回。

## 一句话架构

`argus-skill` 是一个长期运行的 agent harness：外层 `LifeSupervisor` 管 backlog、预算、daemon、L4 planner（forward scheduling）；内层 `SkillLoop` 向角色提供 agent-native Skill-library 路径，并管理 L1 Engineer 执行和 L2 Reviewer 验收。运行时不匹配、解析或注入 Skill 正文；Manager、Planner、Engineer、Reviewer 用自己的文件工具发现 Skill，并可把本角色的耐久方法选择性写入项目层角色目录。`ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING=0/1` 是四角色统一维护开关。当前每个正常 mission round 都走 `Engineer -> independent Reviewer`；历史 Engineer self-review 旁路已退出生产链路，旧事件/兼容字段中的 `engineer_self_review` 只代表历史数据。成功 mission 边界由 Manager 判断 stay / shared-global / shared-vertical，默认把可迁移经验传播给其他项目。历史上的 L3 critic 逐轮打磨循环已经移除。论文 pipeline 是 built-in skill + vertical-owned checklist + Reviewer 裁决 + Planner fallback 共同实现的，不是机器 validator 工具链。

**角色限制责任，不限制能力。** L4 Planner 持续负责 forward planning，但使用完整
Agent 工具主动读代码、跑有界探针/测试，并可在形成或验证计划所必需时直接编辑代码或规划
artifact；不能把它降级成只读摘要后输出 JSON 的生成器。持续实现仍由 Engineer 主责，
Reviewer 负责每个正常 mission round 的独立验收。

**科研以价值为目标，诚信是硬约束。** 可复现、反造假、完整保留负结果都是准入条件，
但它们本身不构成科研完成。一个 bounded 实验可以诚实地以 `redirect` / `stop` 结束；
项目只有在 Reviewer 认证了满足 operator 价值目标的结果、方法或独立见解后才能完成。
Manager 和 Planner 必须把其余负结果用于诊断与重规划，不能把“报告得诚实”当成成功。

**Token-efficiency rule:** prompt 改动先看边际效果/token。禁止为了“更稳”重复注入同义
角色规则；优先用文件状态、按需 checklist 和一个权威短契约。必须保留关键证据/gate，
但新增长文案前先测 prompt 大小，并为常见路径设置回归预算。

**Model evidence boundary:** checksum / content digest / commit id 只允许作为宿主机内部的
缓存、去重、损坏检测和原子身份元数据。四个角色的模型不得读取、复述、比较这些不透明值，
也不得用它们判断 freshness、correctness、provenance、completion 或 contradiction；纯 hash
差异永远不能触发 `continue` / `blocked` / `replan_requested`。语义裁决必须基于直接内容、
结构化字段、时间、命令/测试输出、指标和人类可读 provenance。

**Checklist ownership:** vertical/framework 提供当前 checklist；Reviewer 在普通 verdict
中报告问题，Planner/Manager 通过后续任务修复源 contract。当前运行时不提供
`checklist_ops` 写通道；历史 `research/CHECKLISTS.json` 仅作只读兼容，必须带 vertical，
和当前项目 vertical 不一致时完全忽略。

**Math judgment:** Reviewer 直接判断问题、实际数学结果与论证，不按过程文件验收。只有
在声明 novelty 或目标确实依赖 novelty 时才做相称的一手来源检查；不强制独立审计节点、
命名审计文件或“未触发”记录。正确的 bounded 结果可以完成，同时把 novelty 诚实标为未知。

**Math methods:** 反例搜索、构造检查、计算、premise 追踪和形式化都是 Agent 按题选择的
方法，不是固定阶段或 checklist。需要时保留真实代码、编译输出和来源；禁止为了流程创建
scope/solve/ledger/graph/audit/evidence-packet 文件。

**Budget ownership:** 唯一货币预算是全机 daily USD cap
`ARGUS_SKILL_GLOBAL_DAILY_CAP_USD`。所有项目共享全机 `usage.jsonl` 汇总与调用级原子预留；
没有 per-mission/project budget，也不把 USD 转换成 provider credits/fence。

主链路：

```text
argus-skill / python -m argus_skill
  -> argus_skill/apps/cli/_parser.py + argus_skill/apps/cli/_core.py
  -> Ink/Web cockpit -> argus_skill/webapi/manager_bridge.py
     或 argus_skill/daemon/life_worker.py
  -> argus_skill/life/supervisor/_core.py  # backlog / budget / L4 planner
  -> argus_skill/apps/_runtime.py (_SkillLoopRunner.execute(...))
  -> argus_skill/loop.py                   # Skill-library paths -> engineer -> reviewer
  -> argus_skill/engineer/runner.py        # L1 round loop
  -> argus_skill/reviewer/_core.py      # L2 structured verdict
```

## Agent 层级

| 层 | 角色 | 主要文件 | 改什么时看这里 |
| --- | --- | --- | --- |
| L0 | CLI / daemon / cockpit | `argus_skill/apps/cli/_core.py`, `argus_skill/webapi/`, `frontend/tui/`, `argus_skill/daemon/life_worker.py`, `argus_skill/apps/_watch.py` | 命令行参数、Ink/Web cockpit、daemon 启停、`--status`、`--follow`、Telegram/事件展示 |
| Manager | 前门 + stage 权威 | `argus_skill/manager/_core.py`, `argus_skill/manager/front_door.py`, `argus_skill/manager/dispatch.py`, `argus_skill/webapi/manager_bridge.py` | 操作员自由文本的 chat-vs-task 分流（模型判断，非关键词）、vertical 选择、pipeline stage 转移的**唯一语义权威**（其余角色只能建议；Supervisor 仅可机械撤销 unfinished-DAG 的提前推进）。不在 L1/L2/L4 的编号序列里——它跨越整条流水线，不是流水线上的一站；有自己独立的 backend/model 配置（`ARGUS_SKILL_MANAGER_BACKEND`/`_MODEL`），在 `/roles` 和 cockpit 面板里与其余三个角色平级展示 |
| L1 | Engineer | `argus_skill/loop.py`, `argus_skill/engineer/runner.py` | 单轮执行 prompt、失败重试、session 续接、进度 watchdog |
| L2 | Reviewer | `argus_skill/reviewer/_core.py`, `argus_skill/reviewer/_parsing.py` | 独立检查每个正常 mission round，以命名行给出 done/continue/blocked/replan_requested，并承担论文任务的 peer-review gate；JSON 只作旧会话解析兼容，不再约束模型输出 |
| L4 | Planner | `argus_skill/planner/planner.py`, `argus_skill/life/supervisor/_core.py` | 持续读取真实项目并用完整 Agent 工具调查、运行有界探针/测试、必要时编辑代码或规划 artifact，以维护 forward plan 和自动排新任务；也负责 EMNLP final gate 失败后的自动分流。历史的 L3 critic 逐轮打磨层已移除；Planner 不负责 mission 验收 |
| Skill | 横向能力复用 | `argus_skill/skills/store.py`, `argus_skill/skills/layered.py`, `argus_skill/skills/role_library.py`, `argus_skill/skills/role_memory.py` | 向 Agent 暴露 project / shared-vertical / shared-global 路径；四角色自行发现并选择性维护各自角色目录 |
| Stage | 通用状态机 + vertical checklist | `argus_skill/skills/stage_machine.py`, `argus_skill/verticals/*/stages.py`, `argus_skill/skills/checklist_store.py` | 通用 stage 状态转移/渲染在 `stage_machine`；stage 顺序、seed checklist 和领域渲染归各 vertical |

> **常见误解**：读到 L0/L1/L2/L4 这个编号，容易以为 argus 是"三层 agent"（Planner/Engineer/Reviewer，L3 critic 已退役）。实际常驻跑着的是**四个**角色——Manager/Planner/Engineer/Reviewer（`core/role_config.py`: `ROLES = ("manager", "planner", "engineer", "reviewer")`）；Manager 不占 L 编号只是因为它跨越整条流水线（前门 + stage 权威），不代表它级别更低。另外还有一个可选的 **Curator** 角色（`ARGUS_SKILL_CURATOR_*`），只在并行 subagent/团队模式下才跑，管 skill 池维护和团队排行榜蒸馏，不参与日常单任务流水线，因此不在上表中。README 和三份 pitch 文档（商业计划书/项目介绍/一页纸概览）历史上都只画了三个角色（未包含 Manager），已于 2026-07-07 全部修正为四个角色。

### 四角色 Prompt 的唯一源码

四个常驻角色的 Prompt 本体统一放在 `argus_skill/roles/prompts/`：

- `manager.py`：front door、vertical/domain、stage decision、SELF、维护等 Manager Prompt。
- `planner.py`：continuous、bounded DAG、schema repair 等 Planner Prompt。
- `engineer.py`：mission/continuation 和 live directive 等 Engineer Prompt。
- `reviewer.py`：完整 Reviewer static/delta Prompt 及其审查 block。
- `registry.py`：按 `role / operation / vertical / stage / scope` 解析 banner、
  checklist、completion gate 等 Prompt 参数。

`manager/`、`planner/`、`engineer/`、`reviewer/` 只保留运行、解析、状态副作用和
兼容薄代理；它们收集动态 fragment 后也必须调用 `roles/prompts/` 完成最终字符串
拼接，不得在 runtime 重新组合 Prompt 或出现第二份 Prompt 长字符串。Vertical 的动态内容仍由
`verticals/<name>/stages.py` 提供，通过 `roles/prompts/registry.py` 注入；stage
checklist seed 的数据源是各 `verticals/*/stages.py`；`skills/stage_machine.py`
只负责通用状态转移、active-vertical 解析和渲染。

`builtin_skills/*/argus-*-role.md` 是受保护的兼容/方法 playbook，不是第二套控制平面
Prompt。它们不得重新定义 Reviewer 是否运行、completion status、stage authority 或
等待协议；这些字段必须跟随 `roles/prompts/` 和 runtime 实现。

## 入口和运行面

- `pyproject.toml`: console script 是 `argus-skill = "argus_skill.__main__:main"`。
- `argus_skill/__main__.py`: 只 re-export `apps.cli.main`。
- `argus_skill/apps/cli/_core.py`: 所有顶层 CLI flag 都在这里注册。这里没有 subcommand 模型，`--daemon`、`--status`、`--watch`、`--follow`、`--continuous`、`--objective`、`--bounded`、skill admin 都是 top-level flag。
  - **入口硬门禁（`_lifetime_entry_error`）**：默认进 cockpit / 启动 daemon 时只要求至少有一个受信任的 special prompt（`life/special_prompts.py`）；允许空 objective 等待首条真实任务。首条 TEAM task 由 Manager 判断 `STANDING` / `BOUNDED` 并生成 execution objective，STANDING objective 原子持久化到 `continuous.json`。没有机器规则仍 `exit 2`。只读 / admin flag（`--status`、`--watch`、`--skill-stats`…）不受门禁限制。
  - **默认 lifetime**：chat/simple 请求在前门直接处理；其余 TEAM task 默认 `STANDING`（7×24），只有 Manager 明确判断有自然一次性终点时才 `BOUNDED`。`--bounded` 仍可作为直接 daemon 启动的 operator override。
- `argus_skill/webapi/manager_bridge.py`: Ink/Web cockpit 的统一 Manager 接口；`manager/front_door.py` 管分类与 handoff，`manager/dispatch.py` 管 lifetime 与持久化入队。Python line REPL 已删除。
- Manager front-door 的一次模型调用同时输出 `CONFIG` / `CONTROL` / `ROUTE` 三个结构化轴。`CONTROL: NO_DISPATCH` 是 Manager 对 operator 明确“只读 / 不派任务 / 不启动 daemon”约束的权威裁决：bridge 强制走 SELF，inline 回复失败也 fail-closed，不得入 backlog。harness 不扫 operator prose 关键词来改判。SELF 回合用 read-only sandbox。session 明确区分两根目录：持久化 `workdir` 是四个角色唯一的项目执行目录；project state root 只保存 backlog/events/budget/skills 等内部状态。`launch_cwd` 仅记录 UI 从哪里打开，不能被运行时另行猜成 workspace。旧 session 没有 `workdir` 时继续使用其旧 `cwd`，禁止升级时自动切到 `launch_cwd`。恢复 session 不得重绑 workdir；切换只能在 daemon 停止且 Manager 空闲时完成。daemon 全生命周期持有 canonical-workdir lease，同一主机上任何 session/state root 都不能并发写同一目录。
- Operator 永远只与 Manager 交互。活跃 mission 的方向调整由 front-door Manager 生成专业 `STEER_DIRECTIVE` 后写入团队 inbox，禁止把 operator 原话直接透传成 Engineer 微操。Front-door classify 使用轻量模型/low；真正的 Manager SELF 回复继承当前最强 Manager 模型并使用 xhigh。Web/TUI 通过 SSE 流式显示 classify、steer、assistant block 和 5s 静默 heartbeat。SELF 默认 120s hard-idle fail-visible，不在超时后再追加一次长等待。
- Manager 判定为 `BOUNDED` 的 TEAM 请求不会直接入队成一个巨型 mission：先经过紧凑的 bounded-DAG Planner，原子写入带真实 `key/deps/plan_id` 的 backlog 节点；节点数量和任务大小由 Planner 判断，harness 不按数量、artifact、context、文本长度或关键词阶段做硬限制。普通 bounded-DAG 节点默认最多 3 个 Engineer→Reviewer round（内部兼容 knob `ARGUS_SKILL_BOUNDED_DAG_NODE_MAX_ROUNDS` clamp 为 2–8；progressive experiment matrix 例外），并跳过二次规划；不得在节点内重新写计划、初始化 Git/worktree、commit 或无必要地拉 subagent。`STANDING` 请求仍走 L4 continuous Planner。
- `argus_skill/life/router.py`: operator 自由文本的 chat-vs-task 路由。**不再用关键词/正则分类**（历史的 `is_conversational` 用 60 字符上限 + 中英文正则猜“这是闲聊吗”，harness 比 agent 聪明）。现在 `classify_is_conversational(text, *, run_exec)` 做一次低 reasoning 的模型调用，只有模型精确回答 `CHAT` 才返回 True，其余（TASK / 模糊 / 空 / 非零退出 / 异常）一律按 task 走完整 pipeline——bias 向 task，宁可多跑也不误吞任务。只有 operator 通过 Manager front-door 发送的自由文本才会被分类；planner / backlog / daemon 的任务都不分类，否则就是 harness 二次猜 planner。
- `argus_skill/daemon/life_worker.py`: detached daemon 版本的同一套逻辑。这里管 `continuous.json` 热加载、pid lock、daemon status、预算环境变量，以及 Reviewer 通过的私有 self-maintenance canary/rollback handoff。普通 checkout 更新统一由 TUI/WebAPI 在启动时识别并调度 source-owned daemon 升级；daemon 不再轮询当前 checkout 自重启。
  - `--resume-continuous` 只采用与 Manager handoff identity（objective hash +
    vertical + lineage generation）匹配的持久化 campaign；升级/崩溃恢复不得重新调用
    Manager。缺失或不匹配 identity 的 legacy/raw objective 仍须走真实 Manager divide。
  - 每个 daemon 的 Manager 还维护该 daemon 自身：事件实时写入有界结构化观测，故障触发
    审计、平时按 `ARGUS_SKILL_SELF_MAINTENANCE_AUDIT_SECONDS` 轻量审计。只有绑定真实
    evidence id 的具体框架问题才能派修复；禁止猜测式重构。Engineer/Reviewer 在该 daemon
    私有 framework worktree 内隔离执行，Reviewer 通过后本 daemon 在干净 mission 边界
    blue/green 灰度，失败回滚旧 source；灰度成功即成为该 daemon 的持久本地 source，
    不依赖 GitHub 账号或仓库权限。**发布(推分支/开 PR)需 operator 显式批准**：灰度成功后
    daemon 停在 `local_active`，把 `publication_status` 置为 `awaiting_approval` 并记下
    `awaiting_commit`，emit `manager.self_maintenance.publication_awaiting_approval`；
    operator 用 `argus-skill --list-pending-publications` 查看、
    `argus-skill --approve-publication <commit>` 批准。批准**绑定单个 commit、单次消费、7 天过期**——
    不是「允许发布」的总开关，否则批的就是下一轮它自己写的东西。批准后仍永不自动 merge/main。
    无 push 权限时安静保持 local-active，不把发布失败伪装成修复失败。PR 被拒也不回滚已验证的
    本地修复。其他 daemon 只把可验证的人工
    合并 `origin/main` 当作可选采用证据，各自 Manager 决定采用或延后并本地灰度。隔离
    能力缺失时 fail closed，不退化为 yolo。
    支持的 Linux host 必须预装 `bubblewrap`（Debian/Ubuntu: `apt install bubblewrap`）；
    daemon 启动时会做真实隔离 probe，失败只禁用自维护，不影响科研 mission。
- `argus_skill/life/memory.py`: 磁盘状态。global root 默认 `~/.argus-skill/`，project state 默认 `~/.argus-skill/projects/<fingerprint>/`。`events.jsonl` 是唯一历史事实源；`EventJournal` 只把当前 typed events 投影成短 history entry，不存在独立 `journal.jsonl`、`journal.entry` 或旧版迁移读取。注入 mission 前的 “memory context” prelude(`render_prelude`)走**纯 recency**：surface 最近 N 条项目事件历史，**不再用关键词 Jaccard 给“相关性”打分**——“哪段过往工作相关”是 agent 读这段(标了 non-authoritative 的)advisory 后自己判断的,不是 harness 用词面重叠去猜。

常见状态文件：

```text
~/.argus-skill/identity.md
~/.argus-skill/skills/
~/.argus-skill/projects/<fingerprint>/backlog.jsonl
~/.argus-skill/projects/<fingerprint>/events.jsonl
~/.argus-skill/projects/<fingerprint>/continuous.json
```

## 单任务 SkillLoop

`argus_skill/loop.py` 是单个 mission 的核心胶水。

关键对象：

- `SkillLoopConfig`: engineer/reviewer model、max rounds、四角色选择性 Skill 维护、runner flags、`paper_mission`。
- `SkillLoop.run(...)`: 主流程。
- `_build_engineer_prompt(..., paper_mission)`: 拼 L1 engineer prompt。长 horizon 论文 contract 仅在 `paper_mission=True` 时注入。
- 论文任务的识别**不再用关键词猜 objective 文本**，改由已解析 vertical 的结构化 completion gate 决定：只有 `completion_gate == "full_paper"` 才会把 `SkillLoopConfig.paper_mission` 置 True；缺失/损坏/未决状态一律按 False 处理。`full_emnlp` 只在旧持久化数据迁移中兼容。

主流程：

```text
SkillStore.library_roots() -> Agent 自行搜索/读取 Markdown
task + library paths -> SupervisedEngineer.run(...)
  round k: engineer -> Reviewer.evaluate(...)
  outcome -> 角色可直接维护 project Skill/wiki
  continue -> next_action 注入下一轮
  blocked/max_rounds -> 返回 outcome
```

改 prompt 时注意：

- 普通任务的 L1 prompt 在 `SkillLoop._build_engineer_prompt`。
- L1 prompt 现在保持轻量：当前任务、原始用户目标、Skill-library 路径、Reviewer next_action 和 turn discipline。Agent 按需读 Skill 正文；Harness 不复制正文进 prompt。
- `objective_for_skill` 只保留为干净目标标签和事件兼容字段，不参与 Skill 匹配或历史写入。

## Engineer / Reviewer

L1 engineer round loop 在 `argus_skill/engineer/runner.py`。

这里管：

- 每轮调用 backend runner。
- backend failure / auth failure / context poisoned / effective progress timeout。
- Engineer 和 Reviewer 每轮都使用全新 provider session，不跨轮 resume。
- **共享 `CHECKPOINT.md` 直接编辑接力**（见下）。
- **Background-subagent advisory + cadence wait**（见下）。
- `round.main.completed`、`round.review.completed` 等事件。

### Shared CHECKPOINT.md（上下文管理 / 反 amnesia loop）

Engineer 和 Reviewer 不再继承上一轮 raw transcript。每个角色每轮都是 fresh session，
跨轮状态通过项目根目录的一份普通 `CHECKPOINT.md` 传递。

实现（`argus_skill/engineer/checkpoint.py` + `runner.py` + `argus_skill/reviewer/_core.py`）：

- Engineer 先读取上一位 Reviewer 留下的文件，执行工作后直接修改同一个文件。
- Reviewer 紧接着读取 Engineer 修改后的文件与真实 artifacts/log，直接纠正、删除、补充。
- Reviewer 是每轮最后编辑者；下一轮 fresh Engineer 从它留下的版本继续。
- checkpoint 是当前状态便签，不是追加日志。没有 patch、commit、revision、JSON schema、
  机械压缩或硬大小限制；Agent 使用普通文件工具原地维护。
- Reviewer 的结构化 verdict 不再要求输出 `checkpoint` JSON。
- **一一映射契约**：`CHECKPOINT.md` 只存长期状态、证据引用和开放问题；Reviewer
  `reason` 只存 verdict 理由，`next_action` 只存下一轮指令。旧研究 schema 中的
  `planner_report` / `plan_signal` 仅作兼容，不是当前 reviewer 输出协议。
- `handoffs/latest.json` v2 只引用最新 round handoff 和 mission 文件；round
  handoff 再以 path 引用 CHECKPOINT，不复制 Engineer summary
  或 CHECKPOINT 正文。event/journal/wiki 只做同名字段投影或路径引用，不要求
  agent 重写第二份摘要。
- 每个正常 Engineer round 后都调用独立 Reviewer。`round_self_review.py` 目前只保留
  历史模块名并做进展 bookkeeping，不提供自审完成旁路。
- `ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS` 默认 0，不用绝对墙钟时间截断正常工作。
- 测试：`tests/test_checkpoint_loop.py`、`tests/test_session_resume.py`。

### Replan：Reviewer 如何推翻剩余计划

- Reviewer 可以直接返回 `status=replan_requested`。这**不是** done/failed/blocked，
  不触发 Manager stage transition；当前 item 回到 pending，随后仍走现有 Planner
  rate/budget gate。实现见 `engineer/round_settlement.py` 与
  `life/supervisor/_core.py`。
- Backlog row 持久化 `plan_id` / `plan_version` / `node_key` / `context_refs`。
  替换在一个 backlog 文件锁内 compare-and-swap：done 永不改写，旧 active nodes
  进入不可复活的 `superseded`，新 DAG 一次落盘；Planner/校验/冲突/写盘失败都保留
  旧计划。stage 推进由 `_apply_dynamic_plan_stage_guard` 守住未完成的后继节点。
- `context_refs` 只注入路径、用途与可选 hash，正文由 Engineer 按需读取。
  不新增关键词 relevance scorer。

> **2026-07 更正。** 本节此前描述了一套 `plan_signal: continue|reconsider` +
> `ARGUS_SKILL_DYNAMIC_PLAN_MODE=off|shadow|active` + 连续确认轮次的机制。
> 那套实现**在代码里并不存在**：`dynamic_plan_mode` / `dynamic_plan_confirm_rounds`
> 只被读进配置就再无人消费，`plan_reconsider_streak` 从不读写，`life.plan.signal`
> 事件类型定义了却从不发射。这些惰性符号已删除，本节改为描述真实存在的路径。
> 实测背景：一个长期项目累计 319 次节点级 `done`、仅 1 次 replan，296 次 planner
> 判决全是 `tasks_scheduled`——负结果没有被回灌成重规划。若要重建自动 replan 升级，
> 请**先实现再写文档**，并带上能证伪的测试。

### Live credential guard

- `core/secret_guard.py` 在所有 `JsonlEventSink` / Agent CLI 持久化与下游事件前做
  领域无关凭据脱敏，并在每个 Engineer 回合后、Reviewer 读取前清理本轮新写的小型
  文本 artifact。
- 脱敏不裁决科研质量；若改写了 artifact，会通过 `round.secret_redacted` 告知
  Reviewer 重建相关 hash/provenance。扫描错误或大文本未覆盖也会显式阻止无条件认证。
- 新 Engineer 通过最终回复最后一个非空行的精确 JSON
  `{"wait_for":"subagent|external_work","wait_id":"..."}` 请求等待；进入事件、
  usage、Reviewer prompt 的 Engineer 文本副本必须已脱敏。
- Engineer runtime 不再从 shell command、Codex 私有 session JSONL 或项目文件 mtime
  猜长任务/有效进度/重复失败。每个 provider turn 使用独立 process group；provider
  退出后只清理仍留在该精确 PGID 的无主后代，durable subagent 使用自己独立的
  process group。运行时 watchdog 只看 provider stream/process hard-idle 和显式 stop；
  科研推进与 replan 由 Reviewer/Planner 的结构化判断负责。

### Background-subagent cadence wait（别空转盯长实验）

背景：mission 用 subagent 工具 `--mode supervised` 起一个长跑（如 veRL GRPO
训练）后，那个 job 已经有自己**独立的 supervisor** 每隔 `monitor_interval` 查健康、
崩了能 early-stop、终态会往 inbox 发报告。但 L1 engineer 往往每一轮都去重新轮询同
一个健康 run（实测 RL pilot 出现过几百轮只在重读 `status.json` + 写 `MONITOR_*.md`），
被长程 GPU 实验阻塞，而不是去推进不依赖它的独立工作。

实现（`argus_skill/engineer/external_work.py` + `round_waits.py` + `runner.py`）：

- `external_work.py` 统一读取 `.argus_subagents` 和其他 external-work registry，把 job
  分类为 waitable 或需关注；`background_subagents.py` 现在只负责折算 subagent
  supervisor token 成本。
- **Agent 主导的 cadence 等待**（"只按 supervisor 节奏复查"）：若 engineer 在
  最终回复最后一行写入精确 JSON wait request，runner 检测到且命中一个
  self-watched in-flight job 时，**跳过昂贵的 reviewer 轮**，按该 job 的
  supervisor 节奏（`monitor_interval`，clamp 到 30–900s）休眠，job 到终态会提前唤醒。
  是 **agent 显式选择**等待，不是 harness 替它决定。目标命中不到自看护 job 时被
  忽略（退回正常 reviewed 轮），stale/误发不会挂住循环。
- 开关：`SupervisedConfig.background_subagent_advisory`（env
  `ARGUS_SKILL_BG_SUBAGENT_ADVISORY`，默认 on，0 关闭）。
- 测试：`tests/test_background_subagents.py`、`tests/test_runner_background_subagents.py`。

L2 reviewer 在 `argus_skill/reviewer/_core.py`。

这里管：

- reviewer prompt。
- Reviewer 普通回复末尾的命名 verdict 行（`STATUS` / `REASON` / `NEXT_ACTION` 等）；不再有 provider output schema。
- `parse_decision_text` / JSON verdict。
- 近完成论文任务按结构化 stage/scope 注入一份**精简** peer-review contract；不再每轮塞入完整 `academic-paper-peer-review-benchmark.md`。
- Reviewer role、handoff、project-venv、wiki-curator 都使用短契约；长源 Skill 只提供路径并由 Reviewer 按需读取，避免重复注入。

> **不再有 harness 关键词改判，也不再从 prose 猜 scope。** 历史上 `reviewer.py` 有个
> `_coerce_decision_against_main_summary`，会用关键词正则扫 engineer 的 summary，
> 把 reviewer 的 `done` 强行改成 `continue`（harness 比 agent 聪明，违背设计哲学）。
> 该函数连同它的 `GENERIC_MAIN_PATTERNS` / `CONCRETE_EXECUTION_PATTERNS` 等正则常量
> 已全部删除。需要“别在没有执行证据时判 done”的约束，现在写进 reviewer prompt
> 由 L2 自己判断，而不是 harness 事后覆盖裁决。`is_final_submission` 现在**只认结构化
> scope**（`scope == final_submission`，归一化时 `-`→`_`),删掉了过去 `"scope: final_submission" in objective` 这类 prose 兜底——scope 由 planner 以 backlog tag 形式一路透传到 reviewer。

如果 reviewer 老是误判：

- 先看 `Reviewer._build_prompt` 的精简固定契约和当前 stage checklist。
- 再看对应长源 skill 是否仍有某条真正缺失的规则；不要整份重新注入。
- 最后才改 schema；schema 改动会影响 tests 和所有 verdict parser。

## 外层 LifeSupervisor

`argus_skill/life/supervisor/_core.py` 是长期 harness 的大脑。它不是单任务 runner，而是“一个任务接一个任务”的调度器。

它负责：

- 从 `backlog.jsonl` claim `pending -> running`。
- 注入 memory prelude，但保持原始 objective 不被污染。
- 调用 runner 的 `execute(...)`。
- 成本统计和 budget gate。
- 任务完成后写 canonical `events.jsonl`。
- backlog 空时，L4 planner 自动生成下一批任务（历史的 L3 critic 逐轮打磨层已移除）。
- 论文类 continuous objective 在 planner 误报 `project_done` 时，强制改派一个 `final_submission` 认证任务，由 L2 reviewer 出整链裁决，而不是相信 planner 的早停。

重点函数：

- `LifeSupervisor.run()`: 主循环。
- `LifeSupervisor.tick()`: 处理一个 backlog item。
- `_plan_next_work(...)`: L4 planner，continuous mode 下 backlog 空了就调用；实现拆在 `life/supervisor/_planning_cycle*.py`，论文 objective 的 `final_submission` 改派在 completion phase。
- `LifeSupervisorConfig.paper_mission` / `full_paper_gate` / `open_ended`: 显式信号（前两个默认 False，只有 Manager 已解析出 `completion_gate == "full_paper"` 的 vertical 才开启）。`paper_mission` 决定 planner 给 bounded item 的论文/通用指导语；`full_paper_gate` 决定 `project_done` 前是否必须拿到一次 reviewer 认证的 full-pipeline 通过；`open_ended` 决定 planner 认证 `project_done` 后是“硬停”还是“继续生成新工作”。`open_ended` dataclass 默认 False，但 daemon/cockpit 入口默认置 True（除非 `--bounded`），并随 `LifeWorkerConfig.continuous_open_ended` 做 blue/green handoff 序列化。
- `_journal_has_full_paper_gate_success(...)`: 从 EventJournal 投影中查是否已有一次被 reviewer 认证（`final_submission_certified=True`）且 signature 匹配的 full-pipeline 通过记录。
- 标量：`PLANNER_SCOPE_FINAL_SUBMISSION = "final_submission"`，`FULL_PAPER_GATE_DESCRIPTION` 描述 L2 Reviewer 的 full pipeline checklist，不是 shell validator。

> 注意：历史上的 `_EMNLP_*_CODES` issue-code 分组、`_select_emnlp_finalization_repair_task`、
> `_automatic_emnlp_finalization_task_for_current_gate`、`_build_emnlp_finalization_objective`、
> `_planner_tasks_need_emnlp_finalization_override` 这套“按 issue code 选 repair lane”的确定性
> 分流**已经移除**。现在 supervisor 不读机器 validator 结果来决定派活，
> 完成与否只由 L2 reviewer 对 full-pipeline checklist 的 `final_submission` 认证决定。改完成判定
> 逻辑看上面这几个函数，不要再去找 issue-code 分组。

## Skill 系统

skill 是 markdown 文件，带 YAML-like frontmatter。

关键文件：

- `argus_skill/skills/store.py`: path-only Markdown library；只枚举路径和原子写入 Agent 明确指定的语义路径，不解析、匹配或生成身份。
- `argus_skill/skills/role_library.py`: 把 library roots 和 agent-native discovery 短契约交给角色。
- `argus_skill/skills/loop_skill_library.py`: mission 开始时准备 Engineer/Reviewer 可见的路径，不选择或复制正文。
- `argus_skill/skills/layered.py`: 按顺序暴露 project、active shared-vertical、shared-global roots。
- `argus_skill/manager/skill_tidy.py`: 成功 mission 边界的 Manager placement；shared-global 对所有项目可见，shared-vertical 只对同 vertical 可见。
- 不设 Skill 文本质量门、candidate/provisional、matcher 分数或 confirm 晋升状态。角色在项目层直接创建、更新或归档语义路径。
- `argus_skill/skills/builtins.py`: packaged built-in Skill seed/export。
- `argus_skill/builtin_skills/*.md`: 内置 skill 源文件。
- `argus_skill/builtin_skills/domains/**`: domain skill 包。
- **存储边界**：Git 只保存人工维护的 built-in Skill source；初始化时把它们 seed
  到 runtime。Agent 新建、更新、共享和归档的 Skill 永远只写
  `ARGUS_SKILL_HOME` 下的 project/shared runtime 层，不得反向写回或提交到 Git。
- **`builtin_skills/` 只放跨 vertical 的通用 Skill。** 它会被 seed 进每一个
  runtime 层和项目 workspace；某个 vertical 独有的 playbook 一律放
  `verticals/<name>/skills/<role>/<file>.md`，不要在 `builtin_skills/` 留
  pointer stub，避免无关 Agent 搜索到死文档。需要让子 vertical 复用父层方法论时用
  `builtins.py` 的 `_VERTICAL_SKILL_INHERITANCE`，不要复制文件。
  `tests/skills/test_builtins_seeding.py::test_vertical_owned_skills_are_not_also_flat_builtins`
  守住这条。
- 四角色的自进化 Skill 分别写入项目层 `manager/`、`planner/`、`engineer/`、`reviewer/`；这些是可持续增长的方法池，不是身份说明，也不设机械数量上限。角色从给定 library roots 自行搜索和读取，Harness 不做 matcher。共同的路径解析和简短写回契约在 `skills/role_memory.py`，不要复制四份提示。
- Project wiki 是四个常驻角色共享、可直接读写的声明性知识层，保存概念、结构、机制、
  原理、事实、假设、关系与矛盾；例如 Transformer 的结构或 RL 的原理。Skill 保存
  “如何做”的流程，events/CHECKPOINT 保存历史与当前状态。禁止把 round/handoff 再抄进
  wiki，也不再有结构化 wiki operation 通道。Reviewer 在真实验收时负责基于来源和
  artifact 校正知识页；其余角色也可直接维护。

初始化时：

- `GlobalMemory.init()` 会把 `argus_skill/builtin_skills` seed 到 `~/.argus-skill/skills/`。
- 默认不覆盖用户已经编辑过的 skill。
- 默认 seeding 不覆盖已有文件；语义退休由 Agent 明确执行，Harness 不从正文、摘要或 digest 推断应删除哪个 Skill。
- `argus-skill --export-builtin-skills [DIR]` 可以复制内置 skill 到项目目录，默认 `./argus_builtin_skills`。目标项目尚无 Manager 持久化的 vertical 时只导出公共 skill，不回退到 research；已有 vertical 时再叠加该 vertical 的 skill，并清理其他 vertical 未修改的旧 seed（用户改过的文件保留）。

改内置 skill 时：

- 修改 `argus_skill/builtin_skills/*.md` 是源码。
- 用户本地 `~/.argus-skill/skills/*.md` 不会自动覆盖，除非显式 export/overwrite。
- 论文任务常引用 `argus_builtin_skills/<name>.md`，这是导出后的项目内副本路径；源码仍在 package 内。

## EMNLP 论文 pipeline 总览

这个 pipeline 是一组状态契约，不是单个脚本。目标项目里一般会逐步产生：

```text
research/
  PIPELINE_STATE.json
  LITERATURE_GROUNDING.json
  IDEA_PROVENANCE.json
  CODE_REUSE_PLAN.json
  NARRATIVE_REPORT.md
experiments/
  BENCHMARK_PROVENANCE.json
  BENCHMARK_PROVENANCE.md
  **/manifest.json
  **/status.json
  **/progress.jsonl
  **/raw scored rows / logs / verifier outputs
paper/
  main.tex
  main.pdf
  main.log
  PAGE_BUDGET.md
  PAPER_DRAFT_REPORT.json
  RESULTS_REPORT.md
  ARTIFACT_MANIFEST.json
  ARTIFACT_FRESHNESS.json
  VALIDATION_PRIORITY_POLICY.json
  FIGURE_TABLE_STYLE_GUIDE.json
  ACADEMIC_LANGUAGE_REVIEW.json
  PAPER_INFRASTRUCTURE_REVIEW.json
  LAYOUT_REVIEW.json
  figures/IMAGE2_FIGURES.json
  figures/FIGURE_PROVENANCE.json
  style_ref/
```

主要 stage 和 ownership（下表第三列的 `validate-*` 是历史 CLI 名；当前由 L2 Reviewer 按 research vertical checklist 直接对照 artifact 裁决）：

| Stage | 主要 skill | 主要 artifact / 检查项 |
| --- | --- | --- |
| 选题/grounding | `research-brief-to-experiment-plan.md`, `auto-research-pipeline.md` | `validate-grounding`, `validate-idea-provenance`, `validate-code-reuse` |
| 实验/benchmark | `research-experiment-runner.md` | public evidence provenance, raw `experiments/**` artifacts |
| 结果到 claim | `research-results-analysis-and-figures.md`, `claims-evidence-audit.md` | Reviewer 对照 raw results、tables/figures 和正文直接裁决 |
| 初稿/LaTeX | `emnlp-paper-drafting.md` | `validate-paper-contract`, `validate-paper-format`, `main.tex`, `main.pdf` |
| 格式预检 | `emnlp-format-preflight.md` | `validate-research-md-format`, `FORMAT_PREFLIGHT.md` |
| 图表/IMAGE2 | `research-results-analysis-and-figures.md`, `paper-illustration-image2.md`, `paper-framework-figure-studio-pro.md` | `validate-image2-figures`, `validate-figure-table-style` |
| 学术语言 | `emnlp-academic-language-review.md` | `academic_language_review --write`, `validate-academic-language-review` |
| 基建泄漏 | `emnlp-paper-infrastructure-review.md` | `paper_infrastructure_review --write`, `validate-paper-infrastructure-review` |
| 视觉布局 | `paper-review-revision-loop.md`, `emnlp-format-preflight.md` | `paper_layout_review --write`, `validate-layout-review` |
| 最终提交 | `research-submission-assurance-gate.md`（兼容文件名，skill 名为 Final Paper Review） | Reviewer 直接阅读当前论文、PDF 和 claim-critical sources；不要求 assurance packet |

## 论文检查（vertical-owned stage checklists）

质量校验由 L2 Reviewer 读取 active vertical 在 `verticals/*/stages.py` 定义的 checklist，并通过 `skills/stage_machine.py` 的通用渲染入口对照真实 artifact 裁决。仓库不再提供独立的 pipeline validator/policy 模块。

### 没有 gate router —— 只有 agent 可调用的工具

历史上有一个 `skills/automated_gates.py`：一张 stage→gates 路由表，替 agent 决定
「哪个 gate 在哪个 stage 跑、算 structural(阻断) 还是 advisory(建议)」。它已删除
（2026-07，连同 `--status` 里的 gate 快照渲染与配套测试，约 -2.3k 行）。

删除依据是实测而非口味：全仓只有两个生产调用点，都在 `--status` 的展示函数里；
它**不进任何 agent prompt、不阻断任何 round、不参与完成判定**。而「哪一项该在此刻
被检查、失败算不算致命」本来就是 Reviewer 的判断——正是设计哲学禁止 harness 代劳的
那类判断。它自己的 docstring 也记录了前一版因硬编码科研质量阈值而被 review 否决。

**保留下来的是每一个 validator 本身**（`evidence_chain`、`anti_mediocrity`、
`rl_training_health`/`_plots`、`paper_structural_minimums`、`method_differentiation`、
`experiment_audit_gate`、`reviewer_simulation`、`run_evidence_health`）。它们都带
`python -m ...` CLI 入口，由 Engineer/Reviewer 按需自行调用，是**给 agent 的工具**，
不是替 agent 的裁决。新增这类检查时：写成独立可调用工具 + 在 skill 里说明何时用，
**不要**再造一张路由表。

> 依赖分析注意：本仓有**两条依赖图**——Python import，以及 skill markdown 里的
> `python -m argus_skill...` CLI 调用。只看前者会把活模块误判成死代码。

```bash
# 完成判定由 reviewer 走 stage checklist：
python -c "from argus_skill.skills.stage_machine import format_full_pipeline_checklist; print(format_full_pipeline_checklist(role='reviewer'))"
```

项目是否“EMNLP ready”由 L2 reviewer 的 `format_full_pipeline_checklist` 整链裁决决定
（真实实验、claim 支撑、paper contract、format、figure quality 和独立 review
都在 checklist 里），不是看某个 validator 的返回值，也不只是看 PDF
存不存在。

改 validator 时注意：

- `ContractIssue(code, path, message)` 的 `code` 仍是有用的诊断标识，重命名前先 grep 引用（tests、reviewer 文案）。
- full-pipeline 完成判定现在走 L2 Reviewer 对 active vertical checklist 的整链裁决，不再有 supervisor 里的 EMNLP issue-code 分组；新增检查项时改对应 `verticals/*/stages.py` 的 seed，而不是通用状态机或已删除的 issue-code lane。
- 新增 artifact 后，通常还要更新 manifest/freshness/validation priority 相关逻辑。
- 先改 narrow validator，再考虑是否需要在 checklist 里新增一项。

## Review 工具

这些是 paper pipeline 的模型/视觉 review 工具：

- `argus_skill/verticals/research/academic_language_review.py`
  - CLI: `python -m argus_skill.verticals.research.academic_language_review --project-root . --review-mode model --write`
  - 输出：`paper/ACADEMIC_LANGUAGE_REVIEW.json` 和 `.md`
  - 校验：`validate-academic-language-review`

- `argus_skill/verticals/research/paper_infrastructure_review.py`
  - CLI: `python -m argus_skill.verticals.research.paper_infrastructure_review --project-root . --review-mode model --write`
  - 输出：`paper/PAPER_INFRASTRUCTURE_REVIEW.json` 和 `.md`
  - 用来抓 manuscript prose 里的本地路径、device/cache、Argus/Codex route/config 泄漏。

- `argus_skill/verticals/research/paper_layout_review.py`
  - CLI: `python -m argus_skill.verticals.research.paper_layout_review --project-root . --review-mode vision --write`
  - 输出：`paper/LAYOUT_REVIEW.json`、`.md`、`paper/layout_review/pages/`
  - 用 PDF page snapshots 做视觉布局审核。

这些 review JSON 是生成证据。不要为了让 gate 绿而手改成 PASS；应该改 `main.tex` / PDF / evidence 后重跑工具。

## 科研绘图路由 / IMAGE2

Research vertical 的统一入口是
`verticals/research/skills/engineer/research-visualization-router.md`。图的 renderer
由 Engineer 根据科学语义、可编辑性、最终尺寸和真实 capability 选择；Reviewer
裁决质量。允许的路线包括数据脚本、Vega/ECharts/Recharts/HTML/React、FigureSpec、
Mermaid/Graphviz、Draw.io、PPT Master 和 image-2。paper-facing figure 可选写入
`paper/figures/FIGURE_PROVENANCE.json` 作为 renderer/source handoff，不能成为
完成 gate 或 Reviewer blocker。Reviewer 只看实际图片是否清晰、正确、协调且够好看；
轻微审美问题直接通过，不得反复返工。Harness 不用关键词替 agent 选工具或评价图片。

图像能力分两层：通用能力在 `argus_skill/tools/image_api.py`（模型路由加载、
HTTP/重试/脱敏/每日上限、生成、inspect、通用 vision review，不 import 科研
vertical）；论文图工作流在 `argus_skill/verticals/research/figure_tool.py`
（PAPER_FIGURE_PROMPT_TEMPLATE、paper-prompt 规划、context freeze、candidate
cache、metadata/provenance 同步），可以 import `tools.image_api` 里的通用
helper。

常用命令：

```bash
python -m argus_skill.verticals.research.figure_tool paper-prompt --out paper/figures/overview.prompt.txt --force
python -m argus_skill.tools.image_api generate --prompt-file paper/figures/overview.prompt.txt --out paper/figures/overview.png
python -m argus_skill.tools.image_api inspect --image paper/figures/overview.png
python -m argus_skill.verticals.research.figure_tool review --image paper/figures/overview.png --prompt-file paper/figures/overview.prompt.txt
python -m argus_skill.verticals.research.figure_tool sync-paper-metadata --project-root . --image paper/figures/overview.png --figure-id overview
```

image-2 只是 capability 可用且 router 认为合适时的一条路线（可选，非强制）；
实际使用时必须保留 prompt、sidecar、inspect、review、provenance、
accepted-raster hash 和 `IMAGE2_FIGURES.json` 之间的一致性，并自动同步统一
manifest —— 这些是反造假底线,不能删。`paper-prompt` 生成的 prompt 是推荐的
canonical prompt，但不是强制 marker gate：`sync-paper-metadata` 只要求真实
raster/prompt/review 的 hash 链一致，不再要求 prompt 文本里必须出现内置模板
的 marker 字符串，也不再有"必须先攒够 6 个 reviewed candidate 才算 reusable"
的硬性下限。没有 image API 时不得伪造
image-2 metadata，也不得仅因此阻塞整篇论文；应由 agent 选择语义等价、可审计的
确定性路线。任何本地 SVG/HTML/PPT 输出都必须以真实 renderer 名义登记，不能冒充
image-2。

## Planner 的 full-paper 完成判定

当 `LifeSupervisorConfig.full_paper_gate=True` 时，L4 planner 有额外保护：

- `full_paper_gate` 这个**显式 config flag**（不再从 objective 文本猜）决定：`project_done` 前必须有一次被 reviewer 认证的 full-pipeline gate 通过记录。只有 active vertical 的 completion gate 也是 `full_paper` 时才生效。
- `_journal_has_full_paper_gate_success(...)`: 在 EventJournal 投影里查当前 signature 的认证记录。
- `_plan_next_work(...)`: 若 planner 在尚未认证时就报 `project_done`，这里把它的裁决**替换**成一个 `scope=final_submission` 的 “Prove final submission readiness” 任务，交回 L2 reviewer 做整链认证。
- **scope 是结构化透传的，不再从 prose 里二次解析。** backlog item 的 `scope`（`final_submission` / `bounded` / 空）由 `_planner_scope_from_item(item)`（读 `item.tags`）算出，经 `MissionExecutor.execute(..., scope=...)` 一路传到 `_SkillLoopRunner` 和 reviewer。runner 不再从 objective prose 猜 `mission_scope`，reviewer 也只认结构化 scope(prose fallback 已删)。planner 去重里“`done` 的 final-submission 任务不挡新任务入队”这条豁免,也改走结构化的 `_item_is_final_submission(item)`(读 tag);`_legacy_final_submission_marker(text)` 只作为**老 backlog 迁移**兜底——给“tag 出现前持久化、只在 objective prose 里带 marker”的旧 item 用,保证 resume 的 daemon 不回归,新 item 一律带 tag。

所以：

- 改“项目什么时候算完成 / 还差认证时下一步派什么”，改 `life/supervisor/` 的 planning/lifecycle phase 和 `_journal_has_full_paper_gate_success`。
- 改“full-pipeline checklist 里某一项该不该判过”，改对应 `verticals/*/stages.py` 的 checklist seed；通用状态转移/渲染才改 `skills/stage_machine.py`。
- 改“agent 读到任务后应该怎么做”，改 `builtin_skills/*.md` 或 `SkillLoop._build_engineer_prompt`。

## Backend / runner

Backend 协议在 `argus_skill/core/ports.py`：

```text
RunnerBackend.run_exec(prompt, options, run_label, resume_thread_id=None) -> RunnerResult
```

实现：

- `argus_skill/adapters/agent_cli_backend/`: 真实 codex/claude/copilot/opencode/pi CLI 的稳定适配入口；内部按 admission、spawn、I/O、result/finalize 分层。
- `argus_skill/agent_cli/`: 对外保持 `agent_cli_runner` / `runner_backend` / `models` 三个底层 driver 表面；命令构造、进程控制、事件解析、prompt delivery、ACP 路由和恢复逻辑在私有模块中。`__init__` 不 eager import 子模块，因此 `import argus_skill.agent_cli.agent_cli_runner` 保持轻量。
- `argus_skill/adapters/memory_backend.py`: deterministic 测试/smoke。
- `_SkillLoopRunner` 在 `apps/_runtime.py` 里组装真实 backend，并按角色传给 Manager、Planner、Engineer、Reviewer 和可选 Curator。

常见 env：

```text
ARGUS_SKILL_LIFE_BACKEND=codex|memory
ARGUS_SKILL_RUNNER_BACKEND=codex|claude|copilot|opencode|pi
ARGUS_SKILL_RUNNER_BIN=/path/to/codex
ARGUS_SKILL_RUNNER_EXTRA_ARGS="..."
ARGUS_SKILL_SAFE_MODE=1
ARGUS_SKILL_SKILL_OPS=0|1
ARGUS_SKILL_MAX_ACTIVE_DAEMONS=64
```

USD 预算只读全局配置；项目没有独立预算文件。

## 事件和观测

事件 sink 协议在 `argus_skill/core/ports.py`。

常见事件：

- `life.mission.started`
- `loop.start`
- `skill.match.*`
- `scientist.start`
- `round.start`
- `round.main.completed`
- `round.review.started`
- `round.review.completed`
- `skill.outcome`
- `life.iteration.continued`
- `life.planner.start`
- `life.planner.verdict`
- `loop.done`

展示相关文件：

- `argus_skill/cli/event_format.py`
- `argus_skill/cli/render.py`
- `argus_skill/apps/_watch.py`
- `argus_skill/apps/cli/_core.py` 的 `--follow` helpers
- `argus_skill/life/telegram_bot.py`
- `argus_skill/core/progress_step.py`

### 实时步骤流（cockpit 看得到系统在干什么）

operator 一次 Manager 回合期间，cockpit 显示的是一条**追加式 step trail**，不是一行被
不断覆盖的状态。链路有三道闸，历史上每一道都单独把 Manager 的实时活动吞掉过：

1. `adapters/agent_cli_backend/_io_log.py` 的 `_PROGRESS_STREAM_MARKERS`
   决定哪些 provider stdout 行会被转发去做实时进度（磁盘 raw log 是另一条分支）。
   新增 provider 事件名要在这里登记，否则后面两层根本收不到。
2. `adapters/stream_progress.py` 把这些行解析成 `engineer.progress`。
   Copilot 现在用 `tool.execution_start` / `tool.execution_complete`
   报告工具活动（旧的 `tool.call` / `tool.result` 仅作兼容保留）。
   `_actor_is_visible()` 决定哪些 run label 属于 operator 可见角色——**Manager 的
   run label(`simple-*` / `chat-*` / `manager-*` / `router-*` …)在列**，
   内部协议噪音不在。
3. `core/progress_step.py` 的 `describe_progress_step(event) -> (label, detail)`
   把一个 progress 事件渲染成一行真实动作（跑的命令、调的工具、改的文件），
   并经 `core/secret_guard` 脱敏。**它不做“这步有没有用”的判断**，只如实呈现；
   不要退回旧的“inspecting project state”这类笼统措辞。

前端：`webapi/routes/manager.py` 的 SSE `phase` frame 现在带 `kind` / `detail`；
`frontend/core/src/phaseTrail.ts` 是纯 reducer（TUI 和 Web 共用），
`frontend/tui/src/components/ThinkingLine.tsx` 和 `frontend/web/src/components/ChatBox.tsx`
渲染 trail，回合结束后 TUI 把 trail 折进 scrollback（`ui.activity` 事件）。

### Prompt 改写（`/rewrite` · Ctrl+R · Web 的 Rewrite 按钮）

operator 的短 prompt 在派发前可以先让 Manager 重写成可执行 brief：
`manager/prompt_rewrite.py`（结构与 `manager/plan_mode.py` 对齐）+
`roles/prompts/manager.py:build_prompt_rewrite_prompt` +
`webapi/manager_bridge.py:manager_rewrite` + `POST /api/projects/{sid}/prompt/rewrite`。
**永远是预览**：结果回填到输入框由 operator 审阅/编辑后才发送，不入 backlog、不碰
mission；改写失败时保留 operator 原文。

契约不是"禁止 Manager 提指标"——那是 harness 替 agent 闭嘴。Manager **应该**用自己
的判断看这个任务缺什么（成功指标、阈值、baseline、范围边界、deadline、工具），并
**带上建议值主动问 operator**，让对方一句"可以"就能拍板。唯一的红线是**不许悄悄
决定**：凡是 operator 没表达过的东西，只能出现在 `questions` 里，不能混进
`rewritten`——operator 不该在任务跑起来之后才发现一条自己没同意的要求。

## 实验和论文证据

仓库不再内置 benchmark runner / archive helper；旧 `benchmarks/` 包和只服务它的 legacy mission/container 兼容层已删除。论文 pipeline 里的 `benchmark` stage 仍是研究流程概念，由 stage checklist / reviewer 对照项目内 artifact 裁决。

- `experiments/`: 本地实验输出。
- `paper/`: 这个仓库自己的 claim-to-evidence paper workspace，不等同于每个外部 research project 的 `paper/main.tex` pipeline，但命名会重叠。
- `paper/build_*_artifacts.py`: 从 repo-local evidence 生成 checked-in `paper/artifacts/*`。

## 测试入口

常用快速测试：

```bash
pytest tests/test_loop_smoke.py
pytest tests/skills/test_stage_checklists.py tests/skills/test_verticals.py
pytest tests/skills/test_paper_layout_review_snapshots.py tests/skills/test_paper_layout_review_venue.py
pytest tests/skills/test_academic_language_review_venue.py
pytest tests/test_paper_infrastructure_review.py
pytest tests/tools/test_image_api.py tests/skills/test_figure_tool.py
```

> 2026-07-07 核实：以上文件名已按当前 `tests/` 树校正（原来列的
> `test_architecture_docs_contract.py` 已整个移除、无替代，故删除该行；
> `test_paper_layout_review_prompt.py` / `test_academic_language_review.py`
> 已改名+拆分；`test_paper_infrastructure_review.py` 从 `tests/skills/` 挪到了
> `tests/` 顶层）。

全量：

```bash
pytest
```

只改文档通常不用全跑。改 `stage_machine.py` 或 vertical checklist 至少跑 stage/vertical/Manager/Reviewer 相关 tests。改 `life/supervisor/` 至少跑 life/daemon/planner 相关 tests。

## 修改时的层级规则

1. CLI / cockpit 行为改 `apps/cli/_core.py` / `webapi/` / `frontend/tui/` / `daemon/life_worker.py`；Manager handoff 与入队改 `manager/front_door.py` / `manager/dispatch.py`。
2. 单任务 agent prompt 改 `loop.py`。
3. L1 执行可靠性改 `engineer/runner.py`。
4. L2 验收标准改 `reviewer/_core.py` 和相关 role skill。
5. L4 调度策略改 `life/supervisor/_core.py` / `planner/planner.py`。
6. Skill 路径、分层和直接维护改 `skills/store.py` / `skills/layered.py` / `skills/role_library.py` / `skills/role_memory.py`。
7. Vertical 质量标准改对应 `verticals/*/stages.py` 的 checklist；通用状态转移和 checklist 渲染改 `skills/stage_machine.py`。
8. full-paper 项目何时算完成 / 还差认证时改派什么，改 `life/supervisor/` 的 planning/lifecycle phase 与 `_journal_has_full_paper_gate_success`。
9. Agent 读到 paper 任务后的操作手册改 `argus_skill/builtin_skills/*.md`。
10. 生成 evidence/review JSON 的工具改 `skills/*_review.py`、`tools/image_api.py`
    或 `verticals/research/figure_tool.py`，不要只改 validator 放宽。

## 常见坑

- 不要把 runtime prelude、daemon 路径、Codex route、capability vault、local cache/device 写进论文正文。
- 不要手改 review JSON、manifest 或 freshness 来制造 PASS。优先修源 artifact 后重跑生成器。
- 不要假设还存在 supervisor 里的 EMNLP issue-code 分组 / `_select_emnlp_finalization_repair_task`：它们已删除，完成判定走 L2 reviewer 的 full-pipeline checklist 认证。
- 不要只在 built-in skill 文案里改规则，却忘了 reviewer 的 stage checklist（底层 `validate_*` 函数）仍然会判红。
- 不要只让单个 stage 的 paper-contract 检查过就说 EMNLP ready；最终看 `format_full_pipeline_checklist` 的整链裁决。
- 不要在 full-scale evidence gate 红的时候继续 polish `paper/main.tex`，先补实验/benchmark/source matrix。
- 不要把 pilot、synthetic、same-family-only evidence 写成 full EMNLP-ready result。
- 不要在 user 的 `~/.argus-skill/skills` 里直接覆盖本地编辑，源码改 `argus_skill/builtin_skills`，需要时再 export。
