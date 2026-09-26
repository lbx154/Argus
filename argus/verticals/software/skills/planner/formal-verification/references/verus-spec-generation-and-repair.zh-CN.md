# Planner：Verus 子模块与证明规划（中文审阅稿）

对应[英文角色 Skill](../verus-spec-generation-and-repair.md)。
安排 API 生成前，先读[共享规范](../../../references/verus-spec-generation-and-repair.zh-CN.md)和 Manager 的范围。

## 首先建立模块图

检查实际子模块、类型/impl 所属位置、重导出、委托调用和跨模块依赖。
为 API 分配 canonical identity，不能把重导出或注释镜像重复计为 API。
记录已有 vstd 覆盖与有明确依据的排除项，不得静默缩小范围。

每个子模块要说明源码、docstrings、既有或所需 view、支撑 lemma、API 和依赖。
可以复用项目 inventory，不要求新增一套规划文件协议。

## 工作顺序

1. 先安排共享 view 定义，并明确源码对应义务。
   每个子模块要么指出复用的 view 及其充分性，要么先建立 view 定义任务，
   再安排依赖它的 API spec。
   依赖 constructor 的对应证明与该 API 一起完成，不制造循环前置门槛；
   验收前必须闭合证明。
2. 按实现与证明依赖 bottom-up；采用其他顺序时说明理由。
   相互依赖的 API family 一起处理并明确共同义务。
   依赖 constructor 关系的 observer 不能被孤立验收。
3. 每个 API 任务携带源码和 docstring 位置、选定 view、准确公开签名、
   前置依赖合同和输出路径；不能只给 API 名字或想要的后置条件。
4. 确认真实 Verus 与 `spec-determin-tool` 入口。
   每个 API 规划两类独立证明：源码到合同的 correctness，以及同一合同的工具生成 completeness。
   两类检查进入每个 API 的 repair 循环，不能推迟到全模块写完之后。
5. 将每个子模块映射到 `specs/<submodule>/` 文件及全模块聚合入口，
   同时规划两个证明目录和 `issues/`。
   每个范围内 API 在两条证明链都有独立可识别的入口；
   已有合同若也属于本次验证范围，同样需要这些入口，并关联适用的 issue。
6. Implementation-change 提案必须先成为 issue，经过严格独立 review，
   才能采纳 implementation 修改任务。
   为疑似 Verus 限制安排最小复现，为疑似 std 实现/docstring 缺陷安排 review。
   不能从一个未决证明直接假定上游存在 bug。

## 反馈与重规划

用实际 correctness 失败义务、completeness 反例或未决 query 决定下一项有界修复。
View/helper 改变会重新打开全部依赖它的 API 检查。
保持原语义及检查输入，不能换成会通过的替代目标，也不能让合法输入消失。

阻塞项、已尝试的 proof 产物和 issue 引用保持可见。
共享同一限制的 API 关联同一根因 issue。
对具体源码/工具限制调整计划，不要反复执行未改变的命令；
可以继续独立子模块，但不得绕过 issue/review 要求。
调查、仅 view 的结果或单轴通过，都不等于 API 完成。
最后安排覆盖核对，以及 Reviewer 对模块两条证明链的独立验收。
