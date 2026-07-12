# Argus 全项目架构治理报告

日期：2026-07-12

审阅基线：`ecaafa63fe552bb09d4eadb3a3049ead22454b06`（当时最新 `origin/main`）

工作分支：`copilot/architecture-governance-20260712`

独立 worktree：`/home/argustest/argus-architecture-governance`

## 结论

本次按可追踪清单完成了 **847/847 个纳入范围的一方代码、测试、运行时配置和
UI 源文件审阅**，共 **197,114 行**。所有文件均绑定到基线 commit、Git blob、
批次和审阅证据；本次修改过的文件又完成了 delta review。

这个结论不等于“仓库所有内容都逐行读完”：

- 102 个 Apache-2.0 `Impeccable` vendored 文件不属于一方代码。
- 116 个前端构建输出由源代码、构建命令和 freshness gate 承接，不作为手写源码逐行审阅。
- 2 个 `package-lock.json` 作为生成的依赖解析结果审查其构建/依赖作用，不逐行当作手写代码。
- 93 个普通文档、演示材料和媒体文件不属于一方可执行代码；其中架构、运行契约和治理文档按需作为非权威声明读取。

精确清单见
[`architecture-governance-inventory-2026-07-12.jsonl`](architecture-governance-inventory-2026-07-12.jsonl)。
该文件包含基线 blob、分类、批次、纳入/排除理由、审阅状态和当前 worktree hash。

本次落地 **16 个独立、可验证的完整增量**，修复状态所有权、Reviewer 权威、
持久化并发、daemon handoff、Wiki 写路径、隐式 cwd、能力凭据边界和生成契约问题；
同时把 Python import 强连通分量从 **6 个降到 3 个**。仍有高价值架构问题保留在
明确 backlog 中，没有用大重写或新 wrapper 掩盖。

未执行 commit、push 或 PR。

## 一、范围与清单

### 1.1 审阅批次

| 批次 | 范围 | 文件 | 行数 | 状态 |
| --- | --- | ---: | ---: | --- |
| B1 | Core、Loop、Engineer、Reviewer、Planner 及相关测试/schema | 105 | 28,507 | 105/105 完整 |
| B2 | Apps、CLI、Daemon、LifeSupervisor、Manager、部署与生命周期测试 | 168 | 52,865 | 168/168 完整 |
| B3 | Skill、Wiki、built-in skills、工程治理 Skill 及测试 | 220 | 42,989 | 220/220 完整 |
| B4 | Adapters、agent_cli、Team、Tools、脚本、部署、release 及测试 | 125 | 32,812 | 125/125 完整 |
| B5 | Verticals、regime-jump、研究/量化/benchmark 路径及测试 | 78 | 17,997 | 78/78 完整 |
| B6 | WebAPI、frontend/core、Ink TUI、React Web、配置及测试 | 151 | 21,944 | 151/151 完整 |
| **合计** |  | **847** | **197,114** | **847/847** |

### 1.2 排除和证据范围

| 类型 | 数量 | 处理 |
| --- | ---: | --- |
| Vendored Impeccable v3.9.1 | 102 | 按 Apache-2.0 第三方工具排除；审阅 hook/config/provenance 集成 |
| `frontend/web/dist`、TUI bundle | 116 | 排除生成代码；审阅源、build、package、release/freshness gate |
| npm lockfile | 2 | 审阅 manifest 和解析作用，不作为手写源逐行阅读 |
| 普通文档/媒体 | 93 | 不计入一方代码完成声明；按需读取架构与运行文档 |

基线有 1,160 个 tracked artifact。持久化 inventory 有 1,161 行，因为同时保留了
被替换的基线 hashed Web bundle 路径和本次构建生成的新路径，便于审计 rename。

### 1.3 审阅方法

1. 固定 commit、独立 worktree 和工作树状态。
2. 使用 Git tree 建立文件级 blob/行数/类型清单。
3. 按垂直调用链分批阅读生产代码、测试、schema、配置和运行时 Markdown。
4. 使用 AST 建立 293 个 Python module 的 import graph、fan-in/fan-out 和 SCC。
5. 追踪 CLI/TUI/Web → Manager → LifeSupervisor → SkillLoop → Engineer/Reviewer 的真实调用链。
6. 追踪 backlog、events、usage、cost reservation、checkpoint、lifecycle、Wiki 和 mission view 的写入者。
7. 对兼容层、失效接口和设计迁移查看 Git 历史。
8. 每个修复先确定不变量，再迁移相关入口、删除旧路径并执行针对性验证。
9. 完成后运行全量 Python 测试、前端测试/typecheck/build、生成契约检查和独立 diff review。

