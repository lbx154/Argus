import { Search, Hammer, ScanSearch, Milestone, Bot, Bug, ChartScatter } from 'lucide-react';
import { useI18n } from '../i18n';

const starters = [
  { icon: Search, zh: ['调研清楚，帮我做决策', '比较资料与方案，给出有依据的建议', '我想把一个问题调研清楚，再做决策。请先确认我的问题、比较标准和可用资料，给出有来源的建议、反例和不确定性。只补问必要的信息，不编造依据，也不替我做不可逆决定。'], en: ['Research a decision', 'Compare evidence and options; make a grounded recommendation', 'Help me research a decision. Confirm the question, comparison criteria and available sources, then recommend options with evidence, counterarguments and uncertainties. Ask only what is missing; do not invent evidence or make irreversible decisions for me.'] },
  { icon: Hammer, zh: ['把想法做成可用成果', '从目标出发，规划、执行并交付', '我想把一个想法做成可用成果。请先确认目标、交付物和验收条件，给出简短计划，再在我授权的范围内执行和检查。交付可以直接查看、使用或下载的成果，不只给操作建议。'], en: ['Turn an idea into a result', 'Plan, execute and deliver something usable', 'Help me turn an idea into a usable result. Confirm the goal, deliverable and acceptance criteria, propose a short plan, then execute and check within my authorization. Deliver something I can view, use or download, not just instructions.'] },
  { icon: ScanSearch, zh: ['找出问题，验证解决办法', '定位原因、验证判断、解决卡点', '我遇到了一个问题，希望找到原因并验证解决办法。请先确认现象、预期和已有证据，区分已证实的原因与假设，再验证可行的修复。涉及修改数据、代码或外部操作时先遵守我的授权。'], en: ['Diagnose and verify a fix', 'Find the cause, test the explanation and remove the blocker', 'Help me diagnose a problem and verify a fix. Confirm the symptoms, expected behavior and available evidence. Separate confirmed causes from hypotheses, then test the remedy. Respect my authorization before changing data, code or external systems.'] },
  { icon: Milestone, zh: ['持续推进一个目标', '分阶段推进，在关键节点与你确认', '我希望分阶段推进一个目标。请先确认阶段成果、时间与资源边界，以及何时向我汇报或暂停。在关键节点与我确认；持续后台执行、外部账户操作或定时通知，需要我明确授权且当前能力支持后才能启用。'], en: ['Advance a longer-term goal', 'Work in stages and check in at key decisions', 'Help me advance a goal in stages. Confirm milestones, time and resource limits, and when to report or pause. Check in at key decisions. Background execution, external account actions and scheduled notifications require my explicit authorization and supported capabilities.'] },
];

