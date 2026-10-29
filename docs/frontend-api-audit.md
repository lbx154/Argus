# Web 前端接口对照

本表覆盖 `frontend/web` 与 `frontend/core` 清理前的请求和资源 URL 入口；参数路径以 `:id` 等表示，查询参数不另算路由。依据为 TypeScript 服务端及其实际挂载模块，不以 Python 服务端或 capability 声明推断路由存在。

“有”表示清理后的服务端存在该 HTTP 方法和路径。“原有，已删”仅用于本轮同时删除的事件 REST 接口。工作区接口确实由 `workspace.ts` 挂载，全部保留。不存在的接口及其独有界面、状态、辅助代码和测试均删除；没有新增服务端端点、兼容层或 capability 遮蔽。

| HTTP 方法与路径 | 服务端有没有 | 最终处理 |
| --- | --- | --- |
| GET `/api/meta` | 有，主路由表外 | 保留握手和配对验证 |
| GET 静态资源（`/assets/*`、`/icon-*.png`、`/manifest.webmanifest` 等） | 有，sendStatic | 保留构建产物、字体和内置图标；不属于 JSON API |
| GET `/api/application-brand` | 有 | 保留应用名称读取 |
| GET `/api/application-brand/manifest.webmanifest` | 有 | 保留已有 manifest 地址 |
| GET `/api/application-brand/assets/:name` | 无 | 删除图标、标志资源 URL 校验和图片分支，保留仓库 SVG |
| GET、POST `/api/browser-state/organization` | 有 | 保留侧栏置顶、归档和跟进 |
| GET `/api/search` | 有 | 保留历史搜索 |
| GET、POST `/api/projects/:id/editions/artifact` | 有 | 保留结果版本读取和保存 |
| POST `/api/projects/:id/editions/artifact/diff` | 有 | 保留编辑差异预览 |
| GET、POST `/api/application-workflows` | 无 | 删除共享请求工厂及 Web、TUI 工作流界面 |
| POST `/api/application-workflows/open` | 无 | 同上，删除打开工作流入口 |
| GET、POST `/api/application-workflows/:id` | 无 | 同上，删除工作流读取与操作 |
| GET `/api/application-workflows/:id/document` | 无 | 同上，删除工作流文档读取 |
| GET `/api/projects/:id/map` | 有 | 保留真实项目进度图 |
| GET `/api/map-copy/:source/:name` | 无 | 删除说明读取 hook、生成缓存与已保存说明界面，任务直接读取自身记录和轨迹 |
| GET `/api/projects/:id/map-trace` | 有 | 保留任务调用轨迹 |
| GET `/api/map-datasets` | 有 | 唯一调用者是已删除的历史数据集选择器，一并删除闲置方法；服务端路由未改 |
| GET `/api/map-datasets/:id` | 无 | 删除历史数据集选择器、URL/本地存储状态与加载逻辑 |
| GET `/api/projects` | 有 | 保留实际使用的 projectIndex，删除只供测试调用的重复 listProjects |
| GET `/api/projects/costs` | 有 | 保留项目费用读取 |
| POST `/api/daemons` | 有 | 保留创建项目 |
| PATCH、DELETE `/api/projects/:id` | 有 | 保留重命名和移入回收站 |
| GET `/api/projects/:id/snapshot` | 有 | t1 保留请求并删除 compact/events_limit 参数 |
| GET `/api/projects/:id/config` | 有 | 保留项目设置读取 |
| GET `/api/projects/:id/model-choices` | 有 | 保留模型选择 |
| POST `/api/projects/:id/model-choices/probe` | 有 | 保留模型验证 |
| GET `/api/projects/:id/conversation` | 有 | 保留真实会话分页 |
| GET `/api/projects/:id/events` | 原有，已删 | t3 删除客户端方法、REST 预载和服务端路由，首屏由流回放建立；按单一事件加载要求删除原有实现 |
| GET `/api/projects/:id/backlog/:task` | 有 | 保留任务详情读取 |
| GET `/api/projects/:id/artifacts` | 有 | 保留结果列表 |
| GET `/api/projects/:id/artifact` | 有 | 保留结果详情 |
| GET `/api/projects/:id/artifact/preview` | 有 | 保留 HTML 预览准备 |
| GET `/api/projects/:id/artifact/preview/page` | 有 | 保留沙箱 iframe 资源 URL |
| GET `/api/projects/:id/artifact/bundle` | 有 | 保留单页及关联文件 ZIP 下载 |
| POST `/api/projects/:id/artifacts/bundle` | 有 | 保留多文件 ZIP 下载 |
| GET `/api/projects/:id/artifact/raw` | 有 | 保留文件读取、下载及 PDF/图片/模型预览 URL |
| GET `/api/projects/:id/artifact/model-asset` | 有 | 保留模型引用的网格、纹理和缓冲读取 |
| GET `/api/projects/:id/delivery-diff` | 有 | 保留交付版本差异 |
| GET `/api/system/resources` | 有 | 保留机器资源读取 |
| POST `/api/trash/:entry/restore` | 有 | 保留恢复项目 |
| POST `/api/projects/:id/tasks` | 无 | 删除客户端方法及 addTask mutation，无界面调用者 |
| POST `/api/projects/:id/mission/abort` | 无 | 删除客户端方法和仅验证虚构路由的测试，无 Web 界面调用者 |
| GET、POST `/api/projects/:id/map-notes` | 无 | 删除批注 hook、编辑器、展示、上下文、样式与测试 |
| GET `/api/projects/:id/canvas` | 无 | 删除画布读取 hook、状态与独有组件 |
| POST `/api/projects/:id/canvas/cards` | 无 | 删除人工想法、文件、必经步骤卡片入口 |
| PATCH、DELETE `/api/projects/:id/canvas/cards/:card` | 无 | 删除人工卡片编辑、检查点与删除入口 |
| POST `/api/projects/:id/canvas/links` | 无 | 删除人工连线入口 |
| PATCH、DELETE `/api/projects/:id/canvas/links/:link` | 无 | 删除人工关系编辑和删除入口 |
| PUT `/api/projects/:id/canvas/positions` | 无 | 删除人工位置持久化及卡片拖动入口 |
| POST `/api/projects/:id/backlog/:task/answer` | 无 | 删除方法和旧问题兼容分支，只显示有真实 decision ID 的问题；清理共享分支的 TUI 调用 |
| POST `/api/projects/:id/decisions/:decision/resolve` | 有 | 保留问题回答 |
| GET `/api/projects/:id/attachments/limits` | 有 | 保留上传限制读取 |
| POST `/api/projects/:id/attachments` | 有 | 保留小文件上传 |
| POST `/api/projects/:id/attachments/uploads` | 有 | 保留分块上传创建 |
| GET、PUT、DELETE `/api/projects/:id/attachments/uploads/:upload` | 有 | 保留断点状态、写入和临时文件清理 |
| POST `/api/projects/:id/attachments/uploads/:upload/complete` | 有 | 保留分块上传完成 |
| DELETE `/api/projects/:id/attachments/:attachment` | 有 | 保留草稿附件移除 |
| POST `/api/projects/:id/message` | 有 | 保留消息和 domain_answer 调用 |
| POST `/api/projects/:id/message/stream` | 有 | 保留消息 SSE |
| POST `/api/projects/:id/message/followup` | 有 | 保留追加消息 |
| POST `/api/projects/:id/prompt/rewrite` | 有 | 保留草稿改写 |
| POST `/api/projects/:id/config/set` | 有 | 保留设置保存 |
| POST `/api/projects/:id/reset` | 有 | 保留会话重置 |
| GET `/api/skill-library` | 有 | 保留技能列表 |
| GET `/api/skill-library/document` | 有 | 保留技能正文 |
| GET `/api/projects/:id/research/method` | 无 | 删除方法及独有类型，无生产界面调用者 |
| GET `/api/knowledge/feed` | 有 | 保留学习记录 |
| GET `/api/projects/:id/learning` | 有 | 保留技能修订通知 |
| POST `/api/projects/:id/launch-cwd` | 无 | 删除方法及对应测试调用，无 Web 界面调用者 |
| POST `/api/projects/:id/workdir` | 有 | 保留工作目录设置 |
| POST `/api/projects/:id/continuous` | 无 | t1 删除连续工作方法、状态与入口 |
| POST `/api/projects/:id/daemon/start` | 有 | 保留继续工作 |
| POST `/api/projects/:id/daemon/stop` | 有 | 保留停止工作 |
| GET `/api/projects/stream` | 有 | 保留项目状态 SSE |
| GET `/api/projects/:id/stream` | 有 | 保留 UI 首次进入 replay=40&view=ui 回放、后续追加及 view=rows 会话投影 |
| GET `/api/v2/workspaces` | 有 | 保留工作区选择 |
| GET `/api/v2/workspace/tree` | 有 | 保留 IDE 文件树和输入框路径补全 |
| GET `/api/v2/workspace/file` | 有 | 保留工作区文本读取 |
| GET `/api/v2/workspace/git` | 有 | 保留版本管理视图 |
| GET `/api/v2/workspace/raw` | 有 | 保留 Blob 读取及资源 URL |
| GET `/api/v2/workspace/download` | 有 | 保留文件下载 |
| GET `/admin/status` | 无 | 删除管理数据独立页面及身份读取 |
| GET `/invite/status` | 无 | 删除管理页对应用户身份读取 |
| GET `/invite/compute`（侧栏导航） | 无专用页面或路由 | 删除算力额度入口及 hosted-trial 条件分支 |
| GET `/admin/api/training/collaboration` | 无 | 删除管理数据概览 |
| GET `/admin/api/training/collaboration/:tenant/:id` | 无 | 删除管理数据详情 |
| GET `/admin/api/training/observations/:tenant/:id` | 无 | 删除管理数据过程读取 |
| GET `/admin/api/research/status` | 无 | 删除采集诊断 |
| GET `/admin/api/tenants/:tenant/projects` | 无 | 删除管理数据项目目录 |
| GET `/admin/api/training/audit` | 无 | 删除导出记录 |
| POST `/admin/api/training/export-observations` | 无 | 删除管理数据导出 |

