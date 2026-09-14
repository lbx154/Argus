# Argus 架构清晰度审计与调整方案（2026-09-14）

> 只读调查产物，未改任何代码，未提交。起因：操作者感觉"软件架构不够明晰，需要调整"。
> 本文回答三个问题：这种感觉有没有实证？根因是什么？该怎么调、按什么顺序、哪些要操作者拍板？
> 数据附件：`architecture-clarity-2026-09-14.data.json`（诊断、核验、最终方案的结构化原文）。

## 0. 结论

感觉是对的，而且可以量化。`argus_skill/` 有 687 个 Python 文件、218,619 行、27 个子包，但**没有任何地方（代码、文档、测试）声明哪个包可以依赖哪个包**。跨包 import 共 1,553–1,594 条，其中 67% 写在函数体内，所以 33–35 对互相依赖的包从未在 import 时报错；仅看模块顶层 import，16 个包已经构成一个强连通分量。`core` 被 ~790 处引用却在模块顶层反向 import `agent_cli`、`provider_integrations`、`life`（5 个文件 7 条）。六个最大的包 `__init__.py` 为 0 字节，其中 `core`、`skills`、`apps` 三个包的内容与包名相悖。产品主干（daemon → life/supervisor → apps/_runtime → loop.py → engineer）横跨五个包加包根，靠下划线私有名当作跨包 API（181 条私有 import，172–269 个测试文件直接 import 私有路径）。

推荐方向：**先命名、先把分层规则钉成可执行测试、再做最小必要的搬动**。第 0 阶段（今天可做，24 个文件，不搬任何模块，不需要重启，不需要任何决策）就能把分层规则变成带"只许缩小"白名单的测试；随后 8 个一天量级的机械批次（`git mv` + 一行 import 修改 + 临时别名 shim）把 core 变成真正的叶子、把任务运行时收进一个包、让 `skills/` 只剩 Skill 库。全程零新增运行时机制、零 console-script 变更、每一步测试全绿且守护进程可重启。被否决的三个更激进方案（严格 31 级全序、按概念重划 bounded context、词汇先行大改名）分别要搬 95k–120k 行、在自己的分层规则下自相矛盾、或者要在最热的前端/事件线上改 wire 格式。

## 1. 方法与可信度

两轮多代理工作流，全部只读：

| 轮次 | 代理 | 产出 |
|---|---|---|
| 理解 | 11 个读者（core / 运行时 / 角色 / 知识与垂直 / provider / 入口面 / 前端 / 仓库布局 / 持久状态 / 术语 / 测试布局）+ 1 综合器 | 8 条按结构性排序的根因，200+ 条 `path:line` 证据 |
| 核验 | 每条根因 2 个独立核验者（一个重测每个数字，一个专门找反证） | 8 条根因中 2 条 CONFIRMED / MOSTLY_CONFIRMED、5 条 WEAKENED（数字成立但表述夸大）、多处数字被修正 |
| 设计 | 4 个独立架构师（A 命名先行 / B 严格分层 / C bounded context / D 词汇先行） | 4 份目标架构 + 迁移方案 |
| 评审 | 3 个评委（新人视角 / 运维发布视角 / 原则审查视角），每份方案抽查 ≥3 条事实 | 一致选 A（41/40/41 分；B 33/32/32、C 30/29/28、D 35/32/26） |
| 综合 | 1 个最终架构师 | 本文第 4–7 节的方案，只用核验过的数字，吸收评委的移植建议与纠错 |

本人另行抽查并确认：6 个 0 字节 `__init__.py`；`core/knobs.py:21`、`core/backend_readiness.py:12-13`、`core/usage.py:22`、`core/operator_messages.py:8` 的顶层反向 import；跨包 import 1,588 条 / 68% 函数体内 / 35 对互依 / 28 对至少一向仅懒加载 / 181 条私有 import；webapi 31 个顶层模块只有 2 个引用 fastapi/starlette/pydantic；`MemoryBundle.root` 返回全局根（`life/memory.py:3004`）而 `life/supervisor` 读 `self.memory.root` 84 次、读 `.project_root` 0 次；`tests/test_architecture_invariants.py` 27 个测试当前全绿。

**核验纠正的主要夸大**（后文已按纠正后写）：

- "core 在顶层 import manager" → 错，`core/operator_context.py:230` 在函数体内；core 顶层反向 import 的对象只有 agent_cli、provider_integrations、life 和包根。
- "5 个 doctor 实现" → 3 个不同用途的引擎（bootstrap 环境 / 单项目守护进程诊断 / 主机级报告）+ 5 层包装。
- "`_acquire_daemon_lock_with_timeout` 重复定义" → `daemon/_life_worker_admission.py:64` 是 6 行 monkeypatch 代理，实现只在 `daemon/handoff.py:218`。
- "`_emit_planner_verdict` 在三个 mixin 各实现一遍" → 只有 `_core.py:1515` 一个实现，另两处是 `raise NotImplementedError` 的声明桩。
- "`project done` 的单写入口被 5 处绕开" → 那 5 处 `disable_continuous_config` 是操作者的暂停/停止写入，不是 DONE 写入；REFUTED。
- "core 里 1,547 行论文/venue 策略" → 严格算 761 行（venue_review 330 + manuscript_snapshot 174 + manuscript_narrative_runtime 257）；但 `venue_review.py:22` 确实硬编码 `vertical == "research"`。
- "六个空 `__init__` 的包没人说它们是什么" → 这些包 98–100% 的模块有模块级 docstring，20/27 个包有包级 docstring；缺的是**包一级**的定位和**跨包**的分层声明。
- 四份设计方案都说 `tools/gpu_lease.py`、`gpu_ownership.py` 有人 import → 错，两者零生产 importer（gpu_lease 是文档化的 `python -m` 工具，保留）；`skills/rl_training_health.py` 也零 importer。
- 方案 A 说 `tools/subagent/_direct_run.py` 不 import `run_contract` → 错，`:212` 有；因此 RL gate 搬进 `verticals/research` 后必须经 `research_bridge` 再导出。

## 2. 已核实的诊断（按结构性排序）

