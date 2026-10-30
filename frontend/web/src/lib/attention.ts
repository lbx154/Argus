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
    return { kind: 'reply', key: `reply:${card.id}:${card.revision}:${card.asked_at ?? ''}:${question}`, title: zh ? '等你回答' : 'Waiting for your answer', body: question };
  }
  if (delivery?.kind === 'stage_package') {
    return { kind: 'stage', key: `stage:${delivery.delivery_id}`, title: zh ? '有新的阶段成果' : 'New stage result', body: delivery.title };
  }
  if (delivery) {
    return { kind: 'done', key: `done:${delivery.delivery_id}`, title: zh ? '交付了' : 'Delivered', body: delivery.title };
  }
  return null;
}

export function attentionBadge(kind: AttentionKind, zh: boolean): string {
  if (kind === 'reply') return zh ? '需回复' : 'Reply needed';
  if (kind === 'stage') return zh ? '新成果' : 'New result';
  return zh ? '已交付' : 'Delivered';
}
