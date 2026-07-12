# Argus 全项目架构治理报告 v2

日期：2026-07-12

审阅基线：`ecaafa63fe552bb09d4eadb3a3049ead22454b06`（当时最新 `origin/main`）

最终发布基底：`4156770`（完成前重新 fetch/rebase 的最新 `origin/main`）

工作分支：`copilot/architecture-governance-20260712`

独立 worktree：`/home/argustest/argus-architecture-governance`

上一版报告：
[`full-project-architecture-governance-2026-07-12.md`](full-project-architecture-governance-2026-07-12.md)

## 结论

本次治理在固定基线上完成了 **847/847 个纳入范围的一方文件、197,114 行**
全量审阅；随后对全部修改路径和新增模块完成 delta review。最终一方审阅范围为
**850 个当前文件、199,951 行**。相对固定基线新增的一方文件是中立
source-writeback 模块，以及 rebase 后主线新增的 runner-error contract 和
PendingReply UI；三者均完成 delta review。

本轮在 v1 的 16 个完整增量上继续完成 10 个增量，累计 **26 个**：

- 用户指定的 4 个低风险问题全部完成。
- `DOMAIN-TIDY-CYCLE-001` 次低风险问题完成。
- 实际 sdist 构建发现并修复 Web bundle 发布缺口。
- 独立 diff review 发现并修复通用 `--status` 生命周期事实源漏接。
- 发布前独立 review 再发现并修复 achievement 无生产 emitter、sessionless Wiki
  provenance 为 `unknown`、TOML evidence 被渲染层误拦三个缺口。

最终 Python import 强连通分量由基线 **6 个**降至 **2 个**；
`domain_tidy ↔ skill_tidy` 循环已删除。剩余 SCC 为 entry/runtime 大环和
checklist/vertical 环，没有包装成“整体解耦完成”。

所有最终测试均针对本 worktree 的代码执行：

- Python：**2,997/2,997**。
- Web：**46/46**，typecheck 和 production build 通过。
- TUI：**117/117**，typecheck 和 production build 通过。
- sdist → wheel 真实构建通过；隔离安装后可导入新增模块，Web/TUI 生产资源存在。

精确清单见
[`architecture-governance-inventory-2026-07-12-v2.jsonl`](architecture-governance-inventory-2026-07-12-v2.jsonl)。

## 一、范围与可追踪清单

### 1.1 固定基线审阅

| 批次 | 范围 | 基线文件 | 基线行数 | 状态 |
| --- | --- | ---: | ---: | --- |
| B1 | Core、Loop、Engineer、Reviewer、Planner 及相关测试/schema | 105 | 28,507 | 105/105 完整 |
| B2 | Apps、CLI、Daemon、LifeSupervisor、Manager、部署与生命周期测试 | 168 | 52,865 | 168/168 完整 |
| B3 | Skill、Wiki、built-in skills、工程治理 Skill 及测试 | 220 | 42,989 | 220/220 完整 |
| B4 | Adapters、agent_cli、Team、Tools、脚本、部署、release 及测试 | 125 | 32,812 | 125/125 完整 |
| B5 | Verticals、regime-jump、研究/量化/benchmark 路径及测试 | 78 | 17,997 | 78/78 完整 |
| B6 | WebAPI、frontend/core、Ink TUI、React Web、配置及测试 | 151 | 21,944 | 151/151 完整 |
| **合计** |  | **847** | **197,114** | **847/847** |

### 1.2 最终工作树审阅

| 批次 | 当前文件 | 当前行数 | 变化 |
| --- | ---: | ---: | --- |
| B1 | 106 | 29,114 | 含主线新增 `core/runner_errors.py` 的 delta review |
| B2 | 169 | 53,833 | 新增 `manager/source_writeback.py` |
| B3 | 220 | 43,012 | 修改路径均完成 delta review |
| B4 | 125 | 33,066 | 修改路径均完成 delta review |
| B5 | 78 | 18,008 | 修改路径均完成 delta review |
| B6 | 152 | 22,918 | 含主线新增 `PendingReplyDialog.tsx` 的 delta review |
| **合计** | **850** | **199,951** | **全部当前一方文件有审阅证据** |

