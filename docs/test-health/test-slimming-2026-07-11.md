# Argus 测试精简报告（2026-07-11）

## 1. 报告结论

本轮工作完成了测试清单、基线测量、候选审阅、测试精简、显式分层和清理后复测。

主要结果：

| 指标 | 清理前 | 清理后 | 变化 |
| --- | ---: | ---: | ---: |
| `pytest --collect-only` 收集的真实测试 | 2,825 | 2,824 | -1 |
| 三轮全量测试结果 | 每轮固定 8 failed | 三轮全部通过 | 修复确定性基线故障 |
| 全量外部 wall time 中位数 | 37.58s | 31.52s | -6.06s（-16.13%） |
| 候选慢测试组合 wall time | 12.04s | 3.68s | -8.36s（-69.44%） |
| 跨轮结果不一致的测试 | 0 | 0 | 无新增 flake 信号 |
| 快速/default tier wall time | 无显式 tier | 20.65s | 比最终全量中位数快 34.49% |
| integration tier wall time | 无显式 tier | 14.20s | 10 tests |
| e2e tier wall time | 无显式 tier | 2.94s | 10 tests |

测试数量不是目标。净减少的一个测试来自两个 daemon 生命周期测试合并成一个更强的
连续行为测试；其余重复结构通过参数化保留了全部输入边界。

本轮没有修改 Argus 生产逻辑。生产包内的两个变化
`argus_skill/release_manifest.json` 和
`frontend/core/src/release.generated.ts` 均由既有生成器根据 `pyproject.toml`
变更自动重建。

## 2. 范围和环境

| 项目 | 值 |
| --- | --- |
| 仓库 | `/home/argustest/dev/Storm72/argus-skill` |
| 原始审计分支 | `Storm72/test-slimming-20260711` |
| 干净 PR 分支 | `Storm72/test-slimming-pr-20260711` |
| 清理前基线 commit | `0a2146b5b9d0bd6653b77dd4d61202255c2f105b` |
| 原始基线修复 commit | `11a7f8a` |
| PR 基线修复 commit | `9daa1b5` |
| 原始测试精简 commit | `7f77714` |
| PR 测试精简 commit | `64a050a` |
| Python | 3.13.5 |
| pytest | 9.1.1 |
| 主机 | `dsp7`，Linux 6.8.0-1052-azure，x86_64 |
| 状态隔离 | 每轮使用独立 `ARGUS_SKILL_HOME` |
| 测试入口 | `python -m pytest` |

所有运行均在 Storm72 的独立源码和 venv 中完成，没有使用公共 editable checkout，
也没有读写共享的 `/home/argustest/.argus-skill`。

机器是共享主机。清理前记录的 load average 为 `1.39, 1.23, 1.15`，清理后记录时为
`1.83, 2.22, 2.13`。清理后负载并没有更低，因此不能把时间改善解释成机器更空闲；
但共享主机仍存在调度噪声，所以本报告使用三轮中位数，不使用最快成绩。

## 3. 工作目标和非目标

目标：

1. 盘点每个初始测试用例的行为标识、运行时间、三轮结果、风险类别、历史线索和保留决定。
2. 删除或合并没有独立行为价值的重复结构。
3. 消除测试桩错误、固定 sleep 和真实 wall-clock timeout 带来的无意义等待。
4. 保留预算、安全、数据完整性、反作弊、持久化、恢复和核心执行路径测试。
5. 建立不会改变默认全量行为的 integration/e2e 分层。
6. 使用同条件三轮运行比较清理前后 wall time 和稳定性。

非目标：

1. 不以测试数量或覆盖率百分比为 KPI。
2. 不为了速度降低并发压力、删掉真实进程测试或放宽关键断言。
3. 不在本轮修改生产行为。
4. 不引入 pytest-xdist、pytest-repeat、coverage 等新依赖。
5. 不把“没有找到失败记录”写成“从未失败”。

## 4. 测试盘点方法

### 4.1 完整清单

