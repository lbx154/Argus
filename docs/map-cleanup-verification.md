# 地图状态与手机任务详情验收

地图卡片已移除任务编号，项目状态只保留地图上方的一处。经理回复使用当前调用的最新发言；尚未发言时显示“正在回复你的消息”。项目是否运行、暂停或完成仍以服务端状态为准。

390px 手机上进入任务内部时，输入岛及模型标签暂时收起。引用步骤会返回输入，保留草稿和引用。步骤详情及任务说明复用现有工作区路径格式化能力，工作区之外的路径保持原样；任务说明继续读取原有任务轨迹接口，没有更换日志数据入口。

2026-10-29，使用 Chromium 和构建后的 TypeScript 服务，在 1440 × 900px 与 390 × 900px 下验证。独立状态目录及端口为 `.integration/t3/home`、9413；网页构建输出位于 `.integration/t3/web`。已完成任务来自包含调用开始、结束和交接信的验证记录；经理回复由脚本后端经真实消息接口流式产生，没有调用模型服务。

| 验收项 | 浏览器观察 |
| --- | --- |
| 卡片编号 | 同一张地图有问答卡和两项任务；概览及任务内部均无补零编号，以任务名区分。动画和回放仍保留任务顺序。 |
| 项目状态 | 修复前顶栏、团队行和状态行共显示三处完成状态；修复后只保留团队行。任务自身的完成标签继续显示。进度条已移除。 |
| 经理回复 | 两种宽度下都确认默认回复提示、第一句消息、第二句消息及结束后的“已完成”；卡片和状态同步更新，不再显示固定整理进展文案。手机放大并平移画布检查卡片文字。 |
| 手机步骤详情 | 阅读器左边界约 30px，右边界约 363px；页面宽度仍为 390px。输入岛和模型标签不可见，引用后恢复输入并保留草稿。 |
| 手机任务说明 | 弹窗左边界 12px、右边界 378px；底部点击命中详情自身，输入岛没有覆盖详情。 |
| 长路径 | 验证工作区含空格，文件路径含中文和长目录名。步骤摘要截短之前先移除工作区前缀；步骤阅读器、已保存说明和任务轨迹展示均检查了相对路径。 |

验证结果：

- 地图、状态、详情和手机相关测试：40 个文件，355 项通过。
- 前端全量测试：135 个文件，1,278 项通过。
- 前端构建：通过类型检查与生产构建；构建仅提示已有的大分块警告。
- 根目录检查：在包含当前源码的隔离副本中执行，383 项通过，1 项 Windows 专用测试跳过。
- 浏览器验证：无页面脚本错误；任务说明实际请求任务轨迹接口，长路径按工作区相对路径显示。

检查进程使用 48–63 号 CPU，共 16 核，未分配 GPU，内存预算为 64GiB。服务、构建和测试设置了 64GiB 虚拟地址上限。Chromium 的虚拟地址预留超过该值，浏览器改按实际驻留内存核对预算；一次服务及浏览器进程树采样合计约 1,258MiB。机器的 cgroup 目录只读且没有用户 systemd 总线，因此没有给浏览器设置 64GiB 的驻留内存硬上限。

命令及证据：

- `npm --prefix frontend/web test -- --maxWorkers=8 map submap macroCard workStatus taskDetail mobile semanticCamera`：[相关测试日志](../.integration/t3/tests-map.log)。
- `npm --prefix frontend/web test -- --maxWorkers=8`：[全量测试日志](../.integration/t3/tests-web.log)。
- `npm --prefix frontend/web run build -- --outDir ../../.integration/t3/web`：[前端构建日志](../.integration/t3/build-web.log)。
- 隔离副本中的 `npm run check`：[根检查日志](../.integration/t3/core-check-final.log)。
- [浏览器脚本](../.integration/t3/browser.mjs)、[逐项结果](../.integration/t3/browser-results.json)、[经理新一轮回复脚本](../.integration/t3/browser-manager.mjs)及[检查结果](../.integration/t3/browser-manager-results.json)、[手机经理最新回复](../.integration/t3/fresh-latest-gate-390.png)、[资源记录](../.integration/t3/resources.json)。
- [修复前的完成状态与编号](../.integration/t3/before-map-390.png)、[修复后的手机地图](../.integration/t3/after-completed-390.png)、[手机步骤详情](../.integration/t3/after-step-390.png)、[手机任务说明](../.integration/t3/after-task-390.png)、[手机引用后草稿](../.integration/t3/after-quote-390.png)、[桌面步骤详情](../.integration/t3/after-step-1440.png)。

只提交源码、测试和本说明。构建输出、浏览器依赖、验证状态目录及所有生成的发布文件均不在提交中。
