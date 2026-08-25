# 系统审计：六条抱怨，逐条实测

关于这个运行时提出了六个问题。这份文档**拿代码去核对每一条**，而不是凭印象。下面每个数字
都是在撰写时对代码树实测得到的，并给出了可以自行复算的命令。

English version: [system-audit.md](system-audit.md)

**代码树规模：** `argus_skill/` 下 194,487 行 Python，随发行分发 264 份技能文档、共
22,765 行。

| # | 抱怨 | 判定 |
| --- | --- | --- |
| 1 | 过度防御 | **成立** —— 2,277 个 `try:`，其中 235 个吞掉错误继续跑 |
| 2 | 验证门槛过于 rigorous | **成立** —— 39 个失败码，5 个门要求精确的 CSV 列 |
| 3 | 太经常问人类不必要的问题 | **部分成立** —— 频率并不高，但**路由靠一张词表** |
| 4 | 指令遵循能力弱 | **成立** —— 任务之前已有 3,665 token 常驻指令，大头是 vertical banner |
| 5 | 冗余、过复杂、空转 | **成立** —— 24% 的事件类型是死的；同一件事有四套实现 |
| 6 | schema 乱用 | **在最要紧的地方已经修好了，但没推广** |

---

## 1. 过度防御 —— 成立

```
try:                                2,277
except Exception                      634
吞掉错误继续跑                          235
名字含 gate/verify/check/
audit/guard/contract/validate 的模块     42
```

**每三个 `except Exception` 里就有一个直接丢掉错误继续跑。** 这个习惯在 docstring 里是写明
了的——`"Fail-open: any error is swallowed"`（`argus_skill/skills/capability_trace.py`）、
`"Fail-open to ()"`（`argus_skill/skills/checklist_store.py`），以及
`argus_skill/skills/stage_machine.py` 里"**绝不能弄坏提示词构建**"这类注释。

每一处单独看都合理，加起来就不合理：**一个不会失败的运行时，也就是一个没法告诉你它坏了的运
行时。**

```bash
grep -rc "^\s*try:" --include=*.py argus_skill/ | awk -F: '{s+=$2} END{print s}'
```

## 2. 验证门槛过于 rigorous —— 成立

```
不同的失败码            39   （LIT、MPKG、NOV、NSL、NUM、PT、TH）
门模块                   9
要求精确 CSV 列的门        5
```

光是 Novelty-Seeking 一个门，就要求在能动笔写稿之前先交出**十个候选方向、每个十一列推理、
六项数值打分**（`argus_skill/verticals/physics/gates/novelty_seeking.py`）——**170 个表格
单元，换取"可以做一个声明"的资格。**

代价不在于这道检查本身，而在于**工作会朝着"把表格填满"弯过去**。

## 3. 不必要的人类打扰 —— 部分成立

```
与升级相关的出现次数     269  分布在 51 个文件
实测打扰频率        每 40.7 小时 1 次
其中属于研究判断的        13%  （1,548 小时里 5 次）
```

**频率是低的**，所以问题不是 Argus 老在打断你。问题是这个决定**怎么做出来的**。
`argus_skill/core/role_handoff.py:20` 用一条**在散文上跑的正则**来决定归属权：

> `permission|authorization|authorize|approval|approve|consent|confirmation|credential|access|secret|budget|purchase|pay|publish|release|deploy|production|irreversible|delete|destructive|…`

`access`、`release`、`production`、`delete` 都是**再普通不过的工程词**。一份写着"删掉临时目
录"或"读取配置"的回合摘要就会命中。这条正则是被当作**否决项**用的——它阻止一个请求被改判成
普通的 review 请求——所以**任何含有这些词的东西，默认就留给人类了。**

**一张词表分不清"权限"和"词汇"。**

## 4. 指令遵循能力弱 —— 成立，但不在我们最初说的那个地方

**我们第一次测错了口径，而这个修正很重要。** 我们把
`argus_skill/roles/prompts/manager.py` 里所有字符串字面量加总，报出"Manager 常驻指令
7,438 token"。但那个模块里装的是 **20 个针对不同场景的 prompt 构造器**，一次调用只触发一
个。把它们加总，什么也没测到。

按正确口径实测——一次真实的 Manager 阶段决策，在任何检查表、证据和任务内容加入之前：

| 组成 | 字符 | 是否总是出现 |
| --- | ---: | --- |
| `build_stage_decision_prompt` | 3,844 | 是 |
| `manager_rendering_prompt`（live-view 块） | 2,923 | 是——在 `manager/_stage_ops.py:779` 无条件追加 |
| vertical 的 `role_banner`（物理） | 7,893 | 在该 vertical 下，是 |
| **合计** | **14,660**（约 3,665 token） | |