## 二、当前架构和权威边界

### 2.1 主调用链

```text
argus-skill / argus
  -> apps/cli/_core.py
  -> Ink TUI / WebAPI / daemon command
  -> apps/_runtime.py
  -> Manager (front door + stage authority)
  -> LifeSupervisor (backlog + budget + scheduling)
  -> SkillLoop
  -> SupervisedEngineer
  -> Reviewer
  -> Planner schedules the next mission
```

### 2.2 状态所有权

| 状态 | 唯一语义所有者 | 持久化事实源 | 非权威投影 |
| --- | --- | --- | --- |
| Pipeline stage | Manager | worktree `research/PIPELINE_STATE.json` | CLI/Web/TUI stage display |
| Mission done/continue/blocked | Reviewer | review event + mission result | Planner/Manager summaries |
| Backlog | Life memory/backlog API | project-state `backlog.jsonl` | snapshot/TUI/Web |
| Settled model spend | UsageLedger | project-state `usage.jsonl` | cost gauge/summary |
| In-flight spend | Cost control | global `cost-control.json` + audit JSONL | snapshot/SLO |
| Continuous mission intent | Daemon state API | project-state `continuous.json` | CLI/Web/TUI |
| Project lifecycle | Lifecycle IO | project-state `lifecycle.json` | observable worktree status |
| Reviewer working memory | Reviewer checkpoint | project-state `checkpoint.json` | Engineer prompt |
| Skill | SkillStore/SkillRouter | project/global skill Markdown + history/archive | role matching summaries |
| Wiki page changes | Reviewer `wiki_ops` → WikiRouter | `.autors/*/wiki` | planner/reviewer context |
| Mission view | event projector | project-state `mission-view.json` | Web/TUI |
| Achievement certification | Reviewer certification event | `research.achievement.certified` | backend/frontend mission view |

### 2.3 依赖方向

目标方向为：

```text
CLI / Web / TUI
  -> application composition and orchestration
  -> core ports and durable state contracts
  <- adapters implement backend/process/storage details
```

本次已经消除三个明确反向依赖 SCC，但剩余 entry/runtime 大 SCC 和
checklist/vertical SCC 说明目标方向尚未完全实现，详见未完成 backlog。

## 三、本次完成的 16 个增量

| 增量 | 根因 | 完整改动与删除路径 | 结果 |
| --- | --- | --- | --- |
| INC-001 | daemon handoff 丢失 `resume_continuous` | 接通 serialize/deserialize；测试非默认值；删除隐式回退默认值 | crash/blue-green handoff 保留连续任务恢复意图 |
| INC-002 | prompt 指导读取 vault 原始 API key | 只暴露 route metadata 和 subprocess loader；删除 vault 路径/JSON key recipe | prompt 不再越过凭据边界 |
| INC-003 | stage gate 默认到 `/tmp/learn-skills` | 使用 `core.paths.skills_global_root()`；删除 host-specific fallback | Skill root 只有一个默认来源 |
| INC-004 | background advisory renderer 存在但 runner 永远传空字符串 | Engineer 前渲染、Reviewer 前重新扫描；删除 empty/stale placeholder | `WAIT_FOR_SUBAGENT` 可发现且 review 不读过期状态 |
| INC-005 | `agent_cli_runner ↔ copilot_acp` 循环 | `InactivitySnapshot` 移到已有 `models.py`；删除 ACP back-import | SCC 6 → 5 |
| INC-006 | supervisor helper 回读 orchestration core 常量 | 常量移入已有 `_constants.py`；删除 `_helpers -> _core` | SCC 5 → 4 |
| INC-007 | Wiki ingestion 用进程 cwd；daemon cwd 为 `/` | 显式传 mission workdir；删除相对 cwd source path | daemon 可发现本项目 refs/LIT matrix |
| INC-008 | Reviewer skill 教直接 `WikiStore.write_page` | 改为结构化 `wiki_ops`；删除 PageCard/write/index/validate recipe | WikiRouter 成为唯一页面写入口 |
| INC-009 | `config.json` 并发 read-modify-write 丢更新 | thread lock + `flock` + unique temp + fsync；删除 unlocked/PID temp path | Manager/Web/daemon 配置不会互相覆盖 |
| INC-010 | checkpoint 原地 `write_text` 可截断唯一工作记忆 | lock + unique temp + fsync + replace；删除 in-place write | 写失败保留上一个完整 checkpoint |
| INC-011 | `SkillStore ↔ skill_prompts` 循环 | role pool 作为显式参数；删除 prompt → store lookup | SCC 4 → 3 |
| INC-012 | event TS freshness 只在 pytest 检查 | TUI/Web build 都执行 generator `--check`；删除 build bypass | 生产构建不能带 stale event types |
| INC-013 | lifecycle CLI 与 daemon 写两个 `lifecycle.json` | worktree 只做 observable inference，MemoryBundle project root 持久化 | CLI 与 daemon 使用同一事实源 |
| INC-014 | backend/frontend 根据完成+metric 伪造 reviewer certification | 删除两端 `refreshAchievement`；清理旧 `derived-*` 认证；只接受显式 event | Reviewer 重新成为认证唯一权威 |
| INC-015 | 去掉 Reviewer 直写后，当轮 source 要到 review 后才 ingest | SkillLoop 在真实 review 前执行幂等 evidence preparation | Reviewer 可用当轮 immutable sources 发 `wiki_ops` |
| INC-016 | lifecycle 统一根后暴露并发覆盖和 session fallback | 锁住完整 RMW；unique temp/fsync；显式 session 失败即 abort | 同一 state root 并发安全且不误写别的项目 |

