import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

export type Locale = 'zh-CN' | 'en-US'
export type ThemePreference = 'dark' | 'light' | 'system'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>

export const PREFERENCES_STORAGE_KEYS = {
  locale: 'argus-flywheel.locale',
  theme: 'argus-flywheel.theme',
} as const

const LEGACY_PREFERENCE_KEYS = {
  locale: 'argus-foundry.locale',
  theme: 'argus-foundry.theme',
} as const

const zhCN = {
  'brand.name': 'ARGUS',
  'brand.descriptor': '科研编排与数据飞轮',

  'nav.group.plan': '规划',
  'nav.group.run': '运行',
  'nav.group.decide': '沉淀',
  'nav.group.system': '系统',
  'nav.overview': '总览',
  'nav.context': '团队条件',
  'nav.ideas': '创意雷达',
  'nav.campaigns': '科研项目',
  'nav.review': '评审委员会',
  'nav.dataVault': '数据仓',
  'nav.approvals': '人工审批',
  'nav.outcomes': '结果与答辩',
  'nav.connections': 'Argus 连接',
  'nav.settings': '设置',
  'nav.purpose.overview': '掌握会议窗口、风险与下一步行动',
  'nav.purpose.context': '冻结团队资源、约束和研究目标',
  'nav.purpose.ideas': '生成、辩论、比较和标注研究方向',
  'nav.purpose.campaigns': '跟踪 Argus 执行、版本、证据与产物',
  'nav.purpose.review': '让独立评审者检查创新性、方法与完整性',
  'nav.purpose.dataVault': '验证并封存完整 Research Episode 与数据集快照',
  'nav.purpose.approvals': '处理算力、启动、评审和发布门禁',
  'nav.purpose.outcomes': '沉淀投稿、审稿、答辩与训练反馈',
  'nav.purpose.connections': '管理 Argus 实例和运行连通性',
  'nav.purpose.settings': '配置资源、通知、语言和外观',

  'shell.search': '搜索',
  'shell.searchPlaceholder': '搜索会议、项目、Idea 或证据…',
  'shell.quickGo': '快速前往',
  'shell.searchResults': '搜索结果',
  'shell.liveApi': '实时 API',
  'shell.demoData': '演示数据',
  'shell.demoTitle': '当前显示演示数据',
  'shell.demoDetail': '会议、资源、项目、评分与版本仅用于界面体验，不代表真实运行状态。',
  'shell.activeCampaigns': '个项目正在运行',
  'shell.gpuConfigured': '张 GPU 已配置',
  'shell.operator': '科研操作员',
  'shell.workspace': 'Argus 工作台',
  'shell.notifications': '通知',
  'shell.command': '命令菜单',
  'shell.language': '语言',
  'shell.theme': '主题',
  'shell.dark': '深色',
  'shell.light': '浅色',
  'shell.system': '跟随系统',
  'shell.openNavigation': '打开导航',
  'shell.openNav': '打开导航',
  'shell.closeNavigation': '关闭导航',
  'shell.primaryNav': '主导航',
  'shell.expandNav': '展开导航',
  'shell.collapseNav': '收起导航',
  'shell.skipToContent': '跳到主要内容',

  'spine.plan': '规划',
  'spine.context': '团队条件',
  'spine.target': '会议目标',
  'spine.ideas': '创意雷达',
  'spine.run': 'Argus 研究',
  'spine.review': '评审',
  'spine.learn': '数据回流',

  'page.overview.eyebrow': 'Evidence Horizon',
  'page.overview.title': '科研机会与风险总览',
  'page.overview.description': '从会议窗口、团队条件和真实证据中确定此刻最值得推进的工作。',
  'page.context.eyebrow': 'Context Studio',
  'page.context.title': '把团队真实条件冻结为研究上下文',
  'page.context.description': '同一会议会根据人员、数据、算力、时间与合规条件生成不同方向。',
  'page.ideas.eyebrow': 'Idea Radar',
  'page.ideas.title': '生成并对抗检验研究方向',
  'page.ideas.description': '团队条件、会议证据与可执行性。',
  'page.campaigns.eyebrow': 'Argus Campaigns',
  'page.campaigns.title': '运行可恢复、可审批的科研项目',
  'page.campaigns.description': '查看 Argus 进程、阶段、版本、证据、资源消耗和人工门禁。',
  'page.review.eyebrow': 'Review Council',
  'page.review.title': '独立评审与证据核查',
  'page.review.description': '保留评审分歧，不伪造录用概率，也不掩盖证据缺口。',
  'page.approvals.eyebrow': 'Approval Inbox',
  'page.approvals.title': '需要人工判断的决策',
  'page.approvals.description': '启动、算力、数据、评审和发布操作必须留下明确授权。',
  'page.outcomes.eyebrow': 'Outcome Memory',
  'page.outcomes.title': '从投稿结果中持续学习',
  'page.outcomes.description': '将审稿意见、分数、答辩版本和人工标签沉淀为受控数据。',
  'page.dataVault.eyebrow': 'Research Data Flywheel',
  'page.dataVault.title': '不可变研究数据仓',
  'page.dataVault.description': '把团队条件、Idea、Prompt、Argus 轨迹、论文与真实评审封装成可验证的 Research Episode。',
  'page.connections.eyebrow': 'Argus Connections',
  'page.connections.title': '连接并核验 Argus 运行实例',
  'page.connections.description': '显示协议、能力、版本与实时健康状态，不把配置状态冒充连接成功。',
  'page.settings.eyebrow': 'Workspace Settings',
  'page.settings.title': '工作台设置',
  'page.settings.description': '管理资源、通知、语言、外观与本地偏好。',

  'context.tab.profiles': '团队画像',
  'context.tab.runs': '选题任务',
  'context.tab.bench': 'Flywheel Bench',
  'context.bench.title': 'Flywheel Bench · 人类偏好数据',

  'action.syncSources': '同步证据',
  'action.freshReview': '发起全新独立评审',
  'action.recordOutcome': '记录投稿结果',
  'action.addConnection': '添加 Argus 连接',
  'action.saveChanges': '保存更改',
  'action.reminderRules': '提醒规则',
  'action.teamPlan': '为我的团队生成方案',
  'action.createFromContext': '从团队条件创建',

  'ideas.monitor.live': '来源已连接',
  'ideas.monitor.demo': '演示信号',
  'ideas.source.settings': '来源设置',
  'ideas.source.openreview': 'OpenReview 会场 ID',
  'ideas.source.github': 'GitHub 仓库',
  'ideas.list.count': '{count} 个候选',
  'ideas.collection.label': 'Idea 类型',
  'ideas.collection.conditioned': '团队候选',
  'ideas.collection.baseline': '基线',
  'ideas.state.refreshed': '已同步',
  'ideas.state.collision': '存在冲突',
  'ideas.state.conditioned': '条件化',
  'ideas.state.baseline': '基线 · 不可执行',
  'ideas.state.baselineCollision': '基线 · 存在冲突',
  'ideas.empty.conditioned': '还没有团队条件化候选',
  'ideas.empty.baseline': '暂无基线',
  'ideas.load.failed': '团队候选读取失败',
  'ideas.load.partial': '{count} 个 ideation run 无法校验',
  'ideas.score.novelty': '创新',
  'ideas.score.feasibility': '可行',
  'ideas.inspector.eyebrow': '证据差异',
  'ideas.inspector.delta': '与已有工作的差异',
  'ideas.inspector.sources': '近期信号',
  'ideas.inspector.empty': '暂无证据',
  'ideas.inspector.sync': '同步证据',
  'ideas.inspector.resource': '资源与风险',
  'ideas.inspector.compute': '算力',
  'ideas.inspector.risk': '风险',
  'ideas.inspector.teamAdvantage': '团队特有优势',
  'ideas.inspector.collisionTest': '新颖性碰撞测试',
  'ideas.inspector.openPrompt': '查看 Prompt',
  'ideas.inspector.loadingPrompt': '正在读取…',
  'ideas.inspector.liveRequired': '连接 Argus 后可查看 Prompt',
  'ideas.prompt.unavailable': 'Prompt 暂不可用',
  'ideas.prompt.readonly': '只读合约',
  'ideas.prompt.title': 'Idea Prompt',
  'ideas.prompt.ready': '可进入执行审批',
  'ideas.prompt.notReady': '尚不可执行',
  'ideas.prompt.missing': '缺少',
  'ideas.select.title': '选择一个 Idea',
  'ideas.select.detail': '查看证据差异、资源条件和 Prompt。',
  'ideas.identity.team': '团队',
  'ideas.identity.venue': '会议',
  'ideas.identity.run': 'RUN',
  'ideas.identity.condition': 'CONDITION SHA',
  'ideas.identity.objective': 'OBJECTIVE SHA',
  'ideas.baseline.generate': '按团队条件生成',
  'ideas.campaign.create': '创建方向专属项目',
  'ideas.campaign.open': '打开项目',
  'ideas.campaign.creating': '正在冻结 Prompt…',
  'ideas.campaign.none': '未创建',
  'ideas.campaign.running': '运行中',
  'ideas.campaign.review': '评审中',
  'ideas.campaign.attention': '需处理',
  'ideas.campaign.paused': '已暂停',
  'ideas.campaign.idle': '未启动',
  'ideas.campaign.ready': '可启动',
  'ideas.campaign.completed': '已完成',
  'ideas.campaign.unknown': '状态未知',
  'ideas.campaign.created': '项目 {id} 已创建；Prompt {sha} 已冻结，尚未启动 Argus。',
  'ideas.campaign.failed': '方向专属项目创建失败',
  'ideas.campaign.invalidReceipt': '服务端未返回可验证的 idle Campaign 回执；没有跳转，也没有视为成功。',
  'view.horizon': '时间视野',
  'view.calendar': '日历',
  'settings.tab.compute': '资源池',
  'settings.tab.models': '角色模型',
  'settings.tab.notifications': '通知',
  'settings.tab.releases': '版本政策',
  'settings.tab.appearance': '外观与语言',

  'settings.appearance.title': '外观与语言',
  'settings.appearance.description': '选择界面语言和主题；设置会保存在当前浏览器。',
  'settings.appearance.language': '界面语言',
  'settings.appearance.theme': '界面主题',
  'settings.appearance.themeDark': '深色主题',
  'settings.appearance.themeLight': '浅色主题',
  'settings.appearance.themeSystem': '跟随系统主题',
  'settings.appearance.currentTheme': '当前主题',
  'settings.appearance.currentLanguage': '当前语言',

  'common.open': '打开',
  'common.noResults': '没有匹配结果',
  'common.create': '创建',
  'common.save': '保存',
  'common.cancel': '取消',
  'common.close': '关闭',
  'common.confirm': '确认',
  'common.refresh': '刷新',
  'common.retry': '重试',
  'common.search': '搜索',
  'common.start': '启动',
  'common.stop': '停止',
  'common.continue': '继续',
  'common.approve': '批准',
  'common.reject': '拒绝',
  'common.back': '返回',
  'common.next': '下一步',
  'common.loading': '加载中…',
  'common.empty': '暂无内容',
  'common.error': '发生错误',

  'artifact.title': 'Argus 工件',
  'artifact.catalog': 'Argus 可用工件',
  'artifact.imports': '导入记录',
  'artifact.partialUnavailable': '部分工件状态不可用',
  'artifact.resealCount': '{count} 个已确认工件尚未进入当前 head',
  'artifact.catalogEmpty': '当前 Episode 没有可读取的 Argus 工件。',
  'artifact.importsEmpty': '尚无工件导入记录。',
  'artifact.importsUnavailable': '导入状态未确认；Stage 已关闭。',
  'artifact.roleLabel': '工件角色',
  'artifact.roleChoose': '选择角色',
  'artifact.role.conditionSnapshot': '条件快照',
  'artifact.role.promptContract': 'Prompt 合同',
  'artifact.role.trajectory': '运行轨迹',
  'artifact.role.experimentSpec': '实验设计',
  'artifact.role.experimentResult': '实验结果',
  'artifact.role.paper': '论文',
  'artifact.role.outcome': '研究结果',
  'artifact.role.reviewCertificate': '评审证明',
  'artifact.role.integrityReport': '完整性报告',
  'artifact.role.reproducibilityManifest': '复现清单',
  'artifact.state.staging': 'staging',
  'artifact.state.discarded': '已放弃',
  'artifact.state.sealed': '已封存',
  'artifact.state.confirmedUnsealed': '已确认 · 待封存',
  'artifact.action.stage': 'Stage',
  'artifact.action.staging': 'Staging…',
  'artifact.action.included': '已纳入',
  'artifact.action.inspect': '检查',
  'artifact.action.reseal': '重新封存',
  'artifact.action.discard': '放弃 staging',
  'artifact.action.discarding': '正在放弃…',
  'artifact.action.confirm': '确认工件',
  'artifact.action.confirming': '正在确认…',
  'artifact.sizeUnknown': '大小未知',
  'artifact.shaUnavailable': 'SHA 不可用',
  'artifact.scanPending': '等待扫描',
  'artifact.preview.empty': '（空文本工件）',
  'artifact.preview.truncated': '…预览已按服务端上限截断',
  'artifact.preview.unavailable': '无法在线预览，请在可信环境检查。',
  'artifact.disposition.label': '处置方式',
  'artifact.disposition.asIs': '按原内容封存',
  'artifact.disposition.replaceText': '用脱敏文本替换',
  'artifact.license.label': '许可证 / 使用权依据 · 必填',
  'artifact.license.placeholder': '团队自有；或明确允许的研究使用范围',
  'artifact.replacement.label': '脱敏替换文本 · 必填',
  'artifact.confirm.redaction': '已检查内容与敏感信息，可以封存。',
  'artifact.confirm.manualRedaction': '已在可信环境完成人工脱敏检查。',
  'artifact.confirm.trainingConsent': '允许进入训练快照（默认关闭）。',
  'artifact.toast.staged': '工件已进入 staging。',
  'artifact.toast.stageFailed': '工件 staging 失败：{error}',
  'artifact.toast.previewFailed': '无法读取工件预览：{error}',
  'artifact.toast.confirmed': '工件已确认；请重新封存 Episode。',
  'artifact.toast.confirmFailed': '工件确认失败：{error}',
  'artifact.toast.discarded': '已放弃 staging。',
  'artifact.toast.discardFailed': '放弃 staging 失败：{error}',
} as const

