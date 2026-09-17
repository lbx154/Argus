import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi, type Mock } from 'vitest';
import { Modal } from './Modal';
import { registerModal } from './modalStack';

vi.mock('../lib/motion', () => ({ useGsapMotion: () => {} }));
vi.mock('../i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }));

class FocusNode {
  isConnected = true;
  domOrder = 0;
  nodes: FocusNode[] = [];
  constructor(readonly name: string) {}
  focus() { activeElement = this; }
  contains(node: unknown): boolean { return node === this || this.nodes.some(child => child.contains(node)); }
  compareDocumentPosition(node: FocusNode) {
    if (node === this) return 0;
    if (this.contains(node)) return 4;
    if (node.contains(this)) return 2;
    return this.domOrder < node.domOrder ? 4 : 2;
  }
  querySelector(selector: string) { return selector === '[data-autofocus]' ? null : this.nodes[0] ?? null; }
  querySelectorAll() { return this.nodes; }
  getAttribute() { return null; }
  getClientRects() { return [{}]; }
}
let activeElement: FocusNode;
let origin: FocusNode;
let dialogs: Map<string, FocusNode>;
let listeners: Set<(event: KeyboardEvent) => void>;
let frames: Map<number, FrameRequestCallback>;
let frameId: number;
let renderer: ReactTestRenderer | undefined;
let lowerClose: Mock<() => void>;
let upperClose: Mock<() => void>;
let view: { lower: boolean; upper: boolean };
function tree() {
  return <>
    <Modal open={view.lower} onClose={lowerClose} label="lower"><button>Lower action</button></Modal>
    <Modal open={view.upper} onClose={upperClose} label="upper"><button>Upper action</button></Modal>
  </>;
}
function render() {
  act(() => {
    if (renderer) renderer.update(tree());
    else renderer = create(tree(), { createNodeMock: element => {
      if (element.props.role !== 'dialog') return new FocusNode('backdrop');
      const node = new FocusNode(element.props['aria-label']);
      node.domOrder = node.name === 'lower' ? 1 : 2;
      node.nodes = [new FocusNode(`${node.name}-first`), new FocusNode(`${node.name}-last`)];
      dialogs.set(node.name, node);
      return node;
    } });
  });
}
function flushFrames() {
  const pending = [...frames.values()]; frames.clear();
  act(() => { for (const callback of pending) callback(0); });
}
function key(key: string, shiftKey = false) {
  const event = { key, shiftKey, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, stopImmediatePropagation() {} };
  act(() => { for (const callback of [...listeners]) callback(event as KeyboardEvent); });
  return event;
}
function openUpper() { view.upper = true; render(); flushFrames(); }

beforeEach(() => {
  origin = new FocusNode('origin'); activeElement = origin;
  dialogs = new Map(); listeners = new Set(); frames = new Map(); frameId = 0;
  lowerClose = vi.fn(); upperClose = vi.fn(); view = { lower: true, upper: false };
  vi.stubGlobal('HTMLElement', FocusNode);
  vi.stubGlobal('Node', { DOCUMENT_POSITION_DISCONNECTED: 1, DOCUMENT_POSITION_FOLLOWING: 4 });
  vi.stubGlobal('document', { get activeElement() { return activeElement; }, body: new FocusNode('body') });
  vi.stubGlobal('getComputedStyle', () => ({ visibility: 'visible' }));
  vi.stubGlobal('window', {
    addEventListener: (type: string, callback: (event: KeyboardEvent) => void) => { if (type === 'keydown') listeners.add(callback); },
    removeEventListener: (type: string, callback: (event: KeyboardEvent) => void) => { if (type === 'keydown') listeners.delete(callback); },
    requestAnimationFrame: (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame: (id: number) => frames.delete(id),
  });
});
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; vi.unstubAllGlobals(); });

it('Escape closes only the upper dialog', () => {
  render(); flushFrames(); openUpper(); key('Escape');
  expect(upperClose).toHaveBeenCalledOnce();
  expect(lowerClose).not.toHaveBeenCalled();
});

it('Shift+Tab in the middle of the upper dialog stays there', () => {
  render(); flushFrames(); openUpper();
  const upper = dialogs.get('upper')!;
  upper.nodes[1].focus();
  expect(key('Tab', true).defaultPrevented).toBe(false);
  expect(activeElement.name).toBe(upper.nodes[1].name);
});

it('closing a lower dialog out of order keeps upper focus and restores the surviving origin later', () => {
  render(); flushFrames(); openUpper();
  const upper = dialogs.get('upper')!, lower = dialogs.get('lower')!;
  view.lower = false; render();
  expect(activeElement.name).toBe(upper.nodes[0].name);
  lower.isConnected = false;
  lower.nodes.forEach(node => { node.isConnected = false; });
  view.upper = false; render();
  expect(activeElement.name).toBe(origin.name);
});

