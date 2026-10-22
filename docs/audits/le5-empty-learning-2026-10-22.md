# LE-5：为什么近一半的学习没产出（2026-10-22）

> 路线图 LE-5 的报告。现状一句话是"旧实例 36 次学习里有 16 次没有任何变化"。
> 本文先说明这组数字能不能复核，再从代码列出一次学习"没有变化"的所有路径，
> 最后给出怎么测和该改什么。对应原则 9（越用越好要能证明）。

## 0. 结论

1. **36/16 的原始记录拿不到，本文不给这 16 次的原因分布。** 这组数字最早出现在
   `4144640e`（2026-10-16，"turn the roadmap into the path from 0.1.8 to 1.0"），提交里没有写数据来源。
   仓库、PR 描述和本机都没有那个实例的 `answer-learning.sqlite3` 或知识日志（第 1 节）。
2. **"没有变化"在代码里只有一个判据：这次学习没有发出 `knowledge.learned` 事件。**
   记录表只写 `status=unchanged, reason=no_new_learning`，不区分"模型判断没什么可记"和
   "写了但宿主没认"。所以 16 次里有多少是正常的、多少是漏记，旧数据本来就分不出来。
3. **记下这组数字时，"一次学习"包含两种调用**：每条回复后的 answer-learning（`47f182da`，
   2026-10-20 已删除）和任务结束后的 mission reflection。前者的提示词明确要求闲聊、设置、
   Argus 自述、一次性交付、只引用未读原文等情况写 `WROTE: nothing`，而 2026-10-08 的
   PR #227 之前，连问候也会排一次学习。旧数字里空跑可能主要来自这条路径，但这只是推测，没有数据支撑。
4. **代码里有三处会把"知识库确实变了"或"出了错"记成别的状态**：只退役页面、只写入
   reviewed-facts 摘要，都会记成 `unchanged`；知识目录不可写记成 `skipped/not_needed`（第 3 节）。
5. **本次改动**：`unchanged` 行的 `outcome.detail` 现在记录原因（`wrote_nothing`、
   `claimed_write_missing`、`unreadable_page`、`retired_only`、`reviewed_fact_only`、
   `no_wrote_line`），`skipped/not_needed` 行记录跳过理由。下一个实例跑一段时间后，
   用第 4 节的一条 SQL 就能得到真实分布。

## 1. 找过哪些数据

| 位置 | 结果 |
|---|---|
| `docs/`、`docs/evaluations/`、`experiments/`、`technical_report/evidence/` | 只有路线图本身提到 36/16；主题 LE 的引言另有"线上 31 次，28 次有改动"，同样没有原始记录 |
| `git log -S "16 of 36"` | 只命中 `4144640e`，提交说明没有数据来源 |
| `gh pr list -R lbx154/Argus --state all --search learning` | #219、#227、#238、#249 讲学习状态和触发条件，都没有逐次学习的统计 |
| 本机 `find / -name 'answer-learning.sqlite3*'`、`~/.argus-skill` | 本机只有一个不含学习记录的 `~/.argus-skill`（只有 codex/copilot 用量文件），没有任何 Argus 实例的学习数据库或 `knowledge-journal.jsonl` |

还有一点要注意：当前代码第一次打开 `answer-learning.sqlite3` 时会执行
`DELETE FROM jobs WHERE id NOT LIKE 'mission-%'`（`argus/life/reflection_status.py`，
`PRAGMA user_version` 迁移）。旧实例如果升级到 `47f182da` 之后的版本，answer-learning
的那部分记录就没了。要复核旧数字，必须在升级前复制这个文件。

## 2. "一次学习"和"没有变化"在代码里是什么

一次学习就是 `<ARGUS_SKILL_HOME>/answer-learning.sqlite3` 里 `jobs` 表的一行。
现在只剩 mission reflection 一种：`reflect_after_mission`
（`argus/life/reflection.py`）在学习开关打开、任务有 id、还没反思过时进入
`observe_mission`，插入一行 `running`，跑完由 `_finish` 定状态：