v2 inventory 有 1,166 行，是基线与最终工作树的审计并集：

- 1,052 个路径与基线相同。
- 104 个路径相对基线修改。
- 4 个路径删除：三个旧 Web 生成资源和 operator 指定的 `UX_OVERHAUL.md`。
- 6 个路径新增：三个当前一方文件和三个最终 Web 生成资源。

治理报告和 inventory 本身是审计元数据，不递归纳入自身清单。

### 1.3 排除边界

| 类型 | 基线数量 | 处理 |
| --- | ---: | --- |
| Vendored Impeccable v3.9.1 | 102 | 排除第三方源码；审阅 hook/config/provenance 集成 |
| Web/TUI 生成输出 | 116 个当前文件 | 排除手写源码审阅；验证源、build、package、hash 引用和 sdist/wheel |
| npm lockfile | 2 | 审阅依赖解析作用，不逐行当作手写源码 |
| 普通文档/媒体 | 93 个基线路径 | 不计入一方代码完成声明；按需读取架构契约 |

`UX_OVERHAUL.md` 属于 operator 已有删除，本轮按明确指令纳入同一 PR；没有把它伪装成
架构治理代码删除。

## 二、当前权威边界

### 2.1 主调用链

```text
argus-skill / argus
  -> apps/cli/_core.py
  -> Ink TUI / WebAPI / daemon command
  -> apps/_runtime.py
  -> Manager
  -> LifeSupervisor
  -> SkillLoop
  -> SupervisedEngineer
  -> Reviewer
  -> Planner schedules the next mission
```

### 2.2 状态所有权

| 状态 | 唯一语义所有者 | 持久化事实源 | 非权威投影 |
| --- | --- | --- | --- |
| Pipeline stage | Manager | worktree `research/PIPELINE_STATE.json` | CLI/Web/TUI |
| Mission verdict | Reviewer | review event + mission result | Planner/Manager summaries |
| Backlog | Life memory API | project-state `backlog.jsonl` | snapshot/TUI/Web |
| Settled model spend | UsageLedger | project-state `usage.jsonl` | cost gauge |
| In-flight spend | Cost control | global reservation/audit state | snapshot/SLO |
| Continuous intent | Daemon state API | project-state `continuous.json` | CLI/Web/TUI |
| Project lifecycle | Lifecycle IO | project-state `lifecycle.json` | worktree-derived observation |
| Reviewer memory | Reviewer checkpoint | project-state `checkpoint.json` | Engineer prompt |
| Skill | SkillStore/SkillRouter | project/global skill Markdown | matching summaries |
| Wiki mutation | Reviewer `wiki_ops` → WikiRouter | `.autors/*/wiki` | planner/reviewer context |
| Mission view | event projector | project-state `mission-view.json` | Web/TUI |
| Achievement certification | Reviewer 结构化 verdict | `research.achievement.certified` | mission view |

`--status` 现在明确分开两个根：

```text
observable artifacts -> research worktree
persisted lifecycle  -> bundle.project.root
```

CLI、daemon 和 lifecycle admin 命令不再读取不同的 `lifecycle.json`。

## 三、累计完成的 26 个完整增量

