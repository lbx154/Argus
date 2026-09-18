import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DeliveryReceipt } from '../../../core/src/types';
import { completionNotificationPayload, deliveryNotificationPayload, notifyDesktopCompletion, notifyDesktopDelivery, subscribeDesktopDelivery } from '../lib/desktopBridge';

const receipt: DeliveryReceipt = { schema_version: 1, delivery_id: 'delivery:fixture', kind: 'task_completed', item_id: 'fixture', title: 'Synthetic result', summary: 'Synthetic fixture, not review evidence', status: 'done', review_status: 'pending', delivered_at: 1,
  primary_target: { path: 'results/report.md', label: 'Report', source: 'delivery', why: 'Synthetic fixture' }, targets: [] };
const old = { deliveryId: receipt.delivery_id, title: receipt.title, summary: receipt.summary, path: receipt.primary_target!.path };
let parent: { postMessage: ReturnType<typeof vi.fn> };
let release: (() => void) | undefined;
beforeEach(() => {
  parent = { postMessage: vi.fn() };
  vi.stubGlobal('window', Object.assign(new EventTarget(), { parent }));
  vi.stubGlobal('document', { referrer: 'http://tauri.localhost/' });
});
afterEach(() => { release?.(); release = undefined; vi.unstubAllGlobals(); });
function incoming(payload: unknown, origin = 'http://tauri.localhost', source: unknown = parent) {
  window.dispatchEvent(Object.assign(new Event('message'), { source, origin, data: { type: 'argus:open-delivery', payload } }));
}

describe('optional desktop notification session identity', () => {
  it('preserves legacy JSON without inventing a sessionId', async () => {
    expect(deliveryNotificationPayload(receipt)).toEqual(old);
    expect(await notifyDesktopCompletion(JSON.parse(JSON.stringify(old)))).toBe(true);
    const receive = vi.fn(); release = subscribeDesktopDelivery(receive);
    incoming(old);
    expect(receive).toHaveBeenCalledWith(old);
    expect(Object.hasOwn(receive.mock.calls[0][0], 'sessionId')).toBe(false);
  });

  it.each(['s-A', 's-研究_1', 'legacy.project-1'])('round-trips %s through completion, delivery, native forwarding and click parsing', async sessionId => {
    const completion = completionNotificationPayload({ completionId: 'completion:s-A:fixture', title: '**Synthetic**', summary: 'Fixture', path: 'results/report.md', sessionId });
    expect(completion?.sessionId).toBe(sessionId);
    expect(deliveryNotificationPayload(receipt, sessionId)).toEqual({ ...old, sessionId });
    expect(await notifyDesktopDelivery(receipt, sessionId)).toBe(true);
    const sent = parent.postMessage.mock.calls.at(-1)![0];
    expect(sent).toEqual({ type: 'argus:notify-completion', payload: { ...old, sessionId } });
    const receive = vi.fn(); release = subscribeDesktopDelivery(receive);
    incoming(JSON.parse(JSON.stringify(sent.payload)));
    expect(receive).toHaveBeenCalledWith({ ...old, sessionId });
  });

  it.each(['', ' s-A', 's-A ', '../s-A', 's-A/s-B', 's-A\\s-B', 's-A\u0000', 's-A\n', 's-A%2fs-B', 'x'.repeat(129), null, 4, {}, []])('rejects a malformed explicit identity rather than downgrading or truncating it: %j', async sessionId => {
    const receive = vi.fn(); release = subscribeDesktopDelivery(receive);
    incoming({ ...old, sessionId });
    expect(receive).not.toHaveBeenCalled();
    expect(await notifyDesktopCompletion({ ...old, sessionId } as never)).toBe(false);
    expect(parent.postMessage).not.toHaveBeenCalled();
  });

  it('rejects a non-parent source and a parent message with the wrong origin', () => {
    const receive = vi.fn(); release = subscribeDesktopDelivery(receive);
    incoming({ ...old, sessionId: 's-A' }, 'http://tauri.localhost', {});
    incoming({ ...old, sessionId: 's-A' }, 'https://untrusted.invalid');
    expect(receive).not.toHaveBeenCalled();
    incoming({ ...old, sessionId: 's-A' });
    expect(receive).toHaveBeenCalledTimes(1);
  });

  it('uses browser-provided ancestry for the no-referrer desktop iframe', () => {
    vi.stubGlobal('document', { referrer: '' });
    Object.defineProperty(window, 'location', { value: { ancestorOrigins: ['http://127.0.0.1:1428'] } });
    const receive = vi.fn(); release = subscribeDesktopDelivery(receive);
    incoming(old, 'http://127.0.0.1:9999');
    expect(receive).not.toHaveBeenCalled();
    incoming(old, 'http://127.0.0.1:1428');
    expect(receive).toHaveBeenCalledWith(old);
  });

  it('does not trust arbitrary loopback senders when a referrer is unavailable', () => {
    vi.stubGlobal('document', { referrer: '' });
    const receive = vi.fn(); release = subscribeDesktopDelivery(receive);
    incoming(old, 'http://127.0.0.1:9999');
    expect(receive).not.toHaveBeenCalled();
    incoming(old, 'http://tauri.localhost');
    expect(receive).toHaveBeenCalledWith(old);
  });
});