## 四、重点改进说明

### 4.1 生命周期状态从两个事实源收敛为一个

修改前：

```text
CLI --lifecycle-* -> <worktree>/lifecycle.json
daemon supervisor -> <global>/projects/<fingerprint>/lifecycle.json
```

CLI 可以显示“archive 成功”，但 daemon 永远看不到。现在 CLI 通过
`MemoryBundle` 同时解析：

- `worktree`：仅用于观察 paper/evidence 等真实 artifact。
- `state_root`：唯一 lifecycle sidecar 位置，与 daemon 完全一致。

统一后又补上了完整 read-modify-write 的 thread/process lock，以及显式 session
解析失败时的 fail-closed 行为。

### 4.2 Reviewer 权威从 Python 到 UI 保持一致

修改前 backend 和 frontend 都会把“mission complete + accepted metric”包装成
`reviewer_certified=true`。这不是 Reviewer 发出的项目级认证。

现在：

```text
research.achievement.certified
  -> backend mission view
  -> frontend shared projection
  -> Web/TUI display
```

没有该事件就没有 certification。metric 只用于丰富显式认证的 baseline/best/gain，
不再制造认证。

### 4.3 Wiki 写路径和 evidence 时序闭环

修改前同时存在：

```text
Reviewer prompt -> 直接 WikiStore/PageCard/write_page
Reviewer schema -> wiki_ops -> WikiRouter
```

前者绕过 evidence-verbatim、duplicate judge、版本/tombstone、事件和索引维护。
删除直接写 recipe 后，独立 code review 又发现当轮 source 尚未 ingest。最终路径为：

```text
Engineer writes refs/evidence
  -> deterministic pre-review ingest + scratch lift
  -> Reviewer reads immutable source and emits wiki_ops
  -> post-mission WikiRouter applies proposals
  -> promotion/compaction/cold storage
```

Reviewer 仍不直接写 Wiki；harness 只做无判断的 evidence plumbing。

### 4.4 持久化写入具备真实并发和崩溃语义

本次统一了三个高风险状态写路径：

| 状态 | 修改前 | 修改后 |
| --- | --- | --- |
| `config.json` | unlocked RMW、PID temp | thread lock + flock + unique temp + fsync + replace |
| `checkpoint.json` | 原地 truncate/write | thread lock + flock + unique temp + fsync + replace |
| `lifecycle.json` | unlocked RMW、固定 `.tmp` | thread lock + flock + unique temp + fsync + replace |

测试覆盖了并发进入、跨进程阻塞、replace 失败保留旧文件和 history 不丢失。

### 4.5 依赖图不是“拆文件即解耦”

基线 Python import graph：

```text
293 modules
833 internal import edges
6 SCCs, sizes = [23, 6, 5, 2, 2, 2]
```

当前：

```text
293 modules
833 internal import edges
3 SCCs, sizes = [23, 5, 2]
```

删除的 SCC：

- `agent_cli_runner ↔ copilot_acp`
- `life.supervisor._core/_helpers/mixins`
- `skills.store ↔ skills.skill_prompts`

总 edge 数未下降，说明本次没有把“少几个 cycle”包装成“整体低耦合已完成”。
剩余 23-module entry/runtime SCC、5-module checklist/vertical SCC 和
2-module domain/skill tidy SCC 仍是明确治理对象。

## 五、尚未完成的架构 backlog

这些问题已经有真实调用、测试或运行语义证据，但本次没有为追求漂亮数字而做大爆炸式重写。

### 5.1 高优先级

