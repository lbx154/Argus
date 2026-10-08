import type { DeliveryReceipt, DeliveryTarget, EventMsg, BacklogItem } from '../../../core/src/types';

export function deliveryFiles(receipt: DeliveryReceipt): DeliveryTarget[] {
  const seen = new Set<string>();
  return [receipt.primary_target, ...receipt.targets].filter((file): file is DeliveryTarget => {
    if (!file?.path || seen.has(file.path)) return false;
    seen.add(file.path); return true;
  });
}

/** The project-level research shortcut opens the paper, even after a review-only task. */
export function defaultDeliverySelection(receipts: DeliveryReceipt[], vertical: string) {
  if (vertical === 'research') {
    for (const receipt of receipts) {
      const paper = deliveryFiles(receipt).find((file) => /(?:^|\/)paper\/main\.pdf$/i.test(file.path.replace(/\\/g, '/')));
      if (paper) return { receipt, path: paper.path };
    }
  }
  const receipt = receipts[0];
  return receipt ? { receipt, path: deliveryFiles(receipt)[0]?.path || null } : null;
}

export function cleanDeliverySummary(summary: string): string {
  return summary.replace(/\s*\bRESULT\s*=\s*/g, '\n\n').replace(/\s*\b(?:STATUS|REVIEW_STATUS)\s*=\s*\S+/g, '').trim();
}

/** A task can finish after its Manager reply; an earlier operator turn must not hide it. */
export function selectActiveDelivery(conversation: DeliveryReceipt | null | undefined, mission: DeliveryReceipt | null, events: EventMsg[]): DeliveryReceipt | null {
  const latestOperator = events.reduce((latest, event) => event.type === 'ui.operator' ? Math.max(latest, Number(event.ts) || 0) : latest, 0);
  const currentMission = mission && (conversation === undefined || mission.delivered_at > latestOperator) ? mission : null;
  if (!conversation) return currentMission;
  return currentMission && currentMission.delivered_at > conversation.delivered_at ? currentMission : conversation;
}

/** Intermediate files remain available while their downstream work is still running. */
export function hasPendingDeliveryDependents(items: BacklogItem[], itemId?: string): boolean {
  if (!itemId) return false;
  const downstream = new Set([itemId]);
  const queue = [itemId];
  for (let index = 0; index < queue.length; index++) {
    for (const item of items) {
      if (!downstream.has(item.id) && item.deps?.includes(queue[index])) { downstream.add(item.id); queue.push(item.id); }
    }
  }
  return items.some((item) => item.id !== itemId && downstream.has(item.id) && ['pending', 'running', 'in_progress', 'claimed'].includes(item.status));
}

const INDEPENDENTLY_REVIEWED = new Set(['done', 'passed', 'approved', 'accepted']);

/** Who vouched for this delivery: an independent reviewer, or only the worker that made it. */
export function deliveryReviewLabel(receipt: DeliveryReceipt, zh: boolean): { independent: boolean; text: string; title: string } {
  const status = String(receipt.review_status || '').toLowerCase();
  if (INDEPENDENTLY_REVIEWED.has(status)) return {
    independent: true,
    text: zh ? '独立复核通过' : 'Independently reviewed',
    title: zh ? '另一个审查角色检查过这份成果' : 'A separate reviewer checked this result',
  };
  if (!status || status === 'not_assessed') return {
    independent: false,
    text: zh ? '实现者自检 · 未独立复核' : 'Self-checked · not independently reviewed',
    title: zh ? '只有完成它的人自己检查过，没有另外的审查' : 'Only the worker that produced it checked it; no separate review',
  };
  return {
    independent: false,
    text: zh ? `复核状态：${status}` : `Review: ${status}`,
    title: zh ? '独立复核没有通过或已过期' : 'Independent review did not pass or is out of date',
  };
}

/** The most recent earlier delivered version of ``path`` whose content differs, if both are stored. */
export function previousDeliveryVersion(receipt: DeliveryReceipt, receipts: DeliveryReceipt[], path: string | null): { path: string; before: string; after: string } | null {
  if (!path) return null;
  const current = receipt.snapshots?.find((snap) => snap.path === path && snap.stored);
  if (!current) return null;
  const earlier = receipts.filter((other) => other.delivery_id !== receipt.delivery_id && other.delivered_at < receipt.delivered_at)
    .sort((a, b) => b.delivered_at - a.delivered_at);
  for (const other of earlier) {
    const snap = other.snapshots?.find((item) => item.path === path);
    if (!snap || snap.sha256 === current.sha256) continue;
    return snap.stored ? { path, before: snap.sha256, after: current.sha256 } : null;
  }
  return null;
}
