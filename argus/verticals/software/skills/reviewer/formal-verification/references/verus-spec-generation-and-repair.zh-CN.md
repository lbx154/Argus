# Reviewer：Verus 正确性与完备性验收（中文审阅稿）

对应[英文角色 Skill](../verus-spec-generation-and-repair.md)。
先读[共享规范](../../../references/verus-spec-generation-and-repair.zh-CN.md)。
使用自己的 OWN Skill，不能只参考 Engineer 的方法或自报结果。

## 检查真实范围与语义

核对子模块/API 清单、依赖顺序、view 定义及源码对应。
确认所需 view 先于依赖它的 spec；其他 API 顺序必须有真实依据。
不能把缺失 constructor 关系或任意 observer 当作完整 family。

每条被修改的合同都与实现和 docstring 对照。
检查签名、合法输入、返回分支、内容、修改 frame、已消费的 iterator 状态及有关回调。
检查共享依赖和代表性的 API 组合，不只看孤立声明。

## 独立检查两个证明维度

Correctness：用目标 Verus 基线重放受影响的源代码实现证明。
确认实际检查了当前候选对应的函数体/义务，源码转换的对应义务也已完成。
拒绝循环使用目标假设、跳过函数体、synthetic 占位实现、新增可信答案和零义务运行。
类型检查、原生测试或客户端证明不能替代这一轴。

Completeness：对当前合同运行真实 `spec-determin-tool` 路径，
检查生成的 proof/query、输出比较关系、前提和结果。
拒绝通过削弱比较、输入携带答案、错误前置条件、`UNKNOWN` 或替代工具取得的“成功”。
检查有关合法正例。确定的错误答案仍然错误；仅实现证明也不说明完备。

重放被修改 API 及受影响依赖。
只有确认候选、证明输入和基线仍对应，才能复用未改变的证据。
任何一轴都不能仅从 Engineer 的文字报告推出通过。

## 严格 review implementation 改动和 issues

任何 implementation 改动都要先有 issue 提案，包括源码证明副本的可执行重写。
独立对照原实现/提案差异，检查源码与 docstrings、可观察行为及安全影响、
源码到 proof 的对应，以及受影响的证明。
不能因为另一份实现容易证明，就批准替换原实现。
在 issue 中记录 review 决定；修改原始 std 或 Verus 仍需要用户明确授权。

在目标基线上复现报告的 Verus 限制，包括不支持的 Rust 语法。
确认 std 实现/docstring 缺陷前，先排除 spec/view/proof 或检查编码错误，
指出真实的源码/docstring 错误。证明失败或 `UNKNOWN` 不足以支持该结论。
未确认的诊断保持 `needs-review`。

核对必要问题和修改提案已进入 `issues/`，关联受影响 API/proof，
且 review 与提交状态真实。本地草稿不是上游 issue URL。
Issue 经过 review，不等于任何证明轴通过，也不等于 API 已修复。

## 交付与裁定

对齐按子模块组织的 specs、逐 API 的 Verus correctness proof 目录、
独立的 completeness proof 目录，以及包含索引的 `issues/`。
每个范围内 API 对应其 spec、两类 proof、两个明确结果/重放命令及相关 issues。
没有发现问题时明确已 review 的范围，不能编造报告。
只有状态标签、缺失 proof 文件，或重复计算 helper，都不等于 API 证明覆盖。

同一候选两轴都通过才接受 API；模块完成还要求全覆盖与可用的聚合入口。
规划/view 子任务可以完成较窄目标，但不能据此认证依赖 API。
未通过时指出最小失败义务与下一步 repair，或具体 blocker/replan 理由；不得降低标准。
Verifier 修改仍需作为工具任务另行批准。
