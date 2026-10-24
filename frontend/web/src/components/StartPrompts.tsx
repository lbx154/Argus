import { Search, Hammer, ScanSearch, Milestone } from 'lucide-react';
import { useI18n } from '../i18n';

const starters = [
  { icon: Search, zh: ['调研清楚，帮我做决策', '比较资料与方案，给出有依据的建议', '我想把一个问题调研清楚，再做决策。请先确认我的问题、比较标准和可用资料，给出有来源的建议、反例和不确定性。只补问必要的信息，不编造依据，也不替我做不可逆决定。'], en: ['Research a decision', 'Compare evidence and options; make a grounded recommendation', 'Help me research a decision. Confirm the question, comparison criteria and available sources, then recommend options with evidence, counterarguments and uncertainties. Ask only what is missing; do not invent evidence or make irreversible decisions for me.'] },
  { icon: Hammer, zh: ['把想法做成可用成果', '从目标出发，规划、执行并交付', '我想把一个想法做成可用成果。请先确认目标、交付物和验收条件，给出简短计划，再在我授权的范围内执行和检查。交付可以直接查看、使用或下载的成果，不只给操作建议。'], en: ['Turn an idea into a result', 'Plan, execute and deliver something usable', 'Help me turn an idea into a usable result. Confirm the goal, deliverable and acceptance criteria, propose a short plan, then execute and check within my authorization. Deliver something I can view, use or download, not just instructions.'] },
  { icon: ScanSearch, zh: ['找出问题，验证解决办法', '定位原因、验证判断、解决卡点', '我遇到了一个问题，希望找到原因并验证解决办法。请先确认现象、预期和已有证据，区分已证实的原因与假设，再验证可行的修复。涉及修改数据、代码或外部操作时先遵守我的授权。'], en: ['Diagnose and verify a fix', 'Find the cause, test the explanation and remove the blocker', 'Help me diagnose a problem and verify a fix. Confirm the symptoms, expected behavior and available evidence. Separate confirmed causes from hypotheses, then test the remedy. Respect my authorization before changing data, code or external systems.'] },
  { icon: Milestone, zh: ['持续推进一个目标', '分阶段推进，在关键节点与你确认', '我希望分阶段推进一个目标。请先确认阶段成果、时间与资源边界，以及何时向我汇报或暂停。在关键节点与我确认；持续后台执行、外部账户操作或定时通知，需要我明确授权且当前能力支持后才能启用。'], en: ['Advance a longer-term goal', 'Work in stages and check in at key decisions', 'Help me advance a goal in stages. Confirm milestones, time and resource limits, and when to report or pause. Check in at key decisions. Background execution, external account actions and scheduled notifications require my explicit authorization and supported capabilities.'] },
];

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