## 请求和资源入口

- `frontend/web/src/api.ts`：普通 JSON、Blob、上传 XHR、消息 SSE、项目与事件 SSE，以及结果、模型引用和预览 URL。
- `frontend/web/src/research-workbench/workspaceApi.ts`：工作区 JSON、Blob、下载和 raw URL。
- `frontend/web/src/applicationBrand.ts`：应用身份请求及 manifest 地址。品牌图片以前由 `frontend/core/src/applicationBrand.ts` 校验 URL、`Wordmark` 渲染，相关缺失路由已清理。
- 已删除 `frontend/web/src/admin-data/api.ts` 和 `frontend/core/src/applicationWorkflows.ts`。共享工作流工厂的 TUI 消费者也已删除。
- `PdfPreview`、模型加载器和 HTML iframe 使用上述入口产生的受支持资源地址或 Blob；没有另一个硬编码服务端 API。

## 服务端依据

- `packages/core/src/serve/index.ts`：主路由表、单独处理的 meta、各模块挂载以及 SSE。
- 同目录 `projects.ts`：项目设置、费用、重置、工作目录、组织状态；`workspace.ts`：工作区全部六类 GET。
- 同目录 `attachments.ts`、`trash.ts`、`versions.ts`：上传、删除/恢复、版本与交付差异。
- 同目录 `preview.ts`：预览页面和两种 ZIP；`records.ts`：任务详情和搜索；`resources.ts`：机器资源；`library.ts`：技能和学习。

事件加载的独立验收已完成，依据为 `.integration/snapshot-truth/events/README.md`；本任务不重复该项验收。
