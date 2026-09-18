# 本地 PR regression gate 原型

开发者正常暂存代码后，只额外运行一个检查命令，生成一份可随代码提交的
`pr-regression-report.json`。默认不要求 Docker，不启动 Argus daemon，也不调用其他 agent。

实现统一位于 `argus/release_tools/pr_gate/`，本地回归检查、Copilot runner、
Skill、probe、schema 和证据校验均在其 `regression/` 子包中。
`scripts/pr-gate` 只保留入口；`experiments/pr_regression_50/` 只保留历史实验的
转发脚本和说明。没有修改或安装 Git hook、CI workflow、全局 Argus。

## 最小用法

在当前 checkout 根目录、已准备好的开发 Python 环境中：

```bash
git add <本次代码文件>
./scripts/pr-gate check
git add pr-regression-report.json
git commit -m "Your change"
```

之后可以随时执行便宜的核验：

```bash
./scripts/pr-gate verify
```

也可以使用包入口：

```bash
python -m argus.release_tools.pr_gate check
python -m argus.release_tools.pr_gate verify
```

正常安装该包后，同样提供 `pr-gate check` / `pr-gate verify` 命令。
原有 `python -m argus.release_tools.pr_gate --event ...` 描述一致性检查保持独立。

`verify` 不调用 Copilot、模型、Docker、测试或网络。
它检查当前暂存代码、基线、检查器/Skill 版本、报告结构和内嵌证据的一致性。
它不是第二次语义审查，也不会执行报告中的命令。

如需在另一份可信本地仓库上使用本 checkout 的工具：

```bash
/path/to/Argus-pr-gate/scripts/pr-gate check --repo /path/to/project --base main
```

检查和核验均不自动暂存报告，不修改原有暂存内容或分支。工具使用临时 index，可能在
Git 对象库中写入派生 tree 对象；除报告外不修改工作树，不创建提交。

## 检查的是哪一份代码

检查 **Git 暂存区的源代码快照**，不是当前机器上已安装的 Argus。
未暂存的 tracked 修改或非忽略的 untracked 源文件会得到明确错误，要求先暂存预期修改。
工具不会偷偷把这些文件暂存，也不会把尚未提交的不同内容当成已检查。

基线默认选择本地 `origin/main`，没有时选择本地 `main`；可以显式传 `--base`。
工具不自动 fetch，开发者应按正常工作流更新远端引用。
比较语义是 **merge-base 到暂存源码树**，不是模拟合入最新 main 的集成执行。
报告同时记录目标基线 SHA 和 merge-base SHA；它们前移或发生变化会使旧报告失效。

唯一排除项是仓库根目录的 `pr-regression-report.json`。该文件只能用于证据，
不能成为应用或 Skill 的运行时输入；其他路径、文件内容和 Git 模式均计入源树。
报告本身加入提交不会改变源码指纹，提交后保持同一源码树的报告仍可核验。
修改报告中的 `excluded_paths` 不能扩大排除范围。

当前原型要求已有提交的 SHA-1 Git 仓库，不支持 submodule；不支持时返回
`INCOMPLETE`，不会略过子模块后假装检查成功。
同样拒绝启用了仓库级 clean/process filter 或 promisor/partial-clone 配置的检查：
普通 `git diff` 可能执行过滤命令或自动取回对象。核验会关闭 lazy fetch、禁止网络 Git
传输，并在比较前拒绝这些配置，而不是为了判断报告是否新鲜运行仓库提供的程序。

## 默认 native 模式

`check` 的默认执行路径：

1. 冻结暂存树和比较基线，复制为独立临时 Git 源码快照。
2. 给 Copilot 与 probe 使用私有 HOME、Argus/Copilot 状态目录。
3. 使用现有已登录的 Copilot CLI 和固定 Skill，暴露本地文件/命令工具，不启用 MCP、
   自动加载的仓库指令、远程导出或额外 agent。
4. 优先静态分析；影响可忽略时允许短路，不要求生成测试。
5. 必要时通过有时限的 helper 执行配对 probe，并检查 tracked 源码没有被修改。
6. 在仓库级发布锁内重新确认代码、基线和策略未变，再原子替换 JSON 报告。

较早启动的检查若已过期，会在写入前失败，不覆盖较新检查生成的有效报告。
该锁协调 gate 的报告写入，不锁住编辑器或外部 `git add`；发布后仍会再次检查新鲜度，
提交前的 `verify` 也会拒绝后续代码变更。

