# lbx154/Argus#38：文件名编码升级后，旧任务和退出证据失联

[返回实验总览](README.md)

## 1. 一句话解释

系统把任务记录从旧文件名换成了新编码，但升级时没有把旧记录迁移，也没有兼容查找。
于是旧任务“扫目录时还看得见，按 ID 查找时却找不到”：它可能重新创建同名逻辑任务，
也可能找不到已经存在的退出结果。

原始整体判定为 **`UNCERTAIN`**。报告中 `R1`、`R2` 是同一个缺少迁移机制在两个
持久化接口上的见证，不应直接解释为两个独立编码缺陷。

## 2. PR 与精确对照

| 项目 | 值 |
|---|---|
| PR | [lbx154/Argus#38](https://github.com/lbx154/Argus/pull/38) |
| 标题 | fix(windows): merge startup and recovery hardening |
| 合入时间 | 2026-08-14 10:11:40 UTC |
| Base | `ce02e2fbf61e5667c225fbdc292db47b6ee10bcb` |
| Candidate merge | `4b460b3ce0b48457b94e1c4c0a39dee2dff58cae` |
| 集成差异 | 46 个路径 |
| 原报告局部发现 | `R1`：重复分配；`R2`：旧记录/退出证据查找失败 |

统一可移植文件名有合理动机，但 task ID 对应的持久化身份需要跨版本保持可访问。
不能只验证“新版本创建的新记录能被新版本读取”，还必须覆盖已有记录升级后的行为。

关键历史源码：

- [新的可移植文件名编码](https://github.com/lbx154/Argus/blob/4b460b3ce0b48457b94e1c4c0a39dee2dff58cae/argus_skill/core/portable_filename.py#L16-L45)
- [Task board 的文件名选择](https://github.com/lbx154/Argus/blob/4b460b3ce0b48457b94e1c4c0a39dee2dff58cae/argus_skill/team/task_board.py#L26-L37)
- [Task board 读取、刷新和认领](https://github.com/lbx154/Argus/blob/4b460b3ce0b48457b94e1c4c0a39dee2dff58cae/argus_skill/team/task_board.py#L64-L159)
- [Subagent registry 的读取与 terminal reconciliation](https://github.com/lbx154/Argus/blob/4b460b3ce0b48457b94e1c4c0a39dee2dff58cae/argus_skill/tools/subagent/_registry.py#L173-L245)

## 3. 输入确实来自旧版本，而不是手写一个不存在的旧格式

先在 base 上调用真实 `form`、`claim_top`、`heartbeat` 生成 running task 记录，
保存成 `evidence/legacy-task-records.json`，SHA-256 为：

```text
87e8734efcef8681f25cd3ed9bedf41e22365ba899011c29bf80bc53ba88be85
```

再把相同记录作为两边的升级输入。普通 ID、POSIX 的 `~persisted-task`，
以及 Windows 文件名策略下的 `team::task` 都有同版本 roundtrip 对照。

前者在 candidate 上改为编码路径：

```text
旧：~persisted-task.json
新：~fnBlcnNpc3RlZC10YXNr.json
```

`form`、`heartbeat` 和 mutation 只查新路径，而全目录枚举仍能看到旧 JSON。
这两种访问方式没有迁移或旧路径兼容来对齐。

## 4. R1：刷新旧任务后，同一工作被第二个人认领

保护的性质：**刷新 campaign 必须保留现有 live owner，不能在没有合法重新分配的情况下
把同一个工作重新变成 pending。** 两边的 `form` 都声明保留活跃 ownership，
`claim_top` 又只分配 pending 工作，公共 team 输入没有排除被测 ID。

升级 probe 以 running、owner=`w1` 的真实旧记录开始；执行时间 `10` 的 heartbeat，
再 re-form，最后由 `w2` 在时间 `11` 请求认领。

| 观测：POSIX `~persisted-task` | Base | Candidate |
|---|---|---|
| Heartbeat 后时间戳 | `10` | 仍为 `2` |
| Re-form 后记录 | 一个 running `w1` | 旧 running `w1` + 新 pending |
| `claim_top(..., "w2")` | `None` | 返回同一 task ID，owner=`w2` |
| 最终逻辑任务身份 | 一个任务、一个 owner | 同一 task ID 对应两个分配记录 |

旧记录没有消失，但新路径查不到它，就新建了一份可认领记录。
这里证明的是重复**分配**，没有启动两个真实 teammate 进程来执行同一任务。

## 5. R2：旧 subagent 记录和 matching exit sidecar 不再能被查到

另一个接口采用同一编码 helper。场景有 running 的旧记录、`run_id=run-A`、缺失的
owner PID，以及匹配当前 run 的 exit sidecar，内容为 `0`。

保护的性质不是“所有任务都必须成功”，而是：**应能找到同一任务/当前 run 已存在的退出证据，
不能把它误判为根本不存在。**

| 观测 | Base | Candidate |
|---|---|---|
| 按 task ID 读取旧记录 | 找到 | `None` |
| 枚举目录能否看到旧记录 | 能 | 能 |
| 匹配当前 run 的 sidecar 是否存在、内容为 0 | 是 | 是 |
| Terminal reconciliation | `done`，exit 0 | `crashed`，声称没有 exit sidecar |

Registry 的 base 写入控制也观察到同样的旧文件名规则；不是仅凭假设选择了一个名字。
Windows 文件名策略也表现出旧 hash 与新 base64 key 不兼容，但它只在 Linux 上执行了
文件名逻辑，不代表完整验证了原生 Windows 文件系统。

## 6. 配对结果与正常对照

| Probe | Base | Candidate | 每侧命令耗时 |
|---|---|---|---|
| `task-roundtrip` | 3 passed，exit 0 | 3 passed，exit 0 | 0.565s / 0.565s |
| `task-upgrade` | 3 passed，exit 0 | 1 passed / 2 failed，exit 1 | 0.515s / 0.565s |
| `registry-recovery` | 26 passed，exit 0 | 24 passed / 2 failed，exit 1 | 0.615s / 0.665s |

普通 ID 的升级、同版本 roundtrip 和 23 个未改变的 task-board/status 测试均正常。
这种对照区分了合理的新编码与不合理的历史状态失联。
本 PR 总共使用 6 组配对 probe，完整分析约 8.94 分钟；表中秒数只属于三组相关命令。

## 7. 复现材料

本机证据根目录：

```text
/home/chentianyu/argus-experiments/pr-regression-50-20260916/results/pr-38/
```

| 相对路径 | 用途 |
|---|---|
| `REPORT.md`、`report.json` | 原始解释与两个局部发现 |
| `base-probes/`、`candidate-probes/` | 实际运行的 `test_task_persistence.py` 等文件 |
| `evidence/legacy-task-records.json` | 由旧版本真实生成的升级输入 |
| `evidence/task-roundtrip/receipt.json` | 旧记录生成及同版本正常对照 |
| `evidence/task-upgrade/receipt.json`、`base.log`、`candidate.log` | R1 配对输入、命令和实际观测 |
| `evidence/registry-recovery/receipt.json`、`base.log`、`candidate.log` | R2 配对证据 |
| `evidence/task_persistence_probe.py`、`registry_recovery_probe.py` | 证据目录中的复现源文件副本 |
| `evidence/observations.json`、`input-audit.json`、`hypotheses.json` | 状态观测、版本/环境和初始假设 |

恢复历史源码与完整保存的 probe 目录后，每侧执行：

```bash
python -m pytest -p no:cacheprovider -s \
  tests/_regression_probe/test_task_persistence.py -k upgrade
python -m pytest -p no:cacheprovider -s \
  tests/_regression_probe/test_registry_recovery.py \
  tests/team/test_task_board.py tests/tools/test_subagent_status.py
```

环境为 Linux、Python 3.11.16、pytest 8.4.2、portalocker 4.3.2，状态根和 HOME
为独立临时目录，每侧时限 120 秒。复现应保留生成旧记录这一步及其源身份，不能拿
candidate 新建的记录代替跨版本输入。

## 8. 为什么整体仍然不确定

PR 还改了启动流程、前端提交、预热、进程生命周期和默认 reasoning effort。
Node/npm、原生 Windows 以及实际模型行为未覆盖，原报告因此保留 `UNCERTAIN`。
一次固定 backend 回复只支持请求构造与解析检查，不支持 classifier 准确率结论。
没有实际 provider/ACP 会话、真实 Argus 任务或额外模型运行。

这两个见证说明历史记录兼容性有问题，不证明线上重复执行的频率，也不证明整个 PR
或所有平台均退化。它们共享缺少迁移的根因，不能把数量简单累加成两个独立缺陷。

该报告原先因整体与局部判定并存而被拒收，后处理没有修改结论或重新执行 probe。
原报告的源码未改动声明没有获得原运行器后续的源码不可变性审计；后处理未补做。
本次只整理保存证据，没有进行独立语义裁决或最新 main 复现。
