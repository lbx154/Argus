# 批五设计稿(2026-09-05 交接)——已完成调查、未实施

本文件是 2026-09-05 交接后续批五的**已完成调查、未实施**的设计稿,覆盖
top-10 审计条目第 1-5、8、10 条:grounded-route 超限降级、路由快照标记
前移、mission_summary/delivery_candidates 存储层截断、mission-view
bootstrap 全量重放、next_action 溢出打标、早停 liveness 宽限。每份含事
实核查、现网测量、改动点与测试设计,可直接交给实施者。所有代码锚点
(文件:行号)基于 e274dd161;实施前需按届时 HEAD 对位。


# ==== routing ====

调查完成。以下基于 HEAD `e274dd161`(`/data/v-boxiuli/Argus`,与 main 一致,工作树仅一个未跟踪 md)。今天新落地的 `EventJournal.tail_settlements/tail_for_item/tail_kinds` 与这两条无关(本链路不读 journal 窗口),不引用。

# 第 1 条:`_DEFAULT_GROUNDED_ROUTE_MAX_PROMPT_CHARS = 32_000` 超限致命

## 事实

**上限在防什么。** 防烧钱,不是防模型上下文溢出:
- 32k chars ≈ 8k tokens,远小于任何在用模型的上下文(现网 copilot 跑 `gpt-5.6-sol`)。全仓**没有**模型→上下文长度目录;唯一的"上下文"编码是三处硬编码 `role_session_max_input_tokens = 120_000`(`argus_skill/planner/planner.py:61`、`engineer/round_config.py:282`、`loop.py:122`)。`_manager_model()`(`manager/_helpers.py:63-69` → `core/knobs.py:605 resolve_role_model`)可返回 `""`(交给 backend 默认),即**"按解析出的模型上下文推导上限"在当前代码里无据可依**,需要新建 per-backend 目录且对 custom provider 不可靠。
- 设计意图见 `_vertical_ops.py:484-487` 注释:路由刻意隔离于持久会话,"strict context bound" 使路由成本不随无关历史增长;既有测试 `tests/manager/test_manager.py:1342 test_grounded_route_prompt_cap_fails_before_model_call` 钉死"**在模型调用前**失败"(`runner.calls == []`)——这是止损语义,分诊原则下 fence 本身应保留。

**完整失败路径(操作者看到什么)。**
1. `_vertical_ops.py:736-744` raise `VerticalDecisionError("Manager grounded-route prompt exceeds configured context cap (N > 32000 characters)", phase="contract", contract_field="prompt_length")`;grounding retry 处 `:915-921` 二次同款 raise。
2. `decide_vertical`(`:432` 只拦 `ManagerClassificationContractError`)不记 streak,直接上抛。
3. `front_door.py:826/839-875`:`prepared.failed(exc)` 发 `life.manager.intent.failed` 事件(`:731-765`),包装为 `ManagerHandoffError("Manager handoff failed: routing failed [contract]: ...")`。
4. 操作者面:web 入队 `webapi/routes/workitems.py:51-53` → **HTTP 503**,任务不入队;`_manager_divide_user_task`(front_door.py:879-908)吞掉后返回 None → `require_manager_execution_task`(front_door.py:116-124)报 "task was not dispatched"。另两个消费点同样致命:daemon 启动分类 `daemon/_life_worker_boot.py:481` 与 continuous 换向 `life/supervisor/_planning_cycle.py:876`(后者的 `selection_objective = continuous_objective + operator directive block`,会随指令块增长)。
5. 整单丢弃、无降级、无重试 —— 属"误杀丢沟通内容",分诊=修。

**现网距离。** 扫全部 `~/.argus-skill/projects/*/agent_io.jsonl*`:零次 "exceeds configured context cap";`manager-classify-*` 提示词长度 p100 = **13,191** chars(41% 上限),次高 10,562。是潜在悬崖非现血:实测固定开销 = scaffold 7,220(24 个内建 vertical、空 project domains,`.venv/bin/python` 实测)+ snapshot 块 ~980(40 项)+ route contract ~558 ≈ **8.8k**,即任务超约 **22-23k chars**(一次长 spec/日志粘贴)即触发 503。另注意:fast route 在任务 >12k chars 时静默跳过(`:625-629`,纯成本,宽容),所以所有长任务都落在这个悬崖所在的 grounded 路由上。

**附带发现(不一致性)。** `:860-898` 的三个 correction 重试(context/field/tool-loop)拼接后**完全没有** cap 检查——31.9k 的基础 prompt 过了 `:736` 检查后,context retry 会发出 32.5k 的 prompt;唯独 `:906-922` 的 grounding retry 检查并致命。cap 既不一致执行,也说明"超一点"从未真正伤害过后端。

## 设计:超限降级(截断路由视图 + 警示),fence 保留

不建议按模型上下文推导(无基础设施,见上);常量与 env 覆盖 `ARGUS_SKILL_MANAGER_GROUNDED_ROUTE_MAX_PROMPT_CHARS`(`:732-735`)原样保留为烧钱 fence。关键不变量:**截断只作用于路由调用的任务视图;执行 handoff 永远用原文**——`parse_vertical_decision` 的 `default_execution_task="" if contextual_task else task.strip()`(`:805/:827`)与 `finalize` 的 marker 检查(`:604-616`)继续引用原 `task`,不丢任何沟通内容。

改动点:

1. `argus_skill/manager/_helpers.py`(:22 旁)加两个命名常量:`_GROUNDED_ROUTE_RETRY_HEADROOM_CHARS = 1_024`(实测 correction:context 558 / tool-loop 239 / grounding 278 / field ≈330+cause,预留后全部装下)、`_GROUNDED_ROUTE_MIN_TASK_VIEW_CHARS = 2_000`。
2. `argus_skill/manager/_vertical_ops.py` 模块级新增:
   ```python
   def _fit_task_for_routing(task: str, *, budget: int) -> str:
       """Head+tail elision for the ROUTING VIEW only; execution handoff keeps the full text."""
       text = task.strip()
       if len(text) <= budget:
           return text
       marker = (f"\n\n[...routing view: {{omitted}} of {len(text)} characters elided "
                 "for this classification call only; the execution handoff carries "
                 "the full task verbatim...]\n\n")
       keep = budget - len(marker) - 8   # 8 = digits slack for {omitted}
       head, tail = (keep * 2) // 3, keep - (keep * 2) // 3
       omitted = len(text) - head - tail
       return text[:head] + marker.format(omitted=omitted) + text[-tail:]
   ```
   **必须 head+tail 而非纯 head**:contextual 任务的 `[CURRENT OPERATOR MESSAGE]` 在**尾部**(`webapi/manager_session_intent.py:41-46`),纯 head 截断会剪掉操作者当前消息本身。head+tail 同时保住头部 `[BOUNDED TASK CONTEXT` 开标记(`contextual_task` 判定 `:522-528` 本就用原 task,不受影响)。