### 2.1 没有声明、也没有强制的分层（MOSTLY CONFIRMED）

- 跨包 import 1,553–1,594 条；67% 在函数体内。懒加载比例按来源包：roles 84%、apps 81%、manager 81%、core 80%、skills 80%、life 79%、daemon 73%、webapi 67%，而 verticals 41%、tools 38%、adapters 36%、engineer 27%；stdlib/第三方 import 只有 5.7% 懒加载。**懒加载是框架各包之间的专属风格，不是 house style。**
- 33–35 对互依包；27–28 对至少一个方向只靠函数体 import。但注意：1,071 条懒 import 里只有 10 条是"不懒就会形成模块级环"的必要项；模块级边本身已让 16 个包连成一个强连通分量。所以懒加载不是"撑住"了网状，而是**藏住**了它。
- 181 条跨包 import 直接引用 `_private` 模块或名字，跨 53 对有向包对。
- `core` fan-in ~790–798，模块顶层反向 import 恰好 7 条 / 5 个文件：`knobs.py:21`、`backend_readiness.py:12-13` → agent_cli；`usage.py:22` → provider_integrations；`operator_messages.py:8`、`mission_view/_reduce_mission.py:19` → life；`runtime_identity.py:14` → 包根。这是一个小而机械可修的集合。
- `argus_skill/__init__.py:18` 急切 import `loop.SkillLoop`，所以 `import argus_skill.core.paths` 会加载 8 个子包共 71 个模块（含 engineer/reviewer）。
- 现有可执行边界规则共 8 条、分布在 4 个测试文件里（`tests/test_architecture_invariants.py:261/280/300` 等），全部只守"框架不 import 具名垂直"这一条缝；没有任何一条把 core/life/daemon/apps/manager/webapi/tools 分开。
- 约 387 个被懒 import 的名字是测试的 monkeypatch 目标（`daemon/_life_worker_boot.py:6-10`、`apps/_runtime_construction.py:651-657` 有文档说明），**所以不能一刀切把懒 import 提到顶层**。

### 2.2 包名描述的是历史或愿望，不是内容（WEAKENED，但 core/skills/apps 三处成立）

- 0 字节 `__init__.py`：core 25,804 行、apps 11,311、skills 7,595、daemon 6,882、engineer 5,279、adapters 4,292。daemon/engineer/adapters 名实相符，只是缺定位；core/skills/apps 名实相悖。
- `skills/`：真正处理 Skill 文档的 1,637 行；垂直选择 + 阶段机 2,533 行（`vertical_select.py` 1,094、`stage_machine.py` 1,165、`checklist_store.py` 274）；RL 研究 gate 3,090 行（`run_contract.py` 1,448 等）。垂直清单 `VERTICALS` 住在 `skills/vertical_select.py:69-75`，`verticals/__init__.py:12` 反过来懒 import 它。
- `apps/`：`_runtime*.py` + `_self_reply.py` 共 4,621 行任务运行时，`apps/_runtime.py:3-4` 自己写着"shared by the daemon, teammate runner, and Manager front-door"。
- `core/`：按最窄的"内核"定义（models、ports、event_catalog、contracts、paths、OS 原语）只有 ~5.6k 行；其余是费用/用量账本 4,186 行、后端配置与就绪探测 4,545 行、cockpit 读模型 `mission_view/` 3,150 行（含中英文句子表）、operator 存储 1,547 行、论文/venue 策略 761 行、workbench 插件安装器 1,071 行。按 PRINCIPLES 第 2 条更宽的"capability"定义，其中大部分可以辩护；明确越界的是论文/venue 策略。
- `webapi/__init__.py:1` 自称 "the `[web]` extra"，但 pyproject 里 fastapi/uvicorn 是硬依赖，也不存在 `[web]` extra。31 个顶层模块里 29 个不含任何 FastAPI/Starlette/Pydantic 引用——这是有意的设计，但没有被命名和声明（见 2.7）。
- 名字冲撞：`cli/`（ANSI 主题与事件渲染）vs `apps/cli`（argparse CLI）vs `agent_cli`（驱动外部模型 CLI）；`plugin` 四义；`integrations` 四个目录；`maintenance/`（Doctor + 修复 + 部署边界）vs `verticals/argus_maintenance` vs `~/.argus-skill/maintenance/pending` 决策卡。
- 仓库没有任何一处说明 17 个顶层目录各是什么；README 没有提到 `argus_skill/` 这个目录本身。

### 2.3 任务运行时没有家（WEAKENED：链路有文档，但缺乏包级归属）

- 执行链严格线性：`daemon/_life_worker_run.py:272` → `life/supervisor/_core.py:697/1151` → `_mission_execution_runtime.py:840` → `apps/_runtime_execute.py:412/818` → `loop.py:270`（4 个 mixin 在 `skills/loop_*.py`）→ `engineer/runner.py:103`。每一跳在不同的包。
- 13 个模块的 docstring 写着 "Split out of ... so that module stays under the maintainability line-count target"（apps/_runtime\* 6 个、daemon/\_life_worker\_\* 6 个、engineer/round_config）——按行数而非概念切分，并"re-export 下划线名字以免外部 import 受影响"。
- `apps/_runtime.__all__` 23 个名字里 19 个带下划线；`life/supervisor/__init__` 30/24；`tools/subagent/__init__` 91/69。
- 生产代码里 daemon（`_life_worker_boot.py:240,809`、`_life_worker_runtime_context.py:204`）、team（`teammate_entry.py:189,421`）、manager（`front_door.py:297,344`）直接 import `apps._runtime*` 的私有名。
- `LifeSupervisor` 10 个直接 mixin、MRO 17 个类、207–212 个方法；`_planning_context.py` 单个 mixin 2,432 行 66 个方法。
- 过期 docstring：`life/supervisor/_config.py:185` 的 `MissionExecutor`、`_core.py:8` 的 `JsonlCommandBus`、`agent_cli/__init__.py:10` 的 `_VENDORED.md`、`loop.py:1-8` 与 `argus_skill/__init__.py:1-7` 的 ArgusBot 时代描述、`apps/cli/_core.py:1-18` 的"exactly one entry point / only subcommand is wiki"（实际 84 个 flag、5 个子命令）——引用的东西都不存在。

