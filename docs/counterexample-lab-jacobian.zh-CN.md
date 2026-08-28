# 反例实验室与 Jacobian 配置

这套扩展发布在 `feature/counterexample-lab-jacobian-update` 分支，包含三项
互相独立的能力，并且不携带任何私有猜想或战役数据：

- 科研工作台中的只读“反例实验室”；
- 通过隔离 sidecar 调用 Jacobian 的 `math.find` 与 `math.run` MCP 契约；
- 跟随当前已发布分支的源码更新按钮；工作树不干净、detached 或无法快进时拒绝更新。

## 安装 Preview 分支

在现有 Python 3.11+ 环境中执行：

```bash
python -m pip install --upgrade --force-reinstall \
  "argus-skill @ git+https://github.com/lbx154/Argus.git@feature/counterexample-lab-jacobian-update"
argus --version
argus doctor --advisor none --verify
```

源码开发时，直接 clone 这个分支，创建虚拟环境，再按照主 README 的普通源码安装步骤
执行 editable install。

## 启用 Jacobian

Argus 不会把 Jacobian import 到自身进程，而是把已发布的 `jacobian-mcp` 可执行文件
作为受限 stdio sidecar 启动，只传递必要的进程环境，并保留 operation id、请求、
类型化输出、协议版本和结构化错误。

单独安装 Jacobian 并暴露可执行文件：

```bash
python -m pip install --upgrade jacobian
export ARGUS_SKILL_JACOBIAN_MCP_BIN="$(command -v jacobian-mcp)"
python -m argus_skill.tools.jacobian status
python -m argus_skill.tools.jacobian find --query "exact determinant"
```

只有发现该可执行文件时，数学 Engineer 与 Reviewer 才会收到 Jacobian 能力说明。
Jacobian 输出属于计算证据，不会被自动当成证明；Argus 仍要求命题对齐和独立复核。

## 给反例实验室提供数据

实验室只读投影 Argus 项目工作区中已有的文件：

```text
inputs/priority_pool.csv
outputs/results.csv
outputs/rejected.csv
parallel/<ID>/...
evidence/<ID>/README.md
research/MATH_STATE.json
```

`priority_pool.csv` 提供候选行，应包含 `ID`、`题目`、`具体描述`、`分类`、
`来源等级` 和 `验证级别`。进入 `results.csv` 的条目显示为已验证；进入
`rejected.csv` 的条目显示为已拒绝；`parallel/<ID>` 与 `evidence/<ID>` 中的文件
会推进实时构造和证据状态。API 全程只读，并限制文件大小、候选数量、ID 格式和递归扫描量。

## 在工作台内安全更新

打开“操作”→“运行时”，点击“拉取最新版本”。更新器会检查当前分支，只从公开仓库
拉取同名分支并使用 `--ff-only`，版本变化后重新安装 editable checkout，然后提示在安全
边界重启工作台和 daemon。工作树有本地修改、detached、分支未发布或历史分叉时都会
失败关闭，不会覆盖本地工作。