3. `:722-744` 替换为:
   ```python
   prompt = build_vertical_decision_prompt(task, ...) + active_route_contract + snapshot_block
   grounded_prompt_limit = _manager_route_positive_int(...)          # 原样
   send_budget = grounded_prompt_limit - _GROUNDED_ROUTE_RETRY_HEADROOM_CHARS
   if len(prompt) > send_budget:
       overhead = len(prompt) - len(task.strip())   # builder 在 :461-462 逐字嵌入 task.strip(),算术精确
       task_budget = send_budget - overhead
       if task_budget < _GROUNDED_ROUTE_MIN_TASK_VIEW_CHARS:
           raise VerticalDecisionError(...)          # 原 raise 原文案保留:仅 scaffold/菜单自身撑爆(病态配置)才致命
       log.warning("grounded-route prompt %d > cap %d; eliding %d task chars from the routing view only",
                   len(prompt), grounded_prompt_limit, len(task.strip()) - task_budget)
       prompt = build_vertical_decision_prompt(_fit_task_for_routing(task, budget=task_budget), ...) \
                + active_route_contract + snapshot_block
   ```
4. `:915-921` grounding-retry 的第二个 raise 删除(headroom 预留后 `prompt + 278` 恒 ≤ cap),换防御性 `log.warning` + 回退用未加 correction 的 `prompt`;顺手把 `:874-886` field-retry 里 `exc.cause` 的插值 clamp 到 `[:300]`(该重试今天本就无 cap 检查,clamp 纯改进)。

**测试**(放 `tests/manager/test_manager.py`,紧邻 :1342;`_DecisionRunner`(:36-51)已捕获 `calls[i]["prompt"]`):
- `test_grounded_route_oversized_task_degrades_not_fatal`:`ARGUS_SKILL_MANAGER_FAST_ROUTE=0`、cap env=12000,task = `"HEAD-SENTINEL " + "x"*30000 + " TAIL-SENTINEL"`,合法 software/existing 决策;断言不 raise、`len(calls[0]["prompt"]) <= 12000`、prompt 含 elision 标记与两个哨兵(head+tail 均存活)、`decision.execution_task == task.strip()`(handoff 原文无损)。
- `test_grounded_route_contextual_truncation_keeps_current_operator_message`:超长 `[BOUNDED TASK CONTEXT ...]...[CURRENT OPERATOR MESSAGE]\n<ask>` 体;断言 prompt 尾部含 `[CURRENT OPERATOR MESSAGE]` 与 `<ask>`。
- `test_grounded_route_prompt_cap_fails_before_model_call`(:1342 既有,cap=1):**不改,保持绿**——scaffold 自身超限仍在模型调用前 fail-hard,钉住止损语义。
- `test_grounded_route_retry_correction_fits_under_cap`:用 `_SequenceDecisionRunner`(:54-65)走 required-grounding retry(fake result 无 `tool_activity_observed` → False,天然触发 :906),cap 设为 `len(基础 prompt)+headroom` 边界;断言两次调用均发生且各自 prompt ≤ cap。

# 第 2 条:`_routing_workspace_snapshot` 先截断后扫标记

## 事实(因果确认)

`_vertical_ops.py:83-91`:`entries = sorted(..., key=str.casefold)[:40]`;`:100-104` 的 `marker_names` 从**已截断**的 `entries` 推导。目录 >40 项且 ≥40 项按 casefold 排在 `p` 之前时(数据目录、大量小写前缀文件很常见),`pyproject.toml`/`requirements.txt`/`package.json`/`go.mod` 落在第 40 位之后 → `project_markers=[]`。误导通道有两层,需分清:
- **主通道(实际生效)**:`_render_routing_workspace_snapshot`(`:114-128`)把 `project_markers=none` 以 "authoritative bounded routing evidence" 名义写进 grounded prompt(`:729-731`),模型据此把仓库任务误路由(如 software→research、workflow 误判)。
- **守门不兜底**:`_decision_requires_agent_grounding`(`:193-223`)只检查 `isinstance(markers, list)`(`:216`)不看内容;`choice=="existing"` + 内建 vertical + 非 project domain 时对一致非空快照直接返回 False(`:219-223`)→ 不强制 tool grounding,误判无人纠正。
- 缓解面:`.git`(ASCII `.` 0x2E 排序在字母前)几乎总在前 40 位存活;真实暴露面是**无 .git 的检出**(tarball 导出、子目录 workdir、新脚手架)叠加多文件目录。`workspace_empty = not entries`(`:109`)不受截断影响(切片不会把非空切成空)。

## 设计:最小修——标记扫描移到截断前

`argus_skill/manager/_vertical_ops.py:80-111` 单函数改写(渲染、守门、fast-route 门 `:623` 全部零改动):

```python
_ROUTING_SNAPSHOT_MAX_ENTRIES = 40   # 命名原 magic number,置于 :67 附近

def _routing_workspace_snapshot(root: Path | str) -> dict[str, Any]:
    """Return bounded deterministic routing evidence without model tool use."""
    path = Path(root).expanduser().resolve()
    try:
        names = [
            child.name + ("/" if child.is_dir() else "")
            for child in path.iterdir()
            if child.name not in _ROUTING_RUNTIME_ENTRIES
        ]
    except OSError:
        return {...}                      # 原 :93-99 不变
    marker_names = {                      # 全量扫描,先于截断
        name.rstrip("/")
        for name in names
        if name.rstrip("/") in _ROUTING_PROJECT_MARKERS
    }
    entries = sorted(names, key=str.casefold)[:_ROUTING_SNAPSHOT_MAX_ENTRIES]
    return {
        "root": str(path),
        "accessible": True,
        "workspace_empty": not names,     # 语义等价,改从全量列表判定更诚实
        "entries": entries,
        "project_markers": sorted(marker_names),
    }
```
被截出 `entries` 的标记仍经渲染器独立的 `project_markers=` 行(`:124`)呈现给模型,无需并回 entries。审计建议的"渲染预算跟随 prompt cap"缓议(第 1 条落地后 40 项 ≈1k chars 无压力),本批只修误判因果。

