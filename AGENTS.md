# Argus Skill Agent 修改地图

这份说明给后续接手的 agent 看。目标不是替代代码阅读，而是让你先知道“哪里管哪里”，避免在 EMNLP 论文 pipeline 或 7x24 harness 里乱改错层。

## 一句话架构

`argus-skill` 是一个长期运行的 agent harness：外层 `LifeSupervisor` 管 backlog、预算、daemon、L4 planner（forward scheduling）；内层 `SkillLoop` 管单个任务的 skill 匹配、miss 时调用 Scientist 生成并立即持久化 project-layer skill、L1 engineer 执行、L2 reviewer 验收并基于真实轨迹提出 skill 更新或归档。历史上独立的 L3 critic 逐轮打磨循环已经移除——验收完全交给 L2 reviewer。EMNLP 论文生成 pipeline 是 built-in skill + per-stage reviewer 检查（stage checklists，reviewer 对照 artifact 裁决）+ planner fallback 共同实现的，不是单独一个 `make_paper.py`。`pipeline_contracts.py` 现在只负责 manifest/freshness/validation-priority 这套 artifact 构建-修复工具，不再是质量 gate。

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
| Skill | 横向能力复用 | `argus_skill/skills/store.py`, `argus_skill/skills/scientist.py`, `argus_skill/builtin_skills/` | skill 匹配、miss 后由 Scientist 生成立即可用的 project-layer skill、Reviewer 根据真实使用轨迹 update/archive、内置论文/research playbook |
| Contracts | 论文 artifact 工具 + 状态机 | `argus_skill/skills/pipeline_contracts.py`, `argus_skill/skills/pipeline_policy.py`, `argus_skill/skills/stage_checklists.py` | manifest/freshness/validation-priority 构建-修复（pipeline_contracts）；质量 gate 走 stage checklist（stage_checklists） |

> **常见误解**：读到 L0/L1/L2/L4 这个编号，容易以为 argus 是"三层 agent"（Planner/Engineer/Reviewer，L3 critic 已退役）。实际常驻跑着的是**四个**角色——Manager/Planner/Engineer/Reviewer（`cli/roles_status.py`: `ROLES = ("manager", "planner", "engineer", "reviewer")`）；Manager 不占 L 编号只是因为它跨越整条流水线（前门 + stage 权威），不代表它级别更低。另外还有一个可选的 **Curator** 角色（`ARGUS_SKILL_CURATOR_*`），只在并行 subagent/团队模式下才跑，管 skill 池维护和团队排行榜蒸馏，不参与日常单任务流水线，因此不在上表中。README 和三份 pitch 文档（商业计划书/项目介绍/一页纸概览）历史上都只画了三个角色（未包含 Manager），已于 2026-07-07 全部修正为四个角色。

## 入口和运行面

- `pyproject.toml`: console script 是 `argus-skill = "argus_skill.__main__:main"`。
- `argus_skill/__main__.py`: 只 re-export `apps.cli.main`。
- `argus_skill/apps/cli/_core.py`: 所有顶层 CLI flag 都在这里注册。这里没有 subcommand 模型，`--daemon`、`--status`、`--watch`、`--follow`、`--continuous`、`--objective`、`--bounded`、skill admin 都是 top-level flag。
  - **入口硬门禁（`_lifetime_entry_error`）**：默认进 cockpit / 启动 daemon 时只要求至少有一个受信任的 special prompt（`life/special_prompts.py`）；允许空 objective 等待首条真实任务。首条 TEAM task 由 Manager 判断 `STANDING` / `BOUNDED` 并生成 execution objective，STANDING objective 原子持久化到 `continuous.json`。没有机器规则仍 `exit 2`。只读 / admin flag（`--status`、`--watch`、`--skill-stats`…）不受门禁限制。
  - **默认 lifetime**：chat/simple 请求在前门直接处理；其余 TEAM task 默认 `STANDING`（7×24），只有 Manager 明确判断有自然一次性终点时才 `BOUNDED`。`--bounded` 仍可作为直接 daemon 启动的 operator override。
