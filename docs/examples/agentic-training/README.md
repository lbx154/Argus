# 从用户任务生产 Agentic 训练数据

这些请求模板用于通过普通用户界面发起真实任务。模板中的数值是实验条件，
不是预先规定的成功结果；代码、工具调用、运行输出和交付物必须由实际执行产生。

| 类型 | 用户端请求 | 独立验收重点 |
| --- | --- | --- |
| 论文生成 | [paper-generation.md](paper-generation.md) | 同预算实验、原始记录、统计汇总、真实引用、可读 PDF |
| 数学猜想 | [math-conjecture.md](math-conjecture.md) | 严格证明、真实搜索边界、整数验证、如实报告反例 |
| 工程优化 | [engineering-optimization.md](engineering-optimization.md) | 独立穷举参考解、边界条件、实际计时、报告与 JSON 一致 |

1. 使用普通邀请码登录，阅读当前版本的数据通知并明确同意内部训练用途。
   新建工作区，把一个模板贴入用户端任务输入框并发送。后台只采集授权后的真实事件，
   不从旧日志补造工具轨迹。
2. 等待任务结束，检查实际交付文件。在独立副本中重跑实验或测试，保留原始测量结果，
   将复跑结果另存。证明、实验设计和图表需要内容检查，不能只看退出码。
3. 写验收记录，包含项目和任务标识、实际执行命令及退出码、产物 SHA-256、
   检查结论及局限。自动验收写 `reviewer_kind: automated_acceptance` 和
   `human_reviewed: false`，不能称作人工审查。失败或未核查的样本继续排除。
4. 管理员打开 `/admin/data`，检查项目授权、工具候选、完整性及隔离原因。
   只批准已核对的候选，选择内部训练用途并导出 ZIP。网页手动审查记为
   `human_operator`；自动验收使用已有管理员身份调用导出接口，并提交
   `reviewer_kind: automated_acceptance`、准确的 `approved_event_ids` 和验收文件
   `evidence_sha256`。外部分享需要单独的授权和权利审查。
5. 对导出包运行离线校验：

   ```sh
   python -m argus_skill.trial.training_validate dataset.zip \
     --require-agentic --evidence acceptance.json --report validation.json
   ```

   多份验收文件可重复传入 `--evidence`。只有命令成功且
   `agentic_training_ready` 为 `true` 时，才将该包列入通过格式校验的训练数据。
   格式校验不替代科学、数学或性能结论的验收。

训练包同时提供 `hf_trl_train.jsonl` / `hf_trl_validation.jsonl` 和可移植的
`sft_train.jsonl` / `sft_validation.jsonl`，包括公开的消息、工具定义和调用/返回对应关系。
保留来源、授权、质量依据及哈希文件，按模型和训练器的 chat template 导入。
单条已完成的公开工具 episode 不代表整个任务的所有角色都采集完整；
系统提示、私有推理及被隔离的内容不属于这些样本。

完整部署和数据边界见 [hosted-research-trial.md](../../hosted-research-trial.md)。