**测试**(新增,`tests/manager/test_manager.py`;当前全仓无 `_routing_workspace_snapshot` 直接测试):
```python
def test_routing_snapshot_markers_survive_entry_truncation(tmp_path):
    for i in range(45):
        (tmp_path / f"a{i:02d}.txt").write_text("x")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    snap = _routing_workspace_snapshot(tmp_path)
    assert snap["project_markers"] == ["pyproject.toml"]      # 旧代码红:[]
    assert len(snap["entries"]) == 40
    assert "pyproject.toml" not in snap["entries"]            # 渲染上界仍生效
    assert snap["workspace_empty"] is False
    assert "project_markers=pyproject.toml" in _render_routing_workspace_snapshot(snap)
```
再加一条守门联动用例:同一目录下构造 `choice="existing"/vertical="software"` 决策,断言 `_decision_requires_agent_grounding(...) is False` 前后不变(证明修复只走 prompt 证据通道,不扰动 grounding 强制逻辑)。

# 分诊对齐小结

- (1) 超限致命 = 误杀整单操作者任务(503 + 不入队)→ **修**:降级为路由视图 head+tail 截断 + `log.warning` + handoff 原文无损;32k fence 与"模型调用前拦截"的止损语义**保留**(scaffold 自身超限仍 fail-hard,:1342 既有测试不动)。
- (2) 截断后扫标记 = 静默丢路由证据致误判 → **修**:全量扫描前移,一处函数、零下游改动。
- fast route 12k/24k 跳过(`_helpers.py:20-21`)纯成本 → 宽容,不动。

关键文件:`/data/v-boxiuli/Argus/argus_skill/manager/_helpers.py`、`/data/v-boxiuli/Argus/argus_skill/manager/_vertical_ops.py`、`/data/v-boxiuli/Argus/argus_skill/roles/prompts/manager.py`(:461-462 Task 节,仅作算术锚点)、`/data/v-boxiuli/Argus/argus_skill/manager/front_door.py`、`/data/v-boxiuli/Argus/argus_skill/webapi/routes/workitems.py`、`/data/v-boxiuli/Argus/tests/manager/test_manager.py`。

# ==== storage ====

## 事实(以 HEAD e274dd161 为准;审计中的 :1179/:1204/:1282 在当前 HEAD 整体 +21 偏移,分别为 :1200/:1225/:1299-1303,均在 `_emit_mission_outcome_and_build_result`,def 于 :1069)

### (3) mission_summary [:1200] — 存储层截断,确认属实

生产者链(`argus_skill/life/supervisor/_mission_execution_settlement.py:1178-1200`):
`outcome.summary → final_review_reason → final_message → reason → planner_report["summary"]` 逐级兜底 → `strip_control_footer` → `" ".join(split())`(压平换行)→ `[:1200]`。截断后的 `mission_summary` 写入两处:
- LIFE_MISSION_COMPLETED 事件 `"summary"`(:1297,events.jsonl,即唯一持久层——journal 只是事件投影,`memory.py:575` `JournalEntry.summary = row.get("summary")` 原样透传);
- settle 返回 dict `"summary"`(:1424)。

全文没有任何持久副本:context packet 只存 `engineer_summary[:4000]`(`life/context_packet.py:366`,是工程师侧另一份文本,非结算 summary)。

消费者与各自渲染截断(确认"存全文、渲染才截"迁移面):
| 消费者 | 锚点 | 自带截断? |
|---|---|---|
| Planner 历史窗口(LLM 上下文) | `_planner_rendering.py:190-205` | 有,`_PLANNER_HISTORY_ENTRY_CHARS=1800`/行 + "…" —— 目前被存储层 1200 卡死,1800 预算用不满 |
| mission-view 侧栏投影 | `core/mission_view/_reduce_mission.py:117` | 有,`_text(event,"summary",1200)` |
| 操作者完成聊天消息 | `_core.py:1586-1617 _publish_mission_completion_message` → `core/operator_messages.py` | 无截断(沟通内容,直接透传事件 summary) |
| CLI 事件行 | `cli/event_format.py:370-405` | 无(headline 直接拼接 summary,仅 title 有 `_trunc`) |
| 交付回执 summary | `life/delivery.py:278` `str(summary).strip()[:1200]` | 又一份重复的 1200 存储层截断(回执嵌入事件/transcript/mission-view) |
| 终局项目回执 | `_core.py:1359 latest.get("summary")` | 读的已是截断值 |
| webapi 从 view.summary 反抽链接 | `webapi/artifacts.py:219-225` | 读的是 1200 截断后的 view 副本,超过 1200 字符处的文件链接已丢 |

分诊:事件 summary 是操作者聊天 + planner 决策上下文的唯一来源 → 静默丢沟通内容,属"修"。

#### 改动点
1. `_constants.py` 新增(仿既有 quarantine 常量风格,env 可覆盖):`MISSION_SUMMARY_PERSIST_CHARS = 20_000`(存储 sanity 上限,非展示预算)。
2. `_mission_execution_settlement.py:1200` 改为 `[:MISSION_SUMMARY_PERSIST_CHARS]`;若真被截,尾附 "…" 并在事件中加 `"summary_truncated": True`(可选字段)。注意 :1220-1224 已用 `raw_mission_summary` 全文做链接抽取,不受影响。
3. `life/delivery.py:278` 回执 summary 的 `[:1200]` 收敛到同一常量(回执也是持久凭证)。
4. 渲染层补自己的截断(原先靠存储层兜底):`cli/event_format.py:_render_life_mission_completed` 对 summary 加 `_trunc(summary, 1200)`(纯展示,宽容);`_reduce_mission.py:117` 的 1200 建议升到与 webapi 反抽链接不冲突的值(如 4000)或保持——注明该路径只是老 Reviewer 后端的兜底,新数据的链接已在结算时全文抽取入 `delivery_candidates`。planner 的 1800/行、mission-view 上限均保留(展示/token 预算,宽容/止损)。
5. 压平换行的 `" ".join(split())` 保留(消费者按单行假设格式化,如 planner 的 `- [ts] kind: title — summary` bullet);丢的是排版不是内容,迁移到渲染层属可选后续。

#### 存量数据兼容
- events.jsonl 无 schema、append-only:旧事件 summary ≤1200 照常读;`_entry_from_event` 与所有消费者对长度无假设,新长文本自动流通。`summary_truncated` 缺省按 False(`.get`),无迁移。
- mission-view.json 是可重建投影,无迁移。
- 唯一行为变化:操作者聊天与 CLI 会看到更长文本——CLI 由改动点 4 兜底;聊天端本就是全文语义。

