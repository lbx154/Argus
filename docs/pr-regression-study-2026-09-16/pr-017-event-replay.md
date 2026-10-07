# lbx154/Argus#17：日志轮转造成重复事件回放

[返回实验总览](README.md)

## 1. 一句话解释

为了把轮转前的历史事件也显示出来，新代码把两个日志文件拼起来。但两个文件不是同一时刻
读到的：读到一半时日志发生轮转，已经读过的事件又进入历史文件，最后被返回了两遍。

**原报告整体判定仍是 `UNCERTAIN`。** 下面整理的是 Python 读取路径的局部见证，
不声称整个 PR 都有问题，也不声称浏览器已经出现重复执行或重复显示。

## 2. PR 与精确对照

| 项目 | 值 |
|---|---|
| PR | [lbx154/Argus#17](https://github.com/lbx154/Argus/pull/17) |
| 标题 | Fix cross-platform runtime and event-stream reliability |
| 合入时间 | 2026-08-10 03:23:33 UTC |
| Base | `1a181706dbdbc6427e53beb91f307f8057f4a391` |
| Candidate merge | `6bcbbac6a98db6cd6ff49681ce181f0ddae8652a` |
| 集成差异 | 49 个路径，包含回放、锁、状态恢复、前端和发布产物 |
| 原报告局部发现 | `F1` |

比较的是 merge 第一父提交与 merge，不是当前 main。新增历史回放本身是合理改进；
要保护的性质是：**一次回放不应重复发出同一个有唯一身份的事件。**
它不要求旧版本已经做到完整无遗漏。

## 3. 根因与触发顺序

关键历史源码：

- [当前日志与历史文件合并](https://github.com/lbx154/Argus/blob/6bcbbac6a98db6cd6ff49681ce181f0ddae8652a/argus_skill/apps/cli/_follow.py#L402-L432)
- [WebSocket 回放生成器](https://github.com/lbx154/Argus/blob/6bcbbac6a98db6cd6ff49681ce181f0ddae8652a/argus_skill/webapi/server.py#L332-L411)
- [真实日志轮转逻辑](https://github.com/lbx154/Argus/blob/6bcbbac6a98db6cd6ff49681ce181f0ddae8652a/argus_skill/life/event_log.py#L328-L354)

两边安排相同的并发交错：

1. Reader 从当前日志读到了事件 `[1]`。
2. Writer 追加事件 `2`，文件达到轮转大小；追加事件 `3` 时，旧文件被轮转为 `.1`。
3. 此时 `.1` 包含 `[1,2]`，新的当前日志包含 `[3]`。
4. Candidate 把刚读到的 `.1=[1,2]` 与先前缓存的 current=`[1]` 合并。

去重算法只比较“previous 的后缀”和“current 的前缀”。`[1,2]` 的后缀不是 `[1]`，
因此没有识别出同一 generation 的旧快照包含关系，拼出了 `[1,2,1]`。

## 4. 保存的双版本观测

| 到达的生产路径 | Base 返回事件 ID | Candidate 返回事件 ID |
|---|---|---|
| REST recent-events helper | `[1]` | `[1,2,1]` |
| WebSocket event generator | `[1,3]` | `[1,2,1,3]` |
| 没有轮转交错的对照 | `[1]` | `[1]` |

最初的机制 probe 缩短了轮转阈值；后续确认使用生产代码支持的 **1 MiB
（1,048,576 字节）阈值**，不再依赖该缩短。初始日志为 1,047,661 字节，
追加事件 `2` 后为 1,048,794 字节，追加事件 `3` 触发真实轮转。
每个 reader 重复三次，结果都是 base 保持唯一性、candidate 重复事件 `1`。
两边初始和最终文件哈希一致。

| Probe | Base | Candidate | 每侧命令耗时 |
|---|---|---|---|
| `rotation-race` | 4 passed，exit 0 | 2 passed / 2 failed，exit 1 | 0.716s / 0.715s |
| `rotation-supported` | 6 passed，exit 0 | 6 failed，exit 1 | 0.915s / 1.016s |

这些耗时只属于 probe 命令，不包括约 8.93 分钟的完整 PR 分析。
没有额外模型调用；实际 Reader、Writer 和文件轮转参与执行，没有手写替代实现。

## 5. 为什么不是正常改进或既有问题

增加不同历史事件是本次修改的预期效果；重复同一事件不是。无交错对照两边都正常，
而相同文件内容、相同交错下只有 candidate 新增重复，因此见证不只是“新旧返回内容不同”。

同时，**base 没读到事件 `2`**。这个见证只支持它在该场景下没有重复，不能把 base
说成完整、正确、无丢失的事件流。两个 reader 体现的是同一个合并机制问题，
不是两个独立缺陷。

## 6. 复现材料

本机证据根目录：

```text
/home/chentianyu/argus-experiments/pr-regression-50-20260916/results/pr-17/
```

| 相对路径 | 用途 |
|---|---|
| `REPORT.md`、`report.json` | 原始完整解释与局部发现 `F1` |
| `base-probes/`、`candidate-probes/` | 原始测试文件名及所需辅助文件 |
| `evidence/rotation-race/receipt.json` | 初始机制见证 |
| `evidence/rotation-supported/receipt.json` | 支持阈值下的命令、SHA、耗时和退出码 |
| `evidence/rotation-supported/base.log`、`candidate.log` | 实际输出及日志哈希所绑定的内容 |
| `evidence/test_rotation_threshold_probe.py` | 支持阈值下的最小复现源文件 |
| `evidence/inventory.json`、`hypotheses.json` | 输入和改动清单、预先列出的假设 |

恢复两个历史源码快照及保存的 probe 后，在各自隔离容器的源码根执行同一命令：

```bash
python -m pytest -p no:cacheprovider -s \
  tests/_regression_probe/test_rotation_threshold_probe.py
```

环境为 Linux、Python 3.11.16、pytest 8.4.2，每侧 120 秒时限和独立状态根。
这不是可以直接在现有 Argus 项目中执行的操作指令。

## 7. 结论边界与报告状态

前端缺少 Node/TypeScript 及依赖，原生 Windows/macOS、UI 去重效果和实际服务负载未运行；
`view=ui` REST 路径也不是本次已触发的路径。因此整体保留 `UNCERTAIN`。
不能由事件重复推导出重复 Manager 执行、任务重复提交或线上发生率。

原始运行器因“整体不确定 + 局部确认”拒收报告，后处理保留原结论并恢复其证据汇总。
原报告称 tracked source 未改动，但这份报告没有到达原运行器后续的源码不可变性审计；
后处理也没有补做或独立裁决 oracle。本次只整理历史证据，没有复核最新 main 是否仍受影响。