**所以抱怨成立，但元凶不是我们点名的那个。** Manager 核心提示词是 3,844 字符。**最大的一
块常驻文本是 vertical 自己的 banner，7,893 字符——是它所包裹的那个决策提示词的两倍。** 而
一个 live-view 渲染块会被追加到**每一次**阶段决策上，无论这次决策跟渲染有没有关系。

一个在看到任务之前先被塞了 3,665 token 常驻规则的模型不可能全部遵守，而这个失败看起来像
"不听话"，其实是：**规则比指令遵循的预算更多。** 修法不是把语气写得更硬，而是**把规则写得
更少——先从 vertical banner 开刀。**

```bash
python3 -c "
import ast,pathlib
t=ast.parse(pathlib.Path('argus_skill/verticals/physics/stages.py').read_text())
for n in t.body:
    c=sum(len(x.value) for x in ast.walk(n)
          if isinstance(x,ast.Constant) and isinstance(x.value,str))
    if c>2000: print(c, getattr(n,'name',None) or n.targets[0].id)"
```

## 5. 冗余、过复杂、空转 —— 成立

**死掉的仪表。** 129 个 `EventType` 成员里，**31 个（24%）**在任何地方都没有被引用过——既没
有通过枚举符号，也没有通过它的字符串值。**这 31 个里有 20 个是 `SKILL_*` 和 `WIKI_*`**：
**这个系统本该借以学习的那两个知识面，装的几乎全是没有任何东西会发射的事件。**

另有 **6 个**是用**裸字符串**而不是枚举发射的（`LIFE_MISSION_SKIPPED`、
`LIFE_MISSION_REQUEUED`、`LIFE_VERTICAL_RESOLVED`、`LIFE_INBOX_QUEUED`、`SKILL_OUTCOME`、
`OPERATOR_ALERT`）。它们不是死的；它们是**同一个事件有两种发射方式**——这正是这份目录不再是
"运行时到底在做什么"的可靠索引的原因。

**一个郑重其事地什么都不做的函数。** `argus_skill/wiki/lifecycle.py:54` 接收七个参数、丢掉
其中五个，而且它自己就是这么写的：

> `"""Do nothing: Agents maintain pages and INDEX.md during the mission."""`

**同一件事被实现了好几遍。**

| 关注点 | 实现 |
| --- | --- |
| 账本 | `core/evidence_ledger.py`、`verticals/quant/search_ledger.py`、`verticals/research/literature_ledger.py` |
| 锁 / 租约 | `core/daemon_lock.py`、`core/file_lock.py`、`core/workspace_lease.py`、`tools/gpu_lease.py` |
| 状态持久化 | `core/pipeline_state.py`、`core/knob_store.py`、`daemon/state.py`、`life/memory.py`、`webapi/project_state.py` |

**体积。** 31 个文件超过 1,000 行，6 个超过 2,000 行。最大的是
`daemon/self_maintenance.py`，3,186 行；其次 `life/memory.py`，2,754 行。

**空转面。** 32 个 `while True`，15 处循环内 sleep。

## 6. schema 乱用 —— 在最要紧的地方已经修好了

这是六条里代码**已经部分回答了**的一条，而且它的论证值得原样引出来，因为**这份审计其余部分
主张的正是这个立场**。`argus_skill/core/role_reply.py` 直接从普通散文里读出角色的决定：

> 角色不被强制输出 JSON。一个被要求"只回一个 JSON 对象、别的什么都不要"的模型，会把它的回答
> 花在满足一个序列化器上而不是思考上，失去解释自己的能力，并且在多写一句上下文时让整个决定失
> 败。**Harness 并不比 Agent 更聪明，而要求一种线格式，就是 harness 在决定 Agent 可以怎么说
> 话。**

`KEY=value` 和 `KEY: value` 都行，上下方的散文不花任何代价，最后一次出现的为准；JSON 被接
受，但从不被要求。

**它没有被推广到的地方。** 5 个物理门仍然要求精确的 CSV 列集合；19 个模块在解析
frontmatter；274 个 `@dataclass` 和 21 个 `BaseModel` 描述着内部形状，其中一部分是要模型去
产出的。**这个原则已经存在、也已经写下来了，只是没有贯穿到系统其余部分。**

---

## 这些数字合起来说明什么

六条里五条被代码证实，第六条在"既有修法没被应用到"的所有地方也都成立。**它们不是六个问题，
而是同一个习惯的六种症状：出了事，我们就加一个机制。**

每一次添加在局部都有正当理由。加总起来，是一个有 2,277 个 `try:`、任务前 3,665 token
常驻指令、31 个死事件类型、四种加锁方式的运行时——以及一个**忙着填表、为"delete"这个词请示权
限、并且不遵守那些它根本没有余量读完的指令**的 Agent。

**纠正的办法不是再加一个机制，而是删除。**