| 条件（按顺序判断） | status / reason |
|---|---|
| 调用失败，且 `stop_kind` 是宿主开跑前拒绝（成本未对账、预算用完、冷却、操作者暂停） | `skipped` / `paused` |
| 调用失败（非零退出、`fatal_error`、抛异常） | `failed` / `failed` |
| 结果带 `skipped` 且没有任何 `knowledge.learned` | `skipped` / `not_needed` |
| 至少一个 `knowledge.learned` | `completed` / `saved` |
| 其余 | `unchanged` / `no_new_learning` |

`knowledge.learned` 只在一种情况下发出：调用前后对比监视目录下的 `.md` 文件，
新建或改动的文件能读出 front matter（`title`，或 Skill 的 `name`），并且位于某个监视根目录下。
监视根目录是：领域共享 Wiki、项目 Wiki（有的话）、工作区 `.agents/skills`、操作者私有记忆。
另外，事实页晋升到共享库时也发 `knowledge.learned`（`kind=promoted`）。

已经反思过的任务不会进 `observe_mission`，不产生新行；旧版 answer-learning 的行由
`argus/life/answer_learning.py` 的 `_finish` 写入，状态规则和上表相同。

## 3. "没有变化"的所有代码路径

### 3.1 mission reflection（当前代码）

| 编号 | 路径 | 知识库实际变了吗 | 新的 `detail` |
|---|---|---|---|
| M1 | 模型判断没有可记的，回复 `WROTE: nothing`。提示词允许这样做："Write nothing only when there is no supported, non-duplicate learning" | 没变，正常 | `wrote_nothing` |
| M2 | 模型只用 `RETIRE:` 退役了页面。`retire_page` 把页面移进 `.history`，删掉 INDEX 行，写一条 `retired` 日志，但不发 `knowledge.learned`；快照对比也不看删除 | **变了**，却记成无变化 | `retired_only` |
| M3 | 只在 reviewed-facts 摘要里追加了一条事实（`append_judged_fact`），摘要不在监视目录 | **变了**，却记成无变化 | `reviewed_fact_only` |
| M4 | 写了 `.md` 但 front matter 读不出来，进 `ignored` 列表，不计数。只改了 INDEX.md 也落在这里（INDEX 没有 front matter），统计时已排除 | 文件变了，宿主不认 | `unreadable_page` |
| M5 | 回复说写了文件（`WROTE: <路径>`），但监视目录下没有任何 `.md` 变化。可能原因：沙箱拒绝写入、路径写错、只写了非 `.md` 文件（Skill 旁边的脚本、Wiki `checks/` 下的检查脚本）、写进了以 `.` 或 `_` 开头的子目录 | 不确定，大概率是缺陷 | `claimed_write_missing` |
| M6 | 回复里没有 `WROTE:` 行（提示词要求以它结尾） | 没变 | `no_wrote_line` |

多种情况同时出现时按 M2、M4、M3、M1/M5/M6 的顺序取第一个。

**会让 M1 变多的输入问题**：`_mission_execution_settlement.py` 调用 `reflect_after_mission`
时 `host_round_log` 固定传 `""`，所以提示词里的"Round log"一节永远是 `(no round log kept)`；
"Run reality"只有 `outcome.summary` 或 `final_message`；整段提示词被裁到 12,000 字符。
`4144640e` 时的代码也是这样。模型能看到的证据只有 Engineer 的一段自述和 Reviewer 的理由，
在这种情况下回答"没有可记的"并不奇怪。这是原因，不是独立的结果路径，新字段里体现为 `wrote_nothing`。

**不算"无变化"但值得一提的状态**：

- `skipped/not_needed`：学习开关关闭、首轮通过且 Reviewer 判 `nothing_new`、没有后端、
  **知识目录不可写**。最后一种其实是环境错误，却和"没必要学"记成同一状态。
  现在这一类行的 `detail` 会写出跳过理由，可以单独统计。
- 路线图如果把 `skipped` 也算进"没有任何变化"，那 Reviewer 判 `nothing_new` 的跳过就是一个主要来源；
  旧数字是否这样算，现在无法确认。

### 3.2 answer-learning（旧代码，`47f182da` 已删除）

旧数字记录时存在、现在已删除的路径，只用于解释旧数字可能的组成（代码见 `git show 4144640e:argus/life/reflection.py`
的 `_reflect_after_answer` 和 `git show 4144640e:argus/life/learning_draft.py`）：