| 增量 | 根因 | 完整改动与删除路径 | 结果 |
| --- | --- | --- | --- |
| INC-001 | daemon handoff 丢失 `resume_continuous` | 接通序列化；删除隐式默认回退 | handoff 保留恢复意图 |
| INC-002 | prompt 指导读取 vault 原始 API key | 只暴露 route metadata/loader；删除原始凭据 recipe | prompt 不越过凭据边界 |
| INC-003 | stage gate 默认到 `/tmp/learn-skills` | 使用 canonical skills root；删除 host fallback | Skill root 单一来源 |
| INC-004 | background advisory 永远传空字符串 | Engineer 前渲染、Reviewer 前重扫 | 等待协议可真实使用 |
| INC-005 | `agent_cli_runner ↔ copilot_acp` | shared model 下沉；删除 back-import | 删除一个 SCC |
| INC-006 | supervisor helper 回读 `_core` 常量 | 常量迁入 `_constants.py` | 删除一个 SCC |
| INC-007 | Wiki ingestion 依赖 daemon cwd | 显式传 mission workdir | daemon 可发现项目证据 |
| INC-008 | Reviewer 存在 Wiki 直接写路径 | 只允许结构化 `wiki_ops` | WikiRouter 成为唯一写入口 |
| INC-009 | `config.json` 并发 RMW 丢更新 | thread/process lock + atomic fsync replace | 配置写入并发安全 |
| INC-010 | checkpoint 原地写可能截断 | unique temp + fsync + replace | 失败保留旧 checkpoint |
| INC-011 | `SkillStore ↔ skill_prompts` | role pool 显式注入 | 删除一个 SCC |
| INC-012 | event TS freshness 只在 pytest 检查 | TUI/Web build 前强制 `--check` | 生产构建不能带 stale types |
| INC-013 | CLI 与 daemon 写两个 lifecycle sidecar | 持久化统一到 project state | lifecycle 事实源收敛 |
| INC-014 | backend/frontend 派生 reviewer certification | 删除两端派生路径 | 只接受 Reviewer 显式事件 |
| INC-015 | 当轮 Wiki source 在 review 后才 ingest | review 前幂等 evidence preparation | Reviewer 可引用当轮 immutable source |
| INC-016 | lifecycle 统一根后暴露并发覆盖 | 完整 RMW lock + atomic replace + fail-closed session | 不覆盖、不误写项目 |
| INC-017 | `load_vertical()` 类型谎称只返回 `ModuleType` | 引入 `VerticalDefinition`，传播到 accessors/regime-jump；删除 return ignore | 类型契约包含 `DataDomain` |
| INC-018 | `VenueProfile.from_dict()` 把显式 `null` 当缺失、可选整数当字符串 | 改为声明类型驱动 coercion；增加 Frontiers round-trip | 无页数 venue 可无损往返 |
| INC-019 | deterministic layout 允许 2 个宽图，vision prompt 写死 1 个 | 两处 prompt 复用 `MAX_BODY_WIDE_FIGURES` | 机器事实与 reviewer 指导一致 |
| INC-020 | Web 只裁 events，不裁 dedupe `seen` | seed/push 同步限制 events 和 key set | 长 session 内存有界 |
| INC-021 | `domain_tidy ↔ skill_tidy` | 提取 `manager/source_writeback.py`；迁移所有调用；删除反向 import 和旧导出 | SCC 3 → 2 |
| INC-022 | sdist 忽略 Web `dist/`，最终 hash bundle 未跟踪，release identity 陈旧 | sdist artifact 显式纳入；强制跟踪最终 hash；按最终索引重建 identity/bundle | clean sdist 可重建可运行 wheel |
| INC-023 | 通用 `--status` 仍从 worktree 读 persisted lifecycle | helper 显式接收 worktree/state root；增加 CLI 集成回归 | CLI 与 daemon 展示同一状态 |
| INC-024 | `research.achievement.certified` 只有投影和测试，无生产权威 emitter | Reviewer schema/parser 增加可选结构化 achievement；仅 `done` verdict 由 SkillLoop 发唯一认证事件 | 认证重新由 Reviewer 事实源驱动 |
| INC-025 | 无 `session_id` 的 Wiki hook 把 provenance 写成 `unknown` | 每次 SkillLoop run 生成稳定且唯一的 run ID，并在 review 前后复用 | sessionless 来源可追踪且不会跨 run 混同 |
| INC-026 | artifact allowlist 接受 Reviewer 声明的 TOML，但共享渲染层拒绝读取 | 把 `.toml` 纳入统一可渲染文本后缀并补 API 回归 | `pyproject.toml` 等审阅证据可从 Web/TUI 打开 |

## 四、本轮五个指定问题的成本下降

### 4.1 Vertical 类型契约