| ID | 问题 | 证据与影响 | 下一完整增量 |
| --- | --- | --- | --- |
| COST-SUBAGENT-001 | supervised subagent 的 supervisor Codex 调用绕过统一 ledger/reservation | event fold 不进入生产 `usage.jsonl` 计费事实源，预算可能低估 | 让 supervisor 调用经过 run gateway/cost reservation；写幂等 UsageRecord；删除 event-only 计费 |
| REVIEW-COVERAGE-001 | `final_submission_certified` 不检查 full checklist ID coverage | 任意非空 satisfied 列表可机械置认证，Reviewer 截断输出可能误停项目 | 为 checklist 增加稳定 version/ID，只机械核对覆盖；每项质量仍由 Reviewer 判断 |
| MANAGER-INSTANCE-001 | “唯一 Manager”之外仍构造多个 Manager | stage、rollback、vertical resolution 使用不同 backend/session/context | 向 Supervisor 注入 ManagerPort，复用 runtime Manager，删除 ad-hoc constructors |
| SSE-IDEMPOTENCY-001 | streaming Manager 请求断开后继续执行，客户端又 fallback 重发 | 同一 operator message 可重复 transcript、mission、daemon start 和模型成本 | 增加 request ID/receipt，或只重试确认未 accept 的请求；删除 blind fallback |
| WORKSPACE-SSOT-001 | daemon execution 与 artifact/git-diff 使用不同 workspace | 代码在一个目录运行，UI 从另一个目录展示 artifact | 建立单一 workspace resolver，限制 live `launch_cwd` 变更 |
| ENTRY-SCC-001 | 23-module entry/runtime SCC | apps、CLI、daemon、Telegram、Manager、doctor、WebAPI 互相回引 | 先拆 launcher/admin 和 daemon-state seam；每步删除原 back-import |

### 5.2 中优先级

| ID | 问题 | 下一完整增量 |
| --- | --- | --- |
| EVENT-CATALOG-001 | EventType、payload schema、signal retention、runtime literals 多事实源 | 从单一 schema registry 生成名称、类型和 retention metadata |
| AUTH-READS-001 | 配 token 后敏感 GET 仍公开，TUI snapshot 不带 auth | 统一 sensitive read 分类和客户端 auth header |
| CHECKLIST-SCC-001 | checklist store/stage checklist/vertical selector/base/data-domain 五模块 SCC | 抽取中立 checklist contract/type，逐条删除 reciprocal import |
| USAGE-MIGRATION-001 | usage summary/read 会迁移和改写 ledger | 把 migration/reconcile 移到显式 startup/maintenance，删除 read-side mutation |
| REVIEW-PAYLOAD-001 | `verification_summary` model/event 有，Reviewer schema/parser 无 | 选择完整接通或完整删除 |
| VALIDATION-TAXONOMY-001 | Python policy 4 类、runtime paper skill 12 类 | 选定 Python 唯一 taxonomy，并生成/裁剪 prompt |
| WIKI-DEDUPE-001 | 只看 body 前 200 字去重，会吞 Reviewer 修正版 | full-content hash + entity/op last-write-wins |
| VENUE-CACHE-001 | 损坏 venue cache 因“文件存在”永久禁止重试 | validity-based cache + quarantine |
| VENUE-ROUNDTRIP-001 | no-page-limit `None` 无法 from_dict round-trip | 区分 missing key 与 explicit null |
| QUANT-PROTECTED-001 | Quant 的 protected IDs 未接入通用 integrity floor | vertical contract 暴露 protected IDs，删除 research hardcode |
| LAYOUT-PROMPT-001 | deterministic 允许 2 个宽图，vision prompt 仍说 1 个 | 从常量生成 prompt，明确使旧 hash stale |
| TEAM-SPAWN-001 | manual CLI 与 Curator 重复 teammate spawn | 提取一个进程/roster primitive，删除两份 argv/Popen 逻辑 |
| UI-CLIENT-DUP-001 | Web/TUI 重复 API、SSE、event renderer | 逐步迁入 `frontend/core` 并删除本地 copies |
| UI-EFFORT-001 | backend `reasoning_effort` 与 TUI `effort` 字段漂移 | 直接消费 frontend/core canonical type，删除 TUI alias |
| UI-SEEN-001 | Web 只裁 events，不裁 `seen` ID set | 按保留窗口重建/prune set |
| GENERATED-DIFF-001 | tracked build 输出污染 operator git-diff | build-at-package 或在 UI diff 明确过滤 generated paths |

### 5.3 低优先级但已确认