- `argus_skill/webapi/manager_bridge.py`: Ink/Web cockpit 的统一 Manager 接口；`manager/front_door.py` 管分类与 handoff，`manager/dispatch.py` 管 lifetime 与持久化入队。Python line REPL 已删除。
- Manager front-door 的一次模型调用同时输出 `CONFIG` / `CONTROL` / `ROUTE` 三个结构化轴。`CONTROL: NO_DISPATCH` 是 Manager 对 operator 明确“只读 / 不派任务 / 不启动 daemon”约束的权威裁决：bridge 强制走 SELF，inline 回复失败也 fail-closed，不得入 backlog。harness 不扫 operator prose 关键词来改判。SELF 回合用 read-only sandbox；session 的 `launch_cwd` 仅作为 grounding workspace/cwd 注入，pipeline/artifact/session state 仍留在 project state root，不能混为一处。
- `argus_skill/life/router.py`: operator 自由文本的 chat-vs-task 路由。**不再用关键词/正则分类**（历史的 `is_conversational` 用 60 字符上限 + 中英文正则猜“这是闲聊吗”，harness 比 agent 聪明）。现在 `classify_is_conversational(text, *, run_exec)` 做一次低 reasoning 的模型调用，只有模型精确回答 `CHAT` 才返回 True，其余（TASK / 模糊 / 空 / 非零退出 / 异常）一律按 task 走完整 pipeline——bias 向 task，宁可多跑也不误吞任务。只有 operator 通过 Manager front-door 发送的自由文本才会被分类；planner / backlog / daemon 的任务都不分类，否则就是 harness 二次猜 planner。
- `argus_skill/daemon/life_worker.py`: detached daemon 版本的同一套逻辑。这里管 `continuous.json` 热加载、pid lock、blue/green handoff、daemon status、预算环境变量。
  - `--resume-continuous` 只采用与 Manager handoff identity（objective hash +
    vertical + lineage generation）匹配的持久化 campaign；升级/崩溃恢复不得重新调用
    Manager。缺失或不匹配 identity 的 legacy/raw objective 仍须走真实 Manager divide。
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
- 是否清掉 carried Codex thread id。
- **Curated-memory checkpoint + 结构化 session roll**（见下）。
- **Background-subagent advisory + cadence wait**（见下）。
- `round.main.completed`、`round.review.completed`、`session.roll` 等事件。

### Curated working-memory checkpoint（上下文管理 / 反 amnesia loop）

背景：一个 mission 的 Codex session 会被逐轮 `resume`，长 horizon 任务里它会
涨到几亿 token、被 codex 自动有损压缩上百次，每次压缩丢失工作记忆 → 模型反复
重读同一批 skill 文档空转（amnesia loop）。修复哲学：**不靠看门狗**，而是让
session 结构上短命 + 跨 session 边界只交接「经过筛选的有价值记忆」。

实现（`argus_skill/engineer/checkpoint.py` + `runner.py` + `argus_skill/reviewer/_core.py`）：

- `CheckpointState`：小而**硬上限**的工作记忆（goal / done[] / tried_and_failed[]
  / open_blocker / next_step）。上限在 Python 里强制（不只在 prompt/schema），
  上限本身就是强制「遗忘/筛选」的机制——删除是解毒，不是丢失（地面真相在磁盘
  artifact 里，可重新召回）。
- **作者 = reviewer（记忆审计员）**：reviewer schema 增加 `checkpoint` 对象。
  engineer 在 turn 末尾按 prompt 输出一段 `HANDOFF:` 提案；reviewer 校验它
  （对照 evidence/artifacts）并 CRUD 出下一份 canonical checkpoint。engineer 提议、
  reviewer 验证落定。
- **消费**：runner 每轮把 checkpoint 渲染成「Curated working memory」块 prepend 到
  engineer prompt（同 failed-tool advisory 的拼接方式，`loop.py` 不动）。
