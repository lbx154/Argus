import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { ComposerSurface } from '../components/ComposerSurface';
import { ChatBox } from '../components/ChatBox';
import { MapComposer } from '../map/MapComposer';

const mainProps = {
  value: 'Research question', onChange: vi.fn(), onSend: vi.fn(async () => false), onCancel: vi.fn(),
  disabled: false, pending: false, slashSelection: 0, onSlashSelectionChange: vi.fn(),
  attachments: [] as File[], onAttachmentsChange: vi.fn(),
};

describe('shared conversation surface', () => {
  it('keeps Argus attachment and send controls in the conversation and expandable map composer', () => {
    const main = renderToStaticMarkup(createElement(ChatBox, mainProps));
    const map = renderToStaticMarkup(createElement(MapComposer, {
      ...mainProps, onSend: async () => false, focusSignal: 0, sessionName: 'fixture', historical: false, zh: true,
    }));
    for (const html of [main, map]) {
      expect(html).toMatch(/class="map-composer(?:"| )/);
      expect(html).toContain('data-logo="rounded-mark"');
      expect(html).toContain('class="map-send');
      expect(html).not.toContain('📎');
    }
    expect(main).toContain('composer-surface');
    expect(map).toContain('map-island-surface');
  });

  it('preserves IME composition and Shift+Enter, and sends once on normal Enter', () => {
    const send = vi.fn();
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(ComposerSurface, {
      value: 'test', onChange: vi.fn(), inputRef: { current: null }, fileInputRef: { current: null },
      onFiles: vi.fn(), inputProps: {}, onSend: send, onCancel: vi.fn(), pending: false,
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
});
