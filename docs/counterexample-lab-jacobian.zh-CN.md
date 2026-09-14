# 反例实验室与 Jacobian 配置

这套集成包含三项
互相独立的能力，并且不携带任何私有猜想或战役数据：

- 科研工作台中的只读“反例实验室”；
- 通过隔离 sidecar 调用 Jacobian 的 `math.find` 与 `math.run` MCP 契约；
- 跟随当前已发布分支的源码更新按钮；工作树不干净、detached 或无法快进时拒绝更新。

## 从源码安装

先按照主 README 的说明准备包含此集成的源码 checkout 和虚拟环境，然后在该环境中执行：

```bash
python -m pip install -e .
argus --version
argus doctor --advisor none --verify
```

## 启用 Jacobian

Argus 不会把 Jacobian import 到自身进程，而是把已发布的 `jacobian-mcp` 可执行文件
作为受限 stdio sidecar 启动，只传递必要的进程环境，并保留 operation id、请求、
类型化输出、协议版本和结构化错误。这是进程与依赖隔离，不是操作系统沙箱；
该程序仍拥有操作者的文件系统权限。

按照 Jacobian 自身的安装说明在独立环境中安装，再配置该环境内可执行文件的绝对路径：

```bash
export ARGUS_SKILL_JACOBIAN_MCP_BIN="/path/to/jacobian-environment/bin/jacobian-mcp"
python -m argus.tools.jacobian status
python -m argus.tools.jacobian find --query "exact determinant"
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
扫描上限也包含空目录；解析后越出工作区的路径不会被读取。这些标签只是已有文件的投影，
不代表 Reviewer 已经验收，也不会将 Argus 任务标记为完成。

## 在工作台内安全更新

打开“操作”→“运行时”，点击“拉取最新版本”。更新器会检查当前分支，只从公开仓库
拉取同名分支并使用 `--ff-only`，版本变化后重新安装 editable checkout，然后提示在安全
边界重启工作台和 daemon。工作树有本地修改、detached、分支未发布或历史分叉时都会
失败关闭，不会覆盖本地工作。

版本检查不会清除尚未重启的提示；更新进程意外退出后会显示失败，而不是一直显示忙碌。
如果源码已经成功快进、但安装步骤失败，状态会显示实际变更后的版本和安装错误，
不会声称工作树未发生变化。
