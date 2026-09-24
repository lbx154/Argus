# Verus 模块规格生成与修复：共享规范（中文审阅稿）

对应[英文共享规范](../verus-spec-generation-and-repair.md)。
本文件与下列各角色中文版均为 `references/` 参考资料，不作为重复 Skill 枚举。

对 Verus 模块规格任务，四个角色都必须使用共享规范及自己的角色 Skill。
这是领域任务的交付要求，不是新增调度阶段，也不授予修改 Verus 的权限。
具体项目路径、版本、目标数量、命令和进度保留在项目中。

| 角色 | 责任与中文版 |
|---|---|
| Manager | [范围、基线与交付要求](../manager/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md) |
| Planner | [子模块分析与依赖顺序](../planner/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md) |
| Engineer | [View、API spec、两类证明与修复](../engineer/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md) |
| Reviewer | [独立验收 correctness 与 completeness](../reviewer/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md) |

前门/SELF 做任务范围确认时采用 Manager 的要求；跨角色参考不替代各角色自己的职责。

## 1. 先分析模块，再生成 API 规格

首先检查目标模块真实的子模块、类型、impl、重导出与 API 依赖。
每个范围内 API 都要有 canonical identity 和所属子模块。
区分已有 vstd 覆盖、需要新增的规格以及有明确依据的排除项，不能悄悄删掉困难目标。
这里拆分的是任务，不是修改原始 Rust 模块结构。

对每个子模块，先判断现有 vstd view 是否够用。
如果需要新 view，先定义 view，并明确它与源码表示状态的关系和证明义务，
然后再写依赖该 view 的 API spec。
共享 view 定义及前置 lemma 必须先于依赖它们的子模块。
依赖 constructor 的对应证明可以与 constructor 一起完成，不应制造循环前置门槛；
但 API 验收前必须完成源码对应证明。
只有 observer 声明、却没有被表示状态的对应依据，不能算 view 已完成。

随后按真实依赖 bottom-up 地为各 API 写 spec。
可以采用其他合理顺序，但必须解释实现/证明依赖方面的理由，
而且需要的 view 定义仍须先于依赖它的合同。
相互依赖的 API 应作为一组处理，明确共同的证明义务。
如果 observer/consumer 依赖构造关系，应优先完成 constructor。

## 2. 同时依据源代码和 docstring

生成和修复每个 API 时，同时阅读实现和 docstring，
包括委托调用、泛型约束、安全前提、panic 条件及相关示例。
同时记录源码与 docstring 的位置。
若存在实质分歧，应先明确处理方式，不能只挑容易让证明通过的一方。

保留公开绑定、签名、生命周期和预期输入域。
不得为方便证明增加 `Copy`、`Eq` 或 `'static`；
除非任务明确要求，否则保留已有 vstd 合同。
优先使用直接的 `@`、`Seq`、索引、子区间和适当的既有抽象。
Helper 应表达可复用结构，而不是隐藏 API 的全部答案。

明确观察返回内容、分支、顺序、修改区域及必要写回等哪些性质。
指针元数据不等于指向内容。只有签名、调用域或简单长度关系的合同是部分结果，
不能算完整的内容规格。

## 3. 每个 API 必须有两个独立反馈维度

两类证明必须对应同一版候选 spec、view、输入域和 Rust/Verus 基线。

| 维度 | 必须提供的证明 | 不能拿什么替代 |
|---|---|---|
| Correctness | 用源代码实现证明 API 满足候选合同，并由 Verus 验证通过 | 类型检查、源码审阅、原生测试，或把候选合同当作假设的客户端证明 |
| Completeness | 用 `spec-determin-tool` 为候选的选定观察量生成并成功检查可重放的完整性 proof/harness | 临时换一个检查器、手写成功摘要，或无关客户端证明 |

Correctness 要验证源码函数体，或忠实从源码转换的函数体，并完成转换的源码对应义务。
注明真实 API、源码范围和既有可信依赖。模型若缺少对应关系，不能冒充实现证明。
不能直接假设目标后置条件，不能用 synthetic `loop { }` 替代实现，
也不能通过 `external_body`、`admit`、新公理或可信 wrapper 跳过函数体。
没有检查到相关证明义务的空运行不算通过。