清理前共收集 2,825 个测试用例，归属于：

| 维度 | 数量 |
| --- | ---: |
| 测试文件 | 266 |
| 函数级行为组 | 2,661 |
| 初始测试用例 | 2,825 |
| 三轮 JUnit 匹配失败 | 0 |

逐项清单保存在：

```text
/home/argustest/dev/Storm72/test-health-audit/2026-07-11/
  inventory/test-inventory.csv
  inventory/final-test-inventory.csv
  inventory/slow-tests.csv
  inventory/collection-skips.csv
  inventory/summary.json
  comparison.json
```

`final-test-inventory.csv` 每行对应一个清理前测试 case，包含：

```text
nodeid
file
line
behavior_id
protected_behavior
parameter_id
provisional_layer
risk_categories
run_1_seconds
run_2_seconds
run_3_seconds
median_seconds
baseline_outcomes
failure_history
unique_value
latest_file_change
file_commit_count
markers
fixtures
decision
stronger_or_replacement_test
decision_evidence
review_status
final_test_id
final_tier
```

### 4.2 审阅深度

2,825 个用例全部完成了机械盘点和三轮结果匹配。266 个测试文件按互不重叠的子系统
分组审阅：

| 审阅范围 | 内容 |
| --- | --- |
| 根目录测试 | `tests/test_*.py` |
| 生命周期 | `tests/life`、`tests/daemon`、`tests/deployment` |
| skills/tools | `tests/skills`、`tests/tools` |
| 接口 | `tests/core`、`tests/apps`、`tests/cli`、`tests/webapi` |
| 协作系统 | `tests/team`、`tests/manager`、`tests/planner`、`tests/agent_cli` |

对所有没有充分删除证据的测试，决定均为保守保留。只有实际修改、基线失败或高置信
候选进行了逐测试的生产代码、重复覆盖和故障信号深审。

最终清单中的决定分布：

| 决定 | 初始 case 数 | 含义 |
| --- | ---: | --- |
| `keep` | 2,789 | 未发现无损删除证据，保守保留 |
| `repair_baseline` | 8 | 三轮固定失败，先独立修复 |
| `optimize` | 8 | 行为不变，去除错误重试或真实等待 |
| `parameterize` | 18 | 保留各输入边界，合并复制结构 |
| `merge` | 2 | 两个旧测试由一个更强测试承接 |

### 4.3 失败历史限制

当前 checkout 没有 `.github/workflows`。Git 历史能说明测试何时加入或修改，但不能证明
它是否在外部 CI 中失败过。

因此本报告的 `failure_history` 只确定记录：

1. 本轮三次基线和三次清理后运行的结果。
2. Git 文件修改历史。
3. 测试注释中明确写出的 regression 背景。

外部 CI、历史 PR 或 issue 数据未提供时统一标为 `external_ci_unknown`，不会把它解释成
“从未失败”。

## 5. 清理前基线

### 5.1 三轮结果

| 轮次 | pytest 结果 | pytest 时间 | 外部 wall time |
| --- | --- | ---: | ---: |
| Before 1 | 8 failed, 2812 passed, 7 skipped | 36.86s | 37.58s |
| Before 2 | 8 failed, 2812 passed, 7 skipped | 36.40s | 37.09s |
| Before 3 | 8 failed, 2812 passed, 7 skipped | 37.40s | 38.08s |
| 中位数 | 固定同一组失败 | 36.86s | 37.58s |

三轮没有出现结果不一致的 case，说明失败是确定性的，不是 flake。

### 5.2 八个确定性基线失败