### 2.4 词汇没有在代码里定死（CONFIRMED / MOSTLY CONFIRMED）

- 同一个"项目状态目录"有 12 个标识符：`life_dir` 944、`project_root` 1,910、`state_root` 466、`state_dir` 128、`runtime_root` 99、`project_state_dir` 71、`memory_root` 42、`session_root` 40、`project_dir` 33、`vertical_state_root` 31、`manager_session_root` 20、`life_root` 13（其中 3 个部分是别的概念）。`core/paths.py:114-116` 的 `session_states_root()` 返回的是 `projects/`。
- `project_root` 有**三个**含义：执行 workdir（所有垂直、CORE_CONCEPTS）、Argus 状态目录（`life/memory.py:3012`、`manager/control_state.py:288`）、主机根（`webapi/routes/context.py:111` `project_root_or_404` 返回主机根并在 `routes/workitems.py:44` 作为 `global_root=` 转发）。
- **一个真 bug**：`MemoryBundle.root` 返回主机根 `~/.argus-skill`（`life/memory.py:3004`），`LifeMemory.root` 返回项目目录；守护进程正常路径注入的是 MemoryBundle（`daemon/_life_worker_boot.py:215-223`），`life/supervisor` 读 `self.memory.root` 84 次、`.project_root` 0 次。后果：`_mission_execution_runtime.py:408` 把主机根当 `state_root` 传给垂直 prelude；`letters.json`、`RESEARCH_PLAN.md`、`stage-certificates.json`、`runtime-failure-circuit.lock`、`campaign-control/` 等在主机根出现副本（22 个双层重名文件里约 15 个来源于此；主机根 `letters.json` 的 mtime 是 2026-09-14 00:07，仍在被写）；19 张维护决策卡写在主机根；`manager/front_door.py:140-152 _life_dir_for` 等 4 个 shim 专门用来猜 `root` 是哪个；`front_door.py:268-285` 有 20 行事后分析注释记录它造成过的事故。
- `RunnerBackend` 既是 Protocol（`core/ports.py:24`）又是 CLI 名字的 Literal（`agent_cli/runner_backend.py:9`）；`planner_runner=` 接收的是 backend（`daemon/_life_worker_boot.py:789`）。
- 四角色元组在 6 处各写一遍；Curator 已在 `core/role_config.py:36,65` 配置了路由/模型/effort，却不在 `RoleName` 和 CORE_CONCEPTS 里。
- `task`（py 1,897 处）6 义、`mission`（1,264 处）5 义（其中 4 义是同一 mission 的不同侧面）、`skill` 10+ 指代、`session` 6 义、`campaign` 5 义、`contract` 15 个类、`handoff` 4 义、`supervisor` 3 个类名 5 种用法。

### 2.5 持久状态所有权分散（WEAKENED：是去中心化，不是无主）

- 57 个文件名常量里 55 个只声明一次（`events.jsonl` 例外：`life/event_log.py:50`、`webapi/server.py:199`、`webapi/mission_items.py:43` 三处）；六个"authoritative"docstring 彼此不冲突；`complete_project` 单一调用者。
- 真正成立的机械问题：19 个 `_atomic_write*` 定义（约 12 个是几乎相同的 tmp + `os.replace`，5 个有实质差异：daemon/state 的 ENOSPC 预留、daemon/health 的 Windows 重试、workspace_v2 的 confined 写、image_api 的 force）；31 个文件手写 `fcntl.flock`/`portalocker`，而 `core/file_lock.py` 只提供一种模式、13 个 importer；mission-view 投影在 `JsonlEventSink._append` 内部重写（`life/event_log.py:286-292`），再由 ~380 行 TS reducer（`frontend/core/src/missionView.ts:219-598`）二次归约，没有两边一致性的 golden fixture；`Backlog.update` 把未知 status 静默改成 `pending`。
- `objective` 存在 7 处：3 处项目目标文本、2 处 sha256 指纹、2 处按 mission。

### 2.6 "框架不懂领域"只在 import 层面成立（WEAKENED）

- verticals/ 之外有 32 处严格 `== "research"`（宽口径 47 处）；`DEFAULT_VERTICAL = "research"` 定义两次（`verticals/_base.py:27`、`skills/vertical_select.py:133`）；`skills/stage_machine.py:812-814` 在任何异常时静默回退到 research 检查表；core 里 761 行论文/venue 策略，`venue_review.py:22` 硬编码 research。
- verticals↔skills 的 28/26 互依，成因是一个值类型和一张表放错了包：`ChecklistItem`（`skills/stage_machine.py:20-27`，8 行 frozen dataclass）被 verticals/domains 下 24 个文件 import；skills 侧 26 条 import 全是函数体内且带 6 处 "cycle" 注释。契约 `core/vertical_contract.py` 已有 `checklist_items` 字段。
- "Manager 是 pipeline stage 的唯一写入者"只是 prompt 文本：非 manager 的直接写入点 5 处（`life/supervisor/_planning_cycle_enqueue.py:196,205,497`、`_mission_execution_settlement.py:203`、`skills/vertical_select.py:1021`），且靠 `advanced_by="manager:..."` 字串自标。没有测试。

### 2.7 交付面增生、服务层未被命名（WEAKENED）

- webapi 里 8 个不含 FastAPI 的模块（manager_bridge、mission_items、project_state、daemon_lifecycle、artifacts、manager_pending_question、manager_state、diagnostics）被 5 个包的 6 个模块 import（`plugin/service.py:24-33`、`life/chat/router.py:718`、`apps/_runtime_construction.py:570`、`apps/cli/_core.py` 5 处、`maintenance/doctor.py:460`、`trial/web_runtime.py:59`）——**服务层已经存在，只是叫 webapi，也没有人宣布它是服务层**。`apps/_runtime_construction.py:572` 甚至写明 `webapi.manager_bridge` 是"the authoritative Manager answer path"。
- doctor = 3 个引擎 + 5 层包装；status = 2 个原语上的 4 种呈现；update/upgrade/repair 7 个子关切模块；两个 launcher 互相委托，flag 集合在 `apps/cli/_parser.py`、`apps/tui_launcher.py:13-99`、`frontend/tui/src/args.ts:48` 手抄三份且已漂移。
- `trial/`：39 文件 13,067 行进产品 wheel，产品实际用到 4 个文件约 619 行；`core/knobs.py:628-629,971-972` 反向 import trial 常量。