Completeness 要先确认项目实际的 `spec-determin-tool` 入口，使用受支持的命令和生成的 harness；
不要编造 CLI，也不能悄悄换工具。保存比较关系、query/proof 输入、结果与重放命令。
成功结论只对明确的 view 与合法边界成立。
源码/docstring 允许多解时，应独立论证输出等价关系；
不得抹掉必须观察的性质或把目标答案放入前提。

确定性不等于正确性，矛盾合同或错误前置条件也可能让查询空真。
还需检查相关合法正例和输入域是否忠实。
`UNKNOWN`、跳过义务、编译失败和空证明运行，在任何一轴都不能算成功。

## 4. 两个维度都通过前，反复 repair

```text
源代码 + docstrings -> view -> API 候选
                                |
                  +-------------+-------------+
                  |                           |
          Verus correctness        spec-determin-tool completeness
                  |                           |
                  +-------------+-------------+
                                |
             任一未通过 -> 定位原因 -> repair -> 重跑
                                |
               同一候选的两轴均通过 -> 独立 review
```

根据实际问题修复合同、view、源码证明转换或检查编码，不改变 API 原本语义。
修复可能是删除过强承诺，也可能是补充缺失关系，不总是加强合同。
共享 view/helper 变化时，依赖它的证明也需要重新确认。
语义变化后重跑受影响的两类检查；只有候选、依赖和基线都未变的结果才能复用。

用明确失败或反例指导下一次尝试。
不能反复跑未改变的大套件，不能削弱必要检查或排除合法输入来换取通过。
如果确有源码/工具限制无法闭合，保留候选和两类证明尝试，并按下一节提出对应 issue。
该 API 仍是未完成；报告 blocker 或 raise issue 不等于修复成功。

## 5. 改 implementation 前先 raise issue

Implementation 改动必须先提出 issue，并经严格的独立 review 后才能采纳。
范围包括原始 Rust/std 函数体、源码证明副本中的可执行重写或语法展开，
以及 verifier/tool 的实现修改。不能把可执行代码改动包装成“proof cleanup”。
不改变可执行实现的普通证明注解和 lemma，本身不需要 implementation-change issue。

修改 implementation 前先创建 issue，说明拟议差异及理由。
探索性 patch 只能作为隔离、尚未被接受的提案保留。
Reviewer 必须对照原实现与提案，严格审查源码/docstring 语义、安全与行为影响、
源码到 proof 的对应关系，以及受影响的 correctness/completeness 证明。
构建通过或作者自审不能替代独立 review。
修改原始源码、Verus 或可信基础，还需要用户明确授权；review 通过不等于获得该权限。

以下发现同样必须以 issue 形式提出：

| 分类 | 必须有的依据 |
|---|---|
| `verus-limitation` | 在目标基线上可复现的限制，例如不支持某种 Rust 语法；附最小源码示例、命令和实际诊断 |
| `stdlib-implementation` | 调查失败证明、排除 spec/view/proof/checker 编码错误后，经独立 review 确认实现有错 |
| `stdlib-docstring` | 经独立 review 确认 docstring 错误或源码/docstring 不一致；附准确位置、预期行为和实际行为 |

证明失败或 `UNKNOWN` 本身，不证明 std 实现错误，也不证明某种 Rust 特性不受支持。
未确认的诊断保留为 `needs-review`，不能标成已确认的上游缺陷。
为适配 Verus 而重写可执行源码的 workaround，要写进同一份限制/提案 issue，
并接受同样严格的 review。同一根因影响多个 API 时关联同一 issue，
不必为每次重试重复开单。

每份 `issues/<issue-id>.md` 至少说明：分类和状态、受影响子模块/API、
源码及 docstring 位置、目标工具/源码基线、最小复现和实际输出、
预期与观察到的行为、拟议修改/workaround（如有）、独立 review 结论、
授权状态，以及相关证明结果的引用。
保留原问题记录；针对修改后 implementation 的证明必须标明新基线。

