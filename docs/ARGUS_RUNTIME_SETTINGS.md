# Argus Runtime Settings Contract

> 本文描述配置接口，不保存某台机器的当前值。文档权威层级见
> [`DESIGN_AUTHORITY.md`](DESIGN_AUTHORITY.md)。

## 权威来源

- Operator 可调参数目录：`argus_skill/core/knobs.py::KNOBS`
- 默认值事实源：每个参数的实际读取点；`KNOBS` 必须与其保持一致
- 持久化设置：global root 下的 `config.json`
- 当前有效值：`argus-skill --config-help`
- 可分享的诊断快照：`argus-skill --config-snapshot <path>`

`--config-snapshot` 会包含当前环境、持久化配置和运行路由，因此是**机器状态快照**，不是
仓库设计规范。不要把真实机器生成的 snapshot 覆盖到本文件，也不要提交 token、chat id、
本地路径或其他机器身份信息。

## 解析优先级

普通 operator knob 的优先级为：

```text
显式进程环境变量
  > cockpit 持久化配置
  > KNOBS 中记录的默认值
```

Runner binary、role backend/model 和少数兼容参数有专用 resolver，但仍遵守“显式配置优先，
不从任务 prose 猜配置”的原则。

## 配置组

`--config-help` 按下列组列出当前参数：

- `backend`：共享 backend、role backend 和 runner binary
- `models`：Manager/Planner/Engineer/Reviewer/Curator/matcher 等模型
- `reasoning`：各角色和辅助调用的 reasoning effort
- `budget`：host-global USD cap、provider call guard、daemon cap
- `mission`：round、idle、bounded DAG、continuation、等待和任务级软预算
- `team`：teammate/Curator pool
- `lifecycle`：Skill/Wiki、sandbox、release match、compaction
- `telemetry`：metrics、agent I/O、Telegram 和 reasoning display

完整名称、默认值和一行说明不在本文复制；请运行：

```bash
argus-skill --config-help
```

这样新增或删除 knob 时只有 `core/knobs.py` 和读取点需要同步，不会再维护一份很快过期的
手写表格。

## 关键当前语义

### Backend 与模型

- `ARGUS_SKILL_LIFE_BACKEND` 是共享默认 backend。
- `ARGUS_SKILL_{MANAGER,PLANNER,ENGINEER,REVIEWER}_BACKEND` 可覆盖单个角色。
- `ARGUS_SKILL_MODEL` 是共享默认模型，role-specific model 再覆盖它。
- Provider CLI 路由只能由配置 resolver 决定，任务文本不能偷偷切 backend。

### 预算

- 唯一货币预算是 `ARGUS_SKILL_GLOBAL_DAILY_CAP_USD`。
- 所有项目共享 host-global settled usage 和 in-flight admission。
- 未定价调用的默认策略由 `ARGUS_SKILL_UNPRICED_COST_POLICY=block` 控制；设为 `allow`
  表示 operator 明确接受未知成本暴露。

### Reviewer

当前生产路径每个 Engineer round 都调用独立 Reviewer。不存在
`ARGUS_SKILL_ENGINEER_SELF_REVIEW`，也不存在 `review=skip` 运行开关。旧事件或旧项目中
可能仍保留 `engineer_self_review` 作为历史来源值。

### Replan

当前 replan 由 Reviewer 返回 `status=replan_requested` 直接触发。不存在以下已退役配置：

- `ARGUS_SKILL_DYNAMIC_PLAN_MODE`
- `ARGUS_SKILL_DYNAMIC_PLAN_CONFIRM_ROUNDS`

计划替换仍使用 `plan_id` / `plan_version` / compare-and-swap，但不经过
`off|shadow|active` 模式或连续 signal 计数。

### Paper completion

- 当前 vertical completion gate 名称为 `full_paper`。
- `LifeSupervisorConfig` 字段为 `full_paper_gate`。
- `full_emnlp` 只用于旧数据迁移或历史文档，不是当前配置名。

### Session continuity

- `ARGUS_SKILL_CHECKPOINT_PERSIST` 控制跨 mission/restart 的 `CHECKPOINT.md` 连续性。
- Engineer/Reviewer provider session 始终 fresh；`ARGUS_SKILL_SHIFT_ROUND_LIMIT` 和
  `ARGUS_SKILL_THREAD_TOKEN_LIMIT` 仅为兼容参数，不恢复旧 thread resume 行为。

### Skill ops compatibility

`ARGUS_SKILL_SKILL_OPS` 只控制旧 Reviewer `skill_ops` 的兼容 replay。当前角色通过普通
文件工具直接维护 project-layer Skill；该路径不经过 SkillRouter 的 protected 检查。
这是 operator 接受的边界，`protected` 在直接编辑路径上由角色政策约束。

### Release match

`ARGUS_SKILL_REQUIRE_RELEASE_MATCH=1` 会在 loaded source 与构建 release 不一致时拒绝
daemon/WebAPI 启动。开发默认关闭；生产或受监督部署建议开启。

## 修改规则

新增一个 operator 会合理设置的 knob 时：

1. 在实际读取点实现并测试默认值；
2. 添加到 `core/knobs.py::KNOBS`；
3. 若允许 cockpit 修改，设置 `cockpit=True` 并补解析/校验；
4. 添加或更新配置测试；
5. 若改变系统语义，再更新对应设计文档，而不是在本文复制完整 knob 表。