### 2.8 书面架构过期或缺失；仓库根混放产品、研究产物与日志（MOSTLY CONFIRMED）

- `docs/system-audit.md:178` 引用 2026-08-27 已删除的 `daemon/self_maintenance.py`；`research/ARCHITECTURE_AUDIT.md`（2 MB，仅 1 次提交）六处引用 `.worktrees/.../core/bootstrap.py`；`docs/RESEARCH_AGENCY_AND_VERIFICATION_TODO.md:4` 指向不存在的 `AGENTS.md` 和 `docs/ARCHITECTURE.md`；`docs/CORE_CONCEPTS.md:52-59` 把 `builtin_skills/*/argus-*-role.md` 当角色权威，而运行时加载的是 `roles/prompts/*.py`（那些 md 是作为普通 Skill 种子被读入）。
- 没有 LAYOUT/AGENTS/CLAUDE/docs 索引；`docs/handoff-2026-09-04-capability-tests.md`（2,432 行 52 节）是若干 2026-09 设计决策与事故的唯一记录。
- 测试：650 个测试文件里 201 个散在 `tests/` 根；7 个包没有 `tests/<pkg>/`（含 43,934 行的 verticals）；`tests/skills` 71 个文件里 55 个 import `argus_skill.verticals`；25 个测试目录里 14 个没有 `__init__.py`；两套约定同一周（2026-08-05/06）开始并都仍在接收新文件。
- 根目录：`companions/FLYWHEEL` 39k 行、零耦合、3 次提交；`technical_report/` 205 文件无代码依赖；`research/` 2 MB 生成物；`frontend/web/dist` 186 文件 24 MB 被 405/1,669 次提交触碰（有意为之，`.gitignore:11-13`）；`.npm-ci-*.log` 被跟踪。

### 2.9 现状依赖图（实测）

```
webapi <-6/9-> apps <-12/6-> manager <-13/22-> life <-11/17-> daemon
  |  ^          | ^  ^          |  ^   ^          ^  ^          |
plugin ---------+ |  +-- team --+  |   +-- tools -+  +---- agent_cli <-11/12-> core
                  |                |                |                            ^  ^
                roles <-1/11--- manager             |          provider_integrations -+  |
                  ^                                 v                                    |
                reviewer, engineer, planner ----> core <-144/5-- life        trial <-20/4-+
                                                    ^
                verticals <-28/26-> skills ---------+---- adapters <-2/44----------------+
                   ^                  ^
                domains ------------- + (ChecklistItem)
```

没有叶子，也没有顶。唯一被强制的边是"框架 ↛ 具名垂直"，而 32 处 `== "research"` 绕过了它。

### 2.10 当前活体（决定部署风险）

- 13 个守护进程/子代理进程运行在 pinned checkout `argus-runtime-20260909-385d9b336`（落后 main 5 天）。
- 2 个进程运行在 `argus-runtime-latest`（`e0dc20dfd`）。
- **14 个进程直接从开发树 `/data/v-boxiuli/Argus/.venv` 运行**：trial 的 7 个服务（egress、web_admin、relay_guardian、admin_runtime、compute、web_portal、socket_forward）、3 个 `--web --no-daemon`（8897/8901/8902）、若干实验脚本。任何在开发树里的 `git mv` 都会立刻改变它们懒 import 的目标。这是本方案里每个搬动阶段都要求"同一提交带 alias shim + 重启开发树进程"的原因。

## 3. 什么是清楚的、要保留的

- `VerticalContract` 三层缝（`core/vertical_contract.py` → `verticals/*.py` 桥模块 → `verticals/<domain>/`），24/24 个垂直都声明 `STAGE_ORDER`/`CHECKLIST_ITEMS`，`completion_gate` 三值封闭词汇在加载时校验；`tests/test_architecture_invariants.py` 27 条已有不变量。（2026-09-14 注：审计后同日，17 个垂直拆到社区包 `argus-verticals`，树内 24 → 7；本文其余的 24 计数是审计当时的事实，见 `docs/handoff-2026-09-04-capability-tests.md` §54。）
- 线性执行链本身和它的两个缝：`_MissionRunner` 协议（`life/supervisor/_config.py:181`，只要 `.execute`）与 `RunnerBackend` 端口；`Backlog.claim_next` 原子 CAS 作为唯一的 cockpit/daemon 协调原语。
- `life/event_log.py` 的 `JsonlEventSink` 作为唯一 appender；`Backlog` 内部封装的 20 处 status 写入 + `IllegalStateTransition`。
- `core/mission_view/` 的内部分解（按事件族一模块、dispatch 表、所有人话句子隔离在 `_wording.py`）——形状对，位置和一个依赖方向不对。
- `engineer/`：`SupervisedEngineer` 按 round 阶段分解且 engineer/reviewer 不 import verticals；`reviewer/_parsing.py` 纯函数解析。
- `roles/prompts` 注册表：`RoleName` 枚举、按角色 frozenset 操作集、拒绝未知角色。
- `team/`、`proof_ledger/`、`maintenance/`（内容）、`adapters/agent_cli_backend/` 的三阶段分解、`webapi/routes/` 100% 拥有路由注册、`plugin/service.py` 可注入外观、desktop-tauri 作为薄壳。
- `skills/store.py + layered.py`：Skill = 两字段 frontmatter 的 markdown，project/vertical/global 三层。
- 650 个测试文件、7,030 个测试函数，命名描述性强——**重构可以被机械验证**。

## 4. 推荐方案：命名 → 钉规则 → 最小搬动

### 4.1 八层分层（作为数据写进测试）

模块**顶层** import 只允许指向本层或更低层；函数体内的向上 import 用白名单钉死、只许缩小。