| 编号 | 路径 |
|---|---|
| A1 | 模型按提示词写 `WROTE: nothing`。提示词列了很多应当不写的情况：闲聊、状态和控制；Argus 对自身设置和功能的描述；一次性工程交付；只引用、没有读过原文的来源；事实和理解都没变时要求文件"byte-for-byte unchanged" |
| A2 | `LearningDraft.publish` 去重：新文件的正文（去空白、忽略大小写后）和已有页面相同，静默丢弃 |
| A3 | 内容和原文件完全相同，不发布 |
| A4 | 写在三个草稿根目录（`pages/surveys`、`skills/self`、操作者记忆）之外，例如草稿里的 `knowledge/INDEX.md` 或 `knowledge/pages/lessons/`，不发布 |
| A5 | 已发布但 `_page_meta` 读不出，直接 `continue`，没有任何记录（因为发布前 `_validate` 检查了同样的字段，实际很少发生） |

草稿校验失败、并发修改冲突、试图删除页面都会抛异常，记为 `failed`，不在"无变化"里。
PR #227（2026-10-08）之前，所有回复都会排 answer-learning，包括"你好"；之后前门分类为问候、设置、
控制或无工具证据的简短回复不再排队。旧实例的 36 次落在哪个时间段，现在不知道。

## 4. 怎么测

升级到包含本次改动的版本后，每一行 `unchanged` 都带 `detail`。在实例的 home
（默认 `~/.argus-skill`，或 `ARGUS_SKILL_HOME`）下：

```sh
sqlite3 ~/.argus-skill/answer-learning.sqlite3 \
  "SELECT status, json_extract(outcome,'$.reason') AS reason,
          json_extract(outcome,'$.detail') AS detail, count(*)
   FROM jobs GROUP BY 1,2,3 ORDER BY 4 DESC"
```

本次改动之前写入的行没有 `detail`（显示为 NULL），不要和新行混在一起算。
`GET /api/projects/{sid}/learning` 也会返回 `detail`，但每个项目只返回最近 20 行，统计请直接读数据库。

如果还能拿到旧实例：

1. 升级前复制 `answer-learning.sqlite3`，`id` 不以 `mission-` 开头的行就是 answer-learning。
2. 各项目 `events.jsonl` 里的 `knowledge.learned` 和 home 下 `knowledge-journal.jsonl` 里的
   `retired` 记录可以对出 M2；reflection 调用的日志（run label `reflection` / `answer-learning`）
   里有模型最后的 `WROTE:` 行，可以区分 M1/A1 和其他路径。

## 5. 建议

按先后顺序：

1. **先测再改。** 用新的 `detail` 在一个实例上积累几十次 reflection，再决定下面哪些值得做。
   不要为了降低"无变化"比例去催模型多写：`docs/continuous-learning.md` 明确说一次任务可以合理地什么都不加。
2. **`claimed_write_missing` 和 `unreadable_page` 应当接近零。** 出现就是缺陷（沙箱、路径或格式），
   按具体样本修。
3. **只退役页面、只写摘要不应记为"无新增"。** 可以给 `counts` 加一个退役计数，或者把这两种情况
   记为 `completed`。这会改变状态面的语义和前端显示，单独开一项做。
4. **知识目录不可写应记为 `failed`，不是 `skipped/not_needed`。** 现在界面对它完全不显示。
5. **把真实的轮次记录交给 reflection。** `host_round_log` 一直是空字符串，模型只看到 Engineer 的自述。
   如果 `wrote_nothing` 占比高，先补证据，再看比例是否下降。
6. **`wrote_nothing` 多集中在一轮通过的任务上时**，说明 Reviewer 的 `learning` 字段判得偏保守
   （描述里写着"When unsure, worth_reflecting"），可以考虑让这类任务更多地直接跳过，省一次调用。
7. **路线图里的 36/16 和 31/28 应标明没有原始记录**，等新实例的统计出来后替换。

## 6. 本次代码改动

- `argus/life/reflection.py`：结果里加 `wrote`，取最后一条 `WROTE:` 行，值为 `nothing`、`paths` 或空。
- `argus/life/reflection_status.py`：`unchanged` 行写入 `detail`（上表六个值）；`skipped/not_needed`
  行写入跳过理由。状态判定本身没有改，前端只在 `paused` 时读 `detail`，显示不变。
- 测试：`tests/life/test_reflection_status.py`、`tests/life/test_reflection.py`。
