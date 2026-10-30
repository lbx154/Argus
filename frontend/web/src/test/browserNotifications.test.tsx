import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useBrowserNotifications } from '../useBrowserNotifications';
import { useAttentionSignals } from '../useAttentionSignals';
import { BrowserNotificationSettings } from '../components/BrowserNotificationSettings';
import { projectAttention, type Attention } from '../lib/attention';
import type { Snapshot, DeliveryReceipt } from '../../../core/src/types';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'zh-CN' }) }));
const sent = vi.fn();
const storage = new Map<string, string>();
class BrowserNotification {
  static permission: NotificationPermission = 'default';
  static requestPermission = vi.fn(async (): Promise<NotificationPermission> => {
    BrowserNotification.permission = 'granted';
    return 'granted';
  });
  constructor(title: string, options: NotificationOptions) { sent(title, options); }
  onclick: (() => void) | null = null;
  close = vi.fn();
}
const reply: Attention = { kind: 'reply', key: 'q1', title: '等你回答', body: '用哪个数据集？' };
const delivery: Attention = { kind: 'done', key: 'd1', title: '交付了', body: '结果' };
let view: ReactTestRenderer | undefined;
let controls: ReturnType<typeof useBrowserNotifications>;
let focus: EventTarget;
let otherView: ReactTestRenderer | undefined;
const held = new Set<string>();
const lockRequest = vi.fn<(name: string, options: LockOptions, callback: LockGrantedCallback<unknown>) => Promise<unknown>>();
function Page({ projectId = 'p1', attention = reply }: { projectId?: string; attention?: Attention | null }) {
  controls = useBrowserNotifications();
  useAttentionSignals({ projectId, projectName: '数据研究', attention, zh: true, notificationsEnabled: controls.enabled });
  return <BrowserNotificationSettings notifications={controls} />;
}
const mount = async (attention: Attention | null = reply) => { await act(async () => { view = create(<Page attention={attention} />); }); };
const update = async (attention: Attention | null, projectId = 'p1') => { await act(async () => { view!.update(<Page attention={attention} projectId={projectId} />); }); };
beforeEach(() => {
  held.clear();
  lockRequest.mockReset().mockImplementation((name, options, callback) => {
    expect(options).toEqual({ ifAvailable: true });
    const lock = held.has(name) ? null : { name, mode: 'exclusive' } as Lock;
    if (lock) held.add(name);
    return Promise.resolve().then(() => callback(lock)).finally(() => { if (lock) held.delete(name); });
  });
  vi.stubGlobal('navigator', { locks: { request: lockRequest } });
  storage.clear(); sent.mockReset(); BrowserNotification.permission = 'default';
  BrowserNotification.requestPermission.mockReset().mockImplementation(async () => {
    BrowserNotification.permission = 'granted'; return 'granted';
  });
  focus = new EventTarget();
  const doc = new EventTarget();
  vi.stubGlobal('document', Object.assign(doc, { title: 'Argus', hidden: false }));
  vi.stubGlobal('window', Object.assign(focus, { focus: vi.fn() }));
  vi.stubGlobal('isSecureContext', true);
  vi.stubGlobal('Notification', BrowserNotification);
  vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value) });
});
afterEach(() => { act(() => { view?.unmount(); otherView?.unmount(); }); view = undefined; otherView = undefined; vi.unstubAllGlobals(); });

it('defaults off even with browser permission, and asks only from the enable action', async () => {
  BrowserNotification.permission = 'granted';
  await mount(); await update(delivery);
  expect(controls.enabled).toBe(false);
  expect(view!.root.findByProps({ role: 'switch' }).props['aria-checked']).toBe(false);
  expect(sent).not.toHaveBeenCalled();
  expect(BrowserNotification.requestPermission).not.toHaveBeenCalled();
  await act(async () => { await controls.change(); });
  expect(BrowserNotification.requestPermission).toHaveBeenCalledOnce();
  expect(controls.enabled).toBe(true);
  expect(sent).toHaveBeenCalledExactlyOnceWith('交付了 · 数据研究', expect.objectContaining({ body: '结果' }));
  await act(async () => { await controls.change(); });
  await update(reply);
  expect(sent).toHaveBeenCalledTimes(1);
  expect(storage.get('argus.browserNotifications')).toBe('off');
});