本地 `issues/` 记录必须交付。已获授权时，还应提交到指定的外部 issue tracker，
保留真实 issue URL 和提交状态。
未明确目标或授权时，由 Manager 处理，并标记 `awaiting-filing`；
不能把本地草稿说成已经发布的上游 issue。
不得为了证明通过而静默修改实现或 docstring。
受影响 API 未解决时保持未完成，但其他独立工作可以继续。

## 6. 最少必须交付的产物

至少交付以下四类产物：

| 产物集合 | 内容 |
|---|---|
| `specs/` | 默认按 `specs/<submodule>/` 组织；每个子模块一个或几个 spec/view 文件，需要时共享 view，并提供全模块可构建聚合入口 |
| `proofs/correctness/<submodule>/<api>.rs` | 每个 API 的源代码实现证明，由 Verus 检查 |
| `proofs/completeness/<submodule>/<api>.rs` | 每个 API 供 `spec-determin-tool` 检查的完整性 proof/harness |
| `issues/` | `issues/INDEX.md`、发现的问题和 implementation-change 提案、review 结论，以及适用的外部提交记录 |

Specs 应对应子模块图，不把无关 API 全部混在一个扁平文件里。
真正共用的 view 放在共享规格文件，避免重复；根聚合入口可以 include 各子模块规格。
可以沿用仓库等价布局，但必须明确映射到这四类交付物。
工具原生的 query 文件与重放日志放在相应证明旁；只有状态 JSON 不满足 proof 交付要求。

维护一个简明的逐 API 索引，连接 API、源码/docstring、view/spec、
correctness 证明与命令/结果、completeness 证明与命令/结果，以及相关 issue ID。
可以复用已有 manifest 或简洁文件头，不要求另造大型证据协议。
允许共享 proof helper，但每个 API 的两轴都必须有可识别、可重放的证明入口。
被阻塞的 API 也要保留尝试产物和失败状态，不得漏行。
没有发现问题时，在 `issues/INDEX.md` 明确说明及列出已 review 的范围；
不能为了填满目录而编造 issue。

只有当前候选的两轴均通过，才能接受该 API。
只有全模块覆盖和四类产物都对齐，才能接受整个模块。
规划或仅 view 的子任务可以完成自己的有界目标，但不能声称依赖它的 API 或全模块已通过。

## 7. 保持验证与借用边界

固定目标 upstream Verus/vstd 基线。
普通 lemma 和任务要求的 `assume_specification` 属于规格工作；
lowering、借用检查、lifecycle 跟踪、可信 intrinsic 与公理变动属于独立工具任务，
需要最小复现和明确批准。隔离的 verifier fork 仍会改变可信基础。

借用值写回优先使用现有机制：
整个 Vec 的借出切片通常写 `slice@ == old(vec)@` 和 `final(slice)@ == final(vec)@`；
两个子区域写 `final(slice)@ == final(ret.0)@ + final(ret.1)@`，
并说明初始子区间、保留未借出区域。
`final` 是相关借用的最终值，不代表执行了 `Drop`；允许调用者合法修改。

默认不增加 owner ID、allocation token 或 lifecycle 事件状态机。
确有要求的析构效果必须有明确支持；`forget` 可以跳过清理。
区分借出的 `&mut T` 与移出的 `T`。
构造时 observer 等于 `remaining`，不证明消费后仍然相等；
需要检查 constructor/observer/consumer 的组合。

忠实传递 callback、`FnMut`、`Clone` 和有关 `Drop` 状态。
不得用选择答案的 UF 填补语义空缺，也不能把不透明关系搬进后端。
既有 vstd 抽象只能用于有依据的关系。
原生测试、删句/负控和客户端证明是补充，不替代两条必要证明链。
不得执行含原生 UB 的负例。

参考所选版本中的 `source/vstd/array.rs::ref_mut_array_unsizing_coercion`、
`source/vstd/std_specs/vec.rs` 中的 `Vec::as_mut_slice`/`vec_index_mut`，
以及 `source/vstd/std_specs/slice.rs` 中的 `first_mut`/`split_at_mut`/`iter_mut`。
不要假设不同版本的定义始终相同。