| 层 | 包 | 可 import |
|---|---|---|
| L0 kernel | core, proof_ledger | 仅 stdlib/第三方（唯一带修复注记的例外：`core/usage.py` → `provider_integrations.copilot_usage`，待账本离开 core 时移除） |
| L1 providers | agent_cli, provider_integrations, adapters | L0；层内 adapters→agent_cli、adapters→provider_integrations、provider_integrations→agent_cli |
| L2 capabilities | tools, wiki, terminal（现 cli）, skills（仅 Skill 库） | L0–L2 |
| L3 domain | verticals, domains, builtin_skills | L0–L3（verticals/<x> → 桥模块；verticals → domains） |
| L4 pipeline | pipeline（第 3 阶段起） | L0–L4（垂直只能经桥模块 loader/registry/data_domain/inventory/research_bridge，不得具名——沿用现有规则） |
| L5 roles | roles, planner, engineer, reviewer | L0–L5（engineer/reviewer 不 import verticals——现有测试） |
| L6 runtime | life, manager, mission_runner（第 5 阶段起） | L0–L6（层内 life↔manager、mission_runner→life/manager、life→mission_runner 容忍并计数） |
| L7 process | daemon, team | L0–L7（daemon↔team） |
| L8 delivery | apps, webapi, plugin, doctor（现 maintenance）, trial, integrations, release_tools, 包根入口模块 | 一切 |

按此表，今天的树有 21 条模块级向上 import（按 `文件 -> 目标包` 计）、66 个（文件, 目标包）延迟向上对（函数体内或 `if TYPE_CHECKING:` 下）、107 条跨包私有 import（按 `来源文件 -> 模块[.名字]` 计，合并后为 53 个包对）。第 0 阶段把这三个数字原样写成严格相等的白名单。

### 4.2 目标树（节选，标注阶段）

```
argus_skill/
  __init__.py              ph0: PEP 562 __getattr__ 懒解析 SkillLoop/SkillStore 等；__version__ 改由 core/version.py 提供（ph1）
  loop.py                  ph5 变 shim → ph6 删除（→ mission_runner/skill_loop.py）
  core/                    L0；docstring 分层说明五个 tier（kernel | config+backends | accounting | operator stores | mission_view+paper policy），后四个标为后续抽出候选
    backend_names.py       ph1 ← agent_cli/runner_backend.py（421 行，纯 stdlib）；Literal 改名 BackendName，RunnerBackend 别名保留到 ph6
    process_control.py     ph1 ← agent_cli/_process_control.py（195 行）
    event_log.py           ph1 ← life/event_log.py（370 行，只依赖 core）；仍是唯一 appender，mission-view 钩子不动
    mission_outcome.py     ph1 ← life/mission_outcome.py（200 行，零依赖）
    capability_vault.py    ph1 ← tools/capability_vault.py（881 行）；tools/ 下留永久 3 行转发（prompt 引用 python -m argus_skill.tools.capability_vault）
    version.py             ph1 新增
    vertical_contract.py   ph2 加入 ChecklistItem
    (agent_probe.py        ph1 → adapters/agent_probe.py)
  verticals/
    inventory.py           ph2 新增：VERTICALS / VERTICAL_PURPOSES / DEFAULT_VERTICAL / 别名表，测试钉到目录集合
    loader.py registry.py data_domain.py   ph7 ← _base.py / _registry.py / _data_domain.py（公开桥模块；旧名 shim 到 ph10）
    research_bridge.py     ph2 再导出 run_contract 入口（tools/subagent/_direct_run.py:212 需要）
    research/              ph2 ← skills/{run_contract, rl_training_health, rl_training_plots, anti_mediocrity, evidence_chain}.py（3,001 行）
  pipeline/                ph3 新建 ← skills/{stage_machine, vertical_select, checklist_store}.py（2,533 行）；对 verticals.loader 的懒 import 保持懒（31 处是 monkeypatch 目标）
  skills/                  ph3 后只剩 Skill 库 ~1.7k 行
  life/                    保留并定义（"一个 Project 的连续生命：memory、backlog、supervisor、operator channels"）；ph4 加入 inbox.py、operator_actions.py ← apps/_inbox.py、apps/_life_actions.py
  mission_runner/          ph5 新建 ← apps/_runtime*.py（8 个）、apps/_self_reply.py、loop.py、skills/loop_*.py；公开名 SkillLoopRunner、build_life_runner、run_life_supervisor 等
  terminal/                ph6 ← cli/
  doctor/                  ph6 ← maintenance/
  apps/                    ph4/ph5 后 11,311 → ~6,000 行，纯交付层
  webapi/                  不搬；docstring 如实写明它同时承载 manager_*/map_*/daemon_* 服务模块，待后续服务层提案
docs/LAYOUT.md             ph0 新增：分层表 + 17 个顶层目录 + 27 个包各一句
docs/CORE_CONCEPTS.md      ph0 追加 "## Glossary"
tests/test_architecture_invariants.py   ph0 追加第 8 节"Declared layering"
```

### 4.3 词汇表（关键条目）

| 概念 | 规范名 | 退役名 |
|---|---|---|
| 项目状态目录 `~/.argus-skill/projects/<id>/` | `life_dir`（标识符，944 处沿用）；"project state directory"（散文）；`core.paths.project_state_root(sid)` | `life_root`、`memory_root`、`session_root`、`project_dir`、`manager_session_root`、`session_state(s)_root()` |
| 主机根 `~/.argus-skill` | `global_root` | `MemoryBundle.root`（卡 3 后）、三份 `_resolve_global_root` |
| 执行 workdir | `workdir`；`project_root` 只在 VerticalContract 钩子、verticals/、domains/ 内保留此义 | 框架包内表示状态目录的 `project_root` |
| 后端名（codex/claude/copilot…） | `BackendName`（`core.backend_names`） | Literal 版 `RunnerBackend` |
| Runner vs Backend | Runner 执行一个 mission（`MissionRunner`、`SkillLoopRunner`）；Backend 实现 `RunnerBackend` 端口 | `planner_runner=` 接 backend 的用法（只在文档中说明，不改参数名） |
| life | 保留并定义 | —（149 个测试文件、CLI flag、49 个事件名，不值得为一句话改名） |
| pipeline | 哪个垂直、哪个 stage、哪张检查表 | 住在 skills/ 的阶段机 |
| Skill | 两字段 frontmatter 的 markdown | skills/ 作为阶段机/RL gate/loop mixin 的家 |
| Doctor / Terminal | `doctor/`、`terminal/` | `maintenance/`、`cli/` 作为包名 |
| Overlay vs data domain | docstring 先区分；包名 `domains/`→`overlays/` 待卡 8 | — |
| Mission | = BacklogItem（类名不改） | — |
| 永久别名 | `--life-dir`、`sid`、`ARGUS_SKILL_*` 环境变量 | 不删 |

