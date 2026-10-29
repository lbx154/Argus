import type { DeliveryReceipt, MissionView, Snapshot } from '../../../core/src/types';
import { operatorDecisionCards } from '../../../core/src/decisions';
import type { ProgressHealth } from './progressHealth';

export type AttentionKind = 'reply' | 'done' | 'stage' | 'error';

export interface Attention {
  kind: AttentionKind;
  /** Stable for one occurrence, so a notification fires once per new event. */
  key: string;
  title: string;
  body: string;
}

/** The one thing that needs the operator in this project, if any. Replies come first. */
export function projectAttention({
  snapshot, view, complete, delivery, health, zh,
}: {
  snapshot: Snapshot | undefined;
  view?: MissionView | null;
  /** The operator's whole task finished (no queued work, no standing campaign). */
  complete: boolean;
  delivery?: DeliveryReceipt | null;
  health?: ProgressHealth | null;
  zh: boolean;
}): Attention | null {
  if (!snapshot) return null;
  const hold = snapshot.launch_hold;
  if (hold) {
    return { kind: 'reply', key: `launch:${hold.created_at}`, title: zh ? '需要你确认开始' : 'Waiting for your go-ahead', body: hold.title };
  }
  const cards = operatorDecisionCards(
    snapshot.pending_questions ?? [],
    snapshot.backlog as unknown as Array<Record<string, unknown>>,
    view?.mission.id,
  );
  if (cards.length) {
    const card = cards[0];
    const question = String(card.question || card.reason || card.title || '');
    return { kind: 'reply', key: `reply:${card.item_id}:${question.slice(0, 80)}`, title: zh ? '需要你回复' : 'Your reply is needed', body: question };
  }
  if (health) {
    return {
      kind: 'error', key: `health:${health.key}`,
      title: health.problem === 'repeated_failures' ? (zh ? '连续几个任务没有完成' : 'Several tasks in a row did not finish') : (zh ? '很久没有新的成果' : 'No new result for a long time'),
      body: health.latestFailure?.title || '',
    };
  }
  if (complete && view?.mission.id) {
    return { kind: 'done', key: `done:${view.mission.id}`, title: zh ? '任务已完成' : 'Task finished', body: delivery?.title || view.mission.title };
  }
  if (delivery?.kind === 'stage_package') {
    return { kind: 'stage', key: `stage:${delivery.delivery_id}`, title: zh ? '有新的阶段成果' : 'New stage result', body: delivery.title };
  }
  return null;
}

export function attentionBadge(kind: AttentionKind, zh: boolean): string {
  if (kind === 'reply') return zh ? '需回复' : 'Reply needed';
  if (kind === 'error') return zh ? '出错' : 'Error';
  if (kind === 'stage') return zh ? '新成果' : 'New result';
  return zh ? '已完成' : 'Done';
}
