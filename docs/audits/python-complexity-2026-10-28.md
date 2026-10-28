# Python 运行时复杂度审计（2026-10-28）

基线：`origin/dev` 的 `83f0a0285`。目的：量出 `argus/` 里哪些代码没人用、哪些实现重复、哪些状态被存了两遍，
按“能删多少行 × 风险”排出删除顺序。第一批安全删除已经做了（见第 9 节）；更大的结构性删减需要维护者拍板（第 10 节）。

方向参照 `feat/ts-core` 的 README：四个角色，每次交接是一封自由文本的信，Argus 不反思、不打分、不整理，
也从不判定任务成功与否。凡是 Python 里为“宿主替角色判断”而存在的代码，都是要缩的对象；
凡是 TS 内核（L7）最终会整块替掉的 Python 内核代码，按 ROADMAP L6 的规矩不再打磨，只删不修。

## 1. 方法

| 工具 | 做了什么 | 局限 |
| --- | --- | --- |
| AST 可达性脚本（`/tmp` 下临时脚本，未入库） | 以每个 `argus` 模块的模块级代码、`pyproject` 入口点、非 Python 文件里出现的 `argus.x.y` 路径、带装饰器的定义、`getattr` 用到的名字为根，沿 `import`、`模块.名字`、同模块名字引用做传递闭包；不可达且只被 `tests/` 引用的记为“仅测试使用” | 按名字匹配，偏保守：同名属性会把死代码误判为活的，所以真实死代码只会比表里多 |
| vulture 2.x，置信度 ≥ 60 | 分别在“只看 `argus`”和“`argus` + `tests`”两种范围跑，对比得出“只有测试用的函数/方法” | 类方法的重写（`HTMLParser.handle_*`、`do_POST`、Harbor 回调）是误报，已人工剔除 |
| 精确重复检测 | 函数体去掉 docstring 后做 AST 规范化，参数和函数体完全相同的归为一组 | 只抓字面相同；“几乎相同”的（原子写）另做人工比对 |
| `git grep` | 对每个候选在全仓（含 `frontend/`、`packages/`、`plugins/`、Skill markdown、`deploy/`）再查一遍名字 | — |

## 2. 各包规模（基线）

| 包 | 文件数 | 行数 | 说明 |
| --- | ---: | ---: | --- |
| core | 112 | 32,121 | 其中 `mission_view/` 3,957 行是从事件推出来的读模型 |
| life | 65 | 30,776 | `memory.py` 一个文件 3,700 多行 |
| webapi | 82 | 23,346 | 路由 + 一层没有命名的服务模块 |
| trial | 50 | 14,933 | 托管试用；LAYOUT 记录“产品本身只用到其中约 4 个文件” |
| verticals | 43 | 14,420 | 含复制进项目的绘图/测试脚本 |
| manager | 35 | 11,397 | |
| apps | 28 | 11,390 | `apps/cli/_core.py` 一个文件 import 了 76 个模块 |
| daemon | 15 | 7,140 | |
| agent_cli | 17 | 5,516 | |
| engineer | 16 | 5,258 | |
| skills | 16 | 4,816 | |
| adapters | 13 | 3,955 | |
| tools | 12 | 3,646 | |
| 其余 14 个包 | 76 | 10,718 | roles 2,779、maintenance 1,705、domains 1,545、reviewer 1,508、wiki 1,479 等 |
| 包根模块 | 5 | 1,098 | `loop.py`、`release.py`、`desktop_backend_entry.py` 等 |
| 合计 | 597 | 183,941 | `tests/` 另有 122,410 行 |

全仓 `try:` 2,531 处，宽泛的 `except Exception` / 裸 `except` 692 处（AST 计数，含不在架构测试统计范围内的文件）。

## 3. 依赖图

592 个 `argus` 模块之间有 2,489 条 import 边（不算对包 `__init__` 的边），其中 1,472 条跨包。
被 import 最多的模块：`core/event_catalog` 71、`core/paths` 62、`core/secret_guard` 55、`life/memory` 54、
`core/knobs` 50、`skills/vertical_select` 49、`core/models` 47。import 别的模块最多的：`apps/cli/_core` 76、
`life/supervisor/_core` 55、`webapi/server` 51。跨包最多的方向是 `life → core` 215 条、`webapi → core` 133 条、
`manager → core` 104 条，以及 `webapi → life` 36 条、`webapi → daemon` 26 条（交付层直接伸进运行时）。

