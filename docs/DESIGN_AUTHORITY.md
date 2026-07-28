# Argus 设计文档权威层级

本文件回答一个维护问题：当代码、README、旧方案、技术报告和运行快照互相冲突时，
哪一个才代表当前 Argus。

## 1. 权威顺序

从高到低：

1. **当前实现与行为测试**
   - 运行契约：`argus_skill/`
   - 行为回归：`tests/`
   - 跨端协议：`argus_skill/core/event_catalog.py`、
     `argus_skill/core/event_payload_schemas.json`、`frontend/core/src/`
2. **当前维护文档**
   - 修改地图：`AGENTS.md`
   - 系统结构：`docs/ARCHITECTURE.md`
   - 状态与死锁：`docs/STATE_MACHINE_AND_DEADLOCKS.md`
   - 协议与运行边界：本文件下方“当前契约文档”表格中的文档
3. **公开说明**
   - `README.md`、`README.zh-CN.md`
   - 它们必须与当前维护文档一致，但只保留用户需要的概览。
4. **技术报告与历史材料**
   - `technical_report/` 保留，因为它是正式技术报告及证据；其经验结论绑定报告标注的
     版本，不能覆盖当前运行契约。
   - 技术报告图像 provenance 依赖的
     `docs/superpowers/specs/2026-07-15-ai-redraw-structural-report-figures-design.md`
     作为例外保留。
   - 日期化 goals、旧 reviews、incidents、experiments、showcase、其余 superpowers 计划和
     协作记录不保留在 main；需要时从 Git 历史读取。
   - `argus_skill/Argus-North-Star架构审查与改进方案.md` 保留为架构演进依据，但顶部必须
     明确标记历史基线。
5. **仓库外材料**
   - 融资、BP、VC deck、路演文案和对外私有介绍材料不得放在主分支。
   - 它们不是运行时规范，应保存在仓库外的私有工作区。

发生冲突时，以实现和行为测试为准，并在同一个变更里修正第 2、3 层；不要为了让文档
看起来正确而修改历史证据。

## 2. 当前契约文档

| 主题 | 当前文档 | 代码事实源 |
| --- | --- | --- |
| 总体架构与角色 | `docs/ARCHITECTURE.md` | `manager/`、`life/supervisor/`、`engineer/`、`reviewer/` |
| 任务、阶段、项目、战役状态 | `docs/STATE_MACHINE_AND_DEADLOCKS.md` | `life/memory.py`、`manager/_stage_ops.py`、`core/project_api.py` |
| GoalContract | `docs/ARCHITECTURE.md` | `core/project_contract.py`、`manager/front_door.py` |
| Web/daemon 兼容协议 | `docs/protocols.md` | `webapi/`、`daemon/protocol.py` |
| daemon 命令 | `docs/daemon-command-protocol.md` | `daemon/commands.py`、`daemon/state.py` |
| 事件 | `docs/event-catalog.md` | `core/event_catalog.py`、`core/event_payload_schemas.json` |
| release identity | `docs/release-identity.md` | `release.py`、`scripts/build_release.py` |
| 后端调用边界 | `docs/run-exec-gateway.md` | `core/run_gateway.py`、`adapters/agent_cli_backend/` |
| 编排模块边界 | `docs/orchestration-modules.md` | 对应包和模块 |
| 运行配置 | `docs/ARGUS_RUNTIME_SETTINGS.md` | `core/knobs.py::KNOBS` 与各读取点 |
| 修改方法论 | `docs/edit-principle/` | 当前模块边界、测试与 Git 历史 |
| 成本 | `docs/cost-control.md` | `core/cost_control.py`、`core/usage.py` |
| 指标和 SLO | `docs/observability.md` | `core/metrics.py`、WebAPI metrics routes |
| Wiki/Skill 边界 | `docs/IDEA_WIKI_DESIGN.md` | `wiki/`、`skills/` |
| 长实验 | `docs/LIVE_EXPERIMENT_PROTOCOL.md` | `tools/subagent.py`、`engineer/external_work.py` |
| UI 视觉系统 | 根目录 `DESIGN.md` | `frontend/` |

## 3. 当前不可破坏的系统不变量

- 常驻角色是 Manager、Planner、Engineer、Reviewer；Curator 只属于可选团队模式。
- Manager 是 pipeline stage 的唯一语义决策者。Supervisor 只有一个机械补偿例外：
  bounded DAG 尚有同计划未完成节点时，可把被提前推进的 stage 恢复到本 mission 的起始
  stage；它不能选择新的科研阶段。
- Planner 负责 forward planning 和 DAG 替换，不负责 mission 验收。
- 当前 mission round 固定走 `Engineer -> Reviewer`；不存在活跃的
  `review=skip` Engineer 自审旁路。历史事件和兼容字段可能仍出现
  `engineer_self_review`，但不是当前生产路径。
- Reviewer 的有效状态是 `done`、`continue`、`blocked`、
  `replan_requested`。后者直接请求 Planner 替换剩余计划；不存在
  `off|shadow|active` Dynamic Plan 模式或连续 signal 确认机制。
- `events.jsonl` 是项目历史事实源；`EventJournal` 是它的投影，不是第二份日志。
- 项目工作目录与 Argus project state root 是两个不同目录，恢复 session 不得偷偷重绑
  工作目录。
- `goal_contract.json` 保存 operator 目标、precise/semantic clauses、排除项与歧义；
  precise 约束不能被 Manager 静默放宽。
- 当前角色可直接编辑 project-layer Skill/Wiki；旧 `skill_ops` 是兼容 replay。
  `protected` 对结构化/自动 SkillRouter 操作是机械约束，对直接文件编辑是角色政策约束。
  Operator 已决定不恢复统一机械写入门。
- 论文型 vertical 的 completion gate 名称是 `full_paper`，运行配置字段是
  `full_paper_gate`；`full_emnlp` 只允许出现在旧数据迁移或历史材料中。
- 唯一货币预算是 host-global daily USD cap。
- 一个 daemon 的实际行为由它加载的 source root 和 release identity 决定；包版本号相同
  不代表代码相同。

## 4. 文档变更规则

1. 改变角色、状态、事件、配置、完成门或持久化格式时，必须同时更新对应当前契约文档。
2. 当前契约文档引用符号名和模块路径，不依赖容易漂移的行号。
3. 删除配置或事件生产路径时，同一变更必须删除当前文档中的现行描述；若为历史兼容保留，
   明确写成“legacy/reserved”，不能写成活跃功能。
4. `--config-snapshot` 是机器状态快照，不提交到仓库作为设计规范；仓库只记录配置契约和
   生成方式。
5. 技术报告不因当前实现变化而重写；由本索引限定其版本权威性。其他历史计划/评审不在
   main 保存。
6. 新增设计文档时，先在本文件登记其主题和事实源，避免产生第二份当前架构说明。