it('notifies once per question or delivery across state refreshes, navigation and reloads', async () => {
  await mount();
  await act(async () => { await controls.change(); });
  await update({ ...reply }); await update(null); await update(reply);
  await update(delivery); await update({ ...delivery, title: 'Delivered' });
  await update(reply, 'p2'); await update(reply);
  expect(sent).toHaveBeenCalledTimes(3);
  act(() => view!.unmount()); view = undefined;
  await mount(); await update(delivery);
  expect(controls.enabled).toBe(true);
  expect(sent).toHaveBeenCalledTimes(3);
  expect(BrowserNotification.requestPermission).toHaveBeenCalledOnce();
});

it('keeps a denied request off and explains how to change the browser permission', async () => {
  BrowserNotification.requestPermission.mockImplementation(async () => {
    BrowserNotification.permission = 'denied'; return 'denied';
  });
  await mount();
  await act(async () => { await controls.change(); });
  expect(controls.enabled).toBe(false);
  expect(JSON.stringify(view!.toJSON())).toContain('通知权限已被拒绝');
  expect(storage.get('argus.browserNotifications')).toBe('off');
  await update(delivery);
  await act(async () => { await controls.change(); });
  expect(BrowserNotification.requestPermission).toHaveBeenCalledOnce();
  expect(sent).not.toHaveBeenCalled();
  BrowserNotification.permission = 'granted';
  BrowserNotification.requestPermission.mockResolvedValue('granted');
  act(() => { focus.dispatchEvent(new Event('focus')); });
  await act(async () => { await controls.change(); });
  expect(controls.enabled).toBe(true);
  expect(sent).toHaveBeenCalledOnce();
});

it('turns the setting off when permission was revoked and disables unsupported browsers', async () => {
  storage.set('argus.browserNotifications', 'on'); BrowserNotification.permission = 'granted';
  await mount();
  BrowserNotification.permission = 'denied';
  act(() => { focus.dispatchEvent(new Event('focus')); });
  await update(delivery);
  expect(controls.enabled).toBe(false);
  expect(sent).toHaveBeenCalledTimes(1);
  act(() => view!.unmount()); view = undefined;
  vi.stubGlobal('Notification', undefined);
  await mount();
  expect(controls.permission).toBe('unsupported');
  expect(view!.root.findByProps({ role: 'switch' }).props.disabled).toBe(true);
});

it('does not retry a rejected notification or crash when permission requests fail', async () => {
  sent.mockImplementationOnce(() => { throw new Error('not supported'); });
  await mount();
  await act(async () => { await controls.change(); });
  await update({ ...reply }); await update(null); await update(reply);
  expect(sent).toHaveBeenCalledTimes(1);
  await act(async () => { await controls.change(); });
  BrowserNotification.requestPermission.mockRejectedValueOnce(new Error('request failed'));
  await act(async () => { await controls.change(); });
  expect(controls.enabled).toBe(false);
  expect(controls.requesting).toBe(false);
});

it('reads stable full question identity and real delivery receipts, without treating idle or failure as delivery', () => {
  const question = (id: string, text: string) => ({ operator_decision: { id, revision: 1, status: 'pending', question: text } });
  const snapshot = { backlog: [], pending_questions: [question('manager', 'a'.repeat(80) + ' one')] } as unknown as Snapshot;
  const first = projectAttention({ snapshot, zh: true })!;
  expect(projectAttention({ snapshot, zh: false })?.key).toBe(first.key);
  expect(projectAttention({ snapshot: { ...snapshot, pending_questions: [question('manager', 'a'.repeat(80) + ' two')] }, zh: true })?.key).not.toBe(first.key);
  expect(projectAttention({ snapshot: { ...snapshot, pending_questions: [question('planner', 'a'.repeat(80) + ' one')] }, zh: true })?.key).not.toBe(first.key);
  const clear = { ...snapshot, pending_questions: [] };
  expect(projectAttention({ snapshot: clear, zh: true })).toBeNull();
  expect(projectAttention({ snapshot: clear, delivery: { delivery_id: 'd42', kind: 'result', title: '真实交付' } as DeliveryReceipt, zh: true }))
    .toMatchObject({ kind: 'done', key: 'done:d42', title: '交付了', body: '真实交付' });
});

