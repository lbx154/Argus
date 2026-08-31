import type { DeliveryReceipt } from '../../../core/src/types';

/**
 * The cockpit is served by the existing loopback WebAPI and is embedded only
 * inside the local Tauri shell. It never receives Tauri's privileged IPC;
 * these bounded messages are validated by the parent shell for both source and
 * loopback origin before any native action is taken.
 */
export interface DesktopDeliveryNotification {
  deliveryId: string;
  title: string;
  summary: string;
  path?: string;
}

export interface CompletionNotificationInput {
  completionId: string;
  title: string;
  summary?: string;
  path?: string;
}

type EmbeddedDesktopMessage = {
  type?: unknown;
  payload?: unknown;
};

function nonEmptyString(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

function notificationPlainText(value: unknown, limit: number): string {
  return nonEmptyString(value, limit * 2)
    .replace(/!?(?:\[([^\]]+)\])\([^)]+\)/g, '$1')
    .replace(/[*_`#]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, limit);
}

export function completionNotificationPayload(
  input: CompletionNotificationInput,
): DesktopDeliveryNotification | null {
  const deliveryId = nonEmptyString(input.completionId, 300);
  if (!deliveryId) return null;
  const path = nonEmptyString(input.path, 1_000);
  return {
    deliveryId,
    title: notificationPlainText(input.title, 240) || '已完成的任务',
    summary: notificationPlainText(input.summary, 500),
    ...(path ? { path } : {}),
  };
}

export function deliveryNotificationPayload(
  delivery: DeliveryReceipt,
): DesktopDeliveryNotification | null {
  const deliveryId = nonEmptyString(delivery.delivery_id, 300);
  if (!deliveryId) return null;
  return {
    deliveryId,
    title: nonEmptyString(delivery.title, 240) || 'Argus',
    summary: nonEmptyString(delivery.summary, 1_000),
    ...(delivery.primary_target?.path
      ? { path: nonEmptyString(delivery.primary_target.path, 1_000) }
      : {}),
  };
}

function embeddedDesktopParent(): Window | null {
  if (typeof window === 'undefined' || window.parent === window) return null;
  return window.parent;
}

function notificationPayload(value: unknown): DesktopDeliveryNotification | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const deliveryId = nonEmptyString(row.deliveryId, 300);
  if (!deliveryId) return null;
  const path = nonEmptyString(row.path, 1_000);
  return {
    deliveryId,
    title: nonEmptyString(row.title, 240) || 'Argus',
    summary: nonEmptyString(row.summary, 1_000),
    ...(path ? { path } : {}),
  };
}

export function notifyDesktopCompletion(
  notification: DesktopDeliveryNotification,
): Promise<boolean> {
  const payload = notificationPayload(notification);
  const parent = embeddedDesktopParent();
  if (!payload || !parent) return Promise.resolve(false);
  parent.postMessage({ type: 'argus:notify-completion', payload }, '*');
  return Promise.resolve(true);
}

export function notifyDesktopDelivery(delivery: DeliveryReceipt): Promise<boolean> {
  const payload = deliveryNotificationPayload(delivery);
  return payload ? notifyDesktopCompletion(payload) : Promise.resolve(false);
}

export function setDesktopLargePreview(active: boolean): void {
  const parent = embeddedDesktopParent();
  if (!parent) return;
  parent.postMessage({ type: 'argus:large-preview', payload: active }, '*');
}

export function subscribeDesktopDelivery(
  callback: (payload: DesktopDeliveryNotification) => void,
): () => void {
  const parent = embeddedDesktopParent();
  if (!parent) return () => undefined;
  const listener = (event: MessageEvent<EmbeddedDesktopMessage>): void => {
    if (event.source !== parent || event.data?.type !== 'argus:open-delivery') return;
    const payload = notificationPayload(event.data.payload);
    if (payload) callback(payload);
  };
  window.addEventListener('message', listener);
  return () => window.removeEventListener('message', listener);
}

export function subscribeDesktopNewChat(callback: () => void): () => void {
  const parent = embeddedDesktopParent();
  if (!parent) return () => undefined;
  const listener = (event: MessageEvent<EmbeddedDesktopMessage>): void => {
    if (event.source === parent && event.data?.type === 'argus:new-chat') callback();
  };
  window.addEventListener('message', listener);
  return () => window.removeEventListener('message', listener);
}

/** Route external links through the outer Tauri shell instead of navigating the cockpit iframe. */
export function installDesktopExternalLinkBridge(): () => void {
  if (typeof document === 'undefined') return () => undefined;
  const parent = embeddedDesktopParent();
  if (!parent) return () => undefined;
  const listener = (event: MouseEvent): void => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    const anchor = (event.target as Element | null)?.closest<HTMLAnchorElement>('a[href]');
    if (!anchor) return;
    let target: URL;
    try {
      target = new URL(anchor.href, window.location.href);
    } catch {
      return;
    }
    if (target.origin === window.location.origin || !['http:', 'https:'].includes(target.protocol)) return;
    event.preventDefault();
    parent.postMessage({ type: 'argus:open-external', payload: target.toString() }, '*');
  };
  document.addEventListener('click', listener, true);
  return () => document.removeEventListener('click', listener, true);
}