当前执行支持 Linux。需要 Git、Python 3.11+、本工具所需的 `jsonschema`、`portalocker`，
以及已安装和认证的 Copilot CLI。使用激活环境中的 Python；不自动创建 venv 或安装测试依赖。
CLI 登录信息采用现有账户或受支持的 token 环境变量，不把登录文件复制进报告。

**临时目录和独立状态不是安全沙箱。** Native 模式执行的是开发者信任的本地测试，
仍有当前用户的宿主机权限，没有容器级文件、网络、进程或内存隔离。
工具不会主动启动真实 Argus 服务，Skill 也禁止这类操作，但这不能阻止恶意测试代码。
处理不可信外部 PR 时不要把 native 模式当安全边界。

可选强隔离路径：

```bash
./scripts/pr-gate check --isolation docker
```

只有显式选择该项才需要 Docker。它使用单独的容器、受限网络和独立资源名，
不挂载真实 home 或 Docker socket。此路径目前要求 Linux x86-64 的 standalone Copilot
可执行文件；专用容器、网络和镜像按本次运行的身份清理，不做全局 prune。

## 输出与状态

| Gate 状态 | 退出码 | 解释 |
|---|---:|---|
| `PASSED` | 0 | 在报告范围内未发现回归，且没有要求但未完成的升级检查；不是全局安全证明 |
| `BLOCKED` | 1 | 存在配对证据支持的局部回归候选，需要修复或维护者裁决 |
| `INCOMPLETE` | 2 | 证据不足、执行失败、缺依赖、超时、未暂存修改，或者报告过期/不一致 |

有局部回归候选时，即使分析器整体写了 `UNCERTAIN`，也会得到 `BLOCKED`，
不重复之前将混合判定报告直接拒收的问题。普通不确定性不会自动当成通过。
分析模式明确为 `incomplete` 时也不会仅凭 `NO_REGRESSION_FOUND` 被放行。

代码树与比较基线完全相同时，可以确定性短路，不启动 Copilot，也不运行测试。
其他低影响判断仍由 Skill 提供消费者分析和范围说明，不以 `.md` 等扩展名自动放行。

执行默认使用 `gpt-6-astra`、high effort；模型必须是当前账户可用的明确名称，不接受
自动路由。可以按实际可用模型指定：

```bash
./scripts/pr-gate check --model <model-id> --timeout 600
```

`--timeout` 是 Copilot 调用时限，范围 1–1200 秒，不含源码/镜像准备时间。
每个配对 probe 最多两侧各 120 秒、每次分析最多 6 组。
Native 模式没有 Docker 模式的 CPU/内存硬限制；复用当前测试环境，缺依赖时显式报告。
Linux 上每次命令使用独立的进程 supervisor 管理子孙进程，包括自行创建新 session 的
probe。正常结束、取消和超时都会先清理本次命令的后代；无法完成清理时返回错误并保留
工作目录，不把它报告为干净完成。这只解决进程生命周期，不把 native 模式变成安全沙箱。

## 比较有效性与检查范围

这些约束由 runner 和 verifier 检查，不仅依赖 Skill 的文字要求：

| 约束 | 处理方式 |
|---|---|
| 相同 oracle | 两侧使用同一个直接 Python 命令；冻结测试驱动、相关 pytest 配置/conftest 和声明的 fixture，比较路径与内容并检查执行期间漂移 |
| 源码来源 | 支持根目录和 `src/` Python 布局；记录实际进程、源码文件及 Git blob，与指定侧的源码树核对，拒绝落到外部 editable 安装的项目模块 |
| 完整范围声明 | `inspected_paths` 必须引用实际存在的 `base/<path>` 或 `candidate/<path>`；`change_coverage` 必须逐组说明每个变更路径 |
| 失败不放行 | oracle 不同、来源缺失或不可追踪、遗漏变更文件均不能支持 `PASSED`，也不能用无效配对证据支持 `BLOCKED` |

修改文件需要声明检查两侧版本，新增文件检查 candidate，删除文件检查 base。
每组 `change_coverage` 包含 `paths`、`status`（`analyzed`、`negligible` 或
`unresolved`）、`reason`、`evidence_refs`。主机生成 `scope`，将引用绑定到真实文件的
Git mode/blob；短路要求所有变更组都已说明为 negligible，但仍不强制生成测试。
这些是可核对的覆盖声明，不证明模型真正理解了每一行代码。

执行 probe 由 Copilot 完成，不增加开发者操作。例如在私有工作目录内：

```bash
python /assigned/runner/probe.py --id h1 \
  --command 'python -m pytest -q tests/_regression_probe/test_h1.py' \
  --oracle tests/fixtures/input.json
```

