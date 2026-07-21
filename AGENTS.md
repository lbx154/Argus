# Argus Skill Agent 修改地图

这份说明给后续接手的 agent 看。目标不是替代代码阅读，而是让你先知道“哪里管哪里”，避免在 EMNLP 论文 pipeline 或 7x24 harness 里乱改错层。

## 一句话架构

`argus-skill` 是一个长期运行的 agent harness：外层 `LifeSupervisor` 管 backlog、预算、daemon、L4 planner（forward scheduling）；内层 `SkillLoop` 管单个任务的 skill 匹配、L1 engineer 执行、L2 reviewer 验收和选择性 Skill maintenance。任务内先写 project-layer Skill；成功 mission 边界由 Manager 判断 stay / shared-global / shared-vertical，默认把可迁移经验传播给其他项目。历史上独立的 L3 critic 逐轮打磨循环已经移除——验收完全交给 L2 reviewer。EMNLP 论文生成 pipeline 是 built-in skill + per-stage reviewer 检查（stage checklists，reviewer 对照 artifact 裁决）+ planner fallback 共同实现的，不是单独一个 `make_paper.py`。`pipeline_contracts.py` 现在只负责 manifest/freshness/validation-priority 这套 artifact 构建-修复工具，不再是质量 gate。

**Token-efficiency rule:** prompt 改动先看边际效果/token。禁止为了“更稳”重复注入同义
角色规则；优先用文件状态、按需 checklist 和一个权威短契约。必须保留关键证据/gate，
但新增长文案前先测 prompt 大小，并为常见路径设置回归预算。

**Checklist ownership:** vertical/framework 提供 seed；Planner 通过
`checklist_ops` 独占项目 checklist 写权限；Reviewer 只有 `checklist_feedback`；Engineer
不得用 harness overlay 增删 checklist。`research/CHECKLISTS.json` 必须带 vertical，和当前
项目 vertical 不一致时完全忽略。

**Stage-check integrity:** `STAGE_CHECKS` 的 shell 只做结构存在性检查。禁止内嵌
`grep/awk/sed/jq/cat/head/tail` 或 `python -c` 从内容关键词推断成功、分数或正确性；
这类证据必须交给可单测的结构化 Python validator 解析 CSV/JSON/JSONL。

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
  -> argus_skill/loop.py                   # matcher -> Scientist-on-miss -> engineer -> reviewer
  -> argus_skill/engineer/runner.py        # L1 round loop
  -> argus_skill/reviewer/_core.py      # L2 structured verdict