| ID | 问题 |
| --- | --- |
| CRITIC-COMPAT-001 | L3 critic 已退役，scripted backend/follow/tests 仍保留 critic 专用路径 |
| DOMAIN-TIDY-CYCLE-001 | `domain_tidy ↔ skill_tidy` 两模块循环 |
| VERTICAL-TYPE-001 | `load_vertical()` 标注 `ModuleType`，实际可返回 DataDomain |
| REVIEW-TOOLS-001 | 三个 research review 工具重复 IO/model/history helper 且私有跨模块 import |
| DEAD-UI-001 | 十个 UI module runtime-unreachable，另有 tested-but-unwired gauge/banner |

## 六、复杂度和后续开发成本变化

| 维度 | 修改前 | 修改后 |
| --- | ---: | ---: |
| Python SCC 数 | 6 | 3 |
| SCC sizes | 23, 6, 5, 2, 2, 2 | 23, 5, 2 |
| Lifecycle 持久化事实源 | 2 | 1 |
| Achievement certification 生产路径 | backend derived + frontend derived + explicit event | explicit event only |
| Reviewer Wiki page 写路径 | direct WikiStore + WikiRouter | WikiRouter only |
| Default Skill root | canonical root + `/tmp/learn-skills` | canonical root only |
| API credential prompt path | raw vault JSON recipe + route metadata | safe route metadata/loader only |
| Background advisory integration | renderer存在但真实 prompt 固定为空 | Engineer/Reviewer 同一 advisory |
| Operator config RMW | unlocked | thread/process locked |
| Checkpoint/lifecycle file update | in-place/fixed temp | unique fsynced atomic replace |
| Python tests | 2,946 | 2,958 |
| TUI tests | 115 | 116 |

没有宣称 import edge 数下降：它仍是 833。大型模块仍存在：

- `tools/subagent/_core.py`：2,406 行
- `engineer/runner.py`：约 2,095 行
- `verticals/research/academic_language_review.py`：2,004 行
- `webapi/server.py`：1,801 行
- `verticals/research/paper_layout_review.py`：约 1,704 行

大文件本身不是拆分理由；只有职责和调用证据闭环后才继续拆。

## 七、验证结果

| 验证 | 结果 |
| --- | --- |
| 基线 Python suite | 2,946 collected；全量通过 |
| 当前 Python suite | 2,958 collected；全量通过；最终 wall time 69.00s |
| TUI | 116/116，通过；TypeScript typecheck 通过；production build 通过 |
| Web | 43/43，通过；TypeScript typecheck 通过；production build 通过 |
| Event payload generator | `--check` 通过，并成为 TUI/Web build 前置门 |
| Release manifest | `--check` 通过；当前 release ID `0.1.0+07573927b41dcef9` |
| Changed-file Ruff | 通过 |
| Changed-file diff whitespace | 通过 |
| 14 个独立修改模块的 targeted mypy | 通过 |
| 独立 diff review | 四轮发现的问题均已修复并完成对应回归验证 |

仓库级静态检查仍有既有基线债务：

- 全仓 Ruff：52 个既有错误，changed-file Ruff 为 clean。
- 全仓 mypy：4 个文件中的 15 个既有/环境错误，主要是外部
  `agent_cli`、`finance_argus`、`qlib`、`pandas` stub 和 Python-version NumPy typing；
  本次没有顺手修改这些无关问题。

## 八、改动文件

本次实现和生成物涉及 40 个路径，主要分为：

- Core/状态：`knob_store.py`、`mission_view.py`、`project_lifecycle_io.py`、
  `checkpoint.py`、daemon config。
- Orchestration：CLI lifecycle、SkillLoop、Engineer runner、supervisor constants/helpers。
- Skill/Wiki：Wiki auto hooks、Reviewer curator skill、Skill prompt/store boundary。
- Backend：agent CLI shared model/ACP dependency。
- UI：shared mission projection、TUI regression、TUI/Web build scripts。
- Tests：并发、崩溃、authority、handoff、source timing 和生成契约回归。
- Generated：release manifest/TS、TUI bundle、Web hashed bundle rename。

另外新增本报告和机器可读 inventory。没有修改原 checkout
`/home/argustest/argus-skill` 中用户已有的 `UX_OVERHAUL.md` 删除。

## 九、当前状态

- 所有纳入一方清单的文件已在固定基线上完成审阅。
- 本次修改过的源文件已完成 delta review。
- 16 个增量已实现并验证。
- 高价值未完成问题已保留精确证据和下一完整增量，不伪装为完成。
- 没有 commit、push 或 PR；等待 operator 审阅本报告后再决定后续动作。

Inventory SHA-256：`cde4136b5b9c7dc10e4cf882aa654cabf23e296f11dd577d4e418b107b84cb46`
