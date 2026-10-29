import type { DeliveryReceipt, DeliveryTarget, EventMsg } from '../../../core/src/types';

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

/** The most recent earlier delivered version of ``path`` whose content differs, if both are stored. */
export function previousDeliveryVersion(receipt: DeliveryReceipt, receipts: DeliveryReceipt[], path: string | null): { path: string; before: string; after: string } | null {
  if (!path) return null;
  const current = receipt.snapshots?.find((snap) => snap.path === path && snap.stored);
  if (!current) return null;
  const earlier = receipts.filter((other) => other.delivery_id !== receipt.delivery_id && other.delivered_at < receipt.delivered_at)
    .sort((a, b) => b.delivered_at - a.delivered_at);
  for (const other of earlier) {
    const snap = other.snapshots?.find((item) => item.path === path);
    if (!snap || snap.version === current.version) continue;
    return snap.stored ? { path, before: snap.version, after: current.version } : null;
  }
  return null;
}

/** Each path has its own occurrence count, supplied by the server's record fold. */
export const deliveryNumberLabel = (file: DeliveryTarget, zh: boolean): string =>
  file.delivery_number ? (zh ? `第 ${file.delivery_number} 次交付` : `Delivery ${file.delivery_number}`) : '';

export function deliveryOptionLabel(receipt: DeliveryReceipt, zh: boolean): string {
  const files = deliveryFiles(receipt);
  return [receipt.title, ...files.map(file => [files.length > 1 ? file.label || file.path : '', deliveryNumberLabel(file, zh)].filter(Boolean).join(' '))].filter(Boolean).join(' · ');
}

/** No copy was kept of this file, and a later delivery names a different version of it. */
export function deliveryFileUpdated(receipt: DeliveryReceipt, receipts: DeliveryReceipt[], path: string): boolean {
  const snapshot = receipt.snapshots?.find(file => file.path === path);
  if (snapshot?.stored) return false;
  const file = deliveryFiles(receipt).find(file => file.path === path);
  return receipts.some(other => {
    if (other.delivery_id === receipt.delivery_id) return false;
    const next = deliveryFiles(other).find(file => file.path === path);
    if (!next || !(file?.delivery_number && next.delivery_number
      ? next.delivery_number > file.delivery_number : other.delivered_at > receipt.delivered_at)) return false;
    return !snapshot || other.snapshots?.find(file => file.path === path)?.version !== snapshot.version;
  });
}
