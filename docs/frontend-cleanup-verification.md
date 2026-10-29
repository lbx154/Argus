# 前端零散修复验证

2026-10-29，分支 `self/fe-small`。任务中的 12 项已完成，使用构建后的 TypeScript 服务与 Chromium，在 1440 × 900 和 390 × 900 像素下逐项验证。服务使用独立状态目录；新建、发送、成果交付由仓库的虚拟 Codex 后端驱动，没有调用模型服务。

| 项目 | 结果与证据 |
| --- | --- |
| 1. 工作台样式污染 | 删除重复 Tailwind 指令、全局主按钮和状态点规则，其余选择器限定在工作台内。两种宽度下，懒加载前后主按钮和灰点计算颜色相同；修复前两者都变透明。 |
| 2. 删除与撤销 | 删除通知不含服务器路径，直接提供撤销；POSIX 和 Windows 风格的删除响应均只提取回收条目标识，点击后恢复同一个项目并显示“项目已恢复。”。工作台回收区及无调用方的前端列表客户端已删除。 |
| 3. 轮询与研究说明 | 两种宽度下空闲 11 秒均新增 0 次成果列表请求；显式失效仍能刷新。研究说明目录、提供器、预览模式、专属接口和状态已删除；文件预览专用标题、说明、来源链接、编辑限制以及共享契约的研究说明／进展快照类型也已删除，打开项目没有研究说明请求。 |
| 4. 新建后的反向提示 | 从落地页输入第一句话，项目建立并完成虚拟后端流程；整个过程中没有“新项目已就绪”提示。 |
| 5. 新成果浮条 | 实际交付文件时没有浮条；浮条样式、计时、状态及专属文案已删除。成果查看入口仍可使用。 |
| 6. 改写重发 | 点击后原文直接进入输入框；编辑后实际通过普通消息接口发送，没有弹窗、改写上下文或对话版本请求。服务端对话版本读写均返回 404，文件版本读写测试继续通过。 |
| 7. 搜索焦点 | 两种宽度下打开搜索后，活动元素均为搜索输入框。使用 `data-autofocus`；弹窗入场动画只改变透明度，避免隐藏状态使下一帧聚焦失败。 |
| 8. JSONL 预览 | 页面显示两行 JSONL 原文。HTTP 回归测试同时覆盖未知扩展名、无扩展名、中文 UTF-8、NUL 和非法 UTF-8；后两类继续按二进制下载。 |
| 9. 中英文文案 | 资源接口返回负载与内存数字，中文页面显示“负载／内存已用”；英文页面显示“Project folder”。技能卡不再显示缺少中文版本的英文描述。 |
| 10. 插件入口 | 高级工具展开后只有技能库，没有插件入口；相关组件、导入和专属测试已删除。 |
| 11. 无效令牌 | 空白浏览器显示缺少访问凭证；错误令牌链接通过真实握手显示“这个链接无效或已过期”，令牌从地址栏移除后仍能区分。 |
| 12. 其余小问题 | 保存改名后弹窗关闭，浏览器标题随项目名更新；标题和标签统一为“成果与文件”；390 像素下长路径占用 309 像素，无水平溢出；实际团队生命周期日志不再显示前端添加的表情符号。 |

地图文件只拆除研究说明的引用、专属选项和类型，保留原始记录阅读、已保存说明和引用证据的逻辑。对话行只调整改写按钮传参和移除团队日志图形，未改动角色面板、状态说法、消息顺序、附件或停止逻辑。

验证命令与结果：

- 根目录 `npm run check`：类型检查及构建成功，365 项测试通过；1 项仅适用于 Windows 的文件占用测试在 Linux 跳过。
- `npm --prefix frontend/web run build`：类型检查及生产构建成功。
- `npm --prefix frontend/web test`：139 个测试文件、1,312 项测试通过。
- `git diff --check`：通过。

浏览器脚本、逐项结果和截图保存在本次工作区的 `.integration/fe-small/`，不提交构建产物或浏览器依赖：

- [修复前复现记录](../.integration/fe-small/baseline.json)
- [界面验证结果](../.integration/fe-small/browser-results.json)及[验证脚本](../.integration/fe-small/check.cjs)
- [新建、发送和交付验证结果](../.integration/fe-small/flow-results.json)及[验证脚本](../.integration/fe-small/flow-check.cjs)
- [桌面 JSONL 预览](../.integration/fe-small/after-files-1440.png)、[390 像素 JSONL 预览](../.integration/fe-small/after-files-390.png)
- [390 像素恢复提示](../.integration/fe-small/after-undo-390.png)、[390 像素资源卡](../.integration/fe-small/after-workbench-390.png)
- [根目录检查日志](../.integration/fe-small/core-check.log)、[前端测试日志](../.integration/fe-small/web-test.log)、[前端构建日志](../.integration/fe-small/web-build.log)

补充验证：Windows 回收路径与预览清理。

本次在 Linux 服务上回放 Windows 删除响应，再调用真实恢复接口；没有运行 Windows 主机。1440 和 390 像素下，修复前恢复请求都携带了整段反斜杠路径并返回 404，修复后只携带条目标识并返回 200。正斜杠和反斜杠响应均纳入回归测试，新增 Windows 用例在修复前失败、修复后通过。

文件预览同时回放普通文件响应以及附带旧研究说明、进展快照元数据的响应。两种宽度下都按文件名和 59 B 大小显示，编辑入口可打开；旧元数据不再覆盖标题、限制编辑或向链接清单添加来源。普通 Markdown 文件的登记链接和编辑回调另有回归测试。对前端、共享契约及 TS 服务源码的检索未发现相关专有字段、类型、函数和文案。

本次重跑结果与原始证据：

- [核心检查](../.integration/fe-small/revision/core-check.log)、[前端构建](../.integration/fe-small/revision/web-build.log)、[前端 1,312 项测试](../.integration/fe-small/revision/web-test.log)
- [Windows 撤销修复前](../.integration/fe-small/revision/undo-before.json)、[修复后](../.integration/fe-small/revision/undo-after.json)、[浏览器脚本](../.integration/fe-small/revision/undo-windows.cjs)、[单元回归修复前失败](../.integration/fe-small/revision/undo-unit-before.log)
- [文件预览清理前](../.integration/fe-small/revision/preview-before.json)、[清理后](../.integration/fe-small/revision/preview-after.json)、[浏览器脚本](../.integration/fe-small/revision/preview-cleanup.cjs)、[源码残留检索](../.integration/fe-small/revision/removed-reader-scan.json)
- [390 像素 Windows 撤销成功](../.integration/fe-small/revision/undo-after-390.png)、[390 像素文件预览](../.integration/fe-small/revision/preview-reader_foundation-after-390.png)