修改前，运行时可返回 `DataDomain`，类型系统却只允许 `ModuleType`，靠
`type: ignore[return-value]` 掩盖。现在 resolver、accessor 和 regime-jump 参数共享
`VerticalDefinition`，新增 data-domain 调用方无需继续复制 ignore。

### 4.2 Venue round-trip

修改前的 coercion 从默认值猜类型；默认值为 `None` 时，`main_text_word_limit=12000`
会变成字符串，显式 page `null` 会被当成字段缺失。现在从 dataclass 声明类型读取
`bool/int/tuple/str/nullable`，Frontiers 内置 profile 可完整 JSON round-trip。

### 4.3 Layout prompt 唯一事实源

宽图上限不再同时存在常量 `2` 和 prompt prose `1`。变更会主动使旧 prompt hash
失效，避免继续复用与当前 policy 不一致的 review artifact。

### 4.4 Web stream 有界状态

`events` 和 `seen` 现在共享同一个 2,000 条保留窗口。被裁掉的旧事件 key 可再次进入，
但不会让 dedupe set 随 7×24 session 无限增长。

### 4.5 Tidy source-writeback 边界

修改前：

```text
skill_tidy -> domain_tidy
domain_tidy -> skill_tidy._atomic_write/commit_to_source
```

修改后：

```text
skill_tidy  -> source_writeback
domain_tidy -> source_writeback
skill_tidy  -> domain_tidy (单向 orchestration)
```

共享模块只拥有 source-tree atomic write、source root 和 opt-in commit，不吸收 placement
或 domain 判断，因此不是为了消环制造的万能 wrapper。

## 五、依赖图变化

按“Python 文件为 module、仅记录精确命中的静态 import target、不把 package ancestor
推断成依赖”的 AST 口径：

```text
基线: 293 modules, 799 internal edges, 6 SCCs [22, 6, 5, 2, 2, 2]
最终: 295 modules, 805 internal edges, 2 SCCs [22, 5]
```

边增加 6 条，因为新增中立模块、主线 runner-error contract 和调用方显式依赖；
这不是耦合下降指标。可验证的下降是：

- 删除 `agent_cli_runner ↔ copilot_acp`。
- 删除 supervisor core/helper/mixin 环。
- 删除 `skills.store ↔ skills.skill_prompts`。
- 删除 `manager.domain_tidy ↔ manager.skill_tidy`。

剩余：

- 22-module entry/runtime SCC。
- 5-module checklist/vertical SCC。

## 六、剩余架构 backlog

### 6.1 高优先级

| ID | 问题 | 下一完整增量 |
| --- | --- | --- |
| COST-SUBAGENT-001 | supervised subagent supervisor 调用绕过统一 ledger/reservation | 经过 run gateway 写幂等 UsageRecord，删除 event-only 计费 |
| REVIEW-COVERAGE-001 | final certification 不机械核对 checklist ID 完整覆盖 | 增加稳定 checklist version/ID；质量仍由 Reviewer 判断 |
| MANAGER-INSTANCE-001 | stage/rollback/vertical resolution 构造多个 Manager | 注入 ManagerPort，删除 ad-hoc constructors |
| SSE-IDEMPOTENCY-001 | stream 断开后 blind fallback 可重复执行 operator message | request ID/receipt，只重试未 accept 请求 |
| WORKSPACE-SSOT-001 | daemon execution 与 artifact/git-diff workspace 不同 | 建立单一 workspace resolver |
| ENTRY-SCC-001 | 23-module entry/runtime SCC | 先拆 launcher/admin 和 daemon-state seam |

### 6.2 中优先级

