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