it('syncs another tab’s enable, disable and clear without asking for permission', async () => {
  BrowserNotification.permission = 'granted';
  await mount(null);
  const notifyStorage = (key: string | null) => act(() => {
    focus.dispatchEvent(Object.assign(new Event('storage'), { key }));
  });
  storage.set('argus.browserNotifications', 'on');
  notifyStorage('unrelated');
  expect(controls.enabled).toBe(false);
  notifyStorage('argus.browserNotifications');
  expect(controls.enabled).toBe(true);
  storage.set('argus.browserNotifications', 'off');
  notifyStorage('argus.browserNotifications');
  expect(controls.enabled).toBe(false);
  storage.set('argus.browserNotifications', 'on');
  notifyStorage('argus.browserNotifications');
  storage.clear();
  notifyStorage(null);
  expect(controls.enabled).toBe(false);
  expect(BrowserNotification.requestPermission).not.toHaveBeenCalled();
});

it('claims a notification once across two tabs before invoking the browser', async () => {
  storage.set('argus.browserNotifications', 'on'); BrowserNotification.permission = 'granted';
  let claimedBeforeConstructor = false;
  sent.mockImplementation(() => {
    claimedBeforeConstructor = [...storage.entries()].filter(([key, value]) => key.startsWith('argus.notification:') && value === 'sent').length === 1;
  });
  await act(async () => { view = create(<Page />); otherView = create(<Page />); });
  expect(claimedBeforeConstructor).toBe(true);
  expect(sent).toHaveBeenCalledTimes(1);
  expect(lockRequest).toHaveBeenCalledTimes(2);
});

it('reads a cross-tab disable again before sending, even before its storage event arrives', async () => {
  storage.set('argus.browserNotifications', 'on'); BrowserNotification.permission = 'granted';
  let grant!: () => void;
  lockRequest.mockImplementationOnce((name, _options, callback) => new Promise(resolve => {
    grant = () => { resolve(callback({ name, mode: 'exclusive' } as Lock)); };
  }));
  await mount();
  storage.set('argus.browserNotifications', 'off');
  await act(async () => { grant(); });
  expect(sent).not.toHaveBeenCalled();
  act(() => { focus.dispatchEvent(Object.assign(new Event('storage'), { key: 'argus.browserNotifications' })); });
  expect(controls.enabled).toBe(false);
});

it('does not wait or retry when another tab owns the occurrence', async () => {
  storage.set('argus.browserNotifications', 'on'); BrowserNotification.permission = 'granted';
  lockRequest.mockImplementationOnce(async (_name, _options, callback) => callback(null));
  await mount(); await update({ ...reply });
  expect(lockRequest).toHaveBeenCalledTimes(1);
  expect(sent).not.toHaveBeenCalled();
});

it('does not send without a durable cross-tab claim or native mutual exclusion', async () => {
  storage.set('argus.browserNotifications', 'on'); BrowserNotification.permission = 'granted';
  vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null,
    setItem: () => { throw new Error('storage blocked'); } });
  await mount();
  expect(sent).not.toHaveBeenCalled();
  act(() => view!.unmount()); view = undefined;
  vi.stubGlobal('navigator', {});
  await mount();
  expect(controls.permission).toBe('unsupported');
  expect(view!.root.findByProps({ role: 'switch' }).props.disabled).toBe(true);
});