#### 测试
- `tests/life/`(仿 `test_delivery_completion.py:23` 的 settle fixture):outcome.summary 5000 字符、在 1200 位之后埋哨兵 token → 断言事件 `summary` 含哨兵;超过 `MISSION_SUMMARY_PERSIST_CHARS` 时尾部 "…" 且 `summary_truncated is True`。
- 扩 `tests/life/test_mission_completion_message.py`:发布的操作者消息文本含 1200 位之后的哨兵。
- 渲染护栏:planner 历史行 ≤1800 且以 "…" 结尾;CLI `_render_life_mission_completed` 输出有界;`_reduce_mission` summary 有界。
- 回归:`referenced_delivery_paths` 从全文(哨兵后含链接)仍抽到链接(现状已对,防倒退)。

### (4) 双份 limit=12 — 语义不一致,确认属实(且实为三处 + 一处隐藏 6)

- **:1225** `referenced_delivery_paths(..., limit=12)`:只限从 summary/final_message 文本里抽取的引用路径(达 12 即提前 return,静默丢,`delivery.py:105-106`);此刻 reviewer 的 `frontier.artifacts` 不设限。
- **:1299-1303** `"delivery_candidates": [...][:12]`:限的是持久化的**并集**(frontier 在前、referenced 追加在后)。两处语义不同:frontier 本身 ≥12 时,:1225 放进来的 reviewer 认可链接被 :1303 全部静默尾丢;丢弃策略 = 尾丢、reviewer 证据优先序。
- 并且 :1236 `build_delivery_receipt` 收到的是**未截断**并集 → `_safe_existing_path` 安全过滤 → `MAX_DELIVERY_TARGETS=6` 再次静默截断(`delivery.py:19,263`)——即时回执与持久 candidates 可分叉:回放/终局重建无法复现当时的回执。
- 第三处:`webapi/artifacts.py:225` 又一个裸 `limit=12`(渲染时按需重算,宽容,但应共用命名常量)。

下游影响:
- **终局回执**:`_core.py:1307-1372 _build_terminal_project_delivery` 用最后一次成功结算事件的 `delivery_candidates` 重建 project_done 回执 → 注入 planner verdict 事件(:1390)→ 操作者 "project-completed" 聊天(:1559-1583)→ webapi 交付列表。预截断与终局时点的存在性过滤叠加:任务结束到项目终局之间被删/移的文件,无法由第 13+ 个候选顶上——回执变薄甚至为空。
- **评审**:cap 不影响评审本身(candidates 来自评审产出 frontier.artifacts),影响的是评审之后的持久 proof-of-work。
- transcript 归档(`webapi/manager_dispatch.py:293-302`)与 `webapi/artifacts.py:174-212` 只读回执 targets(≤6),同样只见薄化后的结果。

#### 改动点(收敛为一个命名策略 + 持久层记全 + 溢出计数)
1. `life/delivery.py` 顶部,`MAX_DELIVERY_TARGETS` 旁新增:
   - `MAX_REFERENCED_DELIVERY_PATHS = 12`(文本抽取扫描预算——对 LLM 长文本的解析成本上限,保留其值,给名字);
   - `MAX_PERSISTED_DELIVERY_CANDIDATES = 64`(持久 sanity 上限;路径为几十字节短串,记全很廉价)。
2. `_mission_execution_settlement.py:1225` → `limit=MAX_REFERENCED_DELIVERY_PATHS`;`webapi/artifacts.py:225` 同一常量(消除第三处裸 12)。
3. `_mission_execution_settlement.py:1299-1303` → 持久化全量(至 `MAX_PERSISTED_DELIVERY_CANDIDATES`),并加姊妹字段 `"delivery_candidates_overflow": max(0, total - persisted)`——溢出永不静默。
4. `delivery.py:255-264` 6-cap 处:回执加 `"omitted_target_count": len(去重后候选) - len(targets)`,UI 可渲染 "+N more"(6 的展示上限本身保留,但持久凭证不再静默)。
5. 可选:`_publish_mission_completion_message` / webapi 渲染 `omitted_target_count > 0` 时追加 "+N more"(纯展示,宽容)。

#### 存量数据兼容
- 旧事件 `delivery_candidates` ≤12、无 overflow 字段:`_core.py:1321` 用 `latest.get(...)` 读 list,新旧长度均照常流通(`build_delivery_receipt` 接受任意 iterable);缺省 overflow 按 0。无迁移。
- 回执新增可选字段:所有消费者用 `.get`,`DELIVERY_SCHEMA_VERSION` 可保持 1(纯增量字段),在常量旁注释说明。
- 旧数据已丢的 13+ 候选不可恢复(存储层截断的本性,审计结论一致)。

#### 测试
- `tests/core/test_delivery.py`:9 个有效文件 → receipt 6 targets 且 `omitted_target_count == 3`;≤6 时为 0;`referenced_delivery_paths` 15 个有效链接 → 返回 `MAX_REFERENCED_DELIVERY_PATHS` 个(固化扫描预算语义)。
- `tests/life/test_delivery_completion.py`:结算时 15 个 frontier artifacts + 3 个 summary 链接 → 事件 `delivery_candidates` 18 项、overflow 0;70 项 → 64 + overflow 6。
- 终局顶替(仿 :191/:214 的 journal-noise/success-pick 测试):写入含 15 个候选的成功结算,project_done 前删掉前 11 个文件 → 断言 `_build_terminal_project_delivery` 回执包含第 12-15 个(旧代码只存 12 个、仅 1 个存活;新代码 4 个存活)。
- 分叉消除:即时回执(:1236)与由持久 candidates 重建的终局回执在文件未变动时 targets 一致。

分诊对齐:两处存储层截断([:1200]、[:12])= 静默丢沟通内容/交付凭证 → 修;抽取扫描预算(limit=12 的解析成本面)与 6-target 展示上限 = 止损/展示 → 保留但命名 + 计数;planner 1800、CLI、侧栏截断 = 纯展示 → 宽容不动。

关键文件:`/data/v-boxiuli/Argus/argus_skill/life/supervisor/_mission_execution_settlement.py`(:1200/:1225/:1299-1303)、`/data/v-boxiuli/Argus/argus_skill/life/delivery.py`(:19/:105/:263/:278)、`/data/v-boxiuli/Argus/argus_skill/life/supervisor/_core.py`(:1307-1390/:1556-1617)、`/data/v-boxiuli/Argus/argus_skill/webapi/artifacts.py`(:219-225)、`/data/v-boxiuli/Argus/argus_skill/life/supervisor/_planner_rendering.py`(:16-17/:204)、`/data/v-boxiuli/Argus/argus_skill/core/mission_view/_reduce_mission.py`(:117)、`/data/v-boxiuli/Argus/argus_skill/cli/event_format.py`(:370)、`/data/v-boxiuli/Argus/argus_skill/life/supervisor/_constants.py`(常量落点)。

# ==== bootstrap ====

