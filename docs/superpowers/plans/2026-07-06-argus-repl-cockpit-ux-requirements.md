# Argus REPL/Cockpit 交互体验优化 — 需求文档

> **执行人：** 本文档面向负责实现这块工作的工程师/agent，目的是让其可以独立开工，
> 不需要与作者反复对齐背景。
> **架构师/验收人：** @lbx154（提出需求，负责最终验收）
> **范围：** `argus_skill/manager/repl.py`（cockpit REPL 命令行体验），不含 web
> dashboard、对外展示站点、或 Manager/Planner/Engineer/Reviewer 决策逻辑本身。
>
> **硬性前提：设计/实现这块体验的人，必须亲自使用过 Argus。** 本文档第 3 节的
> 问题清单来自代码走查，只是起点和最低要求，不是终点——不接受"只读代码、照单
> 改字符串就提交"的做法。验收时会核对第 1.1 节列出的亲自使用证据。

## 1. 背景与目标

Argus 的日常操作入口只有一个：cockpit（`argus-skill` 交互式 REPL，
`argus_skill/manager/repl.py`，约 2900 行）。这是 operator 每天接触频率最高的
界面——命令好不好找、状态好不好读、出错时知不知道下一步，直接决定了操作 Argus
的心智负担。

**本次目标不是重新设计交互模型**：自然语言优先、`/xxx` 命令兜底的双通道设计
（`_render_help` 里说的 "one cockpit, one mode"）保持不变。目标是在现有骨架上
补齐四个已经通过代码走查确认存在的具体缺口：

1. 命令可发现性（能做的事 vs 能被用户发现的事严重脱节）
2. `/status` 的信息架构（单一层级堆了太多信息，没有优先级）
3. Live cockpit 实时反馈（已经写好但被隐藏的能力）
4. 错误/提示文案质量（参差不齐，好的样板已经存在）

说到底，这次要解决的是一件很朴素的事：**让用户体验舒服一点**。第 3 节的清单
不是为了凑一份看起来详尽的文档，而是帮忙起步用的参考。

### 1.1 工作方式：必须亲自使用 Argus

**这一条是硬性前提，不是建议。** 设计/实现这块交互体验的人，必须亲自作为一个
真实 operator 把 Argus cockpit 用起来，而不是只读 `repl.py` 源码、对照第 3 节
的清单照本宣科地改字符串。原因很直接：本文档第 3 节的问题是通过静态代码走查
发现的，覆盖不了真实使用中才会暴露的手感问题（节奏、等待感、心理负担、什么
时候会烦躁），而这些恰恰是"体验舒不舒服"的核心。

具体要求：

- 开工前，至少完整走一遍常见 operator 场景：冷启动一个新项目、`/daemon start`、
  用自然语言和 `/xxx` 命令混着下达几个任务、观察一段时间的运行过程、故意打错
  几个命令看提示、遇到至少一次真实报错或卡住的场景、`/status` `/roles`
  `/journal` 都用一遍。
- 过程中记录自己真实的困惑点、等待时的心理感受、哪一刻想"这里要是有 X 就好了"
  ——这些第一手记录的优先级高于第 3 节的清单；如果两者冲突或有出入，以亲自
  使用的真实感受为准，并在交付时说明。
- 改完之后，同样要亲自再用一遍验证"是不是真的舒服了"，而不是只看单元测试
  是否通过。
- 交付物需附上这份亲自使用记录（简短的笔记即可，不需要正式文档），见第 6、9
  节。

## 2. 非目标（Out of scope）

- 不改 Manager/Planner/Engineer/Reviewer 的决策逻辑、事件协议、backend 适配层。
- 不改自然语言路由本身（`argus_skill/life/router.py` 的
  `classify_is_conversational`）——这是有意的模型判断，不回退成关键词分类。
- 不涉及 `argus_skill/tools/dashboard.py`（web dashboard）或对外展示站点，那是
  另一套面向浏览器的界面，有独立的优化需求。