### 4.4 迁移阶段

| 阶段 | 目标 | 文件≈ | 部署影响 | 依赖决策卡 |
|---|---|---|---|---|
| 0 命名与钉规则 | LAYOUT.md、6 个包 docstring、8 处过期 docstring、Glossary、根 `__init__` 懒加载、不变量第 8 节（27→~41 测试）、删根目录噪音、交接文档追加一节 | 24 | 无需重启 | 无（卡 1 仅确认分层表） |
| 1 core 成为模块级叶子 | 5 个叶模块下沉到 core、agent_probe 上移到 adapters、BackendName 改名、version.py、sandbox 一行 | 32 | 重启；**并重启 14 个开发树进程** | 无 |
| 2 契约拥有值类型 | ChecklistItem 进契约；verticals/inventory.py；RL gate 搬 verticals/research 并经 research_bridge 再导出 | 48 | 重启；`verticals/research/stages.py` 是全仓最热文件（125 commits/30d），只改一行 import，当天早上落 | 无 |
| 3 pipeline/ | 阶段机 + 垂直选择离开 skills/ | 14 | 重启 | 卡 7（包名，默认 pipeline） |
| 4 life 不再顶层 import 交付层 | `_inbox`、`_life_actions` 搬进 life/ | 20 | 重启 | 无 |
| 5 mission_runner/ | apps/_runtime\*、_self_reply、loop.py、loop mixins 收进一个包，四个跨包名转公开 | 46 | 必须重启；`apps/_runtime_execute.py`（27 commits/30d）先做零内容 git mv | 卡 7（默认 mission_runner） |
| 6 便宜改名 + 一次 sed + 删 shim | cli→terminal、maintenance→doctor；一次可重跑的 sed 把 ph1–5 的旧路径全部改成新路径；删 `tools/subagent/_core.py`（零 importer） | ~380 | 必须重启；此后旧进程在 ff 过的树里会 import 失败——kill-first 规则不可省 | 无 |
| 7 公开 loader、诚实路径名 | `_base/_registry/_data_domain` → `loader/registry/data_domain`（消掉 ~66 条私有 import）；`core.paths` 加 `projects_root/project_state_root`；`webapi/routes/context.py` 的 `project_root_or_404` 改 `global_root_or_404` | 115 | 重启 | 无 |
| 8（门控）`MemoryBundle.root` 一个含义 | 84 处 `.root` 逐处改成 `.global_root` / `.project_root`；一次性把主机根副本搬进 `projects/<id>/`（仿 `_lifecycle.py:63-64` 先例）；回归测试钉 prelude 收到 `projects/` 下的路径 | 20 | **首个有行为风险的阶段**；操作者盯守护进程的日子做；回滚 = ff 回退一版 | 卡 3 |
| 9（门控）删重复 | 一个 core atomic_write（移植 daemon/state 最严变体）替换 ~12 份近似副本；ROLES 单源；EVENT_FILE 单源；删 gpu_ownership / rl_training_health | 26 | 重启；无磁盘格式变化 | 卡 9、卡 10 |
| 10 测试镜像目录 + 最后的 shim | 55 个垂直测试搬到 `tests/verticals/<domain>/`；14 个目录补 `__init__.py`；删 ph7 shim 与 paths 别名 | 80 | 无功能影响 | 卡 11 |

每个搬动阶段的固定形态：**提交 A = 零内容 `git mv`（100% 相似度，rebase 时对方的修改会跟到新路径）→ 提交 B = 一行 import 修改 + `sys.modules` 别名 shim（同一对象，monkeypatch 目标继续解析）→ 缩小白名单一行**。每阶段 fetch → 执行 → `ruff check --fix --select I` → 全量 pytest → 一小时内 push。

**可量化的终态**：模块级向上边 23 → 6；函数体向上对 66 → ~30；私有 import 对 53 → <30；core 模块级向外 7 → 1；verticals → 编排层 import 30 → 4（全懒、钉死）；空 docstring 包 6 → 0；`skills/` 7,595 → ~1,700 行；`apps/` 11,311 → ~6,000 行；`import argus_skill.core.paths` 加载 0 个引擎模块；`_atomic_write` 定义 19 → ≤6；角色元组 6 → 1。

### 4.5 第一批（今天可做，无需任何决策，无需重启）