```

## Agent 层级

| 层 | 角色 | 主要文件 | 改什么时看这里 |
| --- | --- | --- | --- |
| L0 | CLI / daemon / cockpit | `argus_skill/apps/cli/_core.py`, `argus_skill/webapi/`, `frontend/tui/`, `argus_skill/daemon/life_worker.py`, `argus_skill/apps/_watch.py` | 命令行参数、Ink/Web cockpit、daemon 启停、`--status`、`--follow`、Telegram/事件展示 |
| Manager | 前门 + stage 权威 | `argus_skill/manager/_core.py`, `argus_skill/manager/front_door.py`, `argus_skill/manager/dispatch.py`, `argus_skill/webapi/manager_bridge.py` | 操作员自由文本的 chat-vs-task 分流（模型判断，非关键词）、vertical 选择、pipeline stage 转移的**唯一**权威（其余角色只能建议）。不在 L1/L2/L4 的编号序列里——它跨越整条流水线，不是流水线上的一站；有自己独立的 backend/model 配置（`ARGUS_SKILL_MANAGER_BACKEND`/`_MODEL`），在 `/roles` 和 cockpit 面板里与其余三个角色平级展示 |
| L1 | Engineer | `argus_skill/loop.py`, `argus_skill/engineer/runner.py` | 单轮执行 prompt、失败重试、session 续接、进度 watchdog |
| L2 | Reviewer | `argus_skill/reviewer/_core.py`, `argus_skill/reviewer/reviewer_schema.json` | done/continue/blocked 判断、reviewer JSON schema、论文任务的 peer-review gate |
| L4 | Planner | `argus_skill/planner/planner.py`, `argus_skill/life/supervisor/_core.py` | continuous mode 自动排新任务、EMNLP final gate 失败后的自动分流。历史的 L3 critic 逐轮打磨层已移除（见 `planner/planner.py` 顶部说明），验收只由 L2 reviewer 负责 |
| Skill | 横向能力复用 | `argus_skill/skills/store.py`, `argus_skill/skills/layered.py`, `argus_skill/manager/skill_tidy.py` | project / shared-vertical / shared-global 匹配，Engineer/Reviewer 选择性 maintenance，成功 mission 后 Manager 跨项目传播 |
| Contracts | 论文 artifact 工具 + 状态机 | `argus_skill/skills/pipeline_contracts.py`, `argus_skill/skills/pipeline_policy.py`, `argus_skill/skills/stage_checklists.py` | manifest/freshness/validation-priority 构建-修复（pipeline_contracts）；质量 gate 走 stage checklist（stage_checklists） |

> **常见误解**：读到 L0/L1/L2/L4 这个编号，容易以为 argus 是"三层 agent"（Planner/Engineer/Reviewer，L3 critic 已退役）。实际常驻跑着的是**四个**角色——Manager/Planner/Engineer/Reviewer（`cli/roles_status.py`: `ROLES = ("manager", "planner", "engineer", "reviewer")`）；Manager 不占 L 编号只是因为它跨越整条流水线（前门 + stage 权威），不代表它级别更低。另外还有一个可选的 **Curator** 角色（`ARGUS_SKILL_CURATOR_*`），只在并行 subagent/团队模式下才跑，管 skill 池维护和团队排行榜蒸馏，不参与日常单任务流水线，因此不在上表中。README 和三份 pitch 文档（商业计划书/项目介绍/一页纸概览）历史上都只画了三个角色（未包含 Manager），已于 2026-07-07 全部修正为四个角色。

## 入口和运行面

- `pyproject.toml`: console script 是 `argus-skill = "argus_skill.__main__:main"`。
- `argus_skill/__main__.py`: 只 re-export `apps.cli.main`。
- `argus_skill/apps/cli/_core.py`: 所有顶层 CLI flag 都在这里注册。这里没有 subcommand 模型，`--daemon`、`--status`、`--watch`、`--follow`、`--continuous`、`--objective`、`--bounded`、skill admin 都是 top-level flag。
  - **入口硬门禁（`_lifetime_entry_error`）**：默认进 cockpit / 启动 daemon 时只要求至少有一个受信任的 special prompt（`life/special_prompts.py`）；允许空 objective 等待首条真实任务。首条 TEAM task 由 Manager 判断 `STANDING` / `BOUNDED` 并生成 execution objective，STANDING objective 原子持久化到 `continuous.json`。没有机器规则仍 `exit 2`。只读 / admin flag（`--status`、`--watch`、`--skill-stats`…）不受门禁限制。
  - **默认 lifetime**：chat/simple 请求在前门直接处理；其余 TEAM task 默认 `STANDING`（7×24），只有 Manager 明确判断有自然一次性终点时才 `BOUNDED`。`--bounded` 仍可作为直接 daemon 启动的 operator override。
- `argus_skill/webapi/manager_bridge.py`: Ink/Web cockpit 的统一 Manager 接口；`manager/front_door.py` 管分类与 handoff，`manager/dispatch.py` 管 lifetime 与持久化入队。Python line REPL 已删除。
- Manager front-door 的一次模型调用同时输出 `CONFIG` / `CONTROL` / `ROUTE` 三个结构化轴。`CONTROL: NO_DISPATCH` 是 Manager 对 operator 明确“只读 / 不派任务 / 不启动 daemon”约束的权威裁决：bridge 强制走 SELF，inline 回复失败也 fail-closed，不得入 backlog。harness 不扫 operator prose 关键词来改判。SELF 回合用 read-only sandbox。session 明确区分两根目录：持久化 `workdir` 是四个角色唯一的项目执行目录；project state root 只保存 backlog/events/budget/skills 等内部状态。`launch_cwd` 仅记录 UI 从哪里打开，不能被运行时另行猜成 workspace。旧 session 没有 `workdir` 时继续使用其旧 `cwd`，禁止升级时自动切到 `launch_cwd`。恢复 session 不得重绑 workdir；切换只能在 daemon 停止且 Manager 空闲时完成。daemon 全生命周期持有 canonical-workdir lease，同一主机上任何 session/state root 都不能并发写同一目录。
- Operator 永远只与 Manager 交互。活跃 mission 的方向调整由 front-door Manager 生成专业 `STEER_DIRECTIVE` 后写入团队 inbox，禁止把 operator 原话直接透传成 Engineer 微操。Front-door classify 使用轻量模型/low；真正的 Manager SELF 回复继承当前最强 Manager 模型并使用 xhigh。Web/TUI 通过 SSE 流式显示 classify、steer、assistant block 和 5s 静默 heartbeat。SELF 默认 120s hard-idle fail-visible，不在超时后再追加一次长等待。
- Manager 判定为 `BOUNDED` 的 TEAM 请求不会直接入队成一个巨型 mission：先经过紧凑的 bounded-DAG Planner，原子写入带真实 `key/deps/plan_id` 的 backlog 节点；节点数量和任务大小由 Planner 判断，harness 不按数量、artifact、context、文本长度或关键词阶段做硬限制。每个节点强制 direct workflow、最多一次 Engineer→Reviewer round；不得在节点内重新写计划、初始化 Git/worktree、commit 或拉 subagent。`STANDING` 请求仍走 L4 continuous Planner。
- `argus_skill/life/router.py`: operator 自由文本的 chat-vs-task 路由。**不再用关键词/正则分类**（历史的 `is_conversational` 用 60 字符上限 + 中英文正则猜“这是闲聊吗”，harness 比 agent 聪明）。现在 `classify_is_conversational(text, *, run_exec)` 做一次低 reasoning 的模型调用，只有模型精确回答 `CHAT` 才返回 True，其余（TASK / 模糊 / 空 / 非零退出 / 异常）一律按 task 走完整 pipeline——bias 向 task，宁可多跑也不误吞任务。只有 operator 通过 Manager front-door 发送的自由文本才会被分类；planner / backlog / daemon 的任务都不分类，否则就是 harness 二次猜 planner。
- `argus_skill/daemon/life_worker.py`: detached daemon 版本的同一套逻辑。这里管 `continuous.json` 热加载、pid lock、daemon status、预算环境变量，以及 Reviewer 通过的私有 self-maintenance canary/rollback handoff。普通 checkout 更新统一由 TUI/WebAPI 在启动时识别并调度 source-owned daemon 升级；daemon 不再轮询当前 checkout 自重启。
  - `--resume-continuous` 只采用与 Manager handoff identity（objective hash +
    vertical + lineage generation）匹配的持久化 campaign；升级/崩溃恢复不得重新调用
    Manager。缺失或不匹配 identity 的 legacy/raw objective 仍须走真实 Manager divide。
  - 每个 daemon 的 Manager 还维护该 daemon 自身：事件实时写入有界结构化观测，故障触发
    审计、平时按 `ARGUS_SKILL_SELF_MAINTENANCE_AUDIT_SECONDS` 轻量审计。只有绑定真实
    evidence id 的具体框架问题才能派修复；禁止猜测式重构。Engineer/Reviewer 在该 daemon
    私有 framework worktree 内隔离执行，Reviewer 通过后本 daemon 在干净 mission 边界
    blue/green 灰度，失败回滚旧 source；灰度成功才以
    `lbx154 <lbxhaixing154@sjtu.edu.cn>` 推独立分支并自动开 PR，永不自动 merge/main。
    其他 daemon 只把人工合并后的 `origin/main` 当证据，各自 Manager 决定采用或延后并
    本地灰度。隔离能力缺失时 fail closed，不退化为 yolo。
    支持的 Linux host 必须预装 `bubblewrap`（Debian/Ubuntu: `apt install bubblewrap`）；
    daemon 启动时会做真实隔离 probe，失败只禁用自维护，不影响科研 mission。
- `argus_skill/life/memory.py`: 磁盘状态。global root 默认 `~/.argus-skill/`，project state 默认 `~/.argus-skill/projects/<fingerprint>/`。注入 mission 前的 “memory context” prelude(`render_prelude`)走**纯 recency**：surface 最近 N 条 journal(按所传 journal 做 project 隔离),**不再用关键词 Jaccard 给“相关性”打分**——“哪段过往工作相关”是 agent 读这段(标了 non-authoritative 的)advisory 后自己判断的,不是 harness 用词面重叠去猜。

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

- `SkillLoopConfig`: engineer/reviewer/matcher model、max rounds、writeback、distill-on-miss、runner flags、`paper_mission`。
- `SkillLoop.run(...)`: 主流程。
- `_build_engineer_prompt(..., paper_mission)`: 拼 L1 engineer prompt。长 horizon 论文 contract 仅在 `paper_mission=True` 时注入。
- 论文任务的识别**不再用关键词猜 objective 文本**，改由已解析 vertical 的结构化 completion gate 决定：只有 `completion_gate == "full_emnlp"` 才会把 `SkillLoopConfig.paper_mission` 置 True；缺失/损坏/未决状态一律按 False 处理。已删除旧的 `argus_skill/core/paper_objective.py` 与 `_looks_like_paper_objective`。

主流程：

```text
objective_for_skill -> SkillStore.find_relevant(...)
  miss -> SkillScientist.distill(...) -> SkillStore.save_distilled(...)