| 测试 | 根因 |
| --- | --- |
| `tests/core/test_release.py::test_release_manifest_matches_current_shipped_source` | Storm72 源码变化后 release manifest 未重建 |
| `tests/core/test_release.py::test_release_generated_frontend_contract_is_current` | 同上 |
| `tests/daemon/test_protocol.py::test_daemon_status_sidecar_carries_protocol_and_runtime_identity` | runtime source digest 与旧 manifest 不一致 |
| `tests/deployment/test_multi_process_contract.py::test_real_webapi_process_exposes_release_protocol_metrics_and_projects` | WebAPI 正确报告 source mismatch |
| `tests/webapi/test_server_m0.py::test_api_meta_identifies_protocol_capabilities_and_loaded_checkout` | API meta 正确报告 source mismatch |
| `tests/webapi/test_manager_rotation.py::test_manager_stream_announces_classification_before_model_call` | 测试 double 未接受新增 `root_task_id` |
| `tests/webapi/test_manager_rotation.py::test_web_process_restart_seeds_one_startup_handoff` | 同上 |
| `tests/webapi/test_manager_rotation.py::test_natural_language_config_change_is_applied_inline` | 同上 |

用户确认后，这八项在独立 commit `11a7f8a` 中先行修复：

1. 使用 `scripts/generate_release_manifest.py` 重建后端和前端 release identity。
2. 让三个 manager front-door 测试 double 接受真实接口的
   keyword-only `root_task_id`。
3. 运行八项定向测试和一次全量测试，均通过。

这样后续测试精简不会把旧失败误认为清理引入的回归。

## 6. 实际测试改动

### 6.1 修正 Manager 测试桩，消除四秒假等待

文件：

```text
tests/test_manager_skill_wiring.py
```

原来的 `_CapturingRunExec` 返回裸字符串。生产函数
`manager.stage_decider.extract_answer()` 只接受 `RunnerResult` 风格对象，因此把裸字符串
解析为空回复。生产代码随后正确执行两次一秒重试。

这两个测试本意是返回一个非空 HOLD verdict，却因为错误测试桩各等待约两秒：

| 测试 | 清理前中位数 | 清理后中位数 |
| --- | ---: | ---: |
| `test_manager_decision_prompt_carries_role_skill_when_store_present` | 2.003s | 0.003s 左右 |
| `test_manager_decision_prompt_unchanged_without_store` | 2.003s | 小于 0.005s |

修改后测试桩返回：

```python
RunnerResult(
    exit_code=0,
    agent_messages=['{"action": "hold", ...}'],
)
```

保留的行为：

1. 有 skill store 时注入 Manager role skill。
2. 没有 skill store 时保持旧 prompt。
3. 原始 stage-decision schema 仍存在。
4. 返回 HOLD 时不写 stage。

没有修改生产代码，也没有绕过生产空回复重试。

### 6.2 将 watch subprocess 的固定等待改为输出就绪信号

文件：

```text
tests/apps/test_watch.py
```

三个测试原来均执行：

```python
time.sleep(1.0)
proc.send_signal(signal.SIGINT)
```

固定一秒既不是行为要求，也不能证明 watch 已经渲染目标内容。

新增共享测试 helper `_run_watch_until_output()`：

1. stdout 和 stderr 写入同一个文件。
2. 保持 `PYTHONUNBUFFERED=1`。
3. 等待该测试本来就要断言的语义文本出现。
4. 文本出现后发送 SIGINT。
5. 等待进程干净退出。
6. finally 中继续保留 SIGINT、超时和 kill 的兜底清理。
7. 进程退出后读取完整文件并执行各测试原有断言。

保留的独立行为：

| 测试 | 保护行为 |
| --- | --- |
| journal panel | 真实事件 shape 映射为正确 kind、cost 和 title |
| inbox guidance | 显示 inbox/backlog/continuous 信息且不消费 inbox offset |
| paused budget | 预算耗尽时显示 `remaining $0.00 (paused)` |

三项合计由约 3.06s 降至约 0.55s。

### 6.3 用虚拟时间测试 timeout，不等待真实 0.3 秒

文件：

```text
tests/apps/test_life_repl_free_text.py
```

以下三项原来各等待真实 0.3 秒：

```text
test_tail_mission_events_ignores_other_items
test_tail_mission_events_timeout_returns_none_without_completed
test_tail_mission_events_missing_file_returns_none
```