- 不引入新的重量级依赖。当前 REPL 只用 `termios`/`tty`/`readline`/`shlex` 等标准
  库，继续保持这个基线；确有必要引入新依赖时先与架构师确认。
- 不要求凭空发明新命令；除非现状分析确实指出某个操作现在无法通过任何现有命令
  完成。

## 3. 现状与问题清单（均可在代码中核实，行号以当前 main 分支为准；仅为起点，见 1.1）

### 3.1 命令体系：能做的事和"能发现"的事严重脱节

`dispatch_command`（`repl.py:2733-2884`）实际支持约 20 个顶层命令：

```
/help /commands /status /roles /doctor /daemon /daemons /attach /plan /start
/continuous /identity /backlog /add /stop /done /skip /rm /journal /note
/nudge /inject /notify /backend /config /verbose /quiet /reset /run /skills
```

但 `/help`（`_render_help`，`repl.py:2351-2368`）只输出一段自然语言说明和 4 个
中文例句（"继续上次""现在在干什么"……），**完全不列出这些命令**。用户唯一能
"发现"命令列表的方式是读源码或问别人。

未知命令的兜底提示（`repl.py:2883`）是：

```
unknown command: {cmd}  (try /help)
```

这是个死循环——`/help` 本身不列命令，"试试 /help" 并不能真正帮到打错命令的
用户。

此外还有历史包袱：

- `/verbose`、`/quiet` 是已下线的空操作命令，只打印"这个开关已经被移除"
  （`repl.py:2868-2872`），继续留在命令表里会让新用户误以为还能用。
- 同一操作有多个别名，但语义在 help 里完全不可见：`/done` `/skip` `/rm`
  三者等价（`repl.py:2828-2833`）；`/nudge` `/inject` `/notify` 三者等价
  （`repl.py:2850-2861`）。不一定要合并，但至少要让用户查得到。

**需求：**

1. `/help` 必须列出全部当前可用命令，按使用场景分组（例如：日常查看 / 任务
   管理 / daemon 与诊断 / 配置），每条命令带一行说明和最小 usage 示例。
2. 未知命令、以及命令缺少必要参数时的提示，必须指向具体可执行的下一步（可以是
   该命令的正确 usage，也可以是最接近命令名的模糊匹配建议），不能只甩回
   `/help`。
3. 明确处理 `/verbose` `/quiet`：彻底从命令表移除，或者至少不出现在新
   `/help` 列表里，避免用户学习一个已死的命令。两种做法都可以，需要在实现时
   给出选择理由。
4. 别名命令（`/done|/skip|/rm`、`/nudge|/inject|/notify`）在 `/help` 里显式
   标注"这些是同一操作的别名"，避免用户以为是三个不同功能。

### 3.2 `/status` 信息架构：单一层级堆了 10 类信息

`_status_cmd`（`repl.py:2053-2144`）在没有分组、没有可选详略的情况下，依次
打印：identity、backlog（前 5 条 + 总数）、最近 3 条 journal、continuous
on/off + objective/done_reason/done_at、inbox 待处理数、session timing
（uptime / mission 数 / 累计耗时 / 上次耗时）、daemon 存活状态、四角色
（engineer/reviewer/planner/manager）活跃摘要。

这是调用频率最高、信息量也最大的命令，但缺少优先级——"系统这一刻到底在干
什么"（daemon 是否活着、四角色谁在跑）这个最高频问题目前排在输出最后面。

**需求：**

1. 重新设计 `/status` 的信息分层：至少分成「此刻在发生什么」（daemon 存活、
   当前活跃角色、正在跑的任务）和「近期上下文」（backlog、journal、timing）
   两层，前者应该在输出最前面就能看到。
2. 评估是否需要精简/详细两种模式（例如默认精简，加参数展开完整信息）。具体
   形式由实现者设计，但需要在提交时说明取舍理由。