- **Session roll**：`SupervisedConfig.shift_round_limit`（env
  `ARGUS_SKILL_SHIFT_ROUND_LIMIT`，默认 3，0=禁用）或前一轮 input 达到
  `ARGUS_SKILL_THREAD_TOKEN_LIMIT`（默认 1,500,000，0=禁用）。一个 thread 达到任一
  边界就主动
  drop，下一轮从 checkpoint 重新播种一个**全新 session**，per-session 上下文有界 →
  上百次压缩的 runaway 不可能发生。已有的 context-pressure / poisoned-session 清
  thread 路径现在也带着 checkpoint = 重生而非失忆。
- **无进展**复用已有的 `planner_report.forward_progress`（reviewer 对照前后
  checkpoint 整体判断），不新增看门狗。
- fail-soft：reviewer 漏写/写坏 checkpoint → runner 保留上一份，绝不清空记忆。
- 持久化：`SupervisedConfig.checkpoint_path`（None=mission 内内存）。当前 `loop.py`
  未传 path，所以是 mission 内内存级（已足够修复单 mission 内的 amnesia loop）；
  要跨 mission 续接，给它传一个 project-state 路径即可。
- 测试：`tests/test_checkpoint.py`、`tests/test_checkpoint_loop.py`。

### Dynamic review cadence（简单、agent 主导）

默认仍是每个 Engineer round 后由 Reviewer 独立验收。若 Engineer 已落地真实增量、
下一步局部执行非常明确、此时 Review 只会重复转述，它可以把
`CONTINUE_WORK: <specific next step>` 作为回复的最后一行，申请先再做一个 round。

- 最多连续跳过一次 Reviewer；下一个工作 round 强制回到 Reviewer。
- 真 Reviewer verdict 后额度重置；最后一个可用 round 永不跳过 Reviewer。
- `done` 仍只能由 Reviewer 裁决。harness 不从 prose 猜是否该跳过，只响应这个显式请求。
- 相关实现：`engineer/runner.py`、`loop.py`；事件：`round.review.deferred`。

### Live credential guard

- `core/secret_guard.py` 在所有 `JsonlEventSink` / Agent CLI 持久化与下游事件前做
  领域无关凭据脱敏，并在每个 Engineer 回合后、Reviewer 读取前清理本轮新写的小型
  文本 artifact。
- 脱敏不裁决科研质量；若改写了 artifact，会通过 `round.secret_redacted` 告知
  Reviewer 重建相关 hash/provenance。扫描错误或大文本未覆盖也会显式阻止无条件认证。
- raw Engineer 文本只保留给 `WAIT_FOR_SUBAGENT` / `CONTINUE_WORK` 控制解析；进入事件、
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
- 对近完成论文任务自动注入 `academic-paper-peer-review-benchmark.md`：注入与否**按结构化 stage/scope 裁决**（`is_final_submission or stage in {review, submission}`），不再用关键词扫 objective/evidence 里的 `main.pdf`/`references.bib` 之类 token。`draft` 阶段不注入,避免初稿阶段被过早套上终审标准。
- reviewer-to-engineer handoff skill：`reviewer-engineer-handoff.md`。

> **不再有 harness 关键词改判，也不再从 prose 猜 scope。** 历史上 `reviewer.py` 有个
> `_coerce_decision_against_main_summary`，会用关键词正则扫 engineer 的 summary，
> 把 reviewer 的 `done` 强行改成 `continue`（harness 比 agent 聪明，违背设计哲学）。
> 该函数连同它的 `GENERIC_MAIN_PATTERNS` / `CONCRETE_EXECUTION_PATTERNS` 等正则常量
> 已全部删除。需要“别在没有执行证据时判 done”的约束，现在写进 reviewer prompt
> 由 L2 自己判断，而不是 harness 事后覆盖裁决。`is_final_submission` 现在**只认结构化
> scope**（`scope == final_submission`，归一化时 `-`→`_`),删掉了过去 `"scope: final_submission" in objective` 这类 prose 兜底——scope 由 planner 以 backlog tag 形式一路透传到 reviewer。