新增只注入这三项的 `instant_tail_timeout` fixture。虚拟 clock 的
`sleep(seconds)` 会推进同一个 `monotonic()` 计数，因此生产循环仍真实经历：

1. 建立 deadline。
2. 尝试读取或过滤事件。
3. 调用 `_sleep_until()`。
4. 时间推进到 deadline。
5. 返回 `None`。

没有使用 frozen clock 或 no-op sleep，因此不存在无限循环。三项合计从约 0.90s
降至接近 0s。

### 6.4 合并 daemon 连续 drain 测试

文件：

```text
tests/daemon/test_life_worker.py
```

旧测试：

```text
test_life_worker_drains_backlog_and_stops_on_signal
test_life_worker_drains_multiple_missions
```

替换为：

```text
test_life_worker_drains_successive_missions_and_stops_on_signal
```

新测试完整保护：

1. worker 启动前已经存在的第一项 backlog 能完成。
2. worker 已经运行并完成第一项后，新加入的第二项仍能被捡起。
3. 两项分别到达 `done`。
4. `_stop` 能唤醒并停止 worker。
5. worker thread 实际退出。
6. `run_forever()` 实际返回 `0`。

测试仍保留 30 秒竞争容忍 deadline；只移除了三个固定 `time.sleep(0.3)` 和重复
worker setup。两个旧测试合计约 3.28s，新测试约 2.66s。

这是本轮唯一造成净测试数下降的改动：两个旧函数由一个更强函数承接。

### 6.5 参数化复制用例

#### Project lifecycle

文件：

```text
tests/life/test_project_lifecycle.py
```

两个 submission-artifact promotion 测试合并为：

```text
test_submission_artifact_promotes_to_done[from-writing]
test_submission_artifact_promotes_to_done[from-running]
```

两种初始状态均保留，并统一验证：

```text
to_state == DONE
reason == submission_artifact_present
```

#### Spinner 环境门

文件：

```text
tests/cli/test_live_status.py
```

三个复制测试合并为：

```text
test_spinner_env_gating[no-color]
test_spinner_env_gating[argus-opt-out]
test_spinner_env_gating[enabled]
```

每个 case 开始时都会清除 `NO_COLOR` 和 `ARGUS_SKILL_NO_SPINNER`，避免因环境残留而
“为错误原因通过”。

#### Manager session 损坏状态恢复

文件：

```text
tests/manager/test_manager_session.py
```

十个合法 JSON 但 shape/type 错误的 case，与一个非法 JSON case 合并为 11 个带名称
的 raw-input 参数：

```text
missing-thread-id
empty
whitespace
number
list
mapping
root-list
root-number
root-string
root-null
invalid-json
```

非法 JSON 仍直接写入 `"{not valid json"`，没有通过 `json.dumps()` 变成合法字符串，
所以 `JSONDecodeError` 恢复路径仍被保护。

#### Planner waiting verdict

文件：

```text
tests/planner/test_planner.py
```

普通长实验等待和外部 image capability blocker 合并为两个命名 case：

```text
running-experiment
external-capability
```

两者仍分别验证自己的 `waiting_reason` 证据文本。

## 7. 测试分层

### 7.1 配置

`pyproject.toml` 新增：

```toml
markers = [
  "integration: crosses local process, thread, filesystem, or API boundaries",
  "e2e: exercises a complete workflow through a real entry point or toolchain",
]
```

默认 `addopts` 仍然只有：

```toml
addopts = "-q"
```

没有把 `-m "not integration and not e2e"` 写入默认配置，因此普通 `pytest`
仍执行全部测试。

### 7.2 显式 integration 测试

当前显式标记 10 个：

| 文件 | case 数 | 边界 |
| --- | ---: | --- |
| `tests/apps/test_watch.py` | 4 | 真实 subprocess、signal、stdout/stderr |
| `tests/daemon/test_life_worker.py` | 3 | daemon process/thread lifecycle |
| `tests/manager/test_manager_session.py` | 2 | thread + flock |
| `tests/team/test_store.py` | 1 | multiprocessing + file lock |

