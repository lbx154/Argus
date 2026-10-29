# 反例实验室配置

> **已过时（2026-10-22 核对，RL-4）：**反例实验室的工作台页面已从网页版删除（`948cbb4d`），
> 下文所述的只读接口 `GET /api/projects/{sid}/counterexamples` 也已在 TS 对照精简第一步（B4）中删除。当前工作台见
> [web-control-responsiveness.md](web-control-responsiveness.md)。

这套集成包含两项
互相独立的能力，并且不携带任何私有猜想或战役数据：

- 科研工作台中的只读“反例实验室”；
- 跟随当前已发布分支的源码更新按钮；工作树不干净、detached 或无法快进时拒绝更新。

## 从源码安装

先按照主 README 的说明准备包含此集成的源码 checkout 和虚拟环境，然后在该环境中执行：

```bash
python -m pip install -e .
argus --version
argus doctor --advisor none --verify
```

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

打开“工作台”→“运行控制”，点击“拉取最新版本”。更新器会检查当前分支，只从公开仓库
拉取同名分支并使用 `--ff-only`，版本变化后重新安装 editable checkout，然后提示在安全
边界重启工作台和 daemon。工作树有本地修改、detached、分支未发布或历史分叉时都会
失败关闭，不会覆盖本地工作。

版本检查不会清除尚未重启的提示；更新进程意外退出后会显示失败，而不是一直显示忙碌。
如果源码已经成功快进、但安装步骤失败，状态会显示实际变更后的版本和安装错误，
不会声称工作树未发生变化。