3. 保持现有的 fail-soft 特性——任何一个子模块（daemon 状态、roles 状态等）
   拿不到数据时只跳过那一行，不能让 `/status` 整体抛异常（现状已经是这样，
   `repl.py:2114-2144` 用 try/except 包住 daemon 和 roles 两段，改动时不能
   破坏这个属性）。

### 3.3 Live cockpit：已经做好的体验，默认被藏起来

`read_message_with_live_cockpit`（`repl.py:367-526`）实现了一个相当完整的
实时面板：常驻在输入框上方，约 1 秒刷新一次，展示四角色快照 + daemon 存活
状态；一旦用户开始打字就无缝让位给正常可编辑输入（对 CJK 输入做了专门处理，
见 `repl.py:505-506` 的 UTF-8 decode 逻辑）；组件齐全（TTY 检测、终端高度
检测、优雅降级到 `read_pasted_message`）。

但这个功能默认关闭：`_live_cockpit_enabled()`（`repl.py:725-726`）读取
`ARGUS_SKILL_COCKPIT_LIVE`，默认值 `"0"`，必须显式设成 `"1"` 才会启用；而且
**这个开关没有出现在 `/help` 或任何提示里**，用户几乎不可能自己发现它的
存在。同一模式的另一处入口 `follow_mission_live_roles`（任务运行期间的实时
角色跟随视图，见 `repl.py:1719-1721`、`1757-1758`）同样由一个默认关闭、未
文档化的开关 `ARGUS_SKILL_FOLLOW_LIVE`（`_live_follow_enabled()`，
`repl.py:729-730`）控制。两处是同一个"藏起来的好功能"模式。

相比之下，`/roles`（`repl.py:2151` 起，手动查询一次角色状态）是已知可发现
的，但它的"实时自动版"却被藏了起来。

**需求：**

1. 评估这两个能力是否应该默认开启。需要验证：不同终端下的性能开销、SSH 高
   延迟场景下的体验、多用户共享环境下的兼容性，给出明确结论和依据，而不是
   维持"没人知道"的现状。
2. 无论是否默认开启，都必须让用户能发现它：至少在 `/help` 和 `/roles` 的
   输出里提及这两个能力和各自的开启方式。
3. 如果决定默认开启，需要保留明确的关闭方式（`ARGUS_SKILL_COCKPIT_LIVE=0` /
   `ARGUS_SKILL_FOLLOW_LIVE=0`），并在 `/doctor` 里补充相应诊断（例如终端不
   满足条件时告知用户为什么没看到面板，而不是静默降级）。

### 3.4 错误/提示文案质量参差

好的例子已经存在，可以直接作为"质量对标"：

- `_no_executor_notice`（`repl.py:1786-1805`）：任务已排队但没有 daemon 执行
  时，清楚说明发生了什么、给出两条具体修复路径（cockpit 内 `/daemon start`
  或 `/doctor` 诊断；另开 shell `argus-skill --daemon`），外加"任务已保存、
  不会丢"的安心提示。
- `_cockpit_cli_alias`（`repl.py:968-1038`）：能识别用户把 shell 命令（如
  `argus-skill --daemon`）误粘贴进 cockpit 的情况，自动转换成等价的 cockpit
  命令并说明发生了什么。
- `/doctor`（`_doctor_cmd`，`repl.py:2212-2226`）：不仅诊断，还会把通用建议
  重写成 cockpit 内可执行的命令（`_rewrite_cockpit_daemon_fix`），并附最近
  的 daemon 日志尾部。

但很多其它路径只有 `print(theme.gray("usage: ..."))` 式的最小提示（例如
`/add` `/stop` `/journal` `/note` 等参数缺失时，见 `repl.py:2798-2861`
附近），没有解释"为什么需要这个参数"或"接下来大概率想干嘛"。

**需求：**

1. 以 `_no_executor_notice` / `/doctor` 的文案质量为标准（说清楚发生了什么 +
   具体下一步 + 必要时的安心提示），过一遍所有 usage/error 提示，列出需要
   提升的清单并逐条改写。