### 7.3 显式 e2e 测试

当前显式标记 10 个：

| 文件 | case 数 | 端到端边界 |
| --- | ---: | --- |
| `tests/deployment/test_multi_process_contract.py` | 3 | 多进程预算、daemon command、真实 WebAPI |
| `tests/test_aaai_compile_smoke.py` | 1 | 真实 LaTeX 工具链 |
| `tests/test_aaai_venue_e2e.py` | 5 | venue scaffold 到各 format gate |
| `tests/test_wiki_e2e.py` | 1 | bootstrap 到 ingest/index/validate |

### 7.4 使用命令

快速/default、偏 unit 的日常反馈：

```bash
pytest -m "not integration and not e2e"
```

本地边界集成测试：

```bash
pytest -m integration
```

完整入口或工具链：

```bash
pytest -m e2e
```

权威全量：

```bash
pytest
```

未标记测试目前属于 default fast tier，但本报告不声称这 2,799 项全部是严格意义上的
纯 unit test。只对本轮人工确认的真实边界添加标记，避免根据耗时或目录名称批量误判。
后续新增真实进程、网络、编译器或跨进程锁测试时，应显式选择 integration/e2e。

## 8. 明确保留、没有删除的高风险测试

| 类别 | 保留的代表性测试 | 原因 |
| --- | --- | --- |
| 预算 | `test_budget_source_cap.py`、`test_runner_budget.py`、`core/test_cost_control.py` | 防止绕过 cap、重复结算和并发超支 |
| 安全/可信性 | `test_eval_signing.py`、`core/test_copilot_guard.py` | 结果签名、调用 guard |
| 反作弊/泄漏 | `test_leaderboard_anti_archaeology.py`、`test_quant_leakage_probe.py` | 防止 rescore、数据泄漏和虚假提升 |
| 持久化/恢复 | checkpoint、session、backlog、wiki store 测试 | 恢复和磁盘状态是低频但关键路径 |
| 核心执行 | `test_loop_smoke.py`、life worker、reviewer、planner 测试 | Engineer→Reviewer→Planner 主链路 |
| 并发完整性 | `team/test_store.py::test_locked_serializes_concurrent_writers` | 真实多进程 flock 压力 |
| 发布协议 | `core/test_release.py`、WebAPI protocol tests | 前后端/daemon 运行时兼容 |
| 真实工具链 | AAAI compile smoke | 唯一真实 LaTeX compile signal |

## 9. 审阅后拒绝的删除建议

### 9.1 daemon duplicate-command unit test

候选：

```text
tests/daemon/test_commands.py::test_concurrent_duplicate_has_one_claimant
```

虽然 deployment 中存在跨进程版本，但 unit/integration 级测试仍独立验证：

1. 第二个调用在第一调用运行时返回 `running`。
2. handler 精确执行一次。
3. 第一调用最终得到 `applied`。
4. 失败定位比跨进程 E2E 更直接。

它运行很快，保留价值高于删除收益，因此未删除。

### 9.2 frontend/backend protocol mirror

候选：

```text
tests/webapi/test_server_m0.py::test_frontend_protocol_constants_match_backend_contract
```

它不是 `build_api_meta()` 的重复。前者保护 TypeScript 前端常量与后端协议一致，
后者只保护后端运行时 meta。跨语言镜像是独立发布风险，因此保留。

### 9.3 run gateway AST guard

候选：

```text
tests/core/test_run_gateway.py::test_application_code_has_no_direct_backend_run_exec_bypass
```

该测试确实绑定源代码结构，但保护的是 provider 调用不能绕过统一 gateway 的安全和
成本控制边界。当前未找到同等强度替代测试，不能仅因结构敏感而删除。

### 9.4 多进程 writer stress

候选：

```text
tests/team/test_store.py::test_locked_serializes_concurrent_writers
```

没有减少 4 个进程各 50 次写入。该测试约一秒，但保护真实并发完整性；降低循环数会
降低竞态暴露概率，与本任务原则冲突。

