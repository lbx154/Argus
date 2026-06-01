---
name: integration-via-existing-hooks
description: Find existing extension points (env vars, default check_commands, dataclass schemas) before building parallel infrastructure.
when_to_invoke: When wiring a new module into the runtime (daemon, supervisor, reviewer, runner).
---

# Integration via Existing Hooks

## 核心思路

**改一行接进现有路径，胜过新建一套并行架构。**

argus-skill 已经有大量"约定即接口"的点，先找它们。

## 已知集成点（按层）

| 层 | 接入点 | 怎么用 |
|---|---|---|
| **每轮 engineer round 后** | `supervised_config.check_commands` (`argus_skill/loop.py:55`, `argus_skill/daemon/life_worker.py:108`) | 在 daemon default check_commands 里加你的 CLI；output 自动喂到 reviewer prompt |
| **reviewer 决策强制** | `_coerce_review_for_failed_checks` (`argus_skill/engineer/runner.py:1209`) | 任何 CheckResult.passed=False 都会 force `status='continue'` —— 让你的 CLI 在 fail 时 exit 非 0 即可 |
| **reviewer prompt 注入** | `summarize_checks` 把 CheckResult.output_tail 拼进 prompt | 直接写有用的失败详情到 stdout |
| **stage 路由** | `argus_skill.tools.stage_check.STAGE_CHECKS` + `STAGE_GATES` | 按 stage 决定跑哪些 gate |
| **CLI 顶层 flag** | `argus_skill/apps/cli.py` `build_parser()` + `main()` 的 `action_flags` 元组 | 同时加 arg、加 dispatch、更新 mutual-exclusion 元组 |
| **env-var 配置** | `os.environ.get("ARGUS_SKILL_*")` | 不改函数签名传配置，避免动 supervisor 接口 |
| **BacklogItem 元数据** | `BacklogItem.tags: list[str]` | 加 lifecycle / project / scope 标签 |

## 查找新接入点的方法

```bash
# 1. 找跟你模块同类的已有模块
grep -rn "<same-pattern>" --include="*.py" argus_skill/

# 2. 看它怎么被调用（找上游消费者）
grep -rn "import <same-pattern>\|from .* import <same-pattern>" --include="*.py"

# 3. 看它的入口
grep -n "^def main\|if __name__" <module>.py

# 4. 决定你接哪条线
```

## 集成强度梯度（按改动量从小到大）

1. **CLI 入口 + env var 调用方**（最小）：
   - 写一个 `python -m argus_skill.<area>.<module> --foo bar`
   - 让用户在自己的脚本里调
   - 改动：新增 1 文件，0 修改

2. **接 stage_check 的 default check_commands**（小）：
   - 在 `stage_check.py` 里 import 你的模块，按 stage 跑
   - 改动：1 文件改 ~20 行

3. **加顶层 CLI flag**（中）：
   - `argus_skill/apps/cli.py` 加 `add_argument`、`action_flags` 元组、dispatch 分支、handler `_cmd_<name>`
   - 改动：1 文件改 ~30 行（4 处）

4. **改 schema**（大，仅当真的需要持久状态）：
   - `BacklogItem` 加字段 → `to_jsonable` / `from_jsonable` 同步 → migration（旧 backlog.jsonl 缺字段时给 default）
   - 改动：多文件 + 测试 + 文档

## 反模式

- ❌ 新建一个 `argus_skill/factory/` 大子包，再造一套 supervisor —— 先看现有 `life/supervisor.py` 怎么加
- ❌ 改 `reviewer.evaluate` 签名 —— 走 env var 或 CheckResult 通道
- ❌ 把 supervisor 状态写到新文件 —— 先看 `LifeMemory` 怎么扩
- ❌ 加新 daemon 进程 —— 先看现有 daemon 能不能在 tick 里加一步