## 事实(基于 HEAD e274dd161,与预期 main 一致;锚点均对此提交)

**Mission view 是什么。** `<project>/mission-view.json` 是 cockpit/webapi 的事件溯源投影:每条事件落盘时,`JsonlEventSink._append`(`argus_skill/life/event_log.py:286-292`)对 `_PROJECTED_EVENT_TYPES`(`argus_skill/core/mission_view/_view_state.py:235-274`,任务/回合/评审/技能/wiki 生命周期)调 `update_mission_view_event`(`_dispatch.py:118`)增量归约并持久化。读路径 `snapshot_mission_view`(`_snapshot.py:380`,调用方 `webapi/project_state.py:718`、`apps/_self_reply.py:178`)读该文件并叠加实时 overlay。

**Bootstrap 何时发生——不是每次 daemon 重启。** mission-view.json 持久存在、增量更新;只有 `snapshot_mission_view` 发现 `bootstrapped` 为假才重建(`_snapshot.py:389-391`→`_bootstrap_view`,`:30-36`):(a) 文件缺失——现网 58 个有 events.jsonl 的项目中 15 个无 mission-view.json,首次被 cockpit 查看即触发;(b) JSON 损坏/非 dict(`_view_state.py:123-129`);(c) schema_version 不在接受集(`:131`)——**任何未来 schema 升级若不写迁移就整体重建**,v3→v6 已有先例(`:136` 显式置 `bootstrapped=False` 强制重建);(d) 用户删文件。即:重建是低频但必然反复发生的路径,且每次都只回放 8 MiB 尾部。

**8 MiB 截断具体怎么错。** `_tail_jsonl`(`_view_state.py:277-301`)seek 到 `size-8MiB` 后用 `partition(b"\n")` 丢弃首个半行——**不是**从行中间解析出垃圾;错在语义:从 `empty_mission_view()` 起点、在战役中间任意一点开始归约,且窗口按含噪声的原始字节计(engineer.progress 命令流占绝对多数)。永久算错(此后增量更新在错误基数上累加,永不自愈)的字段:
- `storage.skill_history_compressed/skill_history_bytes_saved`(`_reduce_skill.py:90-95`,逐事件 `+=`)与 `wiki_retired_compressed/wiki_retired_bytes_saved`(`_reduce_wiki.py:40-45`):窗口外的压缩事件全丢 → 永久少算。
- `learned_wiki_pages`(`_reduce_wiki.py:47-110`):窗口外创建/退役的页从视图彻底消失(无磁盘再发现)。
- `learned_skills`:global 作用域技能消失;project 作用域被 `_discover_project_skills`(`_snapshot.py:294-328`)按 mtime 从磁盘找回但 mission 归属靠猜,且窗口外的 `SKILL_ARCHIVED` 丢失后归档技能会以 "active" 复活。
- `achievement`:最近一次 `RESEARCH_ACHIEVEMENT_CERTIFIED` 若在窗口外,认证成就从 cockpit 消失(`_reduce_research.py:27`)。
- `mission.campaign_started_at`(`_reduce_mission.py:77-78`,取回放窗口内第一条 mission.started)→ 战役起点前移、campaign_elapsed 缩水;merge 的 `or` 链(`_snapshot.py:187-191`)里错误值优先。
- `review.rejected_attempts`(`_reduce_mission.py:94` 每任务重置,`:320` 累加):仅当单任务日志超 8 MiB 才错——现网 engineer.progress 占丢失事件 96%,单个长 GPU 任务可达。
- timeline(120 条)/role_work(每角色 40 条)本就是尾部展示窗,不受影响(纯展示,宽容)。

**同锚点的第二个洞。** `_bootstrap_view` 只读 `events.jsonl.1` + `events.jsonl`(`_snapshot.py:32`),而轮转保留全部世代 .2/.3/…(`event_log.py:19-23`,ROLL_BYTES=100 MiB,`event_log_paths()`:55 已提供正确的老→新顺序)——战役一旦轮转两次,整代日志永不回放,8 MiB 修好也没用。

**现网测量(2026-09-05,~/.argus-skill/projects,58 项目)。** 16/58 的 events.jsonl 超 8 MiB;最大 26.8 MiB(s-3e28f79c,50,355 行),次大 25.8 MiB;**尚无任何项目轮转出 events.jsonl.1**,即全部历史在单文件里。对最大文件:39,218 条投影事件中 27,389 条(70%)落在 8 MiB 窗口外,含 89 条 mission.completed、130 条 round.review.completed、148 条 planner.start。**全量流式解析实测 0.41 s**(25.8 MiB 文件 0.34 s,约 65-75 MiB/s,内存 O(最长行))。

**与今日原语的关系。** `EventJournal.tail_settlements/tail_for_item/tail_kinds`(`argus_skill/life/memory.py:443,615,649,698`)是"谓词占槽的窗口尾读",适合"最近 N 次结算"查询;bootstrap 是全量重放以复现状态,形状不同,不要复用。该借鉴的是 `event_log_paths()` 的世代枚举(勿直接 import——`life` 已依赖 `core.mission_view`,反向引用倒置分层;在 mission_view 内复刻约 12 行 glob 逻辑)。

## 设计(分诊:静默丢内容/累计永久算错 = 修;全量流式重放 + 截断戳兜底)

**改动 1 — `_view_state.py:277` `_tail_jsonl` 改为流式生成器:**
```python
def _iter_projected_events(path, max_bytes=None) -> Iterator[dict]:
```
`max_bytes=None`(默认)从头逐行 `for line in fh` 流式读全文件,内存 O(最长行);半行(崩溃中写)由现有 `json.loads` try/except 自然跳过。保留 seek+partition 的尾读逻辑仅供预算兜底用;顺带保留现有"窗口内无换行返回空"守卫只在截断模式生效。过滤逻辑(:296-300)不变。

**改动 2 — `_snapshot.py:30` `_bootstrap_view` 回放全部世代:** 新增本地 `_event_log_generations(root)`(复刻 `event_log.py:55-77` 语义:`events.jsonl.N` N≥2 升序 → `.1` → 当前文件),逐世代、逐行 `reduce_mission_view_event`;结束时 `view["bootstrapped"]=True; view["bootstrap_truncated"]=truncated`。

**改动 3 — 预算护栏(防烧钱止损类,保留但放宽到 sanity 级):** `MISSION_BOOTSTRAP_MAX_BYTES` 改名/升为 `MISSION_BOOTSTRAP_BUDGET_BYTES = 512 MiB`(env 可覆盖,走 knobs 惯例)。超预算时:从最老世代整代丢弃;仅当剩余单文件仍超预算才对其做尾读(沿用换行对齐);任一字节被跳过即 `bootstrap_truncated=True`。理由:bootstrap 持有 mission-view 锁,而事件落盘热路径(`event_log.py._append`→`update_mission_view_event`)取同一把锁——按实测吞吐 512 MiB ≈ 7-8 s 一次性停顿,是可接受上限;现网最大 26.8 MiB 仅 0.4 s。