2. 不要求所有提示都变长——多数一行 usage 提示已经够用，只需要判断哪些场景
   用户容易卡住（例如首次使用、跨会话 daemon 状态不一致）并针对性加强。

## 4. 优先级建议

| 优先级 | 事项 | 理由 |
| --- | --- | --- |
| P0 | 3.1 命令可发现性（`/help` 列出命令 + 修复死循环兜底提示） | 影响面最大、改动风险最低，且是其它一切的入口 |
| P0 | 3.1 清理 `/verbose` `/quiet` 死命令 | 顺手做，避免继续误导新用户 |
| P1 | 3.3 Live cockpit 的可发现性（至少在 help/roles 里提及） | 现成能力被埋没，性价比高 |
| P1 | 3.2 `/status` 信息分层 | 高频命令，但涉及信息架构决策，需要更谨慎的设计 |
| P2 | 3.3 Live cockpit 是否默认开启 | 需要额外的兼容性/性能验证，可以晚一点决定 |
| P2 | 3.4 错误文案全面对标 | 长尾优化，逐条改写工作量大但风险低，可持续迭代 |

## 5. 约束

- `dispatch_command` 是行式 REPL 和 TUI（`tests/manager/test_tui.py` 覆盖的
  TUI 模式）共用的分发入口，任何改动要同时验证两种界面。
- 保持现有 fail-soft 原则：REPL 的任何展示逻辑异常不能导致整个 cockpit 崩溃
  退出（参考 `_status_cmd`、`_doctor_cmd` 现有的 try/except 包裹方式）。
- 保持 CJK 输入安全（`read_message_with_live_cockpit` 对此已有明确处理，
  回归时不要破坏，见 `repl.py:505-506`）。
- 不要静默删除现有斜杠命令——操作者可能已经有肌肉记忆；确需下线的命令（如
  `/verbose` `/quiet`）要从 `/help` 里消失，但命令本身可以保留一条明确的
  "已下线"提示，不必让它变成 unknown command。
- 涉及到的命令都要同步更新到 `/help`，避免再次出现"能用但没人知道"的命令。

## 6. 验收标准（Definition of Done）

- [ ] **前置门槛**：已提交第 1.1 节要求的亲自使用笔记（开工前的真实困惑点/
      卡点记录），且改动后重新完整用过一遍、确认"确实更舒服了"，而不是只靠
      单元测试通过判断完成。没有这份笔记，其余条目不予验收。
- [ ] `/help` 输出完整命令列表（分组、带一行说明 + 示例），并通过人工走查
      确认与 `dispatch_command` 里实际支持的命令一致（无遗漏、无幽灵命令）。
- [ ] 输入错误/未知命令时，提示不再是"试试 /help"的死循环，而是给出具体
      下一步或最接近命令的建议。
- [ ] `/verbose` `/quiet` 不再出现在 `/help` 里，处理方式（移除 vs 保留下线
      提示）有明确结论并落地。
- [ ] Live cockpit（`ARGUS_SKILL_COCKPIT_LIVE`）与实时跟随
      （`ARGUS_SKILL_FOLLOW_LIVE`）的存在和开启方式至少在 `/help` 或
      `/roles` 输出里可以被用户发现；是否默认开启有书面结论（含验证依据）。
- [ ] `/status` 输出经过重新分层，"系统此刻在做什么"类信息在输出最前面可见；
      fail-soft 行为的回归测试通过。
- [ ] 关键错误/usage 提示（至少覆盖 3.4 列出的清单）已对照
      `_no_executor_notice` 的质量标准改写。
- [ ] 下节列出的相关测试全部通过，且为新增行为补充了对应用例。

## 7. 测试

现有相关测试（改动后必须全部保持通过）：

```bash
cd /root/argus-skill
.venv/bin/python -m pytest \
  tests/manager/test_tui.py \
  tests/apps/test_life_repl_free_text.py \
  tests/apps/test_live_cockpit.py \
  tests/apps/test_cli_status.py \
  tests/apps/test_cli_status_snapshot.py \
  -q
```