**生产代码里没有任何模块 import 的模块（46 个，9,820 行）**，逐个核对后几乎都有正当入口：

| 类别 | 个数 | 例子 |
| --- | ---: | --- |
| 命令行 / 控制台脚本入口（`__main__`、`pyproject` scripts、Dockerfile） | 14 | `trial/compute.py`、`trial/web_admin.py`、`release_tools/*`、`plugin/mcp_server.py` |
| 由注册表按名字动态加载的领域模块 | 4 | `verticals/software/stages.py`、`verticals/argus_maintenance/stages.py` |
| 复制进项目、给角色运行的脚本和测试模板 | 13 | `figure_spec_scripts/*`、`spec_test_templates/*` |
| `argus-verticals` 社区包依赖的桥接模块 | 3 | `verticals/metric_evidence.py`、`optimization_base.py`、`path_evidence.py` |
| 别的语言调用的 | 3 | `webapi/read_bridge.py`（`packages/api`）、`agent_cli/process_guard.py`（`packages/runtime`） |
| 只有测试用的 | 1 | `adapters/memory_backend.py`（测试替身，120 行；生产代码从不构造它） |
| 只剩一半在用的 | 1 | `webapi/map_teaching_review.py`（417 行里只有 25 行还被 import，见第 9 节） |

结论：**模块级的死代码几乎已经清干净了**（之前的 RL-4 等清理做过）。剩下的死代码在函数级。

## 4. 死代码（函数 / 类 / 常量级）

| 口径 | 个数 | 行数 |
| --- | ---: | ---: |
| 顶层定义总数 | 7,156 | — |
| 不可达（宽口径脚本） | 147 | 1,511 |
| 其中没有任何测试引用 | — | 666 |
| 其中只有测试引用 | — | 845 |
| 类方法里生产代码从不调用的（vulture，人工剔除重写后） | 约 30 | 约 350 |

剔除误报（领域模块通过 `getattr` 读的 `CHECKLIST_ITEMS`/`role_banner`、复制进项目的脚本、
测试拿来造数据的 setter 如 `write_persisted`、`commit_revision`、`record_consent`）之后，
真正可以删的大约 1,200 行生产代码加上只测它们的测试。**“删掉几千行死代码”这个目标，证据不支持**：
这棵树的大头不是没人调用的代码，而是有人调用、但按 TS 内核的设计不该存在的机制（第 10 节）。

最大的几块（第一批已删，详见第 9 节）：

| 位置 | 行数 | 情况 |
| --- | ---: | --- |
| `webapi/map_teaching_review.py` 的 `review_concepts` 及其 schema、上下文投影、限额 | 约 390 | 地图教学复核的整条模型调用链，生产代码里没有调用者；只剩 `MARKDOWN_TEACHING_CORE` 和两个 schema 小函数还被两个 reader prompt 用 |
| `manager/control_state.py` `consume_authorization` | 59 | 授权只经 repair capability 被领取，这条消费路径没人走 |
| `tools/capability_vault.py` `save_model_api_grant` | 48 | 只有测试用来写 vault |
| `life/event_log.py` `iter_call_events` | 38 | 读它的 `tools/event_log_query.py` 早已删除 |
| `webapi/manager_dispatch.py` `manager_execution_handoff` | 30 | 无调用者 |
| `core/session.py` `touch_session` | 31 | 只有测试用 |
| `skills/builtins.py` 两个“Compatibility wrapper”和两个旧 seed 函数 | 约 50 | 测试在用，运行时用的是 `*_context` 版本 |
| `core/windows_job.py` `durable_windows_creationflags` | 25 | 无调用者（连同只给它用的两个常量和三个 kernel32 原型） |

暂不删、留给后续批次的（理由都是“删了会改变某种对外表现”或“和开着的 PR 冲突”）：

