import { afterEach, describe, expect, it, vi } from 'vitest';
import { installDesktopExternalLinkBridge } from '../lib/desktopBridge';

let dispose: (() => void) | undefined;
afterEach(() => {
  dispose?.();
  dispose = undefined;
  vi.unstubAllGlobals();
});

function setup() {
  const postMessage = vi.fn();
  const windowEvents = new EventTarget();
  const documentEvents = new EventTarget();
  vi.stubGlobal('window', Object.assign(windowEvents, {
    parent: { postMessage }, location: { href: 'http://127.0.0.1:18799/', origin: 'http://127.0.0.1:18799' },
  }));
  vi.stubGlobal('document', documentEvents);
  dispose = installDesktopExternalLinkBridge();
  return { postMessage, windowEvents, documentEvents };
}

function key(name: string, overrides = {}) {
  return Object.assign(new Event('keydown', { cancelable: true }), {
    key: name, ctrlKey: true, metaKey: false, altKey: false, shiftKey: false,
    isComposing: false, ...overrides,
  });
}

describe('embedded desktop controls', () => {
  it('forwards settings and new-chat shortcuts from inside the iframe', () => {
    const { windowEvents, postMessage } = setup();
    for (const [name, type] of [[',', 'argus:show-setup'], ['n', 'argus:request-new-chat']]) {
      const event = key(name);
      windowEvents.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      expect(postMessage).toHaveBeenLastCalledWith({ type }, '*');
    }
  });

  it('does not steal composition, modified shortcuts or ordinary Web shortcuts', () => {
    const { windowEvents, postMessage } = setup();
    for (const event of [key('n', { isComposing: true }), key('n', { shiftKey: true }), key('k'), key(',', { ctrlKey: false })]) {
      windowEvents.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(false);
    }
    expect(postMessage).not.toHaveBeenCalled();
  });

  it('closes shell menus on cockpit interaction and removes listeners on unmount', () => {
    const { windowEvents, documentEvents, postMessage } = setup();
    documentEvents.dispatchEvent(new Event('pointerdown'));
    expect(postMessage).toHaveBeenCalledWith({ type: 'argus:cockpit-interaction' }, '*');
    dispose?.();
    postMessage.mockClear();
    windowEvents.dispatchEvent(key(','));
    documentEvents.dispatchEvent(new Event('pointerdown'));
    expect(postMessage).not.toHaveBeenCalled();
  });

  it('opens modified/middle external clicks via the shell without navigating the iframe', () => {
    const { documentEvents, postMessage } = setup();
    class Anchor {
      href = 'https://example.com/research';
      closest() { return this; }
    }
    vi.stubGlobal('Element', Anchor);
    for (const [type, button] of [['click', 0], ['auxclick', 1]] as const) {
      const event = Object.assign(new Event(type, { cancelable: true }), { button, ctrlKey: true });
      Object.defineProperty(event, 'target', { value: new Anchor() });
      documentEvents.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      expect(postMessage).toHaveBeenLastCalledWith({ type: 'argus:open-external', payload: 'https://example.com/research' }, '*');
    }
  });
});
