# 研究 Trace 保留策略

默认保留 Copilot 会话原始轨迹。升级后，旧的默认 7 天删除策略停止生效；只有显式
设置正数 `ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS` 才启用闲置归档与在线回收。
此修改无法恢复此前已经删除的文件。

| 记录 | 当前策略 |
| --- | --- |
| Argus 项目 `events.jsonl*` | 沿用事件历史轮转，保留所有历史文件 |
| 托管 Copilot `session-state/<id>/` | 默认保留在线；启用回收后，先校验 ZIP 归档到 `<ARGUS_SKILL_HOME>/copilot-home/session-archives/<id>/` |
| 项目 `agent_io.jsonl*` | 默认 128 MiB 触发轮转，在线保留两个旧文件；回收的文件先压缩归档到日志旁的 `trace-archives/agent_io/` |

**归档不会自动到期删除。** 压缩减少在线文件占用，但总存储仍会增长。请关注剩余
空间，把归档导出到研究存储，并按需显式移除不再需要的归档。归档失败时保留原文件。

## 设置与告知

设置页面展示实际生效的会话归档窗口和采集模式，高级设置及 `/config` 可持久保存。
`argus --config-help` 可查看默认值和当前值。设置对本机所有项目生效，环境变量优先
于保存的设置；改变环境变量后需重启实际启动 Argus、Web/API 和 daemon 的进程。
持久化设置在后续调用读取。

| 设置 | 默认值 | 含义 |
| --- | --- | --- |
| `copilot_session_retention_days` | `0` | 保留所有在线会话；正数表示闲置多少天后先归档再回收 |
| `agent_io_max_bytes` | `134217728` | 原始 I/O 在线轮转字节数；`0` 关闭轮转 |
| `agent_io_keep` | `2` | 在线保留的旧文件数；设为 `0` 也会先归档 |
| `agent_io_mode` | `full` | 保留原始提示词和输出流；`compact` 只保留摘要，无法提供完整研究轨迹 |

例如 `/config copilot_session_retention_days=7` 启用 7 天闲置归档，
`/config copilot_session_retention_days=0` 保留全部在线会话。也可使用对应的完整环境变量名：
`ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS`、`ARGUS_SKILL_AGENT_IO_MAX_BYTES`、
`ARGUS_SKILL_AGENT_IO_KEEP`、`ARGUS_SKILL_AGENT_IO_MODE`。

个人目录、显式设置的 `COPILOT_HOME` 和专用账号目录不进行这条自动回收。
清理不会穿过软链接或 Windows junction；正常 worker 仍可使用链接的状态根目录。

## 长任务、并发和续跑

闲置时间取目录及其中所有文件的最新活动时间。普通 worker 和常驻 ACP 在整个进程
存活期间持有共享使用锁，回收需要独占锁；只要有 worker 使用该目录，即使长时间
没有输出也不会回收。启动时还会保护即将恢复的会话。闲置时每小时最多扫描一次，
配置的天数表示回收资格，不承诺达到该时间立即回收。

ZIP 全量读回并验证 CRC、大小和 SHA-256，发布归档后才回收源数据。I/O 写入、
轮转和归档使用同一把跨进程锁。归档失败继续写原文件；写入失败会尝试保存到
`trace-archives/pending/` 并报告错误。

普通 worker 或 ACP 续跑前自动恢复已归档的托管会话。回收中断留下残缺目录时，
会恢复完整归档，同时保留残余目录供排查；归档损坏时明确报错。

## 导出与检查

复制归档 ZIP，以及项目 `events.jsonl*`、在线 `agent_io.jsonl*` 和可能存在的
`trace-archives/pending/*.jsonl`。ZIP 的 `manifest.json` 记录原始文件名、时间、
大小和 SHA-256，`payload/` 保存原始内容；记录中的 `call_id` 和 provider session
关联保持不变，可与项目历史对应。

验证归档而不修改运行状态：

```python
from pathlib import Path
from argus.core.trace_archive import verify_archive

manifest = verify_archive(Path(r"G:\research\archive.zip"))
print(manifest["source_name"], manifest["created_at"])
```

研究分析时把 `payload/` 解压到独立证据目录；续跑由 Argus 自动恢复，不要把 ZIP
直接覆盖到运行中的会话目录。恢复后归档仍然保留。