如果 reviewer 老是误判：

- 先看 `Reviewer._build_prompt` 和固定 role skill。
- 再看 `argus_skill/builtin_skills/reviewer/argus-reviewer-role.md`、`academic-paper-peer-review-benchmark.md`。
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
- `argus_skill/skills/scientist.py`: matcher miss 后让 Scientist/Distiller 生成立即可用、已版本化的 project-layer skill。
- 不设 Skill 文本质量门、candidate/provisional 或 confirm 晋升状态。新建和更新默认有效；真实使用记录、Reviewer 反馈、版本历史和可逆 archive/compaction 为后续 update/split/merge/retire 提供证据。
- `argus_skill/skills/lifecycle.py`: reinforce/distill/revise/retire 决策。
- `argus_skill/skills/builtins.py`: packaged built-in skill seed/export。
- `argus_skill/builtin_skills/*.md`: 内置 skill 源文件。
- `argus_skill/builtin_skills/domains/**`: domain skill 包。
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
- `argus-skill --export-builtin-skills [DIR]` 可以复制内置 skill 到项目目录，默认 `./argus_builtin_skills`。

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
  CLAIM_GRAPH.json
  EVIDENCE_GAPS.json
  ARTIFACT_MANIFEST.json
  ARTIFACT_FRESHNESS.json
  VALIDATION_PRIORITY_POLICY.json
  FIGURE_TABLE_STYLE_GUIDE.json
  SUBMISSION_ASSURANCE.json
  ACADEMIC_LANGUAGE_REVIEW.json
  PAPER_INFRASTRUCTURE_REVIEW.json
  LAYOUT_REVIEW.json
  figures/IMAGE2_FIGURES.json
  style_ref/