| 位置 | 行数 | 为什么这批不动 |
| --- | ---: | --- |
| `trial/training_capture.py` `PI_EXTENSION_SOURCE` | 122 | 文档说是“给现有集成保留的旧采集 profile”，可能有仓外使用者，要维护者确认 |
| `apps/_inbox_protocol.py` `pending_inbox_status`、`migrate_legacy_inbox` | 38 | 生产代码不调用，但运行时遇到旧收件箱会抛“需要迁移”，这个迁移函数是唯一出路；应接上而不是删 |
| `release_tools/repository_parity.py` | 60 | 私有镜像的工具，`docs/LAYOUT.md` 以它为私有文件清单的出处 |
| `roles/prompts/manager.py` 两段没人引用的路由提示（`_COMPOSABLE_WORKFLOW_ROUTING`、`_RESEARCH_DELIVERABLE_ROUTING`） | 26 | 和 #373 的改动相邻，等 #373 合并后再删 |
| `life/memory.py` `Backlog.supersede_active_plan`（54 行，连测试都没有）、`EventJournal.total_cost_since`（39） | 93 | #373 改这个文件，避免冲突 |
| `core/acceptance_dependencies.py`、`adapters/agent_cli_backend/_result.py` 里的残留 | 15 | #373 整块删除或改动中 |
| `agent_cli/_sandbox_commands.py` `reset_unqualified_model_warnings` | 3 | `agent_cli` 有别的 PR 在改 |
| `adapters/memory_backend.py` 测试替身 | 120 | 是挪进 `tests/` 而不是删，单独做 |

## 5. 开关和环境变量

`argus/` 里出现 204 个不同的 `ARGUS_*` 名字，`core/knobs.py` 的操作员开关表登记了 57 个。

| 情况 | 个数 | 例子 / 处理 |
| --- | ---: | --- |
| 登记在开关表、驾驶舱可改，但没有任何代码读 | 2 | `ARGUS_SKILL_MAP_BATCH_SIZE`、`ARGUS_SKILL_MAP_RETRY_SECONDS`：用户改了也没效果。第一批已删 |
| 只写不读（设进子进程环境，仓内无人读取） | 4 | `ARGUS_SKILL_ARTIFACT_ROOT`、`ARGUS_SKILL_SESSION_ID`、`ARGUS_SKILL_SESSION_ROOT`、`ARGUS_PLUGIN_PROGRESS_FILE`（后者是给外部插件的约定，保留）。前三个可能有仓外 Skill 读，需确认 |
| 只在一处读、仓内无测试无文档无部署设置（等于永远取默认值） | 约 25 | `ARGUS_SKILL_STREAM_PROGRESS_*`、`ARGUS_SKILL_AGENT_IO_KEEP`/`_MAX_BYTES`、`ARGUS_SKILL_RUNNER_CAPTURE_STDERR_LINES`、`ARGUS_WEB_*_CACHE_TTL`、`ARGUS_SKILL_RESEARCH_TARGET_LEVEL` 等。每个只省几行，但每个都是一条“宿主自己的规则”；建议在 L4/L6 里成批收成常量 |
| 同一件事两套读法 | 3 组 | 正整数环境变量读取有 6 份实现（`agent_cli/_env.py` 两份、`adapters/stream_progress.py`、`adapters/agent_cli_backend/_io_log.py`、`apps/_env.py`、`core/knobs.env_int`）；真值环境变量 3 份（`apps/_env.env_flag`、`daemon/state._truthy_env`、`daemon/handoff._truthy_env`，最后一份第一批已并掉） |

另外 `pyproject.toml` 的 mypy `files` 还列着 2026 年已删除的 `argus/life/router.py`，导致
`python -m argus.release_tools.typecheck_gate` 在 `dev` 上直接报“cannot read file”。第一批顺手删了这一行。

## 6. 事件类型

`EventType` 有 175 个成员。**18 个在 Python 里既没有按符号引用、也没有按字符串值出现**，也就是从来不会被发出：

`life.config.parse_failed`、`accounting.journal_repaired`、`budget.reservation.denied`、`budget.unpriced.blocked`、
`budget.unpriced.acknowledged`、`life.manager.plan_challenge.decided`、`life.manager.feedback.persisted`、
`life.manager.inbox`、`life.{manager,planner,engineer,reviewer}.backend_resolved`（4 个）、
`life.planner.preview_skipped`、`life.planner.verification_probe`、`life.plan.revision.rolled_back`、
`idea.search.skipped`、`wiki.hook.warning`、`manager.live_view.rejected`。

它们在 `packages/contracts`（schema、生成的 TS 类型）和三个前端渲染器里都还有分支和测试，删除要动契约、重新生成类型、
重建前端包。这批没动，放进第 10 节。“发出了但没人消费”的事件无法可靠判定：Web 时间线按通用方式渲染所有事件，
所以不报。

## 7. 重复实现