### 9.5 stage-check 和 artifact 测试

多个 stage-check 文件存在可参数化结构，但它们涉及 fail-closed、rollback packet、
provenance 和 project-local data domain。其 wall time 很小，本轮不为减少代码行而冒险
合并关键 artifact 断言。

### 9.6 paper layout snapshot helper

`test_paper_layout_review_snapshots.py` 看似只是文件排序，但它保护页码按数值而非字符串
排序的真实回归风险。运行成本极低，未删除。

## 10. 清理后结果

### 10.1 三轮全量

| 轮次 | pytest 结果 | pytest 时间 | 外部 wall time |
| --- | --- | ---: | ---: |
| After 1 | 2819 passed, 7 skipped | 30.86s | 31.52s |
| After 2 | 2819 passed, 7 skipped | 31.53s | 32.17s |
| After 3 | 2819 passed, 7 skipped | 30.16s | 30.84s |
| 中位数 | 三轮全部通过 | 30.86s | 31.52s |

外部 wall time：

```text
Before: 37.58, 37.09, 38.08
After : 31.52, 32.17, 30.84
Median saved: 6.06s
Median improvement: 16.13%
```

### 10.2 稳定性

| 项目 | 清理前 | 清理后 |
| --- | ---: | ---: |
| 三轮结果不一致 case | 0 | 0 |
| 每轮固定失败 | 8 | 0 |
| 全量成功轮数 | 0/3 | 3/3 |

清理前八项是确定性基线故障，不是 flake。清理后没有出现新增的间歇性失败。

### 10.3 pytest skip 解释

`--collect-only` 的真实测试数为 2,824。pytest 运行摘要中的 7 skipped 包含：

1. 5 个正常收集、运行时跳过的 test case。
2. 因环境没有可选 `pypdf`，`tests/tools/test_pdf_chat.py` import skip 产生的两个
   collection pseudo-items。

本轮没有修改依赖清单，也没有为了让数字好看而安装可选依赖或删除 skipped test。

### 10.4 分层耗时

| 命令 | 结果 | 外部 wall time |
| --- | --- | ---: |
| `pytest -m "not integration and not e2e"` | 2799 passed, 7 skipped, 20 deselected | 20.65s |
| `pytest -m integration` | 10 passed, 2 collection skips, 2814 deselected | 14.20s |
| `pytest -m e2e` | 10 passed, 2 collection skips, 2814 deselected | 2.94s |
| `pytest` | 三轮全绿 | 31.52s median |

各 tier 单独运行都会重复 pytest collection/startup，因此 tier 时间不能直接相加并与全量
比较。

## 11. 文件级改动清单

| 文件 | 改动 |
| --- | --- |
| `pyproject.toml` | 注册 integration/e2e markers，不改变默认全量 |
| `tests/test_manager_skill_wiring.py` | 使用真实 `RunnerResult` test double |
| `tests/apps/test_watch.py` | 合并 subprocess harness，用输出就绪信号替代固定 sleep |
| `tests/apps/test_life_repl_free_text.py` | 三个 timeout case 注入推进式虚拟 clock |
| `tests/daemon/test_life_worker.py` | 合并连续 drain 测试；增加 integration marker |
| `tests/life/test_project_lifecycle.py` | 参数化 submission-artifact 初始状态 |
| `tests/cli/test_live_status.py` | 参数化 spinner env gate |
| `tests/manager/test_manager_session.py` | 参数化损坏状态；标记 flock 集成测试 |
| `tests/planner/test_planner.py` | 参数化 waiting reason |
| `tests/apps/test_watch.py` | 标记四个真实 subprocess 集成测试 |
| `tests/team/test_store.py` | 标记多进程 store 集成测试 |
| `tests/deployment/test_multi_process_contract.py` | 标记 e2e |
| `tests/test_aaai_compile_smoke.py` | 同时保留 skipif 和 e2e marker |
| `tests/test_aaai_venue_e2e.py` | 标记 e2e |
| `tests/test_wiki_e2e.py` | 标记 e2e |
| `argus_skill/release_manifest.json` | 生成器重建 |
| `frontend/core/src/release.generated.ts` | 生成器重建 |