生成的 probe 必须放在两侧 `tests/_regression_probe/`，内容相同。
明确指定 pytest 文件/目录时，只冻结所选测试及相关配置/初始化文件，不因无关测试变化
把整个测试集复制进报告；未指定选择器时采用保守发现。计算得到或动态加载的 oracle
依赖需要通过 `--oracle` 显式声明。若 PR 修改了现有测试的预期值，不能把两套不同测试
的结果直接比较，应建立一个有独立契约依据的共同 oracle，或报告 `UNCERTAIN`。

本地 probe 通过 Python 文件读取监听执行一条保守规则：读取源码目录内的非 Python
数据文件时，该文件必须已在 oracle 清单中；遗漏的输入会记录
`unfingerprinted_data_input` 并使观测不完整，即使两侧测试都通过也不能据此放行。
用现有的 `--oracle` 显式补充即可；两侧内容不同仍然不可比较。工具不自动分类、
补齐依赖或重跑，也不自动采用某一侧的标准答案。
产品配置同样受这条规则约束，因此配置变更可能只能得到 `INCOMPLETE`，不能为了强行
比较而覆盖 candidate 配置。临时结果应放在提供的 `TMPDIR` 中；pytest 缓存也定向到
每次 probe 的私有临时目录，避免把前次测试缓存当成输入。该监听不是任意原生 I/O 的
追踪器，也不改变 native 模式的信任边界。

当前本地 v2 只接受直接 Python probe，不接受 shell 复合命令或禁用来源追踪的解释器
选项。外部项目模块、项目二进制扩展、缺失/不完整的来源记录、无法追踪的子进程等
会保留为不完整观测。Python 子进程必须继承追踪上下文；不支持的测试应说明限制，
不能为了得到结论绕过 guard。测试依赖仍复用现有环境，不声明它们在所有平台上可重现。

## 一份报告里有什么

当前报告格式为 `local-pr-regression/v2`，策略为 `local-staged-regression/v2`。
旧 v1 报告不能直接用于此版本的核验，需要重新运行 `check`，不能仅修改版本字段。

- `binding`：目标 base、merge-base、两侧 code-only tree、固定排除规则。
- `policy`：Skill 内容哈希、检查器实现哈希和策略版本。
- `snapshots`：分析用的合成 Git snapshot 身份；不是未来提交的 commit SHA。
- `execution`：原生/容器模式、实际模型与运行信息、源码审计和失败原因。
- `analysis`、`explanation`：分析器结论、假设、适用性质、范围与局限。
- `scope`：实际变更文件、绑定到源树的检查引用，以及未覆盖路径/无效声明。
- `evidence`：必要的复现代码/输入、receipt 和输出日志，带内容哈希，以 base64 内嵌。
- `gate`：由确定性策略派生，`verify` 会重算，不能只把这个字段改成 PASSED。

证据上限为 4 MiB/256 个文件，最终报告上限为 8 MiB。超限不会静默截断后通过。
Probe 输出在读取过程中即限制为每侧 1 MiB；溢出会停止命令并记录不完整观测。
原生 Copilot 调试日志也有 64 MiB 上限。Receipt 的必需字段和每个假设的 probe 引用都需
有效；超时、中断或截断的观测不能支撑放行。若另一项完整的局部见证已成立，则仍返回
`BLOCKED`，不因其他 probe 不完整而隐藏该发现。
负退出码与 shell 的 `128+signal` 状态按不完整观测处理；后者表示信号式退出约定，
并不单凭数字断言发生了 OOM。普通断言失败的退出码 1 不会因此被误分类为中断。
证据路径不能逃逸，也不能使用 symlink；常见凭据形状会被拒绝提交，但这不是完整
秘密扫描器，分享前仍须检查证据。模型原始轨迹、usage 和工作日志留在
`~/.cache/pr-regression-gate/`（或 XDG cache 下），不进入提交报告。
临时源码克隆、模型 HOME 和包缓存会清理，必要诊断记录保留在该私有缓存。

## 信任边界与非目标

这是防遗漏、防过期、便于复核的协作门禁，不是防恶意伪造系统。
哈希和来源追踪约束协作执行中的内容与来源，不是防恶意作者的执行认证；
相同 oracle 是可比较的必要条件，也不自动证明 oracle 的语义正确。
这次实现整理不重跑、不改写 50-PR 历史实验或其结论。

仓库已有的描述一致性 workflow 不会因为这个原型就自动要求该报告。
若以后将 `verify` 接入 CI，应从可信版本执行它，并由 CI 指定真实目标基线；
不能使用 PR 自己修改过的 verifier 来为该 PR 提供有权限的可信认证。
