# lbx154/Argus#29：旧 running 快照清空任务完成回执

[返回实验总览](README.md)

## 1. 一句话解释

任务实际上已经完成，但页面请求先读到“它还在运行”的旧记录，随后才读到新的完成结果。
新代码把那条旧记录误当成任务重新开始的证据，于是在返回页面的数据中清空了完成摘要和时间。

**原报告整体判定为 `UNCERTAIN`。** 局部见证发生在 Python snapshot 路径；
没有证明持久化数据被删除，也没有证明 Reviewer 或任务执行状态的权威被绕过。

## 2. PR 与精确对照

| 项目 | 值 |
|---|---|
| PR | [lbx154/Argus#29](https://github.com/lbx154/Argus/pull/29) |
| 标题 | Preserve full mission output in the web UI |
| 合入时间 | 2026-09-06 19:14:37 UTC |
| Base | `86b1e9b895bf48bdfc3605ee246f7cec317e1624` |
| Candidate merge | `ca088b37d5b889ca25583e6be5208c22bdf111b0` |
| 集成差异 | 31 个路径，覆盖完整输出、snapshot、Web/TUI、协议和发布产物 |
| 原报告局部发现 | `F1` |

PR 希望保留完整任务输出，同时避免新一轮任务显示上一轮的旧结果。清理旧输出是合理需求，
但前提应该是确实出现了新的执行边界，而不是仅观察到一条较旧的 running 记录。

保护的性质是：**同一次已完成任务的较早 running 观测，不能在没有新 start 证据时，
清掉已经存在的完成回执。**

## 3. 根因与可达交错

关键历史源码：

- [新增的 snapshot 清空逻辑](https://github.com/lbx154/Argus/blob/ca088b37d5b889ca25583e6be5208c22bdf111b0/argus_skill/core/mission_view/_snapshot.py#L92-L109)
- [响应中的 live overlay](https://github.com/lbx154/Argus/blob/ca088b37d5b889ca25583e6be5208c22bdf111b0/argus_skill/core/mission_view/_snapshot.py#L394-L423)
- [先读取 backlog](https://github.com/lbx154/Argus/blob/ca088b37d5b889ca25583e6be5208c22bdf111b0/argus_skill/webapi/project_state.py#L661-L674)
- [随后读取 mission view](https://github.com/lbx154/Argus/blob/ca088b37d5b889ca25583e6be5208c22bdf111b0/argus_skill/webapi/project_state.py#L718-L725)

这两次读取没有共享事务，完成事件可以发生在它们之间：

1. 请求捕获任务 `paired-task` 的 running backlog 行。
2. 实际任务完成，持久化 backlog 变成 done，随后写入完成事件。
3. 请求读取较新的 mission view，里面已有 `summary` 和 `completed_at`。
4. 最后把第 1 步捕获的旧行覆盖到第 3 步的新视图上。

Candidate 只根据“存在 active 行 + 视图已有完成状态”触发重置，缺少同一 attempt 内
claim/start/completion 的新旧关系判断，因此旧观测能够清掉新回执。

## 4. 保存的双版本观测

固定输入使用同一个任务 ID、开始时间 `10`、完成时间 `20`，分别测试 claim 时间
`10` 和 `9.5`。真实 `Backlog` 持久化、事件 reducer、view loader 和 snapshot 函数均参与；
只控制时钟和读写交错，没有替换 snapshot 实现。

| 观测 | Base | Candidate |
|---|---|---|
| 返回的完成摘要 | `Completed receipt` | 空 |
| 返回的 `completed_at` | `20` | `null` |
| 持久化 mission state 被 overlay 修改 | 否 | 否 |
| 后续一致读取能恢复/保留回执 | 是 | 是 |
| 同一 terminal backlog 项实际重新改成 running | 被拒绝 | 被拒绝 |

两种 claim 时间的配对输入哈希分别为
`4f4b814457a880eb762024b08abd200e99a2d2868a5cce94a1e9c2b86cb9024d`
和 `dca8115be4ad5139d06d66a7bf45707793c562edc3f5ef020573f255d82793ed`；
base 与 candidate 相同。

| Probe | Base | Candidate | 每侧命令耗时 |
|---|---|---|---|
| `snapshot-receipt` | 4 passed，exit 0 | 2 passed / 2 failed，exit 1 | 0.565s / 0.565s |
| `exact-replay-and-artifacts` | 7 passed，exit 0 | 5 passed / 2 failed，exit 1 | 1.367s / 1.317s |

后一组还包括协议、静态产物和长输出边界的正常对照。完整 PR 分析约 8.95 分钟；
表中秒数只是命令执行时间。

## 5. 为什么属于局部回归

同一 terminal 项不能原地重新 running，是原有 backlog 契约，不是仅从 base 的表现
反推出来的要求。完成与 claim 的固定时间证明这是旧观测，而不是真正的新任务开始。
一致视图和明确的新 start 对照也用于区分正常清理旧输出的预期变化。

必须保留三个限制：

- **返回 `status="working"` 在两边都存在，是既有问题，不是此次见证。**
- 新增的回归是完成摘要和完成时间被清空；candidate 的完整输出也被清空，但 base
  原本没有该新字段，不能只凭该字段差异建立回归。
- 持久化数据仍在，连贯刷新会恢复；这是响应视图的暂时丢失，不是永久删除任务结果。

## 6. 复现材料

本机证据根目录：

```text
/home/chentianyu/argus-experiments/pr-regression-50-20260916/results/pr-29/
```

| 相对路径 | 用途 |
|---|---|
| `REPORT.md`、`report.json` | 原始说明与 `F1` |
| `base-probes/`、`candidate-probes/` | 原始 probe 目录 |
| `evidence/snapshot-receipt/receipt.json` | 首次局部见证 |
| `evidence/exact-replay-and-artifacts/receipt.json` | 固定输入重现与正常对照 |
| `evidence/exact-replay-and-artifacts/base.log`、`candidate.log` | 真实观测及 traceback |
| `evidence/test_snapshot_exact.py`、`test_snapshot_receipt.py` | 最小状态重放 |
| `evidence/source-excerpts.json`、`observations.json`、`probe-hashes.json` | 源码位置、观测和测试文件身份 |

恢复两个历史版本及保存的 probe 后，每个隔离环境中的固定重现命令为：

```bash
python -m pytest -q -s -p no:cacheprovider \
  tests/_regression_probe/test_generated_protocol.py \
  tests/_regression_probe/test_release_artifacts.py \
  tests/_regression_probe/test_runtime_handoff.py \
  tests/_regression_probe/test_snapshot_exact.py
```

运行条件为 Linux、Python 3.11.16、pytest 8.4.2、每侧独立状态和 120 秒时限。
没有启动真实任务或 supervisor tick。

## 7. 为什么整体仍然不确定

Node/npm 和前端依赖缺失，两边的前端命令都在执行测试前以 127 退出。
TypeScript 中有类似重置逻辑，但没有用 Python 替代实现冒充 JavaScript 执行证据；
Web/TUI 渲染和 operator 文本过滤尚未判定。

两边 release manifest 的精确源码检查也都失败。这是共同不匹配，不是候选独有回归；
后续单独运行的协议与资产引用检查不能证明构建可复现或 bundle 语义正确。

原始运行器因混合判定拒收，后处理恢复报告与 receipt 的一致性汇总但不改其
`UNCERTAIN`。这份报告未走到原运行器的源码不可变性审计，分析器的未改源码自述
不是该审计的替代。本次未重新运行或独立裁决，也未确认最新 main 的状态。
