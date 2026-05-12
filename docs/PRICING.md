# OpenAI 模型定价与成本核算口径

> **唯一权威定价表**。其他文档算 cost 一律按此口径，并且**必须**计入 cache-hit 折扣，否则上报数字无效。
>
> 来源：OpenAI 官方 API 定价页（2026 标准价），与 `benchmarks/swebench_pro/runner.py:_DEFAULT_PRICES_USD_PER_MTOK` 保持一致。

---

## 1. 定价表（单位：USD / 1M tokens）

| 模型 | input | **cached input** | output |
|---|---:|---:|---:|
| `gpt-5.4` | **$1.25** | **$0.125** | **$10.00** |
| `gpt-5.4-mini` | **$0.25** | **$0.025** | **$2.00** |

**Cache 折扣规则**：cached input = 标准 input 的 **1/10**（即 90% off）。OpenAI 在 prompt cache 命中时按 cached input 价收费，命中窗口约 5-10 分钟（同一份 prompt 前缀短时间内重发可命中）。

**Batch API**：另有 50% 折扣（异步任务用）。当前所有实验**没用 batch**，所以不进入计算。

---

## 2. 正确的 cost 计算公式

OpenAI 的 `usage` 对象返回三个字段：
- `input_tokens` — 总输入 tokens（**已包含 cached 部分**）
- `cached_input_tokens` — 其中命中缓存的部分
- `output_tokens` — 输出 tokens（含 reasoning_output_tokens）

公式：

```python
def cost(model, input_tokens, cached_input_tokens, output_tokens):
    p = PRICES[model]                            # {in, cached_in, out}
    fresh_input = input_tokens - cached_input_tokens
    return (fresh_input        * p["in"]
          + cached_input_tokens * p["cached_in"]
          + output_tokens       * p["out"]) / 1e6
```

**常见错误**（之前我们犯过）：
1. 把 `gpt-5.4` 的 output 价记成 $5.00（**错**，是 $10.00）
2. 把 `gpt-5.4-mini` 的 input/output 记成 $0.15/$0.60（**错**，是 $0.25/$2.00）
3. **不计 cache 折扣**，全按 input 价算（v12 实测 95% cache hit 时，会把成本高估 3-4×）
4. 只看 `decisions.jsonl` 里的 `scientist_tokens` —— **它有 bug，long-time 都是 0**，必须从 `~/.codex/sessions/` 的 rollout JSONL 兜底（见 EXPERIMENT_PROTOCOL.md §3）

---

## 3. Token 来源对照表

| 角色 | Token 在哪里取 | 备注 |
|---|---|---|
| Engineer（in-container） | `<trial>/agent/argus-skill-round-*.txt` 中最后一条 `turn.completed.usage` | 含 `cached_input_tokens` |
| Reviewer（host） | `<trial>/agent/argus-skill-reviewer-*.txt` 中最后一条 `turn.completed.usage` | 含 `cached_input_tokens` |
| Scientist / Matcher（host） | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` 在实验时间窗内的所有 host-side 会话，扣减 reviewer 部分 | `decisions.jsonl` 里的 `scientist_tokens` **不可信**（v12 时代 runner 没接上 token 抽取），直到 fix。 |

---

## 4. 典型 cache 命中率参考（v12 fullbench 实测）

| 角色 | input tokens | cache 命中率 | 不命中本应付 | 实际付 | 节省 |
|---|---:|---:|---:|---:|---:|
| Engineer | 202.8M | **95.0%** | $50.7 | $13.4 | $37.3 (74%) |
| Reviewer | 1.6M | 14.5% | $2.0 | $2.1（含 output） | 微 |
| Scientist | 2.0M | 61.8% | $2.5 | $3.2（含 output） | ~$1.2 |

→ 在重复任务跑批的场景下，**engineer 的 prompt cache 是单次最大成本杠杆**。任何改 prompt 结构的改动都会打掉缓存（cache 是按前缀精确匹配的），代价巨大。

---

## 5. 检查 cost 报表是否对

每次出结果，至少自己回答这三个问题：
1. 我用的 price/M 是上面那张表里的数吗？（如果用了 `(1.25, 5.0)` 那就是错的）
2. 我减掉了 cached_input_tokens 吗？（如果直接 `input_tokens × 1.25` 就是错的）
3. 我的 scientist tokens 真的不是 0 吗？（如果是 0，去翻 `~/.codex/sessions/`）

如果三个里有任何一个答不上来，**当前的成本数字不能用，必须重算**。
