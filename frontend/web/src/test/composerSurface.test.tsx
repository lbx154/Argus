import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ComposerSurface } from '../components/ComposerSurface';
import { ChatBox } from '../components/ChatBox';
import { MapComposer } from '../map/MapComposer';
import { referenceText } from '../map/presentation';

const mainProps = {
  value: 'Research question', onChange: vi.fn(), onSend: vi.fn(async () => false), 
  disabled: false, pending: false, completionSelection: 0, onCompletionSelectionChange: vi.fn(),
  attachments: [] as File[], onAttachmentsChange: vi.fn(),
};

describe('shared conversation surface', () => {
  it('keeps the shared attachment and send affordances in both composer layouts', () => {
    const main = renderToStaticMarkup(createElement(ChatBox, mainProps));
    const map = renderToStaticMarkup(createElement(MapComposer, {
      ...mainProps, onSend: async () => false, focusSignal: 0, zh: true,
    }));
    for (const html of [main, map]) {
      expect(html).toMatch(/class="[^"]*\bmap-composer\b/);
      expect(html).toContain('type="file"');
      expect(html).toContain('aria-label="attach files"');
      expect(html).toContain('<textarea');
      expect(html).toContain('class="map-send');
      expect(html).not.toContain('📎');
    }
    expect(main).toContain('lucide-paperclip');
    expect(map).toContain('lucide-paperclip');
  });

  it('preserves IME composition and Shift+Enter, and sends once on normal Enter', () => {
    const send = vi.fn();
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(ComposerSurface, {
      value: 'test', onChange: vi.fn(), inputRef: { current: null }, fileInputRef: { current: null },
      onFiles: vi.fn(), inputProps: {}, onSend: send, pending: false,
    })); });
    const key = renderer.root.findByType('textarea').props.onKeyDown;
    const event = (extra = {}) => ({ key: 'Enter', shiftKey: false, defaultPrevented: false,
      nativeEvent: { isComposing: false }, preventDefault: vi.fn(), ...extra });
    act(() => key(event({ nativeEvent: { isComposing: true } })));
    act(() => key(event({ shiftKey: true })));
    expect(send).not.toHaveBeenCalled();
    act(() => key(event()));
    expect(send).toHaveBeenCalledTimes(1);
    act(() => renderer.unmount());
  });

  it('leaves failed submissions and shared attachments with their owning controller', async () => {
    const changed = vi.fn();
    const filesChanged = vi.fn();
    const send = vi.fn(async () => false);
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(ChatBox, { ...mainProps, onChange: changed, onAttachmentsChange: filesChanged, onSend: send })); });
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
    expect(send).toHaveBeenCalledTimes(1);
    expect(changed).not.toHaveBeenCalled();
    expect(filesChanged).not.toHaveBeenCalled();
    act(() => renderer.unmount());
  });

  afterEach(() => vi.unstubAllGlobals());

  it('mirrors only the visible text under the gray suggestion, in the textarea\'s own font', () => {
    const value = `${referenceText({ source: 'map', task_id: 't1', task_title: 'Study', event_ids: [] })}Compare`;
    const html = renderToStaticMarkup(createElement(ComposerSurface, {
      value, ghost: ' the baselines', onChange: vi.fn(), inputRef: { current: null }, fileInputRef: { current: null },
      onFiles: vi.fn(), inputProps: {}, onSend: vi.fn(), pending: false,
    }));
    expect(html).toContain('<div class="composer-ghost" aria-hidden="true">Compare<span> the baselines</span></div>');

    vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
    vi.stubGlobal('getComputedStyle', () => ({ fontFamily: 'Geist', fontSize: '14px', fontWeight: '400', lineHeight: '28px', letterSpacing: 'normal', marginTop: '8px', marginBottom: '8px', maxHeight: '56px' }));
    const textareaNode = vi.fn(() => ({ style: {}, getClientRects: () => [], addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const mirror = { style: {} as Record<string, string> };
    const accept = vi.fn();
    const props = { value: 'Compare', onChange: vi.fn(), inputRef: { current: null }, fileInputRef: { current: null },
      onFiles: vi.fn(), inputProps: {}, onSend: vi.fn(), pending: false, onAcceptGhost: accept };
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(ComposerSurface, props), {
      createNodeMock: (element) => element.type === 'textarea' ? textareaNode() : element.props.className === 'composer-ghost' ? mirror : null,
    }); });
    act(() => renderer.update(createElement(ComposerSurface, { ...props, ghost: ' the baselines' })));
    // A long suggestion grows the box no further than the textarea itself may grow.
    expect(mirror.style).toMatchObject({ fontSize: '14px', lineHeight: '28px', marginTop: '8px', maxHeight: '56px' });
    const span = renderer.root.findByProps({ className: 'composer-ghost' }).findByType('span');
    const down = { preventDefault: vi.fn() };
    act(() => span.props.onPointerDown(down));
    expect(down.preventDefault).toHaveBeenCalled();
    // A touch may still turn into a scroll or a long press; only the tap's click takes the text.
    expect(accept).not.toHaveBeenCalled();
    act(() => span.props.onClick());
    expect(accept).toHaveBeenCalledOnce();
    act(() => renderer.update(createElement(ComposerSurface, props)));
    // The gray text coming and going never remounts the textarea, so focus stays put.
    expect(textareaNode).toHaveBeenCalledOnce();
    act(() => renderer.unmount());
  });
});