| 东西 | 份数 | 说明 |
| --- | ---: | --- |
| 字面完全相同的函数 | 20 组，可省 199 行 | 例：`_front_matter` 3 份（reflection、consolidation、routes/wiki）、`_backend_for` 3 份、正整数环境变量 3 份、`_optional_int/_optional_float` 在 `core/usage` 和 `provider_integrations/copilot_usage` 各一份、`last_agent_message` 在 `agent_cli/models` 和 `core/models` 各一份 |
| 原子写（临时文件 + `os.replace`） | 约 30 个命名的 helper，另有约 55 处内联 | 命名 helper 遍布 wiki、daemon、life、manager、skills、verticals、webapi、trial、core；行为互不相同：有的 `fsync` 有的不，有的写 `indent=2` 有的不写，有的 Windows 下重试、有的不。`core/atomic_replace.replace` 本来是共用件，只有 4 个文件在用。ROADMAP L6.2 记的是“16 份”，实际更多 |
| 读 JSON 对象并在坏文件时退回空值 | 至少 10 份 | `core/team_reports._read_json`、`manager/control_state._read_json`、`tools/capability_vault._read_json`、`life/context_packet._read_json_object`、`verticals/research/method_card._load_json` 等，坏文件的处理各不相同（返回 `{}`、`None` 或抛错） |
| 截断文本 | 15 份 | `_clip`/`_truncate`/`_clip_text`，省略号和上限各不相同 |
| 读 JSONL 尾部 | `life/memory.py` 里 6 个变体 + `core/jsonl_reader` 2 个 | |

“几乎相同”的合并会改变细节（是否 `fsync`、临时文件名、JSON 格式），不算“行为不变”，第一批没做；见第 10 节第 3 项。

## 8. 同一件事存了几份

按项目状态目录里的文件统计（子代理逐个查了写入点和读取点，行数是写入加读取的生产代码）：

| 文件 | 能否从 `events.jsonl` / `backlog.jsonl` 推出 | 相关代码行数 |
| --- | --- | ---: |
| `mission-view.json` | 能：就是事件的 reducer，启动时还会重放事件尾部 | 约 3,960（整个 `core/mission_view/`） |
| `usage.jsonl` | 部分：旧记录能从事件重建（`source="legacy.events"`），新记录只在这里 | 约 2,200 |
| `operator_context.jsonl` / `.json` | `.json` 投影完全可从 `.jsonl` 重建；台账本身是独立状态 | 约 1,920 |
| `failure_experiences.jsonl` + `.sqlite3` | 部分：素材来自失败任务的事件，但另做了提炼和索引 | 约 1,640 |
| `PIPELINE_STATE.json` 及 `STATUS.md` 的当前阶段行 | 前者是权威；后者是前者的投影 | 约 1,230 |
| `handoffs/<id>/mission.json`、`latest.json`、`round-*.json` | 部分：`mission.json` 抄了一遍待办条目和计划，`latest.json` 只是指向最新一轮的指针 | 约 600 |
| `planner-failure-backoff.json`、`runtime-failure-circuit.json`、`completion-rejection-circuit.json` | 部分：连续失败次数能从事件数出来，暂停 / 冷却标记不能 | 约 1,000 |
| `manager-planner-feedback.json`、`planner-verdict-outbox.json`、`pending_notified.json`、`stage-certificates.json`、`mission-events.index.jsonl`、`manager-supervision/latest.json` | 部分或完全 | 约 860 |

合计约 15,600 行（`argus/` 的 8.5%）在维护和事件 / 待办并行的第二份状态。其中“完全可推出”的约 4,300 行，
“部分可推出”的约 6,800 行。这正是 ROADMAP L3（每件事只存一处）要处理的，也是 TS 内核从第一天就只有一份状态模型的原因。

## 9. 第一批：已执行（本 PR）

全部是“没人调用”或“兼容层的调用方已经不在”，不改运行时行为。每个提交按领域分开：

| 提交 | 领域 | `argus/` 删 / 加 | `tests/` 删 / 加 |
| --- | --- | ---: | ---: |
| webapi：退役的教学复核、无调用者的命令包装 | webapi | −477 / +2 | −302 / +11（删 1 个测试文件） |
| core：没人调用的 helper | core、adapters | −150 / +0 | −28 / +14 |
| manager / roles / skills / wiki / tools 的无用 helper | 5 个包 | −240 / +7 | −128 / +34 |
| 删除没人 import 的兼容再导出 | daemon、life | −42 / +4 | 0 |
| life / apps / doctor / research 的无调用者 helper | 6 个包 | −158 / +1 | −113 / +3（删 1 个测试文件） |
| 不起作用的开关、包装和一份重复 helper | core、reviewer、daemon、manager | −41 / +4 | −3 / +1 |
| mypy 配置里已删除的文件 | `pyproject.toml` | — | — |