## 12. 验证命令

变更文件级测试：

```bash
pytest \
  tests/core/test_release.py \
  tests/test_manager_skill_wiring.py \
  tests/apps/test_watch.py \
  tests/apps/test_life_repl_free_text.py \
  tests/daemon/test_life_worker.py \
  tests/life/test_project_lifecycle.py \
  tests/cli/test_live_status.py \
  tests/manager/test_manager_session.py \
  tests/planner/test_planner.py \
  tests/deployment/test_multi_process_contract.py \
  tests/test_aaai_compile_smoke.py \
  tests/test_aaai_venue_e2e.py \
  tests/test_wiki_e2e.py \
  tests/team/test_store.py
```

代码检查：

```bash
ruff check <changed test files>
python scripts/generate_release_manifest.py --check
git diff --check
```

全量测量：

```bash
/usr/bin/time \
  -f 'wall_seconds=%e\nuser_seconds=%U\nsystem_seconds=%S\nmax_rss_kb=%M' \
  -o run-N.time \
  python -m pytest \
  -o addopts='' -q \
  --junitxml=run-N.xml \
  --durations=100
```

每一轮使用不同的临时 `ARGUS_SKILL_HOME`。

## 13. 限制和后续注意事项

1. 缺少外部 CI 历史，所以“历史失败次数”只能标记为未知；不能声称未失败。
2. 未使用 coverage 作为删除判据。覆盖率数字不会证明两个测试的故障信号相同。
3. 共享主机仍有运行噪声，因此报告中只采用三轮中位数。
4. 当前只显式标记人工确认的 20 个真实边界测试；default fast tier 不是严格完成的
   全项目 unit 分类。
5. `pypdf` 是可选依赖，两个 collection skip 保持原状。
6. 本轮没有找到能够安全删除的“已删除功能残留测试”；没有为了交付删除数量而强行
   判定旧功能已经消失。
7. 后续若修改 marker 或 `pyproject.toml`，需要重跑 release manifest 生成器。
8. 新增固定 sleep 前应先证明 wall-clock 本身就是受保护行为；否则优先等待状态、
   event、输出或使用可推进的虚拟 clock。

## 14. 此类报告必须包含的内容

以后重复执行测试精简时，报告至少应包含：

1. 仓库、分支、基线 commit、Python、pytest 和机器信息。
2. 清理前后完全一致的测试命令。
3. 至少三轮 wall time、pytest time 和结果。
4. 每个测试的行为、耗时、结果历史、风险、独有价值和决定清单。
5. 每个删除/合并测试的替代测试与行为映射。
6. 明确保留的预算、安全、完整性、反作弊、持久化、恢复和核心路径测试。
7. 接受和拒绝候选的理由。
8. unit/default、integration、e2e 的定义和运行命令。
9. skipped、xfail、缺依赖和基线失败的解释。
10. 未知历史、共享机器噪声、可选依赖等限制。
11. 清理前后受保护行为对照，而不只是测试数量和覆盖率。
12. 完整复现命令和原始 JUnit/timing 证据位置。

## 15. 验收结论

| 验收项 | 结果 |
| --- | --- |
| 初始 2,825 个测试全部进入清单 | 通过 |
| 三轮基线均有原始 JUnit 和 wall-time 记录 | 通过 |
| 所有删除/合并都有替代行为映射 | 通过 |
| 关键风险测试未因慢而删除 | 通过 |
| 慢但有价值测试采用优化或分层 | 通过 |
| 默认 `pytest` 仍执行全部测试 | 通过 |
| 清理后三轮全量通过 | 通过 |
| 无新增跨轮不一致失败 | 通过 |
| wall time 使用中位数比较 | 通过 |
| 正式 Markdown 报告和原始证据已保存 | 通过 |
