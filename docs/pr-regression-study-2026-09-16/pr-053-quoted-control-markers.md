# lbx154/Argus#53：把引用示例误当成任务控制指令

[返回实验总览](README.md)

## 1. 一句话解释

Agent 说：“说明书里举例写着‘任务已完成’，但我的报告还没写完。”
新版本去掉示例的 Markdown 格式后，把里面的完成标记当成了当前任务的完成声明。

**本 PR 在原始 50 个样本中，报告结论为 `REGRESSION`，并非四个混合判定案例之一。**
它一直包含在总览里；本文件补充独立案例说明，不增加样本数，也不改变原始结论。

原实验记录了两个有配对证据的解析问题：`F1` 错误完成，`F2` 错误操作选项。
其中 `F1` 另于 2026-09-16 在当时最新 main 上单独复现。
两者均不能直接解释整个版本的效果退化或真实模型的触发频率。

## 2. PR 与精确对照

| 项目 | 值 |
|---|---|
| PR | [lbx154/Argus#53](https://github.com/lbx154/Argus/pull/53) |
| 标题 | runtime: reduce repeated token context |
| 合入时间 | 2026-08-15 14:57:37 UTC |
| Base | `3662973a0d0362844d33538121fd5e08052c6d76` |
| Candidate merge | `59965faa2eb1599ac9cfef4d544f4b1c16716bab` |
| 集成差异 | 21 个路径，包含 prompt、路由默认值、控制标记解析及发布产物 |
| 原始运行器 | `completed`；不属于后处理恢复的 9 份拒收报告 |
| 原报告局部发现 | `F1`、`F2` |

这个 PR 的一个合理目标是：Agent 已经完成工作，却因为完成标记带了反引号而未被识别，
导致 harness 继续买入无必要的工作轮次。因此，接受 Markdown 包裹的控制字段本身并不错误。
问题在于扩大识别范围时，没有同步确定哪些字段属于正式的最终交接。

## 3. F1：未完成报告被标成 done

### 输入与适用模式

任务要求先生成 `final-report.txt` 才能完成。文件实际不存在。
使用 `require_independent_review=False`、fresh session 和一轮上限。
确定性 backend 返回以下原始反例：

```text
Inspected the README. It documents the following example control line:
`MILESTONE_STATUS=done`
This is quoted documentation, not my completion status. The required final-report.txt has NOT been written.
MILESTONE_STATUS=continue
OPERATOR_QUESTION=none
OPERATOR_OPTIONS=none
```

保护的性质是：**正文引用的示例不能覆盖最终的 continue，更不能让必需产物缺失的任务
被宣告完成。** 这不是要求新版本复刻旧版所有行为，而是来自任务验收条件及最终结论优先的约定。

### 新旧版本实际表现

| 观测 | Base | Candidate |
|---|---|---|
| 真实 self-review handler 是否到达 | 是 | 是 |
| 必需报告是否存在 | 否 | 否 |
| 被测 backend 调用 | 只有固定的 `engineer-r1` 回复 | 相同 |
| 最终状态 | `max_rounds`，没有完成 | **`done`** |
| Completion source | 无 | **`engineer_self_review`** |

关键历史代码是
[反引号规范化](https://github.com/lbx154/Argus/blob/59965faa2eb1599ac9cfef4d544f4b1c16716bab/argus_skill/engineer/round_self_review.py#L23-L27)
与
[全文 any 匹配](https://github.com/lbx154/Argus/blob/59965faa2eb1599ac9cfef4d544f4b1c16716bab/argus_skill/engineer/round_self_review.py#L68-L71)：

```text
去掉引用示例两边的反引号
-> 全文任意一行匹配到 MILESTONE_STATUS=done
-> 后面的 continue 不能撤销这个匹配
-> 走 self-review 完成路径
```

### 正常对照与既有问题

- 去掉引用的 done 示例：两边都保持未完成。
- 真正生成报告，再给出带反引号的完成标记：candidate 正确完成，体现原 PR 的合理改进。
- 把较早的示例改成不带反引号：两边都可能错误完成，这是既有弱点；本次新回归是将其
  扩大到 base 原本不当作完成指令的 Markdown 输入。
- 强制独立 Reviewer 且 Reviewer 返回 continue：没有因为这个标记就完成。

测试只固定外部 backend 回复，不替换解析、self-review gate、状态结算或事件生成。
真实路径是否到达、队列是否耗尽、文件是否存在均有观测。

## 4. F2：说明书里的选项替换了真正要问用户的选项

同类输入先引用：

```text
The manual contains this example; it is not the current decision:
`OPERATOR_OPTIONS=example :: Example only :: Do not choose this example`
```

随后给出真正的最终交接：

```text
OPERATOR_QUESTION=Which current report format should be used?
OPERATOR_OPTIONS=current-a :: Current A :: Use current A; current-b :: Current B :: Use current B
```

Base 保留当前问题及 `current-a`、`current-b`。Candidate 保留当前问题，但选项变成
`example`，并把部分中间正文吸入选项说明。

[共享选项解析器](https://github.com/lbx154/Argus/blob/59965faa2eb1599ac9cfef4d544f4b1c16716bab/argus_skill/core/operator_decision.py#L54-L77)
先规范化所有 Markdown 行，然后使用第一个匹配，导致较早的示例抢走最终结论的优先级。
见证到达 Reviewer 文本解析和 Engineer 的 blocked 交接两条消费者路径；它们体现的是
同一个选项解析问题，不是两个独立缺陷。

## 5. 原实验配对证据与成本

| Probe | Base | Candidate | 每侧命令耗时 |
|---|---|---|---|
| `completion-witness` | exit 0，未把未完成任务宣告为 done | exit 1，到达错误完成断言 | 0.515s / 0.765s |
| `shared-role-options` | 5 passed，exit 0 | 3 passed / 2 failed，exit 1 | 0.565s / 0.565s |

F1 的确认 probe 合计约 1.28 秒，不包含找问题、构造输入或启动分析容器的时间。
本 PR 完整分析约 8.59 分钟，共用了 5 组配对 probe。
没有为了验证这些条件而调用真实被测模型或启动 Argus daemon。

原实验本机证据根目录：

```text
/home/chentianyu/argus-experiments/pr-regression-50-20260916/results/pr-53/
```

| 相对路径 | 内容 |
|---|---|
| `REPORT.md`、`report.json`、`runner-result.json` | 原始解释、8 个假设中的两个局部发现和运行器状态 |
| `evidence/test_runtime_boundaries.py` | 严格 backend fixture、生产路径观测和相邻对照 |
| `evidence/test_completion_witness.py` | 原始反例与两个正常对照 |
| `evidence/test_shared_reply.py` | Engineer/Reviewer 选项解析对照 |
| `evidence/completion-witness/receipt.json`、`base.log`、`candidate.log` | F1 的精确命令、版本、退出码与日志 |
| `evidence/shared-role-options/receipt.json`、`base.log`、`candidate.log` | F2 的配对证据 |
| `base-probes/`、`candidate-probes/` | 原始测试文件布局 |

在独立容器中还原两个历史版本及保存的完整 probe 目录后，F1 每侧的命令为：

```bash
python -m pytest -q -s -p no:cacheprovider \
  tests/_regression_probe/test_completion_witness.py
```

不要在现有活动 Argus 项目中直接执行。配对日志和原运行器的源码审计不是独立语义裁决，
也不证明所有其他改动均安全。

## 6. 2026-09-16 的 main 追查：F1 在旧文本回退路径仍可复现

这是原 50-PR 批次结束后的单独复现，不计入前面的 149 组配对 probe，也没有重写原实验结果。

| 项目 | 记录 |
|---|---|
| 当时 main SHA | `cc934eed184e32277a56b224a7785a2f28b7621f` |
| 记录时间 | 2026-09-16 11:33:33 UTC |
| 原反例是否改写 | 否，历史 helper 和反例文件保持相同内容 |
| 原输入的实际结果 | 文件不存在，仍返回 `done` / `engineer_self_review` |

源码包已从 `argus_skill` 改名为 `argus`，保留兼容 import 别名。
该版本的
[完成状态选择](https://github.com/lbx154/Argus/blob/cc934eed184e32277a56b224a7785a2f28b7621f/argus/engineer/round_self_review.py#L46-L54)
优先使用结构化 decision，其次读取最终 `Decision:` footer。
但
[无显式 footer 的兼容分支](https://github.com/lbx154/Argus/blob/cc934eed184e32277a56b224a7785a2f28b7621f/argus/core/role_reply.py#L53-L62)
仍返回整段正文，再执行 `any(...)`。

因此，复现范围明确限定为：

```text
不要求独立 Reviewer
且没有已记录的结构化 decision
且没有显式 Decision: footer
且正文较早引用了 Markdown done 示例
且最终明确给出 MILESTONE_STATUS=continue
=> 报告不存在，仍被错误宣告完成
```

补充对照中，显式 `Decision:` footer 内的 continue 使任务保持未完成；
结构化 `status=continue` 的完成选择器单元检查返回 false；强制独立 Reviewer 的场景
也没有错误完成。结构化选择器单元检查不等于验证了完整结构化任务流程。
此次 main 追查只针对 F1，没有确认 F2 在该版本中的状态。

追查证据保存在本机：

```text
/home/chentianyu/.copilot/session-state/391859ca-39ac-4dd4-9a4a-2fcadd0eb3b8/files/pr53-latest-check/
```

其中 `summary.json` 保存源 SHA、观测和证据哈希；`latest.log`、`controls.log` 保存实际输出；
`evidence/` 保留原反例、补充对照与相关源码。原临时说明为同级的
`argus-pr53-counterexample.md`。

**“最新 main”仅指上述 2026-09-16 查询时点，不是本文件整理时重新查询的版本。**
该次执行使用离线容器、独立状态和固定回复，不涉及真实模型、现有 Argus 服务或任务。

## 7. 修复方向与不能外推的结论

已讨论的最小方向是统一最终状态提取，优先采用结构化 decision 或明确结论区，
旧格式也不要再用全文 `any(done)`。保留真正的 Markdown 完成标记兼容，
不要为了修复这个反例强制所有任务走额外 Reviewer 或新增 LLM 调用。
这只是修复建议，本案例没有提交源码修复。

最后一次状态声明优先可以处理当前反例，但不能完全区分只有一条 done 的旧格式回复
究竟是引用还是正式交接。更强的保证需要明确控制边界；不能靠修补一个字符串规则
宣称所有引用注入、任务验收或 stage-certification 问题均已解决。