```

主要 stage 和 ownership（下表第三列的 `validate-*` 是历史 CLI 名，现已是 `stage_checklists.py` 里的 stage checklist 检查项，由 L2 reviewer 对照 artifact 裁决，不再是可调用的 CLI 子命令或 `pipeline_contracts` 函数）：

| Stage | 主要 skill | 主要 artifact / 检查项 |
| --- | --- | --- |
| 选题/grounding | `research-brief-to-experiment-plan.md`, `auto-research-pipeline.md` | `validate-grounding`, `validate-idea-provenance`, `validate-code-reuse` |
| 实验/benchmark | `agent-research-benchmark-runner.md` | `validate-full-scale-evidence`, `experiments/**` |
| 结果到 claim | `research-results-analysis-and-figures.md`, `claims-evidence-audit.md`, `result-to-claim.md` | `validate-claim-graph`, `RESULTS_REPORT.md`, `result_to_claim.tsv` |
| 初稿/LaTeX | `emnlp-paper-drafting.md` | `validate-paper-contract`, `validate-paper-format`, `main.tex`, `main.pdf` |
| 格式预检 | `emnlp-format-preflight.md` | `validate-research-md-format`, `FORMAT_PREFLIGHT.md` |
| 图表/IMAGE2 | `research-results-analysis-and-figures.md`, `paper-illustration-image2.md`, `paper-framework-figure-studio-pro.md` | `validate-image2-figures`, `validate-figure-table-style` |
| 学术语言 | `emnlp-academic-language-review.md` | `academic_language_review --write`, `validate-academic-language-review` |
| 基建泄漏 | `emnlp-paper-infrastructure-review.md` | `paper_infrastructure_review --write`, `validate-paper-infrastructure-review` |
| 视觉布局 | `paper-review-revision-loop.md`, `emnlp-format-preflight.md` | `paper_layout_review --write`, `validate-layout-review` |
| 最终提交 | `research-submission-assurance-gate.md` | `submission` stage checklist + full-pipeline `final_submission` 认证（`SUBMISSION_ASSURANCE.json`、`validate_submission_readiness`） |

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
（evidence、claim graph、paper contract、format、image-2、review、manifest、freshness、
submission assurance 都在 checklist 里），不是看某个 validator 的返回值，也不只是看 PDF
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

## IMAGE2 / 论文图

图像工具在 `argus_skill/tools/image_tool.py`。

常用命令：

```bash
python -m argus_skill.tools.image_tool paper-prompt --out paper/figures/overview.prompt.txt --force
python -m argus_skill.tools.image_tool generate --prompt-file paper/figures/overview.prompt.txt --out paper/figures/overview.png
python -m argus_skill.tools.image_tool inspect --image paper/figures/overview.png
python -m argus_skill.tools.image_tool review --image paper/figures/overview.png --prompt-file paper/figures/overview.prompt.txt
python -m argus_skill.tools.image_tool sync-paper-metadata --project-root . --image paper/figures/overview.png --figure-id overview
```

contract 要求非数据类 paper-facing figure 通过 image-2/codex-image2 路线产生，并保留 prompt、sidecar、inspect、review、provenance、manifest hash。不要用本地 matplotlib/TikZ/SVG 画一个概念图再伪装成 image-2。

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

- `argus_skill/adapters/agent_cli_backend.py`: 包 vendored `agent_cli.agent_cli_runner.AgentCliRunner`，真实 codex/claude/copilot CLI 都从这里走。
- `argus_skill/agent_cli/`: 旧 ArgusBot autoloop 整套已删除，只保留三个底层 CLI driver 模块 `agent_cli_runner` / `runner_backend` / `models`（+ 薄 `__init__`、`LICENSE`、`_VENDORED.md`）。`__init__` 不再 eager import orchestrator/core，所以 `import argus_skill.agent_cli.agent_cli_runner` 没有遗留副作用。历史的 orchestrator / telegram_daemon / feishu_adapter / 第二份 reviewer·planner·checks / dashboard 等 ~33 个模块（~14.9k 行）都已移除——它们早被 `argus_skill.life` / `engineer` / `planner` 取代（Telegram 远控走的是新的 `life/telegram_bot.py`）。
- `argus_skill/adapters/memory_backend.py`: deterministic 测试/smoke。
- `_SkillLoopRunner` 在 `apps/_runtime.py` 里组装真实 backend，并把 backend 传给 Scientist、engineer、reviewer、planner。

常见 env：

```text
ARGUS_SKILL_LIFE_BACKEND=codex|memory
ARGUS_SKILL_RUNNER_BACKEND=codex|claude|copilot
ARGUS_SKILL_RUNNER_BIN=/path/to/codex
ARGUS_SKILL_RUNNER_EXTRA_ARGS="..."
ARGUS_SKILL_SAFE_MODE=1
ARGUS_SKILL_SKILL_OPS=0|1
ARGUS_SKILL_PER_MISSION_CAP_USD=30
ARGUS_SKILL_DAILY_CAP_USD=180
ARGUS_SKILL_GLOBAL_DAILY_CAP_USD=30
ARGUS_SKILL_MAX_ACTIVE_DAEMONS=2
```

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
- 不要手改 review JSON、manifest、freshness、submission assurance 来制造 PASS。优先修源 artifact 后重跑生成器。
- 不要假设还存在 `supervisor.py` 里的 EMNLP issue-code 分组 / `_select_emnlp_finalization_repair_task`：它们已删除，完成判定走 L2 reviewer 的 full-pipeline checklist 认证。
- 不要只在 built-in skill 文案里改规则，却忘了 reviewer 的 stage checklist（底层 `validate_*` 函数）仍然会判红。
- 不要只让单个 stage 的 paper-contract 检查过就说 EMNLP ready；最终看 `format_full_pipeline_checklist` 的整链裁决。
- 不要在 full-scale evidence gate 红的时候继续 polish `paper/main.tex`，先补实验/benchmark/source matrix。
- 不要把 pilot、synthetic、same-family-only evidence 写成 full EMNLP-ready result。
- 不要在 user 的 `~/.argus-skill/skills` 里直接覆盖本地编辑，源码改 `argus_skill/builtin_skills`，需要时再 export。
