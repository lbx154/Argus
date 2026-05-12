# 实验规范（必读）

> **痛点来源**：2026-05-10 复盘 v12 fullbench 时发现，由于历史实验没记录关键信息（pricing 口径、scientist 是否真的跑了、cache 命中情况、commit SHA、运行环境变量），导致几个月后想重算 cost 都要花一晚上做考古。
>
> **这个文档定义"以后任何实验跑之前必须做完的事"。少一项都算实验数据不完整，不可外发。**

---

## 1. 实验目录结构（强制）

每次实验必须创建 `benchmarks/results/<bench-name>-<YYYY-MM-DD>-<tag>/`，下面**必须**包含：

| 文件 | 内容 | 必填 |
|---|---|---|
| `PLAN.md` | 跑之前写：研究问题、设计、对照变量、预期、风险 | ✅ |
| `BUILD_INFO.md` | 跑那一刻的 `git rev-parse HEAD`、`git status --short`、Python/codex CLI 版本、所有 `ARGUS_SKILL_*` 环境变量、模型版本、`reasoning_effort` | ✅ |
| `run-*.sh` | 完整可重放的启动脚本（含所有 env、`set -uo pipefail`、`tee` 到 log） | ✅ |
| `*.log` / `driver.stdout.log` | 全量 stdout/stderr，**不要 grep 过滤**后再存 | ✅ |
| `aggregate.py` | 把原始日志转成 `summary.tsv` 的脚本（含 token / cost / wall / reward） | ✅ |
| `summary.tsv` | 一行一个 trial，列至少包含：cond, task, reward, wall_s, eng_in_tok, eng_cached_in_tok, eng_out_tok, rev_in_tok, rev_cached_in_tok, rev_out_tok, sci_tokens, model_eng, model_rev, model_sci, cost_usd | ✅ |
| `RESULTS.md` | 跑完写：实测结果表、解读、caveats、与基线对照 | ✅ |
| `<cond>/<task>/jobs/...` | harbor 原始 jobs 目录（含 agent transcript）**不删** | ✅ |

Legacy experiment directories that cannot be faithfully reconstructed must
include an `EXEMPT.md` file explaining the omission. The validator below
skips directories carrying that marker:

```bash
python -m benchmarks.validate_results benchmarks/results
```

`BUILD_INFO.md` 模板：

```markdown
# Build & runtime info

- date: 2026-05-10T19:00:00+00:00
- host: $(hostname)
- repo: argus-skill @ $(git rev-parse HEAD)
- working tree dirty: $(git status --short | wc -l) files
- python: $(python3 --version)
- codex CLI: $(codex --version)
- harbor: $(harbor --version)

## Environment
ARGUS_SKILL_HARBOR_SCIENTIST_MODEL=...
ARGUS_SKILL_HARBOR_SCIENTIST_EFFORT=...
ARGUS_SKILL_HARBOR_REVIEWER_MODEL=...
ARGUS_SKILL_HARBOR_REVIEWER_EFFORT=...
ARGUS_SKILL_HARBOR_REVIEWER_GATE=...
ARGUS_SKILL_HARBOR_MAX_ROUNDS=...
ARGUS_SKILL_HARBOR_NO_SKILL=...
ARGUS_SKILL_HARBOR_NO_REVIEWER=...
... (列全)

## Models
engineer  = openai/gpt-5.4-mini, reasoning_effort=high
reviewer  = openai/gpt-5.4,      reasoning_effort=medium
scientist = openai/gpt-5.4,      reasoning_effort=high

## Pricing (from docs/PRICING.md)
gpt-5.4:       in=$1.25 cached=$0.125 out=$10.00 per Mtok
gpt-5.4-mini:  in=$0.25 cached=$0.025 out=$2.00  per Mtok
```

---

## 2. 提交 commit 的硬性要求

实验跑之前**必须**：
1. `git status` 干净，或者把脏 diff 单独提交一次"experiment snapshot"
2. `git rev-parse HEAD` 抄进 `BUILD_INFO.md`
3. 实验启动脚本里 echo 这个 SHA 到 stdout（这样 log 里看得到）

如果做不到（紧急临时改了点东西），**必须** `git diff > BUILD_INFO.diff` 一并存进结果目录。

---

## 3. Token / Cost 报表的硬性要求

**这是 2026-05-10 痛点最大的一项**。

### 3.1 三个来源都要存

任何 trial 都涉及三种 codex 调用，每一种都必须能事后还原 tokens：

| 角色 | 跑在哪 | Token 在哪 |
|---|---|---|
| Engineer | 容器内 | `<trial>/agent/argus-skill-round-N.txt`（codex CLI JSON event stream） |
| Reviewer | host | `<trial>/agent/argus-skill-reviewer-N.txt`（同上）+ `~/.codex/sessions/YYYY/MM/DD/` |
| Scientist | host | `~/.codex/sessions/YYYY/MM/DD/` |

