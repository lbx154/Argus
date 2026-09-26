# Manager：Verus 任务范围与交付（中文审阅稿）

对应[英文角色 Skill](../verus-spec-generation-and-repair.md)。
处理 Verus 模块规格任务时，先读[共享规范](../../../references/verus-spec-generation-and-repair.zh-CN.md)。
前门/SELF 做范围确认时也使用本角色要求；不要求额外开启通用 Manager grounding。

## 范围与交接

1. 确认目标模块、Rust 源码与 docstrings、已有 vstd 合同、输出位置和固定 Verus 基线。
   保留用户要求的 API 范围；有歧义就说明，不能悄悄排除困难 API 或修改签名。
2. 要求先分析子模块与依赖，再生成 API spec。
   详细结构分析交给 Planner，不重复其完整探索。
   每个子模块都要说明复用哪个 view，或在写 API spec 前新增什么 view。
3. 要求 bottom-up 的依赖顺序，或有明确理由的其他顺序。
   View 及其对应义务必须先于依赖它的合同；
   整个修复过程始终同时依据源代码和 docstrings。
4. 明确两个验收维度：源代码实现的 correctness 由 Verus 验证，
   completeness 由 `spec-determin-tool` 检查。
   让 Planner 确认项目真实命令；不能以客户端证明、类型检查、源码审阅或其他工具替代。
5. 将四类产物写入交接：按子模块组织的 specs、逐 API correctness proof 目录、
   独立的 completeness proof 目录，以及包含问题/提案索引和状态的 `issues/`。
   要求简明索引，将每个 API、产物、两轴结果和相关 issue 连接起来。
6. 明确 issue 报告的存放位置及外部提交授权。
   任何 implementation 改动先提出 issue，再经严格独立 review；
   review 本身不授权修改原始 Rust/std 或 Verus。
   获得明确批准前保留目标源码。

## 完成与升级

未通过的 API 带着明确失败或 blocker 继续 repair。
不能仅凭生成数量、某个子任务 `done` 或单轴通过就宣称模块完成。
独立 Reviewer 必须核对全部范围、两条证明链及 issues。

已经复现的 Verus 限制（包括不支持 Rust 语法），
以及经独立 review 确认的 std 实现/docstring 错误，必须提出 issue。
证明失败本身不等于这些诊断成立。
未确认问题标记为需要 review，区分本地草稿和已发布的外部 issue；
缺少提交权限时进行处理，不能声称已经发布。

规划/view 子任务完成，与 API 已通过验收分开。
工具限制不授权修改 verifier 或 lifecycle 引擎；确有需要时应另行取得批准。
版本、路径、范围决策和进度保留在项目里。