it('restores the exact lower opener, then the original page control', () => {
  render(); flushFrames();
  const opener = dialogs.get('lower')!.nodes[1];
  opener.focus(); openUpper();
  view.upper = false; render();
  expect(activeElement.name).toBe(opener.name);
  view.lower = false; render();
  expect(activeElement.name).toBe(origin.name);
});

it('wraps both Tab directions only within the top dialog and recaptures outside focus', () => {
  render(); flushFrames(); openUpper();
  const upper = dialogs.get('upper')!;
  expect(key('Tab', true).defaultPrevented).toBe(true);
  expect(activeElement.name).toBe(upper.nodes[1].name);
  expect(key('Tab').defaultPrevented).toBe(true);
  expect(activeElement.name).toBe(upper.nodes[0].name);
  origin.focus(); key('Tab');
  expect(activeElement.name).toBe(upper.nodes[0].name);
  origin.focus(); key('Tab', true);
  expect(activeElement.name).toBe(upper.nodes[1].name);
});

it('uses the top dialog itself if it has no focusable controls', () => {
  render(); flushFrames(); openUpper();
  const upper = dialogs.get('upper')!;
  upper.nodes = []; key('Tab');
  expect(activeElement.name).toBe('upper');
});

it('does not let an older pending autofocus frame take focus from the newer dialog', () => {
  render();
  const lowerFocus = vi.spyOn(dialogs.get('lower')!.nodes[0], 'focus');
  view.upper = true; render(); flushFrames();
  expect(lowerFocus).not.toHaveBeenCalled();
  expect(activeElement.name).toBe('upper-first');
});

it('a synchronous top close does not deliver the same Escape to the exposed lower dialog', () => {
  render(); flushFrames(); openUpper();
  upperClose.mockImplementation(() => { view.upper = false; render(); });
  key('Escape');
  expect(upperClose).toHaveBeenCalledOnce();
  expect(lowerClose).not.toHaveBeenCalled();
  expect(activeElement.name).toBe('lower-first');
  key('Escape');
  expect(lowerClose).toHaveBeenCalledOnce();
  expect(upperClose).toHaveBeenCalledOnce();
});

it('does not restore over focus deliberately moved outside the closing dialog', () => {
  render(); flushFrames(); openUpper();
  const other = new FocusNode('other'); other.focus();
  view.upper = false; render();
  expect(activeElement.name).toBe('other');
});

it('keeps a nested child above its parent when React registers the child effect first', () => {
  const parent = new FocusNode('parent'), child = new FocusNode('child');
  parent.nodes = [child]; child.nodes = [new FocusNode('child-action')];
  const childClose = vi.fn(), parentClose = vi.fn();
  const removeChild = registerModal(child as unknown as HTMLElement, childClose);
  const removeParent = registerModal(parent as unknown as HTMLElement, parentClose);
  try {
    flushFrames(); key('Escape');
    expect(activeElement.name).toBe('child-action');
    expect(childClose).toHaveBeenCalledOnce();
    expect(parentClose).not.toHaveBeenCalled();
  } finally { removeParent(); removeChild(); }
  expect(activeElement.name).toBe(origin.name);
  expect(listeners.size).toBe(0);
});

it('uses the latest close callback without changing the active layer', () => {
  render(); flushFrames(); openUpper();
  const original = upperClose;
  upperClose = vi.fn(); render(); key('Escape');
  expect(original).not.toHaveBeenCalled();
  expect(upperClose).toHaveBeenCalledOnce();
});

it('removes the keyboard listener on final close and registers it once when reopened', () => {
  render(); flushFrames(); openUpper();
  expect(listeners.size).toBe(1);
  view = { lower: false, upper: false }; render();
  expect(listeners.size).toBe(0);
  key('Escape');
  expect(lowerClose).not.toHaveBeenCalled();
  expect(upperClose).not.toHaveBeenCalled();
  view.lower = true; render(); flushFrames();
  expect(listeners.size).toBe(1);
  key('Escape');
  expect(lowerClose).toHaveBeenCalledOnce();
  expect(upperClose).not.toHaveBeenCalled();
});

it('keeps a visually higher sibling in control when an earlier DOM modal opens later', () => {
  view = { lower: false, upper: true }; render(); flushFrames();
  view.lower = true; render();
  const lowerFocus = vi.spyOn(dialogs.get('lower')!.nodes[0], 'focus');
  flushFrames(); key('Escape');
  expect(lowerFocus).not.toHaveBeenCalled();
  expect(activeElement.name).toBe('upper-first');
  expect(upperClose).toHaveBeenCalledOnce();
  expect(lowerClose).not.toHaveBeenCalled();
  view.upper = false; render();
  expect(activeElement.name).toBe('lower-first');
  view.lower = false; render();
  expect(activeElement.name).toBe(origin.name);
});
