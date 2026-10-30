import type { DeliveryReceipt, Snapshot } from '../../../core/src/types';
import { operatorDecisionCards } from '../../../core/src/decisions';

export type AttentionKind = 'reply' | 'done' | 'stage';

export interface Attention {
  kind: AttentionKind;
  /** Stable for one occurrence, so a notification fires once per new event. */
  key: string;
  title: string;
  body: string;
}

/** The one thing that needs the operator in this project, if any. Replies come first. */
export function projectAttention({
  snapshot, delivery, zh,
}: {
  snapshot: Snapshot | undefined;
  delivery?: DeliveryReceipt | null;
  zh: boolean;
}): Attention | null {
  if (!snapshot) return null;
  const cards = operatorDecisionCards(
    snapshot.pending_questions ?? [],
    snapshot.backlog as unknown as Array<Record<string, unknown>>,
  );
  if (cards.length) {
    const card = cards[0];
    const question = String(card.question || card.reason || card.title || '');
    return { kind: 'reply', key: `reply:${card.item_id}:${question.slice(0, 80)}`, title: zh ? '需要你回复' : 'Your reply is needed', body: question };
  }
  if (delivery?.kind === 'stage_package') {
    return { kind: 'stage', key: `stage:${delivery.delivery_id}`, title: zh ? '有新的阶段成果' : 'New stage result', body: delivery.title };
  }
  return null;
}

export function attentionBadge(kind: AttentionKind, zh: boolean): string {
  if (kind === 'reply') return zh ? '需回复' : 'Reply needed';
  if (kind === 'stage') return zh ? '新成果' : 'New result';
  return zh ? '已完成' : 'Done';
}