**`~/.codex/sessions/` 必须保留**至少到 `aggregate.py` 跑完。建议跑完实验立刻 `rsync` 一份这个目录的当天子目录到结果目录下：
```bash
mkdir -p "$EXP_DIR/codex_rollouts"
cp -r ~/.codex/sessions/$(date -u +%Y/%m/%d) "$EXP_DIR/codex_rollouts/"
```

### 3.2 `aggregate.py` 必须做这三件事

1. 用 `docs/PRICING.md` 的官方定价（**$1.25/$10、$0.25/$2，cached = 1/10**），不是 `(1.25, 5.0)` 之类错的版本
2. 减去 `cached_input_tokens` 部分（v12 实测 engineer cache 命中率 95%，不减就高估 3-4×）
3. 把 scientist tokens 从 `~/.codex/sessions/` 的 rollout 里捞出来，**不能**只看 `decisions.jsonl.scientist_tokens`（那个字段历史上一直是 bug，值=0）

`summary.tsv` 必须列出 `eng_cached_in_tok` / `rev_cached_in_tok` / `sci_cached_in_tok` 三列，
并且保留 `eng_in_tok` / `rev_in_tok` / `sci_tokens` / `cost_usd` 等核心列。

### 3.3 已知 bug，aggregate 里要兜底

- **Bug B-tokens-scientist**：`decisions.jsonl.scientist_tokens` 在大量历史实验里 = 0。修复要去 `argus_skill/adapters/codex_backend.py` 让 RunnerResult 真正填上 input/output_tokens；在修好之前，aggregate 必须从 codex rollout 兜底。
- **Bug B-tokens-reviewer**：`benchmarks/harbor_adapter.py:_invoke_reviewer` 返回的 dict 没带 reviewer 自己的 input/output_tokens。decisions.jsonl 里 reviewer cost 长期 = 0。修复也很简单（5 行代码）。aggregate 也要从 `agent/argus-skill-reviewer-*.txt` 兜底。
- **Bug B-elapsed-reviewer**：`_run_reviewer_on_host` 没有 `time.time()` 包测耗时。reviewer wall 看不到。

修复优先级：在做下一个有结论意义的实验之前**全部修掉**，否则上报的 cost 就是错的。

---

## 4. 实验对照基线的硬性要求

任何实验只跑"我们这个 condition"是没意义的，必须**同时**有：
1. **codex-bare baseline**（`--no-skill --no-reviewer --max-rounds=1`），用相同的 engineer 模型
2. **相同任务集**（不是抽样，不是 cherry-pick）
3. **n ≥ 3 rollout** per (task, condition)，用 `--n-trials 3`（rollout 方差实测可达 3-5×，n=1 数据不能下结论）

如果跑不起 baseline（贵或慢），实验报告里**必须**显式说"无 baseline，结论只能横向对比 condition 之间"，不能装作有可比性。

---

## 5. 写 RESULTS.md 的硬性要求

至少回答这 7 个问题，不能用语术绕开：

1. Reward 是多少？分布？
2. Wall time 中位数 / p90 / max？
3. **Cost 多少？按 `docs/PRICING.md` 算的、含 cache 折扣的、scientist 不为 0 的那种 cost**
4. 相比 codex-bare baseline，reward / wall / cost 各自变了多少（相对比例）？
5. 有几个 trial 是异常（timeout / exception / empty patch）？是否影响 mean？
6. 有没有 cherry-pick？如果跑了多个 condition 但只展示其中一个，必须解释为什么
7. **n 是多少？方差多大？如果 n=1，结论必须打折说"未经统计验证"**

---

## 6. 数据保留

实验结束 30 天内**不删任何东西**：
- `benchmarks/results/<exp>/jobs/` 全留（每 trial 大约 50-200MB）
- `~/.codex/sessions/<date>/` 全留
- 这俩都至少备份一份到结果目录（git LFS 或 rsync 到外部存储）

30 天后可以 tar.gz 压缩，但不删。

---

## 7. 文档同步

实验结束**当天**必须更新：
- `EXPERIMENTS.md`：追加一行（日期、目录、bench、状态、headline reward+cost）
- 如果发现了 bug：在 `docs/KNOWN_BUGS.md` 记一笔（这个文件可能还不存在，需要的话建）
- 如果颠覆了某个之前的结论：在该结论原文档里加 superseded-by 链接

---

## TL;DR

每次开跑前自问：
1. PLAN.md / BUILD_INFO.md / run-*.sh 全写好了？
2. git SHA 锁死、没脏改？
3. aggregate.py 用的是 docs/PRICING.md 的官方价 + cached 折扣 + scientist 兜底？
4. baseline 也一起跑？n ≥ 3？
5. RESULTS.md 模板那 7 个问题能答上？

少一项就别开跑。
