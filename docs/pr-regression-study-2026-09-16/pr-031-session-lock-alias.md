# lbx154/Argus#31：同一目录的两个名字绕过同一把锁

[返回实验总览](README.md)

## 1. 一句话解释

同一个房间可以从长地址或短门牌进入，但两个人必须拿同一把锁。PR 为了处理长路径，
让长地址使用“哈希锁名”、短地址使用“普通锁名”。如果两个地址实际上指向同一目录，
两个人就能各拿一把不同的锁，同时改同一个文件，最后一个人的更新被覆盖。

**原始整体判定为 `UNCERTAIN`。** 局部证据是在 Linux 上通过真实文件锁与独立 Python
进程观察到的状态丢失；不是用 Linux 模拟结果宣称整个 Windows 实现有缺陷。

## 2. PR 与精确对照

| 项目 | 值 |
|---|---|
| PR | [lbx154/Argus#31](https://github.com/lbx154/Argus/pull/31) |
| 标题 | fix(windows): harden runtime lifecycle and recovery |
| 合入时间 | 2026-08-13 11:08:41 UTC |
| Base | `6971b202700476d82226e0bcc3a384b3ea8777d2` |
| Candidate merge | `6df9192a18eb0814a72fd0331667080615e4776d` |
| 集成差异 | 153 个路径 |
| 原报告局部发现 | `F1-session-alias-lost-update` |

本次 PR 包含可移植锁、Windows 生命周期、预热、路由输入和界面等多项修改。
缩短长路径下的锁文件名有合理动机，但必须保持同一会话的锁身份一致。

保护的性质来自两边都保留的 `update_session_meta` 原子 read/mutate/replace 契约：
**针对同一 metadata 文件的两个已完成更新，如果改的是不同字段，应同时保留下来。**

## 3. 根因与触发条件

关键历史源码：

- [Candidate 的锁路径选择](https://github.com/lbx154/Argus/blob/6df9192a18eb0814a72fd0331667080615e4776d/argus_skill/core/session.py#L172-L205)
- [Candidate 的原子 metadata 更新](https://github.com/lbx154/Argus/blob/6df9192a18eb0814a72fd0331667080615e4776d/argus_skill/core/session.py#L234-L255)
- [Base 的锁实现](https://github.com/lbx154/Argus/blob/6971b202700476d82226e0bcc3a384b3ea8777d2/argus_skill/core/session.py#L162-L182)
- [配置状态根的路径处理](https://github.com/lbx154/Argus/blob/6df9192a18eb0814a72fd0331667080615e4776d/argus_skill/core/paths.py#L34-L61)

新 helper 根据路径的字面绝对长度是否达到 240 字符，决定用可读锁名还是哈希锁名，
且该判断也作用于 POSIX。它没有保证同一个状态目录的别名作出同一选择。

触发环境是一个真实长路径，以及指向该目录的较短 symlink；两者的 metadata
通过 `samefile` 确认是同一对象。两种拼法对应的预期可读锁路径长度为 115 与 412，
恰好落在阈值两侧。

相同 session ID `s-probe` 在 candidate 中分别选中：

```text
s-probe.lock
s-probe-6df784290ce6aed61724846b.lock
```

锁目录是同一个，锁文件却是两个。`session_meta_lock` 和 `session_lifecycle_lock`
都使用这个 helper，因此两条路径都能发生重叠。

## 4. 最小并发交错与实测结果

初始 metadata 的 `objective` 为 `seed`。

1. Parent 通过一个目录拼法进入真实更新事务，持锁读取旧 metadata。
2. Child 通过另一个拼法进入，试图写入 `objective="child"`。
3. Parent 在自己的事务里写入另一字段 `display_name="parent"`。
4. 比较两个事务结束后的最终文件，并观察 child 是否在 parent 仍持锁时完成。

| 实际观测 | Base | Candidate |
|---|---|---|
| 两个根与 metadata 是否为同一对象 | 是 | 是 |
| Parent 持锁时 child 已完成更新 | 否 | **是** |
| 最终 `display_name` | `parent` | `parent` |
| 最终 `objective` | `child` | **`seed`** |
| metadata/lifecycle 锁是否重叠进入 | 否 / 否 | **是 / 是** |

Candidate 中 child 确实记录了已完成的更新，随后 parent 写回旧快照，丢掉该字段。
这比单纯观察“测试失败”更具体：是已经发生的 persisted-state lost update。
生产锁、更新函数和内核文件系统没有被 mock；独立 Python 进程不等于启动 Argus daemon。

## 5. 重复与正常对照

| Probe | Base | Candidate | 每侧命令耗时 |
|---|---|---|---|
| `session-alias` | 3 passed，exit 0 | 3 failed，exit 1 | 2.569s / 0.915s |
| `session-alias-controls` | 3 passed，exit 0 | 2 passed / 1 failed，exit 1 | 2.570s / 2.017s |
| `session-alias-repeat` | 9 passed，exit 0 | 9 failed，exit 1 | 7.580s / 2.869s |

原始见证加三轮重复均出现相同结果，反转长/短别名的使用方向仍丢更新。
两边始终使用同一个长根、或始终使用同一个短根时，都能保留两个字段。
这把问题缩小到别名导致的锁身份分裂，而不是泛指 portalocker 不可靠或子进程启动过慢。

另有 11 个共同的邻近契约通过。预热非阻塞行为在 candidate 反而改善，这被保留为
正常改进，不计作候选回归。完整 PR 分析约 11.14 分钟，与上述 probe 秒数不同。

## 6. 复现材料

本机证据根目录：

```text
/home/chentianyu/argus-experiments/pr-regression-50-20260916/results/pr-31/
```

| 相对路径 | 用途 |
|---|---|
| `REPORT.md`、`report.json` | 原始判定、假设、适用性质 |
| `base-probes/test_session_alias.py` | 原始三案例见证 |
| `base-probes/test_session_alias_controls.py` | 同一根与反向别名对照 |
| `candidate-probes/` | 候选侧保存的对应测试文件 |
| `evidence/session-alias/receipt.json`、`base.log`、`candidate.log` | 首次配对执行与实际观测 |
| `evidence/session-alias-controls/receipt.json` | 正常与反向对照 |
| `evidence/session-alias-repeat/receipt.json` | 三轮重复命令与日志绑定 |
| `evidence/observations.json`、`source-excerpts.txt`、`input-audit.json` | 状态、源码位置、环境和输入身份 |

在独立容器还原两个历史版本和完整 probe 目录后，每侧的初始见证命令：

```bash
python -m pytest -q -s -p no:cacheprovider \
  tests/_regression_probe/test_session_alias.py
```

重复命令：

```bash
status=0
for attempt in 1 2 3; do
  echo REPEAT-$attempt
  python -m pytest -q -s --tb=short -p no:cacheprovider \
    tests/_regression_probe/test_session_alias.py || status=1
done
exit $status
```

环境为 Linux、Python 3.11.16、pytest 8.4.2、portalocker 4.3.2。
每侧使用隔离 HOME/状态根和 120 秒时限，不能对真实用户状态目录执行该场景。

## 7. 为什么整体仍然不确定

该 PR 同时修改了原生 Windows 生命周期、JavaScript/TUI 和 Manager 的模型可见路由输入。
这些部分缺少相应运行环境或真实模型证据。构造 prompt 时观察到 advertised vertical 数量
和文本长度变化，不等于模型路由变好或变坏。

本案例不证明原生 Windows 锁表现、线上发生率、其他全部路径或 agent 能力退化。
它只证明在所述合法路径别名条件下，Linux 生产更新路径会丢状态。

原运行器因混合判定拒收；后处理保留 `UNCERTAIN` 并恢复局部证据，没有重跑或独立裁决。
原报告的“未修改源码”是分析器自述，不能替代该案例未执行到的宿主侧源码不可变性审计。
本次没有检查最新 main 是否仍存在相同问题。