（仓库约定：始终使用项目 venv 解释器跑测试；裸 `python`/`python3` 没有装
editable 的 `argus_skill`，涉及子进程拉起 `python -m argus_skill` 的测试会
直接失败。）

新增行为（`/help` 内容、未知命令建议、`/status` 分层等）需要新增测试文件，
或在上述现有文件中补充用例。

## 8. 相关文件索引

| 文件 | 作用 |
| --- | --- |
| `argus_skill/manager/repl.py` | 本次工作的主战场：命令分发、`/help`、`/status`、live cockpit、各类提示文案 |
| `argus_skill/cli/roles_status.py` | `/roles` 和 live cockpit 面板共用的角色快照渲染 |
| `argus_skill/tools/doctor.py` | `/doctor` 诊断逻辑（文案改写在 repl.py，诊断项本体在这里） |
| `argus_skill/daemon/life_worker.py` | daemon 状态读取（`read_daemon_status`）、continuous 状态 |
| `argus_skill/apps/_input_helpers.py` | `read_pasted_message`，live cockpit 降级后的基线输入路径 |
| `tests/manager/test_tui.py` | TUI 模式下的 `dispatch_command` 覆盖 |
| `tests/apps/test_life_repl_free_text.py` | 自由文本路由测试 |
| `tests/apps/test_live_cockpit.py` | live cockpit 面板测试 |
| `tests/apps/test_cli_status.py` / `test_cli_status_snapshot.py` | `/status` 相关测试 |

## 9. 交付方式

建议按第 4 节的优先级分批提 PR（至少 P0 一批、P1 一批），每批附上：

1. 改动前后的终端输出对比（截图或文本 diff 均可）；
2. 第 1.1 节要求的亲自使用笔记（开工前的真实卡点 + 改完后复用一遍的感受），
   不需要正式文档，几段话即可。

方便架构师快速验收，而不是一次性提交一个大 diff、也不是只有代码看不到"人用
过"的证据。

## 10. 亲自使用笔记（P0 部分，2026-07-06）

按第 1.1 节的硬性前提，P0 的两项（命令可发现性、清理死命令）已经由我本人用真实
cockpit（`--life-dir` 指向隔离的临时目录，backend=copilot，`ARGUS_SKILL_PER_MISSION_CAP_USD`
设小额安全阀）实际走了一遍，而不是只读代码。记录如下，供后续接手 P1/P2 的人参考——
这些是真正上手之后才发现的，比第 3 节纯代码走查的清单更准：

- **`SLASH_COMMANDS` 注册表本身就是烂掉的例子**：代码里明确写着"这份列表要跟
  `dispatch_command` 保持同步，喂给 /help 和 TUI 补全"，但实际只登记了
  `/help`、`/exit` 两个，且有一条测试 `test_slash_registry_covers_core_commands`
  把这个不完整状态断言成"预期行为"——即"补全/help 要同步维护"这条规则本身从
  一开始就没人遵守，连 TUI 的 tab 补全也因此只能补全这两个命令。已修复：把全部
  ~29 个真实命令（含别名）补全登记，`/help` 现在从同一份表渲染，并把回归测试
  换成动态解析 `dispatch_command` 源码来断言两者不脱节（`tests/manager/test_tui.py::test_slash_registry_covers_dispatch_commands`），防止再次"改了 dispatcher、忘了改注册表"。
- **`/add ... --budget=$X` 对真实花费上限完全不生效**：真实跑一次
  `/add 任务 --once --budget=1` 后，cockpit 回显 `max_cost=$30.00`——不是
  `$1.00`。追下去发现 `add_backlog_item` 把 `max_cost_usd` 硬编码成 `30.0`，
  而 `LifeBudget.effective_per_mission_cap`（真正决定要不要在花超时喊停的
  那个函数）看的正是 `max_cost_usd`，不是操作者那个数字实际流入的
  `iteration_budget_usd`。也就是说单次任务的 `--budget` 从代码建成那天起就是
  摆设，真正生效的只有全局的 `ARGUS_SKILL_PER_MISSION_CAP_USD`。这条只有真的
  敲一遍命令、盯着回显文字看才会发现——单纯读 `_parse_add_flags` 的解析逻辑
  完全看不出问题（解析本身是对的，问题在解析结果传下去之后被另一处覆盖）。
  已修复并补了回归测试（`tests/apps/test_life_repl_free_text.py::test_add_only_custom_budget_sets_real_enforced_cap`）。这条和"预算规划"那份需求强相关，建议一并告知负责预算审计的人。