skill_text + task -> SupervisedEngineer.run(...)
  round k: engineer -> Reviewer.evaluate(...)
  outcome -> record distinct skill use; reviewer skill_ops may update/archive
  continue -> next_action 注入下一轮
  blocked/max_rounds -> 返回 outcome
```

改 prompt 时注意：

- 普通任务的 L1 prompt 在 `SkillLoop._build_engineer_prompt`。
- L1 prompt 现在保持轻量：当前任务、原始用户目标、匹配/Scientist 生成的 skill、Reviewer next_action、turn discipline。不要再把 vertical role banner、stage checklist、operator injected guidance、paper/non-paper long contract 塞回 engineer prompt。
- `objective_for_skill` 是干净用户目标；不要把 memory prelude 写进 skill history。`SkillStore.append_task_history` 已经在防这个坑。

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
- **一一映射契约**：`CHECKPOINT.md` 只存长期状态/证据引用/开放问题；
  Reviewer `reason` 只存 verdict 理由，`next_action` 只存下一轮指令；
  `planner_report` 只存 `forward_progress`、`plan_signal` 和 `evidence_files`。
  不再生成 `round_summary_markdown`、`completion_summary_markdown` 或
  `step_back`；旧事件仍由 parser 兼容读取。
- Engineer 把 `review`、`skill_action`、`skill_name` 三个控制字段写到
  mission-scoped `engineer-controls/<scope>/round-NNNN-engineer-control.json`，
  普通回复只写自然语言结果；旧
  `ARGUS_ENGINEER_DECISION` 仅作为运行中老 agent 的兼容 fallback，新 prompt
  不再出现 marker/template。
- `handoffs/latest.json` v2 只引用最新 round handoff 和 mission 文件；round
  handoff 再以 path/hash 引用 CHECKPOINT/control file，不复制 Engineer summary
  或 CHECKPOINT 正文。event/journal/wiki 只做同名字段投影或路径引用，不要求
  agent 重写第二份摘要。
- 每个 Engineer round 都必须经过 Reviewer，不再支持 `CONTINUE_WORK` 跳审。
- `ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS` 默认 0，不用绝对墙钟时间截断正常工作。
- 测试：`tests/test_checkpoint_loop.py`、`tests/test_session_resume.py`。

### Backlog-native Dynamic Plan（默认关闭）

- Reviewer 的 `planner_report.plan_signal` 只有 `continue|reconsider`；
  Reviewer 只报告“当前剩余计划是否已被新证据推翻”，L4 仍是唯一计划作者。
- `ARGUS_SKILL_DYNAMIC_PLAN_MODE=off|shadow|active`，默认 `off`。
  `shadow` 只写 `life.plan.signal`；`active` 需要连续
  `ARGUS_SKILL_DYNAMIC_PLAN_CONFIRM_ROUNDS` 次 reviewed `reconsider`
  （默认 2）才在 round 边界返回 `replan_requested`。
- `replan_requested` 不是 done/failed/blocked，不触发 Manager stage
  transition。当前 item 先回到 pending；随后仍经过现有 Planner
  rate/budget gate。
- Backlog row 持久化 `plan_id` / `plan_version` / `node_key` /
  `context_refs`。替换在一个 backlog 文件锁内 compare-and-swap：
  done 永不改写，旧 active nodes 进入不可复活的 `superseded`，新 DAG
  一次落盘；Planner/校验/冲突/写盘失败都保留旧计划。
- `context_refs` 只注入路径、用途与可选 hash，正文由 Engineer 按需读取。
  不新增关键词 relevance scorer，也不复制 Claude 的 JS workflow runtime。
- 实现：`engineer/runner.py`、`life/memory.py`、
  `life/supervisor/{_core,_mission_execution,_planning_context,_planning_cycle}.py`。

### Live credential guard

- `core/secret_guard.py` 在所有 `JsonlEventSink` / Agent CLI 持久化与下游事件前做
  领域无关凭据脱敏，并在每个 Engineer 回合后、Reviewer 读取前清理本轮新写的小型
  文本 artifact。
- 脱敏不裁决科研质量；若改写了 artifact，会通过 `round.secret_redacted` 告知
  Reviewer 重建相关 hash/provenance。扫描错误或大文本未覆盖也会显式阻止无条件认证。
- raw Engineer 文本只保留给 `WAIT_FOR_SUBAGENT` 控制解析；进入事件、
  usage、Reviewer prompt 的副本必须已脱敏。

### Background-subagent cadence wait（别空转盯长实验）

背景：mission 用 subagent 工具 `--mode supervised` 起一个长跑（如 veRL GRPO
训练）后，那个 job 已经有自己**独立的 supervisor** 每隔 `monitor_interval` 查健康、
崩了能 early-stop、终态会往 inbox 发报告。但 L1 engineer 往往每一轮都去重新轮询同
一个健康 run（实测 RL pilot 出现过几百轮只在重读 `status.json` + 写 `MONITOR_*.md`），
被长程 GPU 实验阻塞，而不是去推进不依赖它的独立工作。

实现（`argus_skill/engineer/background_subagents.py` + `runner.py`）：

- `background_subagents.py`：读
  `<workdir>/.argus_subagents/*.json` 注册表，把在跑的 job 分成 `self_watched`
  （supervised + 健康 + registry mtime 新鲜 + 无 concern + decision≠early_stop）和
  `needs_attention`（direct 模式 / discussing / degrading|stuck|diverging / 有
  concern / supervisor 心跳过期 / worker pid 已死）。
- **Agent 主导的 cadence 等待**（"只按 supervisor 节奏复查"）：若 engineer 显式回复
  `WAIT_FOR_SUBAGENT: <task_id>`，runner 检测到该 sentinel 且命中一个
  self-watched in-flight job 时，**跳过昂贵的 reviewer 轮**，按该 job 的
  supervisor 节奏（`monitor_interval`，clamp 到 30–900s）休眠，job 到终态会提前唤醒。
  是 **agent 显式选择**等待，不是 harness 替它决定。sentinel 命中不到自看护 job 时被
  忽略（退回正常 reviewed 轮），stale/误发不会挂住循环。
- 开关：`SupervisedConfig.background_subagent_advisory`（env
  `ARGUS_SKILL_BG_SUBAGENT_ADVISORY`，默认 on，0 关闭）。
- 测试：`tests/test_background_subagents.py`、`tests/test_runner_background_subagents.py`。

L2 reviewer 在 `argus_skill/reviewer/_core.py`。

这里管：

- reviewer prompt。
- `reviewer_schema.json` 结构化输出。
- `parse_decision_text` / JSON verdict。
- 近完成论文任务按结构化 stage/scope 注入一份**精简** peer-review contract；不再每轮塞入完整 `academic-paper-peer-review-benchmark.md`。
- Reviewer role、handoff、project-venv、wiki-curator 都使用短契约；长源 skill 从 matcher 排除，避免重复注入。

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
- 任务完成后写 journal。
- backlog 空时，L4 planner 自动生成下一批任务（历史的 L3 critic 逐轮打磨层已移除）。
- 论文类 continuous objective 在 planner 误报 `project_done` 时，强制改派一个 `final_submission` 认证任务，由 L2 reviewer 出整链裁决，而不是相信 planner 的早停。

重点函数：

- `LifeSupervisor.run()`: 主循环。
- `LifeSupervisor.tick()`: 处理一个 backlog item。
- `_plan_next_work(...)`: L4 planner，continuous mode 下 backlog 空了就调用；论文 objective 的 `final_submission` 改派也在这里（约 `supervisor.py:1776` 一带）。
- `LifeSupervisorConfig.paper_mission` / `full_emnlp_gate` / `open_ended`: 显式信号（前两个默认 False，只有 Manager 已解析出 `completion_gate == "full_emnlp"` 的 vertical 才开启）。`paper_mission` 决定 planner 给 bounded item 的论文/通用指导语；`full_emnlp_gate` 决定 `project_done` 前是否必须拿到一次 reviewer 认证的 full-pipeline 通过；`open_ended` 决定 planner 认证 `project_done` 后是“硬停”还是“继续生成新工作”。**已删除**旧的关键词判断 `_objective_requires_full_emnlp_gate` / `_objective_is_paper_long_horizon` / `_objective_is_open_ended`，全部改用 config flag。`open_ended` dataclass 默认 False，但 daemon/REPL 入口路径默认置 True（除非 `--bounded`），并随 `LifeWorkerConfig.continuous_open_ended` 一起做 blue/green handoff 序列化（`_config_payload` / `_config_from_payload`）。
- `_is_emnlp_finalization_objective(...)`: 识别一个任务是否就是项目级 `final_submission` 认证任务（看 `scope: final_submission` 标记，不再看 retired 的 `validate-*` 命令）。
- `_journal_has_full_emnlp_gate_success(...)`: 从 journal 里查是否已有一次被 reviewer 认证（`final_submission_certified=True`）的 full-pipeline 通过记录——这才是项目完成的唯一判据。
- 标量：`_PLANNER_SCOPE_FINAL_SUBMISSION = "final_submission"`，`_FULL_EMNLP_GATE_DESCRIPTION` 现在就是“L2 reviewer 的 full pipeline checklist（research → submission）”这句话，不是任何 shell 命令。

> 注意：历史上的 `_EMNLP_*_CODES` issue-code 分组、`_select_emnlp_finalization_repair_task`、
> `_automatic_emnlp_finalization_task_for_current_gate`、`_build_emnlp_finalization_objective`、
> `_planner_tasks_need_emnlp_finalization_override` 这套“按 issue code 选 repair lane”的确定性
> 分流**已经移除**。现在 supervisor 不读 `pipeline_contracts` 的 validator 结果来决定派活，
> 完成与否只由 L2 reviewer 对 full-pipeline checklist 的 `final_submission` 认证决定。改完成判定
> 逻辑看上面这几个函数，不要再去找 issue-code 分组。

## Skill 系统

skill 是 markdown 文件，带 YAML-like frontmatter。

关键文件：

- `argus_skill/skills/store.py`: markdown skill store、frontmatter parse、matcher、save/writeback。
- `argus_skill/skills/scientist.py`: 兼容的 matcher-miss Scientist/Distiller 路径；默认主路径由完成任务的 Engineer 同 session 提交选择性 create/update。
- `argus_skill/skills/layered.py`: project > active shared-vertical > shared-global 的匹配层；共享 Skill 被修改或记录复用时先 fork 回项目层。
- `argus_skill/manager/skill_tidy.py`: 成功 mission 边界的 Manager placement；内容 hash ledger 避免重复判断，shared-global 立即对所有项目可见，shared-vertical 只对同 vertical 可见。
- 不设 Skill 文本质量门、candidate/provisional 或 confirm 晋升状态。新建和更新先在项目层生效；跨项目传播需要成功 mission 后的 Manager placement，保留版本历史和可逆 archive。
- `argus_skill/skills/lifecycle.py`: reinforce/distill/revise/retire 决策。
- `argus_skill/skills/builtins.py`: packaged built-in skill seed/export。
- `argus_skill/builtin_skills/*.md`: 内置 skill 源文件。
- `argus_skill/builtin_skills/domains/**`: domain skill 包。
- **存储边界**：Git 只保存人工维护的 built-in Skill source；初始化时把它们 seed
  到 runtime。Agent 新建、更新、共享和归档的 Skill 永远只写
  `ARGUS_SKILL_HOME` 下的 project/shared runtime 层，不得反向写回或提交到 Git。
- Project wiki 在每个真实 Reviewer verdict 后立即机械写入
  `.autors/<project>/wiki/sources/runs/<mission>-r<round>.md`：内容只来自
  Reviewer verdict / planner_report / research_result，是 immutable、可引用的
  RoundCard；mission close 仅在没有 RoundCard 时写 fallback RunCard。Planner
  只读最近 3 条；Reviewer 后续可基于这些 source 自主提出
  `wiki_ops` 合成 technique/conflict/pattern page。不要强制每轮造 page，也不要
  绕过 evidence-verbatim gate。

初始化时：

- `GlobalMemory.init()` 会把 `argus_skill/builtin_skills` seed 到 `~/.argus-skill/skills/`。
- 默认不覆盖用户已经编辑过的 skill。
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

主要 stage 和 ownership（下表第三列的 `validate-*` 是历史 CLI 名，现已是 `stage_checklists.py` 里的 stage checklist 检查项，由 L2 reviewer 对照 artifact 裁决，不再是可调用的 CLI 子命令或 `pipeline_contracts` 函数）：

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

## EMNLP 论文检查（stage checklists + validator 函数）

质量校验由 L2 reviewer 读 stage checklist 完成（`argus_skill/skills/stage_checklists.py` 的 `format_stage_checklist` / `format_full_pipeline_checklist`），对照 artifact 做裁决。`pipeline_contracts.py` 现在只剩 **manifest / freshness / validation-priority** 这套 artifact 构建-修复子系统，不再托管成套的论文质量 `validate-*` gate。

```bash
# 完成判定由 reviewer 走 stage checklist：
python -c "from argus_skill.skills.stage_checklists import format_full_pipeline_checklist; print(format_full_pipeline_checklist(role='reviewer'))"
```

`pipeline_contracts.py` 现存的 CLI / 可 import 表面（全部是 artifact 构建-修复工具，**不是**质量 gate）：

```text
# CLI（skill 文案会让 agent 直接跑这几个）
python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .
python -m argus_skill.skills.pipeline_contracts refresh-artifact-freshness --project-root .
python -m argus_skill.skills.pipeline_contracts write-validation-priority-policy --project-root .
python -m argus_skill.skills.pipeline_contracts repair-emnlp-contract-artifacts --project-root .

# 对应可 import 的函数
refresh_artifact_manifest      / validate_artifact_manifest
refresh_artifact_freshness     / validate_artifact_freshness
write_validation_priority_policy / validate_validation_priority_policy
repair_emnlp_contract_artifacts
```

> 历史上 `pipeline_contracts.py` 还有约 20 个成套的论文质量 `validate-*` 函数
> （`validate_emnlp_paper_contract` / `validate_claim_graph` / `validate_full_scale_experiment_evidence`
> / `validate_image2_figures` / `validate_layout_review` …）和一个把它们串起来的
> `validate_full_emnlp_readiness` 总 gate。完成判定改成 L2 reviewer 的 full-pipeline checklist
> 认证后，这些函数**没有任何运行时 / 测试 / skill 调用方**，已整体**删除**（文件从 ~11.5k 行降到 ~2.5k 行）。
> 要重新引入某项机器校验，优先在 `stage_checklists.py` 的 checklist 里加一条，由 reviewer 验证。

项目是否“EMNLP ready”由 L2 reviewer 的 `format_full_pipeline_checklist` 整链裁决决定
（真实实验、claim 支撑、paper contract、format、figure quality 和独立 review
都在 checklist 里），不是看某个 validator 的返回值，也不只是看 PDF
存不存在。

改 validator 时注意：

- `ContractIssue(code, path, message)` 的 `code` 仍是有用的诊断标识，重命名前先 grep 引用（tests、reviewer 文案）。
- full-pipeline 完成判定现在走 L2 reviewer 的 `stage_checklists` 整链裁决，不再有 `supervisor.py` 里的 EMNLP issue-code 分组；新增检查项时改 `stage_checklists.py` 的 checklist，而不是去找已删除的 issue-code lane。
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

图像工具在 `argus_skill/tools/image_tool.py`。

常用命令：

```bash
python -m argus_skill.tools.image_tool paper-prompt --out paper/figures/overview.prompt.txt --force
python -m argus_skill.tools.image_tool generate --prompt-file paper/figures/overview.prompt.txt --out paper/figures/overview.png
python -m argus_skill.tools.image_tool inspect --image paper/figures/overview.png
python -m argus_skill.tools.image_tool review --image paper/figures/overview.png --prompt-file paper/figures/overview.prompt.txt
python -m argus_skill.tools.image_tool sync-paper-metadata --project-root . --image paper/figures/overview.png --figure-id overview
```

image-2 只是 capability 可用且 router 认为合适时的一条路线；实际使用时必须保留
prompt、sidecar、inspect、review、provenance、accepted-raster hash 和
`IMAGE2_FIGURES.json`，并自动同步统一 manifest。没有 image API 时不得伪造
image-2 metadata，也不得仅因此阻塞整篇论文；应由 agent 选择语义等价、可审计的
确定性路线。任何本地 SVG/HTML/PPT 输出都必须以真实 renderer 名义登记，不能冒充
image-2。

## Planner 的 EMNLP 完成判定

当 `LifeSupervisorConfig.full_emnlp_gate=True`（默认）时，L4 planner 有额外保护：

- `full_emnlp_gate` 这个**显式 config flag**（不再从 objective 文本猜）决定：`project_done` 前必须有一次被 reviewer 认证的 full-pipeline gate 通过记录。非论文的 continuous mission 可把它置 False。
- `_journal_has_full_emnlp_gate_success(...)`: 在 journal 里查这条认证记录（`final_submission_certified=True`）。
- `_plan_next_work(...)`: 若 planner 在尚未认证时就报 `project_done`，这里把它的裁决**替换**成一个 `scope=final_submission` 的 “Prove final submission readiness” 任务，交回 L2 reviewer 做整链认证。
- **scope 是结构化透传的，不再从 prose 里二次解析。** backlog item 的 `scope`（`final_submission` / `bounded` / 空）由 `_planner_scope_from_item(item)`（读 `item.tags`）算出，经 `MissionExecutor.execute(..., scope=...)` 一路传到 `_SkillLoopRunner` 和 reviewer。runner 不再从 objective prose 猜 `mission_scope`，reviewer 也只认结构化 scope(prose fallback 已删)。planner 去重里“`done` 的 final-submission 任务不挡新任务入队”这条豁免,也改走结构化的 `_item_is_final_submission(item)`(读 tag);`_legacy_final_submission_marker(text)` 只作为**老 backlog 迁移**兜底——给“tag 出现前持久化、只在 objective prose 里带 marker”的旧 item 用,保证 resume 的 daemon 不回归,新 item 一律带 tag。

所以：

- 改“项目什么时候算完成 / 还差认证时下一步派什么”，改 `supervisor.py` 的 `_plan_next_work` 改派分支和 `_journal_has_full_emnlp_gate_success`。
- 改“full-pipeline checklist 里某一项该不该判过”，改 `skills/stage_checklists.py`（reviewer 实际读的 checklist）；manifest/freshness/policy 这类 artifact 的构建-修复仍在 `pipeline_contracts.py`。
- 改“agent 读到任务后应该怎么做”，改 `builtin_skills/*.md` 或 `SkillLoop._build_engineer_prompt`。

## Backend / runner

Backend 协议在 `argus_skill/core/ports.py`：

```text
RunnerBackend.run_exec(prompt, options, run_label, resume_thread_id=None) -> RunnerResult
```

实现：

- `argus_skill/adapters/agent_cli_backend.py`: 包 vendored `agent_cli.agent_cli_runner.AgentCliRunner`，真实 codex/claude/copilot/opencode CLI 都从这里走。
- `argus_skill/agent_cli/`: 旧 ArgusBot autoloop 整套已删除，只保留三个底层 CLI driver 模块 `agent_cli_runner` / `runner_backend` / `models`（+ 薄 `__init__`、`LICENSE`、`_VENDORED.md`）。`__init__` 不再 eager import orchestrator/core，所以 `import argus_skill.agent_cli.agent_cli_runner` 没有遗留副作用。历史的 orchestrator / telegram_daemon / feishu_adapter / 第二份 reviewer·planner·checks / dashboard 等 ~33 个模块（~14.9k 行）都已移除——它们早被 `argus_skill.life` / `engineer` / `planner` 取代（Telegram 远控走的是新的 `life/telegram_bot.py`）。
- `argus_skill/adapters/memory_backend.py`: deterministic 测试/smoke。
- `_SkillLoopRunner` 在 `apps/_runtime.py` 里组装真实 backend，并把 backend 传给 Scientist、engineer、reviewer、planner。

常见 env：

```text
ARGUS_SKILL_LIFE_BACKEND=codex|memory
ARGUS_SKILL_RUNNER_BACKEND=codex|claude|copilot|opencode
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

## 实验和论文证据

仓库不再内置 benchmark runner / archive helper；旧 `benchmarks/` 包和只服务它的 legacy mission/container 兼容层已删除。论文 pipeline 里的 `benchmark` stage 仍是研究流程概念，由 stage checklist / reviewer 对照项目内 artifact 裁决。

- `experiments/`: 本地实验输出。
- `paper/`: 这个仓库自己的 claim-to-evidence paper workspace，不等同于每个外部 research project 的 `paper/main.tex` pipeline，但命名会重叠。
- `paper/build_*_artifacts.py`: 从 repo-local evidence 生成 checked-in `paper/artifacts/*`。

## 测试入口

常用快速测试：

```bash
pytest tests/test_loop_smoke.py
pytest tests/skills/test_pipeline_contracts_cli.py
pytest tests/skills/test_paper_layout_review_snapshots.py tests/skills/test_paper_layout_review_venue.py
pytest tests/skills/test_academic_language_review_venue.py
pytest tests/test_paper_infrastructure_review.py
pytest tests/tools/test_image_tool.py
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

只改文档通常不用全跑。改 `pipeline_contracts.py` 至少跑 pipeline/review/image 相关 tests。改 `supervisor.py` 至少跑 life/daemon/planner 相关 tests。

## 修改时的层级规则

1. CLI / cockpit 行为改 `apps/cli/_core.py` / `webapi/` / `frontend/tui/` / `daemon/life_worker.py`；Manager handoff 与入队改 `manager/front_door.py` / `manager/dispatch.py`。
2. 单任务 agent prompt 改 `loop.py`。
3. L1 执行可靠性改 `engineer/runner.py`。
4. L2 验收标准改 `reviewer/_core.py` 和相关 role skill。
5. L4 调度策略改 `life/supervisor/_core.py` / `planner/planner.py`。
6. Skill 匹配、蒸馏、writeback 改 `skills/store.py` / `scientist/*`。
7. EMNLP 质量是否合格改 `skills/stage_checklists.py` 的 checklist；manifest/freshness/policy artifact 构建-修复改 `skills/pipeline_contracts.py`。
8. EMNLP 项目何时算完成 / 还差认证时改派什么，改 `life/supervisor/_core.py` 的 `_plan_next_work` 与 `_journal_has_full_emnlp_gate_success`。
9. Agent 读到 paper 任务后的操作手册改 `argus_skill/builtin_skills/*.md`。
10. 生成 evidence/review JSON 的工具改 `skills/*_review.py` 或 `tools/image_tool.py`，不要只改 validator 放宽。

## 常见坑

- 不要把 runtime prelude、daemon 路径、Codex route、capability vault、local cache/device 写进论文正文。
- 不要手改 review JSON、manifest 或 freshness 来制造 PASS。优先修源 artifact 后重跑生成器。
- 不要假设还存在 `supervisor.py` 里的 EMNLP issue-code 分组 / `_select_emnlp_finalization_repair_task`：它们已删除，完成判定走 L2 reviewer 的 full-pipeline checklist 认证。
- 不要只在 built-in skill 文案里改规则，却忘了 reviewer 的 stage checklist（底层 `validate_*` 函数）仍然会判红。
- 不要只让单个 stage 的 paper-contract 检查过就说 EMNLP ready；最终看 `format_full_pipeline_checklist` 的整链裁决。
- 不要在 full-scale evidence gate 红的时候继续 polish `paper/main.tex`，先补实验/benchmark/source matrix。
- 不要把 pilot、synthetic、same-family-only evidence 写成 full EMNLP-ready result。
- 不要在 user 的 `~/.argus-skill/skills` 里直接覆盖本地编辑，源码改 `argus_skill/builtin_skills`，需要时再 export。
