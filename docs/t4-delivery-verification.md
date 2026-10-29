交付现在按文件显示第几次交付，预览和下载使用所选交付保存的版本。内容不变的再次交付也会递增次数；同一次交付中的多个文件分别计数。旧二进制被后续交付更新时，旧卡片明确显示“已被后来的交付更新”。保存副本缺失时，弹窗说明副本不可用并禁用文件下载。

次数由服务端读取交付记录时计算，传在各文件的 `delivery_number` 字段中，与文本副本的版本标识分开。成果预览请求携带交付身份，沿用已有保存副本与预览路由。网页 iframe、原文件下载、zip、Markdown 图片、图片缩略图和模型资源都保留这个身份。网页依赖只有在同次交付有可用版本时才会加载，否则显示资源缺失提示。交付预览保留“继续修改”；工作区的“编辑与版本”仍从当前文件入口打开。

验证使用正常消息和经理交接流程生成的真实 TS 服务端记录，模型后端由脚本化 Codex app-server 替身提供交接表单。文本报告先交付数值 10，再改成 20 并增加 CSV，最后原样再交付；另有二进制更新、两版同名网页和删除旧副本的记录。基线与修改版使用同一批文件和记录。

| 验收项 | 修改前的浏览器观察 | 修改后的 1440px 桌面与 390px 手机观察 |
| --- | --- | --- |
| 重复交付 | 三张同名报告卡和三个同名选项无法区分 | 报告显示第 1、2、3 次交付；第三次仍复用第二次的文本副本；CSV 分别为第 1、2 次 |
| 旧文本 | 第一版说明为 10，预览却是 20 | 第一版预览与下载均为 10；第二、三次均为 20；从旧说明中的链接打开也保持第一版 |
| 旧网页 | 读取当前工作区页面 | 旧交付 iframe 显示“旧版网页”，切换后显示“新版网页”，网页下载请求携带相应交付身份 |
| 二进制更新 | 旧交付读取当前文件 | 旧卡片显示更新提示；旧弹窗说明没有保存副本；新版仍可下载正确字节 |
| 保存副本缺失 | 旧交付读取当前文件 | 弹窗明确显示“这次交付的保存副本已不可用”，不显示当前版本内容 |
| 单文件按钮 | 同时有“下载全部（zip）”和“下载网页（zip）” | 仅有“下载网页（zip）”；实际下载成功 |
| 旧模型的资源候选 | 包内网格直接读取成功，但无关的根目录网格更新导致模型资源请求失败 | 按候选顺序命中即停，模型资源请求成功；机器人在两种尺寸正常渲染 |

模型补修使用同样的正常交付流程重新生成记录：先交付机器人、包内网格和根目录网格，再只更新根目录网格。模型资源查找现在命中后立即停止，所选资源自身不可用仍明确报错。新增服务端回归测试在修复前因 HTTP 410 而失败，修复后通过；它也验证了所选网格失效且后续候选可读时仍返回 HTTP 410。两个尺寸的浏览器还复验了这个失效场景：明确显示模型不可用，不改读后续候选。

两种尺寸的浏览器脚本均通过全部断言，页面脚本错误均为 0。弹窗边界在视口内，页面没有横向溢出。截图已等待开场动画完成。

| 检查 | 结果 |
| --- | --- |
| 根目录 `npm run check` | 384 项通过，1 项 Windows 专用测试跳过，0 项失败；含契约生成一致性、编译、测试类型检查与所有 TS 服务端测试 |
| 服务端定向测试 | 43 项通过，覆盖真实记录重放、分页/增量计数、旧文本、网页与配套资源、模型资源、zip、缺失副本和未保存版本 |
| 成果及预览相关前端测试 | 13 个测试文件、144 项通过，含缓存按交付区分与预览调用者 |
| 前端构建 | 成功，输出在本任务独立目录 |
| 首轮前端全量测试 | 135 个测试文件、1278 项全部通过（含当前并行地图修改） |

运行使用独立临时 ARGUS_HOME、端口 9414、CPU 16–31（16 个核心）、64 GiB 内存预算和空 GPU 可见列表。检查工具使用共享的 `~/.cache/argus-browser-tools` 与 `~/.cache/ms-playwright`。原有文本保存上限与十份副本保留规则沿用；未保存文件仍按既有大小、修改时间标识核对。

证据与复查入口：

- [真实交付重放夹具说明](../packages/core/test/fixtures/delivery-versions/README.md)与[记录及保存文本](../packages/core/test/fixtures/delivery-versions/record.json)。
- [浏览器脚本](../.integration/t4/browser.mjs)、[断言与请求记录](../.integration/t4/browser.json)、[基线观察](../.integration/t4/before.json)。
- [手机旧报告](../.integration/t4/old-text-390.png)、[桌面旧报告](../.integration/t4/old-text-1440.png)、[手机新报告](../.integration/t4/new-text-390.png)、[原样再次交付](../.integration/t4/unchanged-text-390.png)。
- [手机二进制卡片](../.integration/t4/binary-card-390.png)、[旧二进制不可用提示](../.integration/t4/binary-unavailable-390.png)、[保存副本缺失提示](../.integration/t4/missing-copy-390.png)。
- [桌面单文件旧网页](../.integration/t4/old-html-single-1440.png)、[手机单文件旧网页](../.integration/t4/old-html-single-390.png)、[手机新版网页](../.integration/t4/new-html-single-390.png)。
- [模型候选回归夹具](../packages/core/test/fixtures/delivery-versions/model-candidates.json)、[补修前失败的测试](../.integration/t4/model-test-before.log)、[浏览器复验脚本](../.integration/t4/model-browser.mjs)、[补修前观察](../.integration/t4/model-browser-before.json)、[补修后观察](../.integration/t4/model-browser-after.json)。
- [桌面模型](../.integration/t4/model-after-1440.png)、[手机模型](../.integration/t4/model-after-390.png)、[桌面所选网格失效](../.integration/t4/model-selected-unavailable-1440.png)、[手机所选网格失效](../.integration/t4/model-selected-unavailable-390.png)。
- [根目录检查日志](../.integration/t4/model-root-check.log)、[服务端测试日志](../.integration/t4/model-server-tests.log)、[相关前端测试日志](../.integration/t4/model-frontend-tests.log)、[前端全量测试日志](../.integration/t4/frontend-all-tests.log)、[前端构建日志](../.integration/t4/model-frontend-build.log)。

浏览器复查可使用仍运行的 `http://127.0.0.1:9414/?token=t4-local-check&project=p-3fecfce4`；模型回归项目为 `http://127.0.0.1:9414/?token=t4-local-check&project=p-95ed46fd`。隔离状态、构建、工具脚本和截图仅保留在工作区验证目录，提交只包含源码、测试与本文档。