export type MessageKey = keyof typeof zhCN

const enUS: Record<MessageKey, string> = {
  'brand.name': 'ARGUS',
  'brand.descriptor': 'RESEARCH DATA FLYWHEEL',

  'nav.group.plan': 'PLAN',
  'nav.group.run': 'RUN',
  'nav.group.decide': 'LEARN',
  'nav.group.system': 'SYSTEM',
  'nav.overview': 'Overview',
  'nav.context': 'Team context',
  'nav.ideas': 'Idea radar',
  'nav.campaigns': 'Campaigns',
  'nav.review': 'Review council',
  'nav.dataVault': 'Data vault',
  'nav.approvals': 'Approvals',
  'nav.outcomes': 'Outcomes & rebuttal',
  'nav.connections': 'Argus connections',
  'nav.settings': 'Settings',
  'nav.purpose.overview': 'See venue windows, risks, and the next best action',
  'nav.purpose.context': 'Freeze team resources, constraints, and research goals',
  'nav.purpose.ideas': 'Generate, debate, compare, and label research directions',
  'nav.purpose.campaigns': 'Track Argus execution, versions, evidence, and artifacts',
  'nav.purpose.review': 'Challenge novelty, methods, feasibility, and integrity independently',
  'nav.purpose.dataVault': 'Verify and seal complete Research Episodes and dataset snapshots',
  'nav.purpose.approvals': 'Resolve compute, launch, review, and release gates',
  'nav.purpose.outcomes': 'Capture submissions, reviews, rebuttals, and training feedback',
  'nav.purpose.connections': 'Manage Argus instances and runtime connectivity',
  'nav.purpose.settings': 'Configure resources, notifications, language, and appearance',

  'shell.search': 'Search',
  'shell.searchPlaceholder': 'Search venues, campaigns, ideas, or evidence…',
  'shell.quickGo': 'Quick access',
  'shell.searchResults': 'Search results',
  'shell.liveApi': 'Live API',
  'shell.demoData': 'Demo data',
  'shell.demoTitle': 'Demo data is being shown',
  'shell.demoDetail': 'Venues, resources, campaigns, scores, and versions are UI examples, not live runtime state.',
  'shell.activeCampaigns': 'campaigns active',
  'shell.gpuConfigured': 'GPUs configured',
  'shell.operator': 'Research operator',
  'shell.workspace': 'Argus workspace',
  'shell.notifications': 'Notifications',
  'shell.command': 'Command menu',
  'shell.language': 'Language',
  'shell.theme': 'Theme',
  'shell.dark': 'Dark',
  'shell.light': 'Light',
  'shell.system': 'System',
  'shell.openNavigation': 'Open navigation',
  'shell.openNav': 'Open navigation',
  'shell.closeNavigation': 'Close navigation',
  'shell.primaryNav': 'Primary navigation',
  'shell.expandNav': 'Expand navigation',
  'shell.collapseNav': 'Collapse navigation',
  'shell.skipToContent': 'Skip to main content',

  'spine.plan': 'Plan',
  'spine.context': 'Context',
  'spine.target': 'Venue',
  'spine.ideas': 'Ideas',
  'spine.run': 'Argus',
  'spine.review': 'Review',
  'spine.learn': 'Data return',

  'page.overview.eyebrow': 'Evidence Horizon',
  'page.overview.title': 'Research opportunities and risks',
  'page.overview.description': 'Use venue windows, team conditions, and real evidence to choose the most valuable work now.',
  'page.context.eyebrow': 'Context Studio',
  'page.context.title': 'Freeze real team conditions into research context',
  'page.context.description': 'The same venue produces different directions for different people, data, compute, time, and policies.',
  'page.ideas.eyebrow': 'Idea Radar',
  'page.ideas.title': 'Generate and adversarially test research directions',
  'page.ideas.description': 'Team context, venue evidence, and executability.',
  'page.campaigns.eyebrow': 'Argus Campaigns',
  'page.campaigns.title': 'Run recoverable, approval-gated research campaigns',
  'page.campaigns.description': 'Inspect Argus processes, phases, versions, evidence, resource use, and human gates.',
  'page.review.eyebrow': 'Review Council',
  'page.review.title': 'Independent review and evidence checks',
  'page.review.description': 'Preserve reviewer disagreement without inventing acceptance odds or hiding evidence gaps.',
  'page.approvals.eyebrow': 'Approval Inbox',
  'page.approvals.title': 'Decisions that need a person',
  'page.approvals.description': 'Launches, compute, data, review, and release actions require explicit authorization.',
  'page.outcomes.eyebrow': 'Outcome Memory',
  'page.outcomes.title': 'Keep learning from submission outcomes',
  'page.outcomes.description': 'Capture reviews, scores, rebuttal versions, and human labels as governed data.',
  'page.dataVault.eyebrow': 'Research Data Flywheel',
  'page.dataVault.title': 'Immutable research data vault',
  'page.dataVault.description': 'Package team conditions, ideas, prompts, Argus traces, papers, and real reviews into verifiable Research Episodes.',
  'page.connections.eyebrow': 'Argus Connections',
  'page.connections.title': 'Connect and verify Argus runtimes',
  'page.connections.description': 'Show protocol, capabilities, version, and live health without confusing configuration with connectivity.',
  'page.settings.eyebrow': 'Workspace Settings',
  'page.settings.title': 'Workspace settings',
  'page.settings.description': 'Manage resources, notifications, language, appearance, and local preferences.',

  'context.tab.profiles': 'Team profiles',
  'context.tab.runs': 'Ideation runs',
  'context.tab.bench': 'Flywheel Bench',
  'context.bench.title': 'Flywheel Bench · Human preference data',

  'action.syncSources': 'Sync evidence',
  'action.freshReview': 'Request fresh independent review',
  'action.recordOutcome': 'Record outcome',
  'action.addConnection': 'Add Argus connection',
  'action.saveChanges': 'Save changes',
  'action.reminderRules': 'Reminder rules',
  'action.teamPlan': 'Generate for my team',
  'action.createFromContext': 'Create from team context',

  'ideas.monitor.live': 'Sources connected',
  'ideas.monitor.demo': 'Demo signals',
  'ideas.source.settings': 'Source settings',
  'ideas.source.openreview': 'OpenReview venue ID',
  'ideas.source.github': 'GitHub repository',
  'ideas.list.count': '{count} candidates',
  'ideas.collection.label': 'Idea type',
  'ideas.collection.conditioned': 'Team candidates',
  'ideas.collection.baseline': 'Baselines',
  'ideas.state.refreshed': 'Synced',
  'ideas.state.collision': 'Collision found',
  'ideas.state.conditioned': 'Conditioned',
  'ideas.state.baseline': 'Baseline · non-executable',
  'ideas.state.baselineCollision': 'Baseline · collision',
  'ideas.empty.conditioned': 'No team-conditioned candidates yet',
  'ideas.empty.baseline': 'No baselines',
  'ideas.load.failed': 'Team candidates could not be loaded',
  'ideas.load.partial': '{count} ideation runs failed verification',
  'ideas.score.novelty': 'Novelty',
  'ideas.score.feasibility': 'Feasibility',
  'ideas.inspector.eyebrow': 'Evidence delta',
  'ideas.inspector.delta': 'Difference from prior work',
  'ideas.inspector.sources': 'Recent signals',
  'ideas.inspector.empty': 'No evidence',
  'ideas.inspector.sync': 'Sync evidence',
  'ideas.inspector.resource': 'Resources and risk',
  'ideas.inspector.compute': 'Compute',
  'ideas.inspector.risk': 'Risk',
  'ideas.inspector.teamAdvantage': 'Team-specific advantage',
  'ideas.inspector.collisionTest': 'Novelty collision test',
  'ideas.inspector.openPrompt': 'View Prompt',
  'ideas.inspector.loadingPrompt': 'Loading…',
  'ideas.inspector.liveRequired': 'Connect Argus to view the Prompt',
  'ideas.prompt.unavailable': 'Prompt unavailable',
  'ideas.prompt.readonly': 'Read-only contract',
  'ideas.prompt.title': 'Idea Prompt',
  'ideas.prompt.ready': 'Ready for execution approval',
  'ideas.prompt.notReady': 'Not execution-ready',
  'ideas.prompt.missing': 'Missing',
  'ideas.select.title': 'Select an Idea',
  'ideas.select.detail': 'Inspect its evidence delta, resource fit, and Prompt.',
  'ideas.identity.team': 'Team',
  'ideas.identity.venue': 'Venue',
  'ideas.identity.run': 'RUN',
  'ideas.identity.condition': 'CONDITION SHA',
  'ideas.identity.objective': 'OBJECTIVE SHA',
  'ideas.baseline.generate': 'Generate for this team',
  'ideas.campaign.create': 'Create direction campaign',
  'ideas.campaign.open': 'Open campaign',
  'ideas.campaign.creating': 'Freezing Prompt…',
  'ideas.campaign.none': 'Not created',
  'ideas.campaign.running': 'Running',
  'ideas.campaign.review': 'In review',
  'ideas.campaign.attention': 'Needs attention',
  'ideas.campaign.paused': 'Paused',
  'ideas.campaign.idle': 'Not started',
  'ideas.campaign.ready': 'Ready to start',
  'ideas.campaign.completed': 'Completed',
  'ideas.campaign.unknown': 'Unknown',
  'ideas.campaign.created': 'Campaign {id} created; Prompt {sha} is frozen and Argus has not started.',
  'ideas.campaign.failed': 'Direction campaign could not be created',
  'ideas.campaign.invalidReceipt': 'The server did not return a verifiable idle Campaign receipt; nothing was treated as successful.',
  'view.horizon': 'Horizon',
  'view.calendar': 'Calendar',
  'settings.tab.compute': 'Compute pools',
  'settings.tab.models': 'Models by role',
  'settings.tab.notifications': 'Notifications',
  'settings.tab.releases': 'Release policy',
  'settings.tab.appearance': 'Appearance & language',

  'settings.appearance.title': 'Appearance and language',
  'settings.appearance.description': 'Choose the interface language and theme. Preferences are saved in this browser.',
  'settings.appearance.language': 'Interface language',
  'settings.appearance.theme': 'Interface theme',
  'settings.appearance.themeDark': 'Dark theme',
  'settings.appearance.themeLight': 'Light theme',
  'settings.appearance.themeSystem': 'Use system theme',
  'settings.appearance.currentTheme': 'Current theme',
  'settings.appearance.currentLanguage': 'Current language',

  'common.open': 'Open',
  'common.noResults': 'No matching results',
  'common.create': 'Create',
  'common.save': 'Save',
  'common.cancel': 'Cancel',
  'common.close': 'Close',
  'common.confirm': 'Confirm',
  'common.refresh': 'Refresh',
  'common.retry': 'Retry',
  'common.search': 'Search',
  'common.start': 'Start',
  'common.stop': 'Stop',
  'common.continue': 'Continue',
  'common.approve': 'Approve',
  'common.reject': 'Reject',
  'common.back': 'Back',
  'common.next': 'Next',
  'common.loading': 'Loading…',
  'common.empty': 'Nothing here yet',
  'common.error': 'Something went wrong',

  'artifact.title': 'Argus artifacts',
  'artifact.catalog': 'Available Argus artifacts',
  'artifact.imports': 'Import ledger',
  'artifact.partialUnavailable': 'Some artifact state is unavailable',
  'artifact.resealCount': '{count} confirmed artifacts are not in the current head',
  'artifact.catalogEmpty': 'This Episode has no readable Argus artifacts.',
  'artifact.importsEmpty': 'No artifact imports yet.',
  'artifact.importsUnavailable': 'Import state is unknown; staging is disabled.',
  'artifact.roleLabel': 'artifact role',
  'artifact.roleChoose': 'Choose role',
  'artifact.role.conditionSnapshot': 'Condition snapshot',
  'artifact.role.promptContract': 'Prompt contract',
  'artifact.role.trajectory': 'Run trajectory',
  'artifact.role.experimentSpec': 'Experiment specification',
  'artifact.role.experimentResult': 'Experiment result',
  'artifact.role.paper': 'Paper',
  'artifact.role.outcome': 'Research outcome',
  'artifact.role.reviewCertificate': 'Review certificate',
  'artifact.role.integrityReport': 'Integrity report',
  'artifact.role.reproducibilityManifest': 'Reproducibility manifest',
  'artifact.state.staging': 'Staging',
  'artifact.state.discarded': 'Discarded',
  'artifact.state.sealed': 'Sealed',
  'artifact.state.confirmedUnsealed': 'Confirmed · unsealed',
  'artifact.action.stage': 'Stage',
  'artifact.action.staging': 'Staging…',
  'artifact.action.included': 'Included',
  'artifact.action.inspect': 'Inspect',
  'artifact.action.reseal': 'Reseal Episode',
  'artifact.action.discard': 'Discard staging',
  'artifact.action.discarding': 'Discarding…',
  'artifact.action.confirm': 'Confirm artifact',
  'artifact.action.confirming': 'Confirming…',
  'artifact.sizeUnknown': 'Size unknown',
  'artifact.shaUnavailable': 'SHA unavailable',
  'artifact.scanPending': 'Scan pending',
  'artifact.preview.empty': '(Empty text artifact)',
  'artifact.preview.truncated': '…Preview truncated at the server limit',
  'artifact.preview.unavailable': 'No inline preview. Inspect it in a trusted environment.',
  'artifact.disposition.label': 'Disposition',
  'artifact.disposition.asIs': 'Seal original content',
  'artifact.disposition.replaceText': 'Replace with redacted text',
  'artifact.license.label': 'License / rights basis · required',
  'artifact.license.placeholder': 'Team-owned; or the explicitly permitted research-use scope',
  'artifact.replacement.label': 'Redacted replacement text · required',
  'artifact.confirm.redaction': 'Content and sensitive information checked; ready to seal.',
  'artifact.confirm.manualRedaction': 'Manual redaction review completed in a trusted environment.',
  'artifact.confirm.trainingConsent': 'Allow in training snapshots (off by default).',
  'artifact.toast.staged': 'Artifact staged.',
  'artifact.toast.stageFailed': 'Artifact staging failed: {error}',
  'artifact.toast.previewFailed': 'Artifact preview could not be loaded: {error}',
  'artifact.toast.confirmed': 'Artifact confirmed; reseal the Episode.',
  'artifact.toast.confirmFailed': 'Artifact confirmation failed: {error}',
  'artifact.toast.discarded': 'Staging discarded.',
  'artifact.toast.discardFailed': 'Discarding staging failed: {error}',
}

