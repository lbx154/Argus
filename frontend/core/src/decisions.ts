export interface DecisionEvidence {
  label: string;
  path: string;
  summary: string;
}

export interface DecisionOption {
  id: string;
  label: string;
  description: string;
  requires_note: boolean;
}

export interface OperatorDecisionCard {
  id: string;
  item_id: string;
  revision: number;
  status: 'pending' | 'resolved';
  title: string;
  reason: string;
  question: string;
  evidence: DecisionEvidence[];
  options: DecisionOption[];
  selected_option: string;
  note: string;
  legacy?: boolean;
}

const text = (value: unknown): string => String(value ?? '').trim();

export function operatorDecisionCards(
  pending: Array<Record<string, unknown>>,
  backlog: Array<Record<string, unknown>>,
): OperatorDecisionCard[] {
  const rows = [...pending, ...backlog];
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
      cards.push({
        id,
        item_id: text(card.item_id) || itemId,
        revision: Number(card.revision ?? 1),
        status: 'pending',
        title: text(card.title) || text(row.title) || 'Decision required',
        reason: text(card.reason),
        question: text(card.question) || text(row.pending_question),
        evidence: Array.isArray(card.evidence) ? card.evidence as DecisionEvidence[] : [],
        options: Array.isArray(card.options) ? card.options as DecisionOption[] : [],
        selected_option: '',
        note: '',
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
      options: [{
        id: 'custom',
        label: 'Give guidance',
        description: question,
        requires_note: true,
      }],
      selected_option: '',
      note: '',
      legacy: true,
    });
  }
  return cards;
}
