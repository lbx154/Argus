# Manager 会话容量与连续性

Manager 的逻辑身份属于项目；provider thread 是可以维护、更换的执行资源。`.manager_session.json` 的版本 3 分别保存 `logical_manager_id`、`provider_generation`、最近四轮摘录、容量依据和最近一次轮换原因。普通进程重启继续读取这些状态，不需要用户手动重启会话。

## 容量策略

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `ARGUS_SKILL_MANAGER_SESSION_MAX_INPUT_TOKENS` | `16384` | 应用层上下文维护水位，最小有效值为 1024；不是账户额度或模型硬上限。 |
| `ARGUS_SKILL_MANAGER_SESSION_CONTEXT_WINDOW_TOKENS` | 未配置 | 显式指定已知模型上下文窗口；未指定时，可从选中模型的 `PI_CODING_AGENT_DIR/models.json` 读取窗口和输出余量。其他后端没有元数据时继续由其原生上限负责。 |

上一轮有明确输入 usage 时，维护依据是该轮输入加输出 token；输入已经包含 cached input，不再重复相加，也不因缓存便宜就把它当作不占上下文。工具循环的整轮合计可能高于最后一次请求的实际上下文，因此该依据偏保守。缺失 usage 时，只累计本 provider generation 内可见提示和回答的 UTF-8 字节，采用水位四倍的字节阈值，并明确标记 `visible_text_bytes`。这些维护计数不会改写 `RunnerResult`、用量账本或配额预留。

达到维护水位后，在**下一次调用前**携带交接开启新 provider thread。正常维护至少保留四个 turn，给观察、决策、反馈和确认留下有效窗口，避免每轮重建。16384 是可调的运行成本折中：它在大约 15 KiB 的完整监督提示下保留多轮交流；在本次 fixture 的 128000-token 模型窗口下，早于 native Pi 的默认自动压缩触发点维护历史。

已知的小模型窗口优先于四轮下限：上一轮上下文，加上下一条提示按 UTF-8 字节计算的保守 token 估计及输出余量，接近窗口时可以提前轮换。明确的 native 输出上限优先；缺少输出上限时预留窗口的四分之一。新提示和必要控制不会被截短以强行装入窗口，最终准入仍由 provider 和现有预算链路决定。

## 交接保留什么

历史摘录总量限制为 8 KiB，标明它们是过去的对话数据。当前完整请求和最新 OperatorContext 不受这个历史预算裁剪。

轮换还会读取一份独立、最多 12 KiB 的当前持久状态，包括：

- 与当前目标作用域匹配、仍有效的 Manager directive。
- 最新 supervision 回执；`latest.json` 有合法 ID 时，优先采用对应 `{id}.json` 的持久状态。`issued` 表示待交付，交接不会重判、补发或将它描述为已应用。
- 当前未答问题，独立于普通 observation 的八任务展示范围；已解决的问题和已清除、撤销或失去作用域的指令不会重新成为有效控制。
- continuous、pipeline、campaign 状态及来源哈希或明确的未观察标记。

控制字段要么完整保留，要么整字段标为缺失并给出来源；不会把一段约束的前缀当作完整授权。未展示的未答问题保留数量和来源标记。没有受控、有效的当前监督 evidence 块时，从同一次已恢复的 Backlog/control 快照生成最多 16 KiB 的证据投影，不再次扫描 observation 的其他存储和锁。

Backlog 快照在其规范锁内先执行既有 commit recovery，再严格检查完整 JSONL。读取上限为 2 MiB；损坏、不可读或超限会中止这次轮换，不能等同于“没有阻塞事项”。其他控制源沿用 128 KiB 的安全读取上限和有限 JSON 检查。

## 锁、停止与失败

取得 Manager session 锁后才读取最新 OperatorContext，避免等待期间发生的权限变化被遗漏。状态准备采用 250 ms 的锁等待预算和最多 50 ms 的取消轮询；嵌套 Backlog 读取继承同一预算。显式 capsule 读取也约束进程内线程锁和跨进程文件锁。provider 执行不处于这个读锁 scope 内。

Backlog 持久化格式没有因本改动改变；扩展的是可选锁 API。scope 外未指定参数的 writer 保留原来的锁顺序和 recovery 协议。预算约束锁等待，不取消已经开始的规范 recovery 或承诺任意底层文件系统 I/O 都可被强制打断。

provider 调用前权限或交接准备失败时，不启动无权限块、无 capsule 的替代调用，也不改写原 session pointer。停止沿用取消语义。已有 provider 结果的保存规则仍沿用原规则：返回 thread ID 且未取消时尝试保存；这不代表回滚已经发生的 provider 副作用。未知结果、配额拒绝不会因容量维护获得额外重试。原来针对明确不可恢复 resume 状态的恢复分支仍独立存在。

旧版本 2 只保存最近四轮摘录，不能据此推断完整 native transcript 的容量，因此第一次使用会做一次明确、有当前事实交接的迁移，之后按版本 3 持续恢复。同一项目应使用同版本 Manager writer，避免旧进程覆盖新容量元数据。

## 验证范围与额度限制

确定性长程 fixture 使用真实 Manager wrapper、AgentCliBackend、native Pi 持久化、loopback provider，以及原有 `trial.prepare` 和 `Store`。它验证真实请求历史增长及保守 reservation；返回的 usage 是明确标注的合成 fixture 数据，不是实际模型测量，也没有外部或付费推理。历史四阶段 real acceptance 没有重跑。

维护水位不能保证账户余额总能通过下一次保守预留。固定 300000 的 fixture 中，原会话在第 18 次请求被拒绝，仍剩 90606；维护后也保留后续真实发生的额度拒绝，不把“完成 40 轮”作为调低预留或增加额度的目标。新的长程测量、锁等待、跨轮换控制、重启身份及失败回归记录分别保存；它们不构成生产额度或所有模型窗口的承诺。

| 最终 fixture | 成功轮次 / 请求数 | 各 provider thread 的请求数 | 结果 |
| --- | --- | --- | --- |
| 41 字节短对话，额度 300000 | 40 / 40 | 40 | 不轮换，保持同一个逻辑身份。 |
| 约 4 KiB 监督提示，额度 300000 | 29 / 30 | 13、11、6 | 第 30 次请求保留额度拒绝；余量 33112 小于预留 45589。 |
| 约 15 KiB 完整监督提示，额度 1000000 | 24 / 24 | 4、4、4、4、4、4 | 六个物理会话保持同一个逻辑身份。 |