**改动 4 — schema 缺省与归一:** `empty_mission_view()`(`_view_state.py:41`)加 `"bootstrap_truncated": False`;`_read_unlocked` 加 `payload.setdefault("bootstrap_truncated", False)`(:163 附近)。webapi 直传视图,cockpit 可据此渲染"历史不完整"角标(纯展示,可后补)。

**不改:** timeline/role_work 上限;`_discover_project_skills` 的 mtime 归属兜底(全量回放后仅剩真正无事件的技能才走它)。

## 测试(`tests/core/test_mission_view.py`,放在 `test_snapshot_bootstraps_from_existing_event_log`(:871)旁)

1. `test_bootstrap_replays_events_beyond_former_8mib_window`:首行写 `skill.history.compressed`(count=5, bytes_saved=100)与一条 `wiki.created`,再垫 >8 MiB 大 text 的 engineer.progress 行(~1100×8 KiB,写盘瞬时、解析 ~0.15 s),末行 mission.started;断言 `storage["skill_history_compressed"]==5`、learned_wiki_pages 含该页、`bootstrap_truncated is False`。
2. `test_bootstrap_replays_all_rolled_generations`:`events.jsonl.2/.1/当前` 各放一条 count=1 的压缩事件,断言累计 ==3 且顺序正确(老→新:用 mission.started/completed 的先后验证终态)。
3. `test_bootstrap_stamps_truncated_when_over_budget`:monkeypatch 预算为 4096,两个世代,断言 `bootstrap_truncated is True`、`bootstrapped is True`、最新世代事件在场、最老世代整代缺席。
4. `test_bootstrap_tail_fallback_drops_partial_first_line`:截断模式下窗口起点落在行中间时无解析垃圾(保住现有 :286-289 行为)。
5. 回归:`test_v3_snapshot_rebuilds_to_include_completion_summary`(:172)等现有 bootstrap 测试应全绿;新增断言重建后 `campaign_started_at` 等于日志首条 mission.started 的 ts(全量回放的语义正确性)。

**最小兜底方案(若全量重放被否):** 仅在 `_bootstrap_view` 对每个文件比较 `stat().st_size` 与已读字节、任一超窗即 `view["bootstrap_truncated"]=True`,约 10 行——但按分诊原则这是"静默丢内容"类,首选修(全量重放),戳只作超预算标记保留。

# ==== nextAction ====

调查完成。以下为事实与设计,基于 HEAD = `e274dd161`(与你所述 main 一致,读码时未见他人新推)。

## 事实:next_action 完整流转链

**产生端(Reviewer 文本回复)**
- 提示词要求 Reviewer 在具名行 `NEXT_ACTION` 里给出具体工作指令:`argus_skill/roles/prompts/reviewer.py:465`("MUST name a CONCRETE new …")、`:701`("instruction only in next_action")。

**截断点(唯一一处,且两条解析路径不对称)**
- 具名行路径:`argus_skill/reviewer/_parsing.py:398` `next_action=read_block(text, "NEXT_ACTION", _VERDICT_KEYS).strip()[:1500]` —— 硬切片、无标记。`read_block`(`argus_skill/core/role_reply.py:221`)本身不限长,专为多段 prose 设计,注释还明言"截断会静默丢弃解释判决的部分"——同文件同函数里 reason 却被 `[:5000]`、operator_question 被 `[:500]` 同样静默切。
- JSON payload 路径:`_parsing.py:317` `next_action=next_action.strip()` **完全不截断**。同一字段,两条路径行为分叉。
- 该 1500 来自初始公开发布提交 `f51524258`,无任何前史/理由记录。
- 截断后 `_apply_model_judgment_policy`(`_parsing.py:162`)只做完整性词汇消毒,不再截断。

**消费端(截断值即被执行的指令)**
1. 执行链(最要害):`engineer/round_settlement.py:339` 存入 `state.reviewer_next_action`(status=="continue" 时)→ `engineer/runner.py:248` → `engineer/round_prompt.py:35,53` → `roles/prompts/engineer.py:261-266` 原文(仅消毒)注入下一轮 Engineer 提示的 "## Reviewer guidance from prior round"。工程师照半截干活。
2. 持久化:`engineer/round_signals.py:19` → `core/models.py:224` `to_event_payload` 把 `next_action` 原样写入 `round.review.completed` 事件(events.jsonl);`life/context_packet.py:399` 再 `[:4000]` 静默切一刀写入 mission packet(作 stage 证据回放);研究纵向 `reviewer/_core.py:418,429` 写 REVIEW.md 与 pipeline_state。
3. 下游读者:`manager/plan_challenge.py:39`(alternative 回退到 next_action)、`life/supervisor/_planning_cycle.py:499`、`_mission_execution_settlement.py:584-601`、`manager/_stage_ops.py:89-105`(与中性文案做 casefold 全等比较)。
4. 纯展示(分诊:宽容,不动):`cli/event_format.py:307`(200 字带 "…")、`core/mission_view/_reduce_mission.py:356`(2000)。

**"评审按全文判"的机制确认**:Reviewer 会话经 `resume_thread_id` 复用自身 transcript(`engineer/round_reviewer.py` 传入),其记忆里是完整指令;跨会话摘要 `_previous_review_summary`(`round_reviewer.py:53-65`)只带 `reason[:600]` 不带 next_action。所以一旦截断触发,评审端(全文)与工程端(1500 字)确实静默分叉,且事件日志里只有截断版,事后无法从日志发现。

**1500 在防什么**:防提示膨胀/烧钱——next_action 会逐轮注入 Engineer 提示,且 `round_settlement.py:462-466` 的 continue_adaptor 还会把 "Scientist playbook + Reviewer guidance" 叠加进去;上限是对失控 Reviewer 的 prompt 成本止损,不是正确性保障。