合计：`argus/` −1,108 / +18 行，`tests/` −574 / +63 行，净删约 1,600 行。另有一个只重建发布产物的提交
（`release_manifest.json` 记录各模块大小，`tests/release_tools/test_check_artifacts.py` 要求它和源码一致）。

## 10. 后续批次（需要维护者决定），按“可删行数 × 风险”排序

| 顺序 | 内容 | 预计删除 | 风险 | 需要谁决定什么 |
| ---: | --- | ---: | --- | --- |
| 1 | **托管试用 `trial/` 移出主仓**（独立包或 `argus-hosting`） | 约 14,900 行生产 + 对应测试 | 低（产品主路径不依赖它） | 维护者：试用服务是否还在主仓发版 |
| 2 | **派生状态只留一份**（L3.1）：`mission-view.json` 改为纯内存 reducer 不落盘、`latest.json` 指针删掉、`manager-supervision/latest.json` 删掉、`mission-events.index.jsonl` 删掉、三个熔断文件里能从事件数出的计数改成现算 | 约 1,500–2,500 | 中：读模型的缓存失效路径、Web 首屏速度 | 维护者：L3.4 存储方案（纯文件还是 SQLite）先定 |
| 3 | **一个原子写**（L6.2）：`core` 一个 `write_bytes/write_json`（统一 `fsync`、Windows 重试），替换约 30 个 helper 和 55 处内联 | 约 600–900 | 低到中：临时文件名和 `fsync` 时机变化；JSON 格式要逐个保持 | 维护者：统一 `fsync`（更慢更稳）是否可接受 |
| 4 | **旧数据迁移代码**：47 个名字里带 legacy/migrate/compat 的函数（1,366 行，`core/usage.py` 一家 275 行），加 `argus_skill` 别名包（141 行）和 `argus-skill` 命令 | 约 1,500 | 中：0.1.x 的旧项目打不开 | 先做 L3.6（脱敏旧项目升级测试），再按测试结果删；`argus_skill` 别名已过“保留一个版本”的期限，`9957c427` 在某个本地分支上 |
| 5 | **#373 之后的文字解析残留**：`core/role_reply.py` 的容错解析、`manager/plan_mode.py`、`prompt_rewrite.py` 的 `_extract_text`、各处 `NEXT_OWNER` / footer 读法 | 约 800–1,200 | 中 | 先合并 #373（它已删掉大部分），再量剩下的 |
| 6 | **重叠的守卫和开关并到一个所有者**：约 25 个“只在一处读、永远默认值”的环境变量收成常量；三个熔断器（planner backoff、runtime circuit、completion rejection）和 L4.1 的人设预算合成一个 | 约 600–1,000 | 中 | 维护者：哪些预算 / 熔断由人设、在网页上可见（L4.1） |
| 7 | **从不发出的 18 个事件类型**：Python 枚举、payload schema、TS 生成类型、三个前端渲染器分支和测试一起删 | Python 约 40，契约和前端约 300 | 低 | 只需确认旧日志里出现时前端按“未知事件”显示即可 |
| 8 | **兼容 facade 收口**：`daemon/life_worker.py`、`engineer/runner.py`、`webapi/manager_bridge.py` 里给测试 monkeypatch 用的再导出，测试改为 patch 定义处 | 约 150 | 低 | 无，纯机械；`engineer/runner.py` 等 #373 合并后做 |
| 9 | **宽泛 except 收缩**（L6.3）：按 `docs/simplification-plan.md` 的四条规则，先删“返回成功形状默认值”的 handler；`life` 181、`webapi` 125、`manager` 66 最多 | 净删几百行，主要价值是不再吞错 | 中到高：每一处都可能让原来静默的失败变成可见 | 按包做，每包一个 PR，配故障注入测试 |
| 10 | **Python 内核整体删除**（L7.6）：TS 内核通过回放和标准任务集后，`life/`、`engineer/`、`reviewer/`、`manager/`、`daemon/`、`apps/_runtime*` 等 | 约 100,000 | 高，但这是路线图既定终点 | L7.5 影子运行通过之后 |

第 1–3 项不改变角色看到的任何东西，建议最先做；第 4–6 项会改变旧数据或运行时规则，需要先有对应的测试和决定；
第 10 项之前，按 L6 的规矩，Python 内核只删不打磨。