const dictionaries: Record<Locale, Readonly<Record<MessageKey, string>>> = {
  'zh-CN': zhCN,
  'en-US': enUS,
}

type TranslationParameters = Record<string, string | number>

export interface PreferencesContextValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  theme: ThemePreference
  setTheme: (theme: ThemePreference) => void
  resolvedTheme: ResolvedTheme
}

export interface I18nValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: string, parameters?: TranslationParameters) => string
}

const PreferencesContext = createContext<PreferencesContextValue | null>(null)
const I18nContext = createContext<I18nValue | null>(null)

const SYSTEM_THEME_QUERY = '(prefers-color-scheme: dark)'

function isLocale(value: unknown): value is Locale {
  return value === 'zh-CN' || value === 'en-US'
}

function isThemePreference(value: unknown): value is ThemePreference {
  return value === 'dark' || value === 'light' || value === 'system'
}

function readStorage(key: string): string | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // The in-memory preference still works when storage is unavailable.
  }
}

function readInitialPreferences(): { locale: Locale; theme: ThemePreference } {
  const storedLocale = readStorage(PREFERENCES_STORAGE_KEYS.locale) ?? readStorage(LEGACY_PREFERENCE_KEYS.locale)
  const storedTheme = readStorage(PREFERENCES_STORAGE_KEYS.theme) ?? readStorage(LEGACY_PREFERENCE_KEYS.theme)
  return {
    locale: isLocale(storedLocale) ? storedLocale : 'zh-CN',
    theme: isThemePreference(storedTheme) ? storedTheme : 'dark',
  }
}

function readSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark'
  return window.matchMedia(SYSTEM_THEME_QUERY).matches ? 'dark' : 'light'
}

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState(readInitialPreferences)
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(readSystemTheme)
  const resolvedTheme: ResolvedTheme = preferences.theme === 'system' ? systemTheme : preferences.theme

  const setLocale = useCallback((locale: Locale) => {
    setPreferences((current) => current.locale === locale ? current : { ...current, locale })
  }, [])

  const setTheme = useCallback((theme: ThemePreference) => {
    setPreferences((current) => current.theme === theme ? current : { ...current, theme })
  }, [])

  useLayoutEffect(() => {
    document.documentElement.lang = preferences.locale
    document.documentElement.dataset.theme = resolvedTheme
    document.documentElement.style.colorScheme = resolvedTheme
  }, [preferences.locale, resolvedTheme])

  useEffect(() => {
    writeStorage(PREFERENCES_STORAGE_KEYS.locale, preferences.locale)
  }, [preferences.locale])

  useEffect(() => {
    writeStorage(PREFERENCES_STORAGE_KEYS.theme, preferences.theme)
  }, [preferences.theme])

  useEffect(() => {
    if (preferences.theme !== 'system' || typeof window.matchMedia !== 'function') return undefined
    const media = window.matchMedia(SYSTEM_THEME_QUERY)
    const update = () => setSystemTheme(media.matches ? 'dark' : 'light')
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [preferences.theme])

  useEffect(() => {
    const syncAcrossTabs = (event: StorageEvent) => {
      const nextValue = event.newValue
      if (event.key === PREFERENCES_STORAGE_KEYS.locale && isLocale(nextValue)) {
        setPreferences((current) => current.locale === nextValue ? current : { ...current, locale: nextValue })
      }
      if (event.key === PREFERENCES_STORAGE_KEYS.theme && isThemePreference(nextValue)) {
        setPreferences((current) => current.theme === nextValue ? current : { ...current, theme: nextValue })
      }
    }
    window.addEventListener('storage', syncAcrossTabs)
    return () => window.removeEventListener('storage', syncAcrossTabs)
  }, [])

  const t = useCallback((key: string, parameters?: TranslationParameters): string => {
    const dictionary = dictionaries[preferences.locale] as Readonly<Record<string, string>>
    const template = dictionary[key] ?? key
    if (!parameters) return template
    return template.replace(/\{(\w+)\}/g, (match, name: string) =>
      parameters[name] === undefined ? match : String(parameters[name]),
    )
  }, [preferences.locale])

  const preferencesValue = useMemo<PreferencesContextValue>(() => ({
    locale: preferences.locale,
    setLocale,
    theme: preferences.theme,
    setTheme,
    resolvedTheme,
  }), [preferences.locale, preferences.theme, resolvedTheme, setLocale, setTheme])

  const i18nValue = useMemo<I18nValue>(() => ({
    locale: preferences.locale,
    setLocale,
    t,
  }), [preferences.locale, setLocale, t])

  return (
    <PreferencesContext.Provider value={preferencesValue}>
      <I18nContext.Provider value={i18nValue}>{children}</I18nContext.Provider>
    </PreferencesContext.Provider>
  )
}

export function usePreferences(): PreferencesContextValue {
  const context = useContext(PreferencesContext)
  if (!context) throw new Error('usePreferences must be used within PreferencesProvider')
  return context
}

export function useI18n(): I18nValue {
  const context = useContext(I18nContext)
  if (!context) throw new Error('useI18n must be used within PreferencesProvider')
  return context
}