**现网实例扫描:零命中**。扫了 `/home/v-boxiuli/.argus-skill` 全部 40,908 个 json/jsonl 状态文件(含 projects/*/events.jsonl 共 1,360 条含 next_action 的事件,全部持久化 next_action 值 2,521 个):最长 903 字符,p99≈517,长度 ≥1495 为 0。**该上限从未触发过**——这是预防性修复,不是止血。

## 设计(分诊:静默吞沟通内容=最坏类 → 修;但保留膨胀止损)

不建议纯删上限(消费端会逐轮复读注入,失控 Reviewer 会烧钱),也不建议发射端让 Reviewer 重写(多一次评审调用,为一个现网零命中的条件付费,过度工程)。**推荐:提高上限 + 溢出显式打标,双端可见**:

1. 新增共享助手(建议放 `argus_skill/core/model_visible_text.py` 或新 `core/text_bounds.py`):
   ```python
   def bound_visible_text(text: str, limit: int) -> str:
       if len(text) <= limit: return text
       cut = text[:limit].rsplit(None, 1)[0] or text[:limit]
       omitted = len(text) - len(cut)
       return cut + f"\n[TRUNCATED BY RUNTIME: {omitted} of {len(text)} chars omitted; treat this directive as incomplete and say so in your summary]"
   ```
   标记不含 hex/digest,可穿过 `_apply_model_judgment_policy` 的逐行消毒(`sanitize_model_judgment_text` 只删完整性判断子句)。
2. 改动点:
   - `argus_skill/reviewer/_parsing.py:398`:`[:1500]` → `bound_visible_text(..., 4000)`。上限 4000 ≈ 现网最大值的 4.4 倍,且与 packet 上限对齐。
   - `argus_skill/reviewer/_parsing.py:317`(`decision_from_payload`):同样包一层,消除两路径不对称(此处今天是无界的)。
   - `argus_skill/life/context_packet.py:399`:`[:4000]` → `[:4400]` 或同助手(留出标记余量,避免把上游标记再切掉,变成新的静默截断点)。
   - 顺带(同 PR 或跟进):`_parsing.py:397` reason `[:5000]`、`:399` operator_question `[:500]` 是同型静默切,operator_question 是发给人的问题,同属沟通内容,建议同助手处理。
3. 为何双端可见:标记随事件 payload 持久化(`core/models.py:224`),随 Engineer 提示注入(`engineer.py:266`),且 Reviewer 下轮按提示词要"Inspect the prior next_action"(`reviewer.py:43`)时也能看到——分叉从"静默"变"显式声明",工程师被明确告知指令不完整。
4. 兼容性核查已做:`manager/_stage_ops.py:105` 对 `_NEUTRAL_REVIEW_TEXT` 是短文案全等比较,只在溢出才附加标记,不受影响;展示端 `_trunc` 各自再截,无碍。

**测试**(锚 `tests/test_reviewer_named_contract.py`,现有断言在 :26/:51 均为短串不受影响):
- 具名路径:构造 10,000 字符 NEXT_ACTION 块 → 断言解析结果以标记结尾、长度 ≤ 4000+标记、边界前内容逐字保留、且经 `_apply_model_judgment_policy` 后标记仍在。
- payload 路径:`decision_from_payload` 传同样长串 → 断言与具名路径同界(回归锁死路径对称)。
- 边界:恰好 4000 字符 → 无标记、无改动(零命中现状不回归)。
- packet:`record_reviewed_handoff` 写入带标记的 next_action → packet 里标记完整未被 `[:4000]` 再切。

# ==== grace ====

## 事实(以当下 HEAD e274dd161 为准)

**锚点勘误**:文件在 `/data/v-boxiuli/Argus/argus_skill/tools/subagent/_supervised_run.py`(任务给的 `tools/subagent/...` 少了 `argus_skill/` 前缀);`proc.wait(timeout=30)` 在 HEAD 上是 **515 行**(审计锚 503 已漂移到其上方注释块)。审计条目本体:JSON `docs/audits/magic-hyperparameters-2026-09-05.json`(file=..._supervised_run.py, line=503, recommendation="adaptive"),followups md 345-347 行。

**1. 早停信号如何传给训练进程 —— 文件,不是信号。**
`_supervised_handle_early_stop`(_supervised_run.py:475-562)先写 `<resolved_run_dir or cwd>/STOP`(507-513 行),然后 `proc.wait(timeout=30)`(515 行)。初始阶段不发任何 signal。训练侧按 RunWriter 契约消费:契约模板 `experiment_io.py` 在当前 HEAD 分支线上已不存在(历史提交 42109eba3 引入、非 HEAD 祖先;workspace 项目里仍有副本),但 harness 仍按该契约编程(_supervised_run.py:499 docstring、_cli.py:374-387 的 stale-STOP 预检、_registry.py:570-588 的 status.json 读取)。契约要点(读自 `git show 42109eba3:argus_skill/tools/project_templates/code/experiment_io.py`):
- `raise_if_stopped()` 检查 STOP 文件,**限速一次/30 秒**(`stop_check_interval_seconds=30.0`);
- 见到 STOP 抛 `RunCancelled` → `__exit__` 把 status.json 终态写成 `"cancelled"`、关闭句柄、`sys.exit(130)`。

**关键的误杀算术**:训练侧的 STOP 轮询节流(30s)恰好等于监督者的宽限(30s)——最坏情况训练进程在 SIGTERM 到来前根本没机会看见 STOP 文件。任何"收到停止后保存最终 checkpoint"的动作还要叠加在这之上。

**2. 30s 后的 kill 链。**
超时 → `_terminate_proc(proc)`(`argus_skill/tools/subagent/_direct_run.py:267`,grace=10.0):`os.killpg(pgid, SIGTERM)`(301 行,launch 用 `start_new_session=True`,_registry.py:244,bash 包装器是组长,训练器及其所有 DDP rank 同组)→ 等 10s → `os.killpg(pgid, SIGKILL)`(313 行)→ 等 5s。总宽限上限 ≈45s。无 SIGTERM handler 的 Python 训练器被 SIGTERM 直接杀;注册了 save-on-SIGTERM 的只有 10s。7B 级 checkpoint(bf16 权重 ~14GB + Adam fp32 状态 ~56GB)在共享盘上要分钟级 → SIGKILL 落在写到一半的 safetensors 分片上 → **早停本要保的工件变成损坏的半个 checkpoint,status.json 也永远到不了 "cancelled" 终态**。

**3. 对照**:硬超时路径(_supervised_run.py:844-845)不写 STOP、直接 `_terminate_proc`——那是防烧钱围栏,按分诊原则保留不动。

## 分诊

误杀毁工件(且毁的恰是早停的目的物)→ **修**。但"训练器无视 STOP 继续烧 GPU"的防烧钱兜底必须保留 → 用**有限总上限**替代,而非无限等。

## 设计:liveness 宽限 + 防烧钱总上限

新 helper `_await_cooperative_stop(proc, watch_dir, *, poll_s=5.0, idle_kill_s=60.0, ceiling_s, monotonic=time.monotonic) -> dict`,放 `_direct_run.py` 紧邻 `_terminate_proc`(该模块现 514 行,进程生命周期的自然归属;_supervised_run.py 模块 docstring 明言要控体积)。每 poll 一轮:

1. `proc.poll()` 非 None → 返回 `{"outcome": "cooperative_exit", "waited_s": ...}`。
2. **liveness 探针**(任一命中即"活着,继续等"):
   - `watch_dir`(run_dir)递归 max(mtime/size) 有推进——"还在写文件"(os.scandir 走树,条目上限 ~10k 防超大树);
   - Linux:遍历 `/proc/*/stat` 匹配 `pgrp == proc.pid`,求和 `/proc/<pid>/io` 的 `write_bytes` 有增量——兜住写到 run_dir **之外**(HF output_dir)的 checkpoint;
   - 同组 `utime+stime` 有增量——序列化阶段两次 flush 之间可能无新 mtime。
   Windows / 无 /proc:退化为仅 mtime 探针(`_terminate_proc` 的 nt 分支已有,不动)。
3. 连续 `idle_kill_s` 无任何 liveness → `_terminate_proc(proc)`,outcome=`"idle_kill"`。
4. 总时长 ≥ `ceiling_s` → 无条件 `_terminate_proc(proc)`,outcome=`"ceiling_kill"`。

**取值论证**:
- `idle_kill_s=60`:必须 > RunWriter 的 30s STOP 轮询节流(给训练器至少一个完整轮询周期去*发现* STOP);整个进程组 60 秒零 CPU、零写入是可靠的挂死信号。误判为 idle 的代价也不对称:kill 走的仍是 `_terminate_proc` 的 SIGTERM+10s 阶梯,save-on-SIGTERM 训练器还有最后一次机会。
- `ceiling_s=900`(15 分钟),注册 knob `ARGUS_SKILL_SUBAGENT_EARLY_STOP_GRACE_CEILING_SECONDS`(`argus_skill/core/knobs.py`,budget 类,先例 147-148 行)。论证:本 harness 目标是单机 ≤8B 级 RL;7B 全量 checkpoint ≈70-85GB,按保守共享盘 100-200MB/s 是 6-14 分钟,15 分钟盖住现实最坏情形。防烧钱侧:无视 STOP 但持续训练的进程在 liveness 下会一直显得"活着",ceiling 是唯一围栏,故必须有限且克制——15 分钟 GPU 时的代价(单机 8 卡量级 <$10)相对它保住的多小时训练工件可忽略。
- `poll_s=5`:探针本身是廉价 stat/readdir。

**调用点改动**(_supervised_run.py:514-517):
```python
grace = _await_cooperative_stop(proc, Path(resolved_run_dir or cwd), ceiling_s=...)
```
把 `grace`(outcome/waited_s/命中的 liveness 源)追加进 `supervisor_log` JSONL 一行,并写入 td:`stop_grace_outcome`、`stop_grace_seconds`——随 `_persist_experiment_record` 落盘,即成为**观测到的收尾时长**的持久记录,供后续"按观测 checkpoint 时长自适应 idle_kill_s(clamp(2×观测值, 60, ceiling))"的二期使用。注:今天落地的 `EventJournal.tail_settlements/tail_for_item/tail_kinds`(argus_skill/life/memory.py:615/649/698)是 life 侧 planner 的历史读取原语;subagent worker 是 registry 落盘、不写 journal,此处的观测存储应走 experiment record / task registry,不硬套 journal——但"对观测历史推理而非拍死常数"的模式与其一致。RunWriter 契约今天没有 save 时长字段,早停又是稀发事件,故 **liveness 为主、观测自适应为二期** 是正确顺序(审计原文也允许 "at minimum watch process liveness")。

## 测试(模拟慢收尾进程)

新文件 `tests/tools/test_subagent_early_stop_grace.py`(或并入 `tests/tools/test_subagent_supervisor.py`,那里已有 `_terminate_proc` 的 stub-Proc 模式,~1696 行起):

1. **慢保存者(核心误杀回归)**:`subprocess.Popen([sys.executable, "-c", script], start_new_session=True)`,script 每 1s 向 `tmp_run_dir/ckpt/part` 追加+flush,持续 8s 后 exit 0。参数取 `idle_kill_s=3, ceiling_s=60` → 断言 outcome=`cooperative_exit`、`returncode==0`、`waited_s≈8` **> idle_kill_s**(证明 liveness 把等待延过了固定宽限)。
2. **真挂死**:script 只 `time.sleep(3600)` 不写不算。`idle_kill_s=2, ceiling_s=60` → outcome=`idle_kill`,waited < ceiling,`proc.poll() is not None`(组已被收割)。
3. **无视 STOP 持续活跃(防烧钱围栏)**:script 每 0.5s 写一笔、永不退出。`ceiling_s=5` → outcome=`ceiling_kill`,waited≈5。
4. **纯 CPU liveness**:busy-loop 6s 不写文件后退出,空 watch_dir → 靠 /proc CPU 探针存活到 cooperative_exit;`@pytest.mark.skipif(not Path("/proc").exists())`。
5. **纯单元变体**:helper 的 `monotonic` 与探针函数做成可注入参数,用假时钟把 1-4 压到 <1s;保留 1 条真子进程集成测试。
6. **调用点测试**:monkeypatch `_supervised_run._await_cooperative_stop` 为记录器,以 stub proc 调 `_supervised_handle_early_stop`,断言 (a) STOP 文件在 helper 调用**之前**已写入 run_dir,(b) `stop_grace_outcome/stop_grace_seconds` 出现在 task record 和 supervisor_log 行中。

## 改动点清单

- `/data/v-boxiuli/Argus/argus_skill/tools/subagent/_direct_run.py`:新增 `_await_cooperative_stop` + `/proc` 组探针(紧邻 267 行 `_terminate_proc`);
- `/data/v-boxiuli/Argus/argus_skill/tools/subagent/_supervised_run.py:514-517`:替换 `proc.wait(timeout=30)/_terminate_proc`,记录 grace 结果到 supervisor_log 与 td;import 处 26-32 行追加新符号;
- `/data/v-boxiuli/Argus/argus_skill/core/knobs.py`(~147 行区):注册 `ARGUS_SKILL_SUBAGENT_EARLY_STOP_GRACE_CEILING_SECONDS="900"`;
- 测试:`/data/v-boxiuli/Argus/tests/tools/test_subagent_early_stop_grace.py`(新)+ `tests/tools/test_subagent_supervisor.py` 调用点断言;
- 不动:硬超时路径(_supervised_run.py:844-845)与 `_terminate_proc` 自身的 10s/5s 阶梯(防烧钱围栏)。