// Worked examples: each was run end to end on gpt-6.1-sol on 2026-10-28;
// the duration and cost shown are what that run took, not a promise.
const showcases = [
  { icon: Bot, zh: ['设计一条四足机器人腿', '工程计算、URDF 模型、工作空间图，模型可直接 3D 查看', '约 25 分钟 · 约 $4',
      '设计一条四足机器人的单腿（髋外展、髋屈伸、膝三个关节），目标是一台总重 12 kg 的小型四足机器人，能以 1 m/s 小跑。要求：1) 根据负载和步态估算各关节需要的峰值力矩和转速，给出电机选型建议；2) 构建这条腿的 URDF（连杆、关节限位、质量惯量按几何计算），并实际加载验证；3) 计算并画出足端工作空间，检查能否覆盖小跑所需的步长和抬腿高度；4) 渲染几个典型姿态的预览图。最后写成中文设计报告，图和模型放在最前面。做完就停。'],
    en: ['Design a quadruped leg', 'Engineering estimates, a URDF and workspace plots; open the model in 3D', 'about 25 min · about $4',
      'Design one leg (hip abduction, hip flexion, knee) for a 12 kg quadruped that trots at 1 m/s. 1) Estimate peak joint torques and speeds from load and gait and suggest motors; 2) build the leg as a URDF (joint limits; mass and inertia from geometry) and load it to check; 3) compute and plot the foot workspace and check it covers the trot stride and swing height; 4) render previews of typical poses. Write a report with the figures and model first. Stop when done.'] },
  { icon: Bug, zh: ['修复一个开源库的真实 bug', '找到仍未修复的问题，复现、修复、跑通全部测试，交付 patch', '约 12 分钟 · 约 $2',
      '在开源库 python-dateutil 里找一个至今仍未修复、能稳定复现的真实 bug（可以从它的 GitHub issues 里挑），写出最小复现脚本证明 bug 存在，修复它，补上回归测试，跑通该库原有的全部测试，最后给出可直接提交的 patch 和中文说明（问题、根因、修法、测试结果）。不要挑文档类问题。做完就停。'],
    en: ['Fix a real bug in an open-source library', 'Find an unfixed issue, reproduce, fix, run the whole test suite, deliver a patch', 'about 12 min · about $2',
      'Find a real, still unfixed, reliably reproducible bug in python-dateutil (its GitHub issues are a good source), write a minimal script that shows it, fix it, add regression tests, run the full existing test suite, and deliver a ready-to-apply patch with an explanation (problem, root cause, fix, test results). No documentation issues. Stop when done.'] },
  { icon: ChartScatter, zh: ['用真实数据做一个交互看板', '下载世界银行 30 年数据，做统计检验，交付可拖动年份的看板', '约 30 分钟 · 约 $3',
      '从公开数据源下载近 30 年全球各国的人均 GDP 和预期寿命数据（例如世界银行开放数据），清洗后做分析：两者的关系如何随时间变化、哪些国家是显著离群点、2008 和 2020 年冲击前后有什么变化。要求统计结论有检验支撑，并做一个可交互的 HTML 看板（可拖动年份、悬停看国家），再写一页中文结论，看板放在最前面。所有数字来自真实数据。做完就停。'],
    en: ['Build an interactive dashboard from real data', 'Download 30 years of World Bank data, test the claims, deliver a dashboard with a year slider', 'about 30 min · about $3',
      'Download 30 years of GDP per capita and life expectancy for all countries from a public source such as World Bank Open Data, clean it and analyse how the relationship changes over time, which countries are significant outliers, and what changed around the 2008 and 2020 shocks. Back every statistical claim with a test, build an interactive HTML dashboard (year slider, hover for a country), and write a one-page conclusion with the dashboard first. All numbers from the real data. Stop when done.'] },
];

export function ShowcasePrompts({ onPick, disabled = false }: { onPick: (text: string) => void; disabled?: boolean }) {
  const { locale } = useI18n();
  return <div className="starter-grid showcase-grid">
    {showcases.map(({ icon: Icon, zh, en }) => {
      const [title, hint, measured, prompt] = locale === 'zh-CN' ? zh : en;
      return <button type="button" key={title} className="starter-card showcase-card" onClick={() => onPick(prompt)} disabled={disabled}>
        <Icon size={18} strokeWidth={1.6} aria-hidden="true" /><span><strong>{title}</strong><small>{hint}</small><em>{locale === 'zh-CN' ? '历史案例 · ' : 'Historical example · '}{measured}{locale === 'zh-CN' ? '（非当前配置报价）' : ' (not a quote for this configuration)'}</em></span>
      </button>;
    })}
  </div>;
}

export function StartPrompts({ onPick, disabled = false }: { onPick: (text: string) => void; disabled?: boolean }) {
  const { locale } = useI18n();
  return <div className="starter-grid">
    {starters.map(({ icon: Icon, zh, en }) => {
      const [title, hint, prompt] = locale === 'zh-CN' ? zh : en;
      return <button type="button" key={title} className="starter-card" onClick={() => onPick(prompt)} disabled={disabled}>
        <Icon size={18} strokeWidth={1.6} aria-hidden="true" /><span><strong>{title}</strong><small>{hint}</small></span>
      </button>;
    })}
  </div>;
}
