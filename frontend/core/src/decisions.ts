export type { DecisionEvidence, DecisionOption, OperatorDecisionCard } from '../../../packages/contracts/src/decisions.js';
import type { DecisionEvidence, DecisionOption, OperatorDecisionCard } from '../../../packages/contracts/src/decisions.js';

const text = (value: unknown): string => String(value ?? '').trim();
const genericOperatorDecisionReason = /^[a-z][a-z -]+ requires an operator-owned decision before continuing\.?$/i;

const decisionReason = (value: unknown): string => {
  const reason = text(value);
  return genericOperatorDecisionReason.test(reason) ? '' : reason;
};

const customDecisionOption = (title: string, question: string): DecisionOption => {
  const chinese = /[\u3400-\u9fff]/.test(`${title}\n${question}`);
  return {
    id: 'custom',
    label: chinese ? '自己输入' : 'Write my own answer',
    description: chinese ? '直接告诉 Argus 你的决定。' : 'Tell Argus your decision directly.',
    requires_note: true,
  };
};

export function operatorDecisionCards(
  pending: Array<Record<string, unknown>>,
  backlog: Array<Record<string, unknown>>,
  currentTaskId?: string | null,
): OperatorDecisionCard[] {
  const rows = [...pending, ...backlog];
  const backlogById = new Map(backlog.map(row => [text(row.id), row]));
  const taskContext = (itemId: string, row: Record<string, unknown>) => {
    const owner = backlogById.get(itemId) ?? (text(row.id) === itemId ? row : undefined);
    return { task_title: text(owner?.title) || undefined, task_status: text(owner?.status) || undefined };
  };
  const cards: OperatorDecisionCard[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const itemId = text(row.id);
    const raw = row.operator_decision;
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      const card = raw as Record<string, unknown>;
      const id = text(card.id);
      if (!id || seen.has(id) || text(card.status) !== 'pending') continue;
      seen.add(id);
      const optionsSource = text(card.options_source);
      const intake = card.kind === 'domain_intake';
      const options = (optionsSource === 'agent' || (intake && optionsSource === 'workflow')) && Array.isArray(card.options)
        ? (card.options as DecisionOption[])
            .filter((option) => (
              Boolean(text(option?.id)) && Boolean(text(option?.label))
            ))
            .map((option) => ({ ...option, requires_note: option.requires_note === true }))
        : [];
      if (!options.some(option => option.id === 'custom')) {
        options.push(customDecisionOption(text(card.title), text(card.question)));
      }
      const ownerId = text(card.item_id) || itemId;
      cards.push({
        id,
        ...(intake ? { kind: 'domain_intake' as const } : {}),
        item_id: ownerId,
        revision: Number(card.revision ?? 1),
        status: 'pending',
        title: text(card.title) || text(row.title) || 'Decision required',
        reason: decisionReason(card.reason),
        question: text(card.question) || text(row.pending_question),
        evidence: Array.isArray(card.evidence)
          ? (card.evidence as DecisionEvidence[]).filter(
              (row) => text(row?.label) !== 'Acceptance check',
            )
          : [],
        options,
        options_source: intake ? 'workflow' : options.length ? 'agent' : 'none',
        selected_option: '',
        note: '',
        ...taskContext(ownerId, row),
        ...(intake ? { task_title: text(card.task_title), task_status: undefined } : {}),
        ...(typeof card.asked_at === 'number' && Number.isFinite(card.asked_at) && card.asked_at > 0
          ? { asked_at: card.asked_at } : {}),
      });
      continue;
    }
    const question = text(row.pending_question ?? row.question ?? row.text);
    if (!itemId || !question) continue;
    const id = `legacy-${itemId}`;
    if (seen.has(id)) continue;
    seen.add(id);
    cards.push({
      id,
      item_id: itemId,
      revision: 1,
      status: 'pending',
      title: text(row.title ?? row.objective) || 'Blocked task',
      reason: '',
      question,
      evidence: [],
      options: [customDecisionOption(text(row.title ?? row.objective), question)],
      options_source: 'none',
      selected_option: '',
      note: '',
      legacy: true,
      ...taskContext(itemId, row),
    });
  }
  if (!currentTaskId) return cards;
  const scoped = cards.map(card => ({ ...card, is_current_task: card.kind === 'domain_intake' ? undefined : card.item_id === currentTaskId }));
  return [...scoped.filter(card => card.kind === 'domain_intake'), ...scoped.filter(card => card.is_current_task), ...scoped.filter(card => card.kind !== 'domain_intake' && !card.is_current_task)];
}