1. `docs/LAYOUT.md`（新，~90 行）：八层表；17 个顶层目录各一句（companions/、technical_report/、research/、contrib/、update/ 标"不构建、不测试、不发布"）；27 个包各一句。README 加 20 行 "Repository layout" 链过去。
2. 六个 0 字节 `__init__.py`（core、skills、apps、adapters、daemon、engineer）写入 "Layer: / 属于这里 / 不属于这里 / 计划搬走" 四段。改写 8 处过期 docstring：`argus_skill/__init__.py:1-7`、`loop.py:1-8`、`agent_cli/__init__.py:8-11`、`life/__init__.py:1-20`（补"life"的定义，删 `MissionExecutor`）、`life/supervisor/_config.py:185`、`life/supervisor/_core.py:6-9`、`webapi/__init__.py:1`、`apps/cli/_core.py:1-18`。
3. `docs/CORE_CONCEPTS.md` 在 `:104-114` 的概念-存储表后追加 "## Glossary"（4.3 节的表）。
4. `argus_skill/__init__.py:11-19`：core.models/core.ports 四个 import 保持急切，SkillLoop/SkillLoopConfig/Skill/SkillStore 改 PEP 562 `__getattr__`（同 `life/__init__.py:67-71` 的写法）。约 +14/−2 行。
5. `tests/test_architecture_invariants.py` 追加第 8 节（~320 行）：`LAYERS`、`_cross_package_imports(path)`（沿用 `:58-74` 的相对 import 解析，加"是否在 FunctionDef 内"标记），以及：
   - `test_every_package_is_assigned_to_exactly_one_layer`
   - `test_module_level_imports_never_point_to_a_higher_layer`（21 条白名单，键为 `文件 -> 目标包`，严格相等：修好一条边却没删白名单也会红）
   - `test_function_body_imports_to_higher_layers_are_pinned`（66 对，按 (文件, 目标包) 粗粒度；函数体内与 `if TYPE_CHECKING:` 下的 import 同记为延迟）
   - `test_private_modules_are_not_imported_across_packages`（107 条，键为 `来源文件 -> 模块[.名字]`；合并后为 53 个包对）
   - `test_every_package_docstring_names_its_layer`、`test_layout_map_lists_every_directory`
   - `test_importing_the_kernel_does_not_load_the_engine`（子进程；在旧 `__init__` 上必须是红的——71 个模块）
   - `test_module_paths_cited_by_prompts_and_skills_resolve`（prompt/Skill md 里所有 `argus_skill.x.y` 路径 `find_spec` 可解析）
   - `test_subprocess_reentry_module_paths_stay_importable`（`team.teammate_entry`、`tools.subagent`、`daemon.spawn_helper`、`desktop_backend_entry`、`plugin.mcp_server` 等 argv 匹配路径）
   - `test_only_the_manager_advances_the_pipeline_stage`（非 manager 对 advance/rollback/complete_final_stage 的绑定与引用钉在 10 = 5 处 import 绑定 + 5 处引用，别名也算，只许降）
   - `test_retired_names_do_not_spread`（`life_root` 13、`memory_root` 42、`session_root` 40 … 精确计数基线）
   - `test_memory_root_reads_do_not_grow`（84 + 2）
   - 现有 12 包的"不 import 具名垂直"名单扩到 tools/adapters/agent_cli/provider_integrations/apps
6. 删根目录噪音：`.npm-ci-tui.log`、`.npm-ci-web.log`；pyproject/.gitignore 里指向从未存在路径的 exclude（argus/app、codex/app、pyknotid-scratch、verticals/crystalpilot）。
7. 交接文档追加 "## 53. Declared package layering (2026-09-14)"：规则（"修一条向上边 = 同一 PR 删掉它的白名单行"）、ph1–7 计划日、搬动后重启开发树进程的提醒。
8. 验证：`python -m ruff check argus_skill tests && python -m pytest -q` 全绿；按团队要求**先证明新测试在旧代码上是红的**：(a) 在旧 `__init__` 上跑 `test_importing_the_kernel_does_not_load_the_engine`；(b) 本地删一条白名单看它变红；另跑 `tests/desktop/test_frozen_runtime.py`。
9. 预期 diff：~24 个文件，约 +700/−45 行。

## 5. 待操作者决策卡

| # | 问题 | 建议默认 | 阻塞 |
|---|---|---|---|
| 1 | 确认八层顺序（特别是 tools 在 verticals 之下、skills 在 roles 之下、life+manager+mission_runner 同一层且层内环容忍） | 按写批准；`tools/team.py`、`tools/manager_live_view.py`、`tools/subagent/_direct_run.py` 三条向上边带注记钉住 | 不阻塞 ph0，修订表在 ph1 前生效 |
| 2 | Curator 是第五个持久角色，还是守护进程组件？ | 组件（仅文档；零代码） | 仅 Glossary 措辞 |
| 3 | `MemoryBundle.root` 返回主机根：项目级写入应落哪里？ | 项目状态目录；主机根只放主机级账本；一次性搬迁现有副本 | **ph8** |
| 4 | Mission View 以 Python reducer 为权威（钩子留在 appender 内），TS 只做追赶？ | 是；记入 CORE_CONCEPTS；本方案不改代码；请 Atlas 会话做一个 Python/TS 共享 golden fixture | 不阻塞 |
| 5 | FLYWHEEL / technical_report / research / contrib 是否迁出仓库；trial 是否退出产品 wheel？ | 本方案不做；LAYOUT.md 标注"不构建不测试不发布"；ph10 后再议 | 不阻塞 |
| 6 | "Manager 是 stage 唯一写者"要驱到 0 还是只做棘轮？ | 棘轮钉在当前基线（5 处非 manager 写入点，测试按绑定+引用计 10） | 不阻塞 |
| 7 | 新包名：`pipeline/` 与 `mission_runner/`？ | 是（前者对应 PIPELINE_STATE.json 和 prompt 里的"pipeline stage"；后者对应 `_MissionRunner` 协议） | ph3、ph5（前一天无回复则用默认） |
| 8 | `domains/`→`overlays/`？两种"domain"概念长期都保留吗？ | 只改 docstring；包名等产品决定 | 不阻塞 |
| 9 | 删零 importer 的 `tools/gpu_ownership.py`（541 行）与 `rl_training_health.py`（526 行）？保留 `gpu_lease.py`？ | 删两个、留一个 | ph9 |
| 10 | 接受一个 core 级 atomic_write 作为"合并删重"而非"新增机制"？ | 接受（19 → ≤6） | ph9 |
| 11 | 测试目录统一包约定并镜像每个包？ | 是 | ph10 |
| 12 | ph1 之后，core 的账本 tier（4,186 行）和 config tier（~2k 行）是否作为后续计划抽成 ledger/ 与 config/；761 行论文策略是否移到 VerticalContract 字段后面？ | 是，作为 ph10 之后的后续计划；本方案只在 core docstring 里标出 tier | 不阻塞 |

## 6. 被否决的方案及原因