- **`/status` 和 `/doctor` 里的 "backend" 字样具有误导性**（2026-07-06 追加：已修复）：
  本机把 `ARGUS_SKILL_RUNNER_BACKEND` 配成了 `copilot`（`/roles` 面板正确显示
  `Copilot · gpt-5.5`），但 `/status` 的 `daemon: alive (..., backend codex)`
  和 `/doctor` 的 `backend preflight ✓ codex backend runnable ...` 都显示
  `codex`。这是两套不同的"backend"概念（daemon 级的 `ARGUS_SKILL_LIFE_BACKEND`
  默认 `codex`，跟每个角色实际执行用的 `ARGUS_SKILL_RUNNER_BACKEND` 是分开的），
  但呈现给操作者时用了同一个词，容易让人怀疑"到底是不是真的在用 copilot"。
  顺手挖出一个更严重的关联 bug：`/doctor` 的 `backend preflight` 检查硬编码
  `shutil.which("codex")`，完全不看 `ARGUS_SKILL_RUNNER_BACKEND` 实际配置的是
  什么——纯用 copilot/claude、本来就没装 `codex` npm 包的操作者，会在每次
  `/doctor` / 启动横幅上收到一条虚假的"codex binary not found"报错。已修复：
  `/status`、`--status`、`/doctor` 三处都换成检查真正配置的 backend 对应的
  二进制，`/status`/`--status` 的展示文字也从误导性的 `backend codex` 改成
  `backend live — see /roles`（`memory` 模式保持 `backend memory (test)`
  不变）。改动文件：`apps/_runtime.py`、`tools/doctor.py`、`apps/cli/_core.py`、
  `manager/repl.py`；新增 3 个测试（`tests/test_doctor.py`）。
- **`/add` 排队时，Manager 会先做一次任务分诊（`_manager_divide_user_task`），
  这一步没有任何"正在处理"提示**：真实环境里这一步偶尔会明显变慢（本次验证
  时甚至观察到几次在 40–100 秒内都没有返回），期间 cockpit 完全没有输出，
  操作者从体感上无法区分"卡住了"还是"正常在等"。建议列入 P1/P2：至少给一个
  "正在解析任务…"之类的即时反馈。这次没有动手改（涉及 Manager 分诊调用链，
  超出 REPL 展示层的最小改动范围），只记录现象。
- 已确认：改完之后重新跑 `/help`，命令列表和分组清晰可读；故意打错
  `/stauts`，提示变成"did you mean /status?"，不再是"试试 /help"却
  `/help` 里什么都没有的死循环。

**关于并发编辑的说明**：验证过程中发现同一台机器上有另一个 `claude` 会话正在
对 `agent_cli_backend.py`、`tools/subagent/_core.py`、`life/supervisor/_config.py`
等文件做一次独立的预算/用量重构（新增了 `global_daily_cap_usd` 等字段），与本
文档的工作无关。已用 `git stash` 逐一验证：`tests/life/test_supervisor.py` 和
`tests/apps/test_cli_status.py` 里失败的用例在去掉我的改动后依然失败，确认
是那次并发编辑导致、与本次改动无关，因此未touch、也未尝试修复。基于同样的
理由，本次改动**未提交/未推送**，避免在对方工作到一半时把两边的半成品混进
同一个 commit；`/add` 疑似因对方改动导致的调用链变慢/挂起现象也一并记录在
上面，供其收尾后复核。