| ID | 问题 | 下一完整增量 |
| --- | --- | --- |
| EVENT-CATALOG-001 | EventType、schema、retention、runtime literals 多事实源 | 从单一 registry 生成 |
| AUTH-READS-001 | 配 token 后敏感 GET 仍公开，TUI snapshot 不带 auth | 统一敏感读分类和 header |
| CHECKLIST-SCC-001 | checklist/vertical 五模块 SCC | 抽取中立 checklist contract |
| USAGE-MIGRATION-001 | usage read 会迁移并改写 ledger | 移到显式 startup/maintenance |
| REVIEW-PAYLOAD-001 | `verification_summary` model/event 有，Reviewer schema/parser 无 | 完整接通或完整删除 |
| VALIDATION-TAXONOMY-001 | Python policy 4 类、runtime skill 12 类 | 选定唯一 taxonomy 并生成 prompt |
| WIKI-DEDUPE-001 | body 前 200 字去重会吞修正版 | full-content hash + entity/op LWW |
| VENUE-CACHE-001 | 损坏 cache 因文件存在永久禁止重试 | validity cache + quarantine |
| QUANT-PROTECTED-001 | Quant protected IDs 未接通通用 integrity floor | vertical contract 暴露 IDs |
| TEAM-SPAWN-001 | CLI 与 Curator 重复 teammate spawn | 提取单一 process/roster primitive |
| UI-CLIENT-DUP-001 | Web/TUI 重复 API、SSE、renderer | 逐步迁入 frontend/core |
| UI-EFFORT-001 | backend `reasoning_effort` 与 TUI `effort` 漂移 | 统一 canonical wire type |
| GENERATED-DIFF-001 | tracked build 输出污染 operator diff | build-at-package 或 UI 明确过滤 |

### 6.3 低优先级但已确认

| ID | 问题 |
| --- | --- |
| CRITIC-COMPAT-001 | L3 critic 已退役，scripted backend/follow/tests 仍有兼容路径 |
| REVIEW-TOOLS-001 | 三个 research review 工具重复 IO/model/history helper |
| DEAD-UI-001 | 十个 UI module runtime-unreachable，另有 tested-but-unwired surface |

## 七、最终验证

| 验证 | 最终结果 |
| --- | --- |
| 指定五项 targeted Python | 118/118 |
| Lifecycle SSOT targeted | 68/68 |
| 发布前三个缺口 targeted Python | 52/52 |
| 全量 Python | **2,997/2,997**，变基后最终树 41.62s |
| Web | **46/46**；typecheck、production build 通过 |
| TUI | **117/117**；typecheck、production build 通过 |
| Event payload generator | `--check` 通过 |
| Release manifest | `--check` 通过；`0.1.0+a4d78c313b790385` |
| Narrow mypy | 7 个新增/变更架构 seam 无错误 |
| Changed-file Ruff | 通过 |
| Diff whitespace | 通过 |
| Python package | sdist 和由 sdist 重建的 wheel 通过 |
| Installed wheel | 隔离 cwd/import 解析到 site-packages；Web/TUI 资源存在 |
| Independent diff review | 早期轮发现 3 个发布/SSOT 问题，发布前轮再发现 3 个缺口；全部修复后最终独立 agent 无高置信 finding |

一次最初的全量 `pytest` 调用了环境中的 editable
`/home/argustest/argus-skill`，产生 26 个无效 collection error；该次运行未被计为验证。
变基后最终全量测试显式使用
`PYTHONPATH=/home/argustest/argus-architecture-governance`，并打印确认 import 来自本 worktree。

全仓 Ruff/mypy 的既有基线债务没有在本轮顺手修复；本报告只声明 changed-file Ruff 和
7 个本轮架构 seam 的 narrow mypy 结果。对发布前四个 Python 文件的附加 narrow mypy
没有计为通过：它只报告 `loop.py` 6 个既有错误（两处 optional model 返回值、四处
untyped usage dict 加法），`git blame` 和基线 diff 均确认不在本轮新增行。

## 八、交付状态

- v1 报告与清单保留，v2 作为最终治理快照。
- 所有新增和修改源文件均完成 delta review。
- 用户指定的低风险、次低风险问题全部闭环。
- `UX_OVERHAUL.md` 的 operator 删除已纳入同一变更集。
- 分支已在最终验证前变基到 `origin/main@4156770`。
- 最终 commit、push 和 PR 信息由该分支的 Git/GitHub 元数据承载。

Inventory SHA-256：
`a0add0f24c6f10bb4b2fcdaece3e5d39ee9c60e66c1ee850e41fb6dcf4210773`