- **B 严格 31 级全序**：目标树自身就违反自己的顺序（`core/cost_control.py:29` import knobs 把 ledger 放到 config 之上；`daemon/state.py:22` 的 `LifeBudget` 让 state 依赖 runtime）；搬 ~95k 行、约 420 生产 + 520 测试文件；console script 改目标需要在 8 个 pinned venv 里 `pip install -e`，违反 2026-09-05 启动器劫持事故后"维护工作禁止对框架 pip install"的纪律。
- **C bounded context**：搬 ~120k 行；自己的分层规则下自相矛盾（`LettersMixin` 是 `LifeSupervisor` 的基类却被搬到 operator/channels）；对 `life/memory.py`（3,164 行）、`daemon/state.py`（1,677 行）做按行范围拆分，rebase 时无法被 rename 检测；把 mission-view 钩子从 appender 里拆出去是行为变更（`_snapshot.py` 的有界 tail 无法重建长任务），却被当作"搬位置"。
- **D 词汇先行**：对 ~940 处 `life_dir` 和 ~1,000 处框架内 `project_root` 逐处人工判定改名，在最热的包（webapi 401 处、life 143 处）做文件内编辑，rebase 冲突面最大；重命名 49 个 `life.*` 事件、HTTP 路由、Snapshot schema、i18n——为一个词在前端热路径和混合版本守护进程集群上改 wire 格式。
- **A 的错误部分**（已剔除）：把 26 处 `from ..verticals._base import` 提到顶层（31 处是 monkeypatch 目标，会绑定过期名）；`tools/subagent/_core.py` "10 个 importer" 实际为 0；`_acquire_daemon_lock_with_timeout` 去重（是代理不是重复）；改运维手册为 kill-first（交接文档 `:166` 本来就是 kill-first，过期的是记忆笔记）；把不变量测试搬到 `tests/architecture/`（约定要求留在原文件）。
- 其他：从文件系统自动发现 VERTICALS（行为变更，改为测试钉住）；退役 "life"（149 个测试文件 + 运行中守护进程传的 CLI flag，换一句话的清晰度）；删 `builtin_skills/*/argus-*-role.md`（它们是被 `skills/builtins.py:25-32` 种入 Skill 库的文档，删了会改每个角色读到的库）。

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 与论文/Atlas/copilot 额度会话在热文件上 rebase 冲突（`verticals/research/stages.py` 125 commits/30d、`core/knobs.py` 29、`life/memory.py` 30、`apps/_runtime_execute.py` 27） | 每次搬动是独立的零内容 `git mv` 提交，热文件只改一行 import；每阶段一小时内 fetch→执行→测→push；ph6 的 sed 可在冲突后重跑 |
| 进程在被改动的树里懒 import 已搬走的模块（14 个开发树进程；runtime-latest 在 ff 与重启之间） | 每次搬动的同一提交带 alias shim；每个搬动阶段清单含"重启开发树进程或把它们迁到 pinned checkout"；ph6 之后 kill-first 不可省 |
| `sys.modules` 别名 shim 让 `__module__` 报新路径，按字串断言旧路径的测试会红 | 已核：`daemon/config.py`、`handoff.py` 不持久化点分模块路径；仅 1–2 个测试按字串 patch 被 shim 的模块且解析到同一对象；shim 最多活两阶段 |
| 严格相等白名单给其他会话添麻烦（修好边没删行会红；两个 PR 同时缩同一列表会冲突） | 失败信息直接打印要删的那一行；一行一条、排序；这就是棘轮在工作 |
| 搬走被 prompt 引用或 argv 匹配的模块路径（`tools.subagent` 此刻有活任务；`teammate_entry` 在 `daemon/state.py:1306` 被 argv 匹配） | ph0 先落 `test_module_paths_cited_by_prompts_and_skills_resolve` 与 `test_subprocess_reentry_module_paths_stay_importable`；tools/ 公开路径冻结并留永久转发；本方案不搬 team/ 和 tools/subagent/ |
| core 增加 ~2.1k 行，看上去与"小而稳的内核"相反 | 五个都是纯 stdlib/仅依赖 core 的叶模块；先让 core 成为模块级叶子，后续抽 ledger/config（卡 12）时只改 import 目标、不改方向 |
| 冻结二进制 `argus-core` 或其 smoke 脚本被搬动打断（`desktop-tauri/argus_backend.spec:10-11`、`tests/desktop/test_frozen_runtime.py:20-21`、`smoke-trial-release.py:210`） | 与搬动同一提交更新；spec 用 `collect_in_tree_modules` 收集全部模块无需改 hiddenimports；ph0/2/5/7 都跑 `test_frozen_runtime.py` |
| ph8 在守护进程运行时把活战役文件从主机根搬进 `projects/<id>/` | 卡 3 门控；操作者盯守的日子做；沿用 `_lifecycle.py:63-64` 先例双读一个周期；回滚 = ff 回退一版 |
| 大规模 sed 后 ruff isort 规则让 CI 红 | 每阶段清单含 `ruff check --fix --select I` |
| 去重或词汇工作混进搬动阶段、借"搬动"之名改行为 | 行为相关工作只在 ph8/ph9，各有决策卡、命名测试与活项目回放检查；其余阶段的 diff 只有 git mv + import 行 + shim + 测试 |

## 8. 附录

- 工作流原始记录（本机）：理解轮 `~/.claude-yijia/projects/-data-v-boxiuli/b64726fc-874e-4589-ab2d-6d36254bb68c/subagents/workflows/wf_51cc88ed-43a/journal.jsonl`；设计轮 `.../wf_30d02e9a-6d3/journal.jsonl`。
- 结构化数据附件：`docs/audits/architecture-clarity-2026-09-14.data.json`（synthesis、verify、judges 分数、final plan）。
- 相关既有文档：`docs/PRINCIPLES.md`（第 2 条 Core vs Vertical）、`docs/architecture-simplification-plan.md`（MISSION.md 共同上下文与垂直拆库计划——本方案与之兼容）、`docs/simplification-plan.md` 与 `docs/system-audit.md`（行为层面精简，未触及包结构）、`tests/test_architecture_invariants.py`（可执行边界规则的家）。
